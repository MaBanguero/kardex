import json
import datetime
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .models import Documento, ConfiguracionSistema, DocumentoDetalle, Medicamento, User, Ubicacion, SolicitudStock
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from datetime import timedelta

# Importamos nuestros modelos y la lógica ACID
from .models import InventarioStock, PerfilUsuario
from .services import generar_excel_kardex, registrar_salida_paciente_inteligente, registrar_devolucion_agrupada, procesar_carga_masiva_productos
from django.contrib.auth.views import LoginView
from django.contrib.auth.models import Group
from django.urls import reverse_lazy


class CustomLoginView(LoginView):
    template_name = 'kardex/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        """Enruta al usuario según sus grupos (roles) después de un login exitoso"""
        usuario = self.request.user

        # Leemos los grupos a los que pertenece el usuario
        grupos_usuario = usuario.groups.values_list('name', flat=True)

        # Enrutamiento inteligente basado en múltiples roles
        if 'ADMIN' in grupos_usuario or 'REGENTE' in grupos_usuario:
            return reverse_lazy('admin_dashboard')
        elif 'ENFERMERA' in grupos_usuario:
            return reverse_lazy('dashboard')

        # Ruta por defecto si un usuario fue creado pero aún no se le asigna ningún rol
        return reverse_lazy('dashboard')



# ==========================================
# 1. VISTA PRINCIPAL (EL DASHBOARD SPA)
# ==========================================
@login_required
def dashboard_kardex(request):
    """
    Renderiza el contenedor principal. Como usamos una arquitectura SPA
    (Single Page Application) con LocalStorage, ya no enviamos todo el 
    inventario aquí. Solo enviamos la ubicación para los títulos.
    """
    try:
        ubicacion_actual = request.user.perfil.ubicacion_asignada

        if not ubicacion_actual:
            return render(request, 'kardex/error.html', {
                'mensaje': 'Tu usuario no tiene una sede/ubicación asignada. Contacta al administrador.'
            })

        return render(request, 'kardex/dashboard.html', {
            'ubicacion': ubicacion_actual
        })

    except PerfilUsuario.DoesNotExist:
        return render(request, 'kardex/error.html', {
            'mensaje': 'Este usuario no tiene un perfil clínico configurado. Créalo en el panel de administración.'
        })


# ==========================================
# 2. API PARA EL LOCALSTORAGE (PWA)
# ==========================================
@login_required
def sincronizar_inventario_api(request):
    """Envía el inventario al frontend, indicando si hay pedidos en curso"""
    ubicacion = request.user.perfil.ubicacion_asignada
    stock = InventarioStock.objects.filter(ubicacion=ubicacion).select_related('medicamento')

    # 1. Buscamos qué medicamentos YA tienen un pedido "PENDIENTE" en esta sede
    meds_pendientes = SolicitudStock.objects.filter(
        sede_solicitante=ubicacion,
        estado='PENDIENTE'
    ).values_list('medicamento_id', flat=True)

    data = []
    for item in stock:
        data.append({
            'id': item.id,
            'medicamento_id': item.medicamento.id,
            'principio_activo': item.medicamento.principio_activo,
            'forma_farmaceutica': item.medicamento.forma_farmaceutica,
            'concentracion': item.medicamento.concentracion,
            'lote': item.lote,
            'fecha_vencimiento': item.fecha_vencimiento.strftime('%Y-%m-%d') if item.fecha_vencimiento else '',
            'cantidad_actual': item.cantidad_actual,
            'stock_minimo': item.stock_minimo,
            'busqueda': f"{item.medicamento.principio_activo} {item.lote}".lower(),
            # 2. Marcamos TRUE si el ID del medicamento está en la lista de pendientes
            'en_tramite': item.medicamento.id in meds_pendientes
        })
    return JsonResponse({'inventario': data})


# ==========================================
# 3. PROCESAMIENTO DE MOVIMIENTOS (AJAX)
# ==========================================
@login_required
@require_POST
def registrar_movimiento_view(request):
    try:
        data = json.loads(request.body)
        tipo = data.get('tipo_mov')
        cantidad = int(data.get('cantidad', 0))
        id_paciente = data.get('id_paciente')

        if tipo == 'SALIDA':
            nombre_med = data.get('nombre_medicamento')
            registrar_salida_paciente_inteligente(request.user, nombre_med, cantidad, id_paciente)
            return JsonResponse({'status': 'success', 'requiere_sincronizacion': True})

        elif tipo == 'DEVOLUCION':
            # Cambiamos doc_id por el nombre del medicamento para permitir devoluciones masivas
            nombre_med = data.get('nombre_medicamento')

            registrar_devolucion_agrupada(request.user, nombre_med, cantidad, id_paciente)

            return JsonResponse({
                'status': 'success',
                'mensaje': f'Se han reingresado {cantidad} unidades al inventario.'
            })

    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)


# ==========================================
# 4. EXPORTACIÓN DE REPORTE EXCEL
# ==========================================
@login_required
def exportar_kardex_excel(request):
    """
    Genera el formato Horizontal del Kardex leyendo todos los movimientos
    del mes en curso, empaquetado usando OpenPyXL.
    """
    hoy = datetime.datetime.now()
    ubicacion_id = request.user.perfil.ubicacion_asignada.id

    # Llamamos a la lógica pesada que armamos en services.py
    wb = generar_excel_kardex(hoy.month, hoy.year, ubicacion_id)

    # Preparamos la respuesta HTTP para que el navegador descargue el archivo
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    # Nombramos el archivo con la fecha actual
    response['Content-Disposition'] = f'attachment; filename=Kardex_{hoy.strftime("%Y%m%d")}.xlsx'

    wb.save(response)
    return response


@login_required
def historial_movimientos_api(request):
    config = ConfiguracionSistema.objects.first()
    limite_horas = config.horas_limite_devolucion if config else 2

    hace_24h = timezone.now() - timedelta(hours=24)
    limite_devolucion = timezone.now() - timedelta(hours=limite_horas)

    # Obtenemos todas las salidas del usuario en las últimas 24h
    movimientos = Documento.objects.filter(
        usuario=request.user,
        tipo_mov='SALIDA',
        fecha__gte=hace_24h
    ).order_by('-fecha')

    historial = []
    for mov in movimientos:
        # Sumamos la cantidad total de este documento (por si afectó varios lotes)
        total_salida = mov.detalles.aggregate(total=Sum('cantidad'))['total'] or 0

        if total_salida == 0:
            continue

        # Buscamos todas las devoluciones que referencian a este documento
        total_devuelto = Documento.objects.filter(
            documento_referencia=mov,
            tipo_mov='DEVOLUCION'
        ).aggregate(total=Sum('detalles__cantidad'))['total'] or 0

        cantidad_restante = total_salida - total_devuelto
        tiempo_agotado = mov.fecha < limite_devolucion

        # Determinamos el estado
        if cantidad_restante <= 0:
            estado_txt = 'Devolución Completa'
            puede_devolver = False
        elif tiempo_agotado:
            estado_txt = 'Tiempo Expirado'
            puede_devolver = False
        else:
            estado_txt = 'Activo'
            puede_devolver = True

        # Tomamos el nombre del primer detalle para mostrarlo en la lista
        primer_detalle = mov.detalles.first()
        nombre_med = primer_detalle.medicamento.principio_activo if primer_detalle else "Medicamento desconocido"

        historial.append({
            'doc_id': mov.id,
            'medicamento': nombre_med,
            'cantidad_original': total_salida,
            'cantidad_devuelta': total_devuelto,
            'cantidad_restante': cantidad_restante,
            'paciente': mov.id_paciente,
            'fecha': mov.fecha.strftime("%I:%M %p - %d/%b"),
            'puede_devolver': puede_devolver,
            'estado_txt': estado_txt,
            # Enviamos un ID de stock genérico para la animación si es necesario
            'stock_id': primer_detalle.medicamento.id if primer_detalle else None
        })

    return JsonResponse({'historial': historial})


@login_required
def admin_dashboard_view(request):
    """Renderiza el dashboard administrativo según los roles del usuario"""

    roles_disponibles = Group.objects.all()
    if not roles_disponibles.exists():
        for r in ['ADMIN', 'REGENTE', 'ENFERMERA']:
            Group.objects.get_or_create(name=r)
        roles_disponibles = Group.objects.all()
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    es_admin = 'ADMIN' in grupos_usuario
    es_regente = 'REGENTE' in grupos_usuario

    if not (es_admin or es_regente):
        return render(request, 'kardex/error.html',
                      {'mensaje': 'Acceso denegado. Se requieren permisos administrativos.'})

    if es_admin:
        solicitudes = SolicitudStock.objects.select_related('medicamento', 'sede_solicitante').order_by(
            '-fecha_solicitud')
    else:
        solicitudes = SolicitudStock.objects.select_related('medicamento', 'sede_solicitante').filter(
            sede_solicitante=request.user.perfil.ubicacion_asignada).order_by('-fecha_solicitud')

    return render(request, 'kardex/admin_dashboard.html', {
        'es_admin': es_admin,
        'es_regente': es_regente,
        'ubicacion': request.user.perfil.ubicacion_asignada,
        'sedes': Ubicacion.objects.all(),
        'roles': roles_disponibles,
        'solicitudes': solicitudes,
        'usuarios': User.objects.select_related('perfil').prefetch_related('groups').all(),
        'medicamentos': Medicamento.objects.all()
    })


@login_required
@require_POST
def api_gestion_producto(request):
    """Crea o edita un registro de stock de forma manual (ADMIN y REGENTE)"""
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    if not ('ADMIN' in grupos_usuario or 'REGENTE' in grupos_usuario):
        return JsonResponse({'status': 'error', 'mensaje': 'No autorizado'}, status=403)

    try:
        data = json.loads(request.body)
        producto_id = data.get('id')
        ubicacion_actual = request.user.perfil.ubicacion_asignada

        with transaction.atomic():
            medicamento, _ = Medicamento.objects.get_or_create(
                principio_activo=data.get('principio_activo').strip().upper(),
                forma_farmaceutica=data.get('forma_farmaceutica').strip().upper()
            )

            # Control de nulos para campos únicos
            codigo_ingresado = data.get('codigo', '').strip()
            medicamento.codigo = codigo_ingresado if codigo_ingresado else None
            medicamento.concentracion = data.get('concentracion', medicamento.concentracion)
            medicamento.presentacion = data.get('presentacion', medicamento.presentacion)
            medicamento.laboratorio = data.get('laboratorio', medicamento.laboratorio)
            medicamento.save()

            lote_ingresado = data.get('lote').strip().upper()

            # Validación de Integridad de Lotes
            if producto_id:
                stock = InventarioStock.objects.get(id=producto_id, ubicacion=ubicacion_actual)
                if stock.lote != lote_ingresado and InventarioStock.objects.filter(ubicacion=ubicacion_actual,
                                                                                   medicamento=medicamento,
                                                                                   lote=lote_ingresado).exists():
                    raise ValueError(f"El lote '{lote_ingresado}' ya pertenece a este medicamento.")
            else:
                if InventarioStock.objects.filter(ubicacion=ubicacion_actual, medicamento=medicamento,
                                                  lote=lote_ingresado).exists():
                    raise ValueError(f"El lote '{lote_ingresado}' ya está registrado.")

                stock = InventarioStock(ubicacion=ubicacion_actual)

            stock.medicamento = medicamento
            stock.lote = lote_ingresado
            stock.fecha_vencimiento = data.get('fecha_vencimiento')
            stock.cantidad_actual = int(data.get('cantidad'))
            stock.stock_minimo = int(data.get('stock_minimo', 10))
            stock.save()

        return JsonResponse({'status': 'success', 'mensaje': 'Producto guardado correctamente'})

    except ValueError as ve:
        return JsonResponse({'status': 'error', 'mensaje': str(ve)}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)


@login_required
@require_POST
def api_carga_masiva(request):
    """Procesa un archivo CSV para cargar inventario de forma masiva (Solo ADMIN)"""

    # 1. Validación de Seguridad Estricta
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    if 'ADMIN' not in grupos_usuario:
        return JsonResponse({'status': 'error',
                             'mensaje': 'Solo los Administradores Centrales pueden realizar cargas masivas de inventario.'},
                            status=403)

    # 2. Validación de Archivo
    if 'archivo' not in request.FILES:
        return JsonResponse({'status': 'error', 'mensaje': 'No se detectó ningún archivo en la petición.'}, status=400)

    archivo_csv = request.FILES['archivo']
    if not archivo_csv.name.endswith('.csv'):
        return JsonResponse(
            {'status': 'error', 'mensaje': 'Formato inválido. Por favor sube estrictamente un archivo .CSV'},
            status=400)

    try:
        # Importamos el servicio que procesa el Excel (Asegúrate de tener esta función en services.py)
        from .services import procesar_carga_masiva_productos

        # Obtenemos la sede en la que está el administrador
        ubicacion_actual = request.user.perfil.ubicacion_asignada

        # 3. Procesamiento ACID
        # Le pasamos el archivo y la sede a tu lógica de servicios
        total_procesados = procesar_carga_masiva_productos(archivo_csv, ubicacion_actual)

        return JsonResponse({
            'status': 'success',
            'mensaje': f'¡Carga masiva exitosa! Se procesaron {total_procesados} registros correctamente.'
        })

    except ValueError as ve:
        # Errores específicos (ej: columnas faltantes en el CSV)
        return JsonResponse({'status': 'error', 'mensaje': str(ve)}, status=400)
    except Exception as e:
        # Errores fatales de base de datos
        return JsonResponse({'status': 'error', 'mensaje': f'Error interno procesando el archivo: {str(e)}'},
                            status=400)


@login_required
@require_POST
def api_gestion_usuario(request):
    """Crea o edita un usuario y sus roles asignados (Solo ADMIN)"""
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    if 'ADMIN' not in grupos_usuario:
        return JsonResponse({'status': 'error', 'mensaje': 'Solo administradores pueden gestionar usuarios'},
                            status=403)

    try:
        data = json.loads(request.body)
        user_id = data.get('id')
        roles_seleccionados = data.get('roles', [])

        with transaction.atomic():
            if user_id:
                user = User.objects.get(id=user_id)
                user.first_name = data.get('first_name')
                user.last_name = data.get('last_name')
                user.email = data.get('email')
                if data.get('password'):
                    user.set_password(data.get('password'))
                user.save()

                perfil = user.perfil
                perfil.ubicacion_asignada_id = data.get('ubicacion_id')
                perfil.numero_identificacion = data.get('identificacion')
                perfil.save()
            else:
                user = User.objects.create_user(
                    username=data.get('username'),
                    password=data.get('password'),
                    first_name=data.get('first_name'),
                    last_name=data.get('last_name'),
                    email=data.get('email')
                )
                PerfilUsuario.objects.create(
                    usuario=user,
                    ubicacion_asignada_id=data.get('ubicacion_id'),
                    numero_identificacion=data.get('identificacion')
                )

            # Asignación múltiple de roles (Grupos)
            user.groups.clear()
            for rol_name in roles_seleccionados:
                grupo, _ = Group.objects.get_or_create(name=rol_name)
                user.groups.add(grupo)

        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)

@login_required
@require_POST
def api_crear_solicitud(request):
    """Recibe la solicitud rápida desde el botón rojo de alerta"""
    try:
        data = json.loads(request.body)
        SolicitudStock.objects.create(
            medicamento_id=data['medicamento_id'],
            sede_solicitante=request.user.perfil.ubicacion_asignada,
            usuario_solicitante=request.user,
            cantidad_pedida=data.get('cantidad', 50), # Cantidad sugerida por defecto
            estado='PENDIENTE'
        )
        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)


@login_required
@require_POST
def api_atender_solicitud(request):
    """Aprueba un pedido, cambia su estado y suma el inventario automáticamente"""

    # Solo los administradores pueden despachar pedidos
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    if 'ADMIN' not in grupos_usuario:
        return JsonResponse({'status': 'error', 'mensaje': 'Solo el Administrador Central puede despachar pedidos.'},
                            status=403)

    try:
        data = json.loads(request.body)
        solicitud_id = data.get('solicitud_id')

        # Usamos atomic() para asegurar que todo se guarde perfecto, o nada se guarde.
        with transaction.atomic():
            solicitud = SolicitudStock.objects.select_related('medicamento', 'sede_solicitante').get(id=solicitud_id)

            # Evitar doble clic o doble despacho
            if solicitud.estado != 'PENDIENTE':
                raise ValueError("Esta solicitud ya fue atendida y despachada anteriormente.")

            # 1. ACTUALIZAR EL INVENTARIO DE LA SEDE
            # Buscamos el registro de stock de ese medicamento en esa sede específica
            stock = InventarioStock.objects.filter(
                medicamento=solicitud.medicamento,
                ubicacion=solicitud.sede_solicitante
            ).order_by('-fecha_vencimiento').first()  # Tomamos el lote activo

            if stock:
                # Si ya existía, le sumamos la cantidad que pidió el regente
                stock.cantidad_actual += solicitud.cantidad_pedida
                stock.save()
            else:
                # Si el medicamento nunca había estado en esa sede, lo creamos
                InventarioStock.objects.create(
                    ubicacion=solicitud.sede_solicitante,
                    medicamento=solicitud.medicamento,
                    lote='ASIGNADO-CENTRAL',  # Lote genérico si no existía antes
                    cantidad_actual=solicitud.cantidad_pedida,
                    stock_minimo=10
                )

            # 2. ACTUALIZAR EL ESTADO DEL PEDIDO
            solicitud.estado = 'SOLICITADO'
            solicitud.save()

        return JsonResponse({'status': 'success', 'mensaje': 'Despacho realizado y stock sumado correctamente.'})

    except SolicitudStock.DoesNotExist:
        return JsonResponse({'status': 'error', 'mensaje': 'No se encontró la solicitud en el sistema.'}, status=404)
    except ValueError as ve:
        return JsonResponse({'status': 'error', 'mensaje': str(ve)}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': f'Error interno: {str(e)}'}, status=500)