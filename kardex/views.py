import json
import datetime
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .models import Documento, ConfiguracionSistema, DocumentoDetalle, Medicamento, User, Ubicacion, SolicitudStock
from django.db import transaction
from django.db.models import Sum, Q as models_Q
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
            'codigo': item.medicamento.codigo,
            'presentacion': item.medicamento.presentacion,
            'laboratorio': item.medicamento.laboratorio,
            'lote': item.lote,
            'fecha_vencimiento': item.fecha_vencimiento.strftime('%Y-%m-%d') if item.fecha_vencimiento else '',
            'cantidad_actual': item.cantidad_actual,
            'stock_minimo': item.stock_minimo,
            'tipo': item.medicamento.tipo,
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
            doc_id = data.get('doc_id')
            nombre_med = data.get('nombre_medicamento')

            if doc_id:
                # Devolución específica contra un documento de salida concreto
                from .services import registrar_devolucion
                registrar_devolucion(request.user, doc_id, cantidad)
            else:
                # Devolución agrupada: busca todas las salidas del paciente para este medicamento
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
    Genera el kárdex en formato Excel según el tipo:
    - MEDICAMENTO → Formato PM-SF-FR12
    - DISPOSITIVO → Formato PM-SF-FR11
    """
    hoy = datetime.datetime.now()
    ubicacion_id = request.user.perfil.ubicacion_asignada.id
    tipo = request.GET.get('tipo', 'MEDICAMENTO')

    wb = generar_excel_kardex(hoy.month, hoy.year, ubicacion_id, tipo)

    tipo_label = 'Medicamentos' if tipo == 'MEDICAMENTO' else 'Dispositivos'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename=Kardex_{tipo_label}_{hoy.strftime("%Y%m%d")}.xlsx'

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
        'sedes_json': json.dumps(list(Ubicacion.objects.values('id', 'nombre', 'es_bodega_principal'))),
        'medicamentos': Medicamento.objects.filter(
            inventariostock__ubicacion=request.user.perfil.ubicacion_asignada
        ).distinct().order_by('principio_activo'),
        'tipos_medicamento': Medicamento.TIPOS,
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
            tipo_val = data.get('tipo', 'MEDICAMENTO')
            if tipo_val not in ('MEDICAMENTO', 'DISPOSITIVO'):
                tipo_val = 'MEDICAMENTO'

            principio_activo = data.get('principio_activo', '').strip().upper()
            if not principio_activo:
                raise ValueError('El principio activo/nombre es obligatorio.')

            forma_farmaceutica = data.get('forma_farmaceutica', '').strip().upper()

            if tipo_val == 'DISPOSITIVO':
                # Para DISPOSITIVO: lookup solo por principio_activo, forma_farmaceutica = 'NO APLICA'
                forma_farmaceutica = 'NO APLICA'
                medicamento, _ = Medicamento.objects.get_or_create(
                    principio_activo=principio_activo,
                    defaults={'forma_farmaceutica': forma_farmaceutica}
                )
                medicamento.forma_farmaceutica = forma_farmaceutica
            else:
                # Para MEDICAMENTO: lookup por principio_activo + forma_farmaceutica
                if not forma_farmaceutica:
                    raise ValueError('La forma farmacéutica es obligatoria para medicamentos.')
                medicamento, _ = Medicamento.objects.get_or_create(
                    principio_activo=principio_activo,
                    forma_farmaceutica=forma_farmaceutica
                )

            # Control de nulos para campos únicos
            codigo_ingresado = data.get('codigo', '').strip()
            if codigo_ingresado:
                # Verificar que el código no esté duplicado en otro medicamento
                codigo_qs = Medicamento.objects.filter(codigo=codigo_ingresado)
                if medicamento.pk:
                    codigo_qs = codigo_qs.exclude(pk=medicamento.pk)
                if codigo_qs.exists():
                    raise ValueError(f"El código '{codigo_ingresado}' ya está registrado en otro producto.")
                medicamento.codigo = codigo_ingresado
            else:
                medicamento.codigo = None
            medicamento.tipo = tipo_val
            medicamento.concentracion = data.get('concentracion', medicamento.concentracion) if tipo_val == 'MEDICAMENTO' else None
            medicamento.presentacion = data.get('presentacion', medicamento.presentacion)
            medicamento.laboratorio = data.get('laboratorio', medicamento.laboratorio)
            medicamento.vida_util = data.get('vida_util', medicamento.vida_util)
            medicamento.clasificacion_riesgo = data.get('clasificacion_riesgo', medicamento.clasificacion_riesgo)
            medicamento.save()

            lote_ingresado = data.get('lote').strip().upper()

            # Validar que la fecha de vencimiento no sea pasada
            fecha_venc = data.get('fecha_vencimiento')
            if fecha_venc:
                from datetime import date
                fecha_venc_date = date.fromisoformat(fecha_venc) if isinstance(fecha_venc, str) else fecha_venc
                if fecha_venc_date < date.today():
                    raise ValueError("La fecha de vencimiento no puede ser anterior a la fecha actual.")

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
        total_procesados = procesar_carga_masiva_productos(request.user, archivo_csv)

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
        identificacion = data.get('identificacion', '').strip()

        # Validar unicidad de identificación
        if identificacion:
            duplicado = PerfilUsuario.objects.filter(
                numero_identificacion=identificacion
            )
            if user_id:
                duplicado = duplicado.exclude(usuario_id=user_id)
            if duplicado.exists():
                return JsonResponse({
                    'status': 'error',
                    'mensaje': f'Ya existe un usuario con la identificación "{identificacion}".'
                }, status=400)

        with transaction.atomic():
            if user_id:
                user = User.objects.get(id=user_id)
                user.first_name = data.get('first_name')
                user.last_name = data.get('last_name')
                # Bug 10: Solo actualizar email si se envió un valor no vacío
                if data.get('email'):
                    user.email = data.get('email')
                raw_password = data.get('password', '')
                if raw_password and raw_password.strip():
                    user.set_password(raw_password)
                user.save()

                perfil = user.perfil
                perfil.ubicacion_asignada_id = data.get('ubicacion_id')
                perfil.numero_identificacion = data.get('identificacion')
                perfil.save()
            else:
                user = User.objects.create_user(
                    username=data.get('username'),
                    password=data.get('password') or 'changeme123',
                    first_name=data.get('first_name'),
                    last_name=data.get('last_name'),
                    email=data.get('email', '')
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

            # 1. VERIFICAR QUE LA BODEGA CENTRAL TENGA STOCK
            bodega_central = Ubicacion.objects.filter(es_bodega_principal=True).first()
            if bodega_central:
                stock_origen = InventarioStock.objects.filter(
                    ubicacion=bodega_central,
                    medicamento=solicitud.medicamento
                ).select_for_update().first()

                total_stock_origen = InventarioStock.objects.filter(
                    ubicacion=bodega_central,
                    medicamento=solicitud.medicamento
                ).aggregate(total=Sum('cantidad_actual'))['total'] or 0

                if total_stock_origen < solicitud.cantidad_pedida:
                    raise ValueError(
                        f"Stock insuficiente en bodega central. Disponible: {total_stock_origen}, Solicitado: {solicitud.cantidad_pedida}")

                # Descontar de la bodega central (FEFO)
                cantidad_restante = solicitud.cantidad_pedida
                stocks_origen = InventarioStock.objects.filter(
                    ubicacion=bodega_central,
                    medicamento=solicitud.medicamento,
                    cantidad_actual__gt=0
                ).select_for_update().order_by('fecha_vencimiento')

                for s in stocks_origen:
                    if cantidad_restante <= 0:
                        break
                    a_descontar = min(s.cantidad_actual, cantidad_restante)
                    s.cantidad_actual -= a_descontar
                    s.save()
                    cantidad_restante -= a_descontar

            # 2. ACTUALIZAR EL INVENTARIO DE LA SEDE
            stock = InventarioStock.objects.filter(
                medicamento=solicitud.medicamento,
                ubicacion=solicitud.sede_solicitante
            ).order_by('-fecha_vencimiento').first()

            if stock:
                stock.cantidad_actual += solicitud.cantidad_pedida
                stock.save()
                lote_destino = stock.lote
            else:
                InventarioStock.objects.create(
                    ubicacion=solicitud.sede_solicitante,
                    medicamento=solicitud.medicamento,
                    lote='ASIGNADO-CENTRAL',
                    fecha_vencimiento=timezone.now().date() + timedelta(days=365),
                    cantidad_actual=solicitud.cantidad_pedida,
                    stock_minimo=10
                )
                lote_destino = 'ASIGNADO-CENTRAL'

            # 3. CREAR DOCUMENTO CONTABLE
            doc = Documento.objects.create(
                tipo_mov='ENTRADA',
                usuario=request.user,
                destino=solicitud.sede_solicitante,
                origen=bodega_central,
                id_paciente=f"SOL-{solicitud.id}"
            )
            DocumentoDetalle.objects.create(
                documento=doc,
                medicamento=solicitud.medicamento,
                lote=lote_destino,
                cantidad=solicitud.cantidad_pedida
            )

            # 4. ACTUALIZAR EL ESTADO DEL PEDIDO
            solicitud.estado = 'SOLICITADO'
            solicitud.save()

        return JsonResponse({'status': 'success', 'mensaje': 'Despacho realizado y stock sumado correctamente.'})

    except SolicitudStock.DoesNotExist:
        return JsonResponse({'status': 'error', 'mensaje': 'No se encontró la solicitud en el sistema.'}, status=404)
    except ValueError as ve:
        return JsonResponse({'status': 'error', 'mensaje': str(ve)}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': f'Error interno: {str(e)}'}, status=500)


# ==============================================================================
# CRUD Ubicacion
# ==============================================================================
@login_required
@require_POST
def api_gestion_ubicacion(request):
    """Crea, edita o elimina una ubicación (solo ADMIN)"""
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    if 'ADMIN' not in grupos_usuario:
        return JsonResponse({'status': 'error', 'mensaje': 'No autorizado'}, status=403)

    try:
        data = json.loads(request.body)
        accion = data.get('accion', 'guardar')
        ubicacion_id = data.get('id')

        if accion == 'eliminar':
            ubi = Ubicacion.objects.get(id=ubicacion_id)
            # Verificar que no tenga stock ni usuarios asociados
            if InventarioStock.objects.filter(ubicacion=ubi).exists():
                return JsonResponse({'status': 'error', 'mensaje': 'No se puede eliminar: la ubicación tiene inventario asociado.'}, status=400)
            if PerfilUsuario.objects.filter(ubicacion_asignada=ubi).exists():
                return JsonResponse({'status': 'error', 'mensaje': 'No se puede eliminar: la ubicación tiene personal asignado.'}, status=400)
            if SolicitudStock.objects.filter(sede_solicitante=ubi).exists():
                return JsonResponse({'status': 'error', 'mensaje': 'No se puede eliminar: la ubicación tiene solicitudes asociadas.'}, status=400)
            ubi.delete()
            return JsonResponse({'status': 'success', 'mensaje': 'Ubicación eliminada correctamente.'})

        nombre = data.get('nombre', '').strip()
        if not nombre:
            return JsonResponse({'status': 'error', 'mensaje': 'El nombre es obligatorio.'}, status=400)

        es_principal = data.get('es_bodega_principal', False)

        if ubicacion_id:
            ubi = Ubicacion.objects.get(id=ubicacion_id)
            ubi.nombre = nombre
            if es_principal:
                # Solo una bodega principal
                Ubicacion.objects.filter(es_bodega_principal=True).exclude(id=ubi.id).update(es_bodega_principal=False)
            ubi.es_bodega_principal = es_principal
            ubi.save()
        else:
            if es_principal:
                Ubicacion.objects.filter(es_bodega_principal=True).update(es_bodega_principal=False)
            Ubicacion.objects.create(nombre=nombre, es_bodega_principal=es_principal)

        return JsonResponse({'status': 'success'})

    except Ubicacion.DoesNotExist:
        return JsonResponse({'status': 'error', 'mensaje': 'Ubicación no encontrada.'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)


# ==============================================================================
# CRUD ConfiguracionSistema
# ==============================================================================
@login_required
@require_POST
def api_gestion_configuracion(request):
    """Obtiene o actualiza la configuración del sistema (solo ADMIN)"""
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    if 'ADMIN' not in grupos_usuario:
        return JsonResponse({'status': 'error', 'mensaje': 'No autorizado'}, status=403)

    try:
        data = json.loads(request.body)
        accion = data.get('accion', 'guardar')

        if accion == 'obtener':
            config = ConfiguracionSistema.objects.first()
            if not config:
                config = ConfiguracionSistema.objects.create(horas_limite_devolucion=2)
            return JsonResponse({
                'status': 'success',
                'config': {
                    'id': config.id,
                    'horas_limite_devolucion': config.horas_limite_devolucion
                }
            })

        # Guardar
        config = ConfiguracionSistema.objects.first()
        if not config:
            config = ConfiguracionSistema(horas_limite_devolucion=2)

        horas = int(data.get('horas_limite_devolucion', 2))
        if horas < 1:
            return JsonResponse({'status': 'error', 'mensaje': 'Las horas deben ser al menos 1.'}, status=400)
        config.horas_limite_devolucion = horas
        config.save()

        return JsonResponse({'status': 'success', 'mensaje': 'Configuración actualizada.'})

    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)


# ==============================================================================
# CRUD Documentos (visor de movimientos)
# ==============================================================================
@login_required
def api_listar_movimientos(request):
    """Lista todos los movimientos del sistema (admin) o de la sede (regente)"""
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    es_admin = 'ADMIN' in grupos_usuario
    es_regente = 'REGENTE' in grupos_usuario

    if not (es_admin or es_regente):
        return JsonResponse({'status': 'error', 'mensaje': 'No autorizado'}, status=403)

    try:
        movimientos = Documento.objects.select_related('usuario', 'origen', 'destino')\
            .prefetch_related('detalles__medicamento').order_by('-fecha')

        if es_regente:
            # Filtrar por sede del regente
            ubi = request.user.perfil.ubicacion_asignada
            movimientos = movimientos.filter(
                models_Q(origen=ubi) | models_Q(destino=ubi)
            )

        data = []
        for mov in movimientos:
            detalles = []
            for det in mov.detalles.all():
                detalles.append({
                    'medicamento': str(det.medicamento),
                    'lote': det.lote,
                    'cantidad': det.cantidad
                })
            data.append({
                'id': mov.id,
                'tipo_mov': mov.tipo_mov,
                'fecha': mov.fecha.strftime('%Y-%m-%d %H:%M'),
                'usuario': mov.usuario.get_full_name() or mov.usuario.username,
                'origen': str(mov.origen) if mov.origen else '-',
                'destino': str(mov.destino) if mov.destino else '-',
                'id_paciente': mov.id_paciente or '-',
                'detalles': detalles
            })

        return JsonResponse({'status': 'success', 'movimientos': data})

    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)


# ==============================================================================
# Cancelar SolicitudStock
# ==============================================================================
@login_required
@require_POST
def api_cancelar_solicitud(request):
    """Cancela/elimina una solicitud pendiente"""
    try:
        data = json.loads(request.body)
        solicitud_id = data.get('solicitud_id')

        solicitud = SolicitudStock.objects.get(id=solicitud_id)

        # Solo se puede cancelar si está PENDIENTE
        if solicitud.estado != 'PENDIENTE':
            return JsonResponse({'status': 'error', 'mensaje': 'No se puede cancelar una solicitud ya despachada.'}, status=400)

        # Verificar permisos: solo el creador o un ADMIN
        grupos_usuario = request.user.groups.values_list('name', flat=True)
        if not ('ADMIN' in grupos_usuario or solicitud.usuario_solicitante == request.user):
            return JsonResponse({'status': 'error', 'mensaje': 'No autorizado para cancelar esta solicitud.'}, status=403)

        solicitud.delete()
        return JsonResponse({'status': 'success', 'mensaje': 'Solicitud cancelada correctamente.'})

    except SolicitudStock.DoesNotExist:
        return JsonResponse({'status': 'error', 'mensaje': 'Solicitud no encontrada.'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)


# ==============================================================================
# Eliminar InventarioStock / Medicamento
# ==============================================================================
@login_required
@require_POST
def api_eliminar_stock(request):
    """Elimina un registro de stock, y opcionalmente el medicamento si queda huérfano"""
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    if 'ADMIN' not in grupos_usuario:
        return JsonResponse({'status': 'error', 'mensaje': 'No autorizado'}, status=403)

    try:
        data = json.loads(request.body)
        stock_id = data.get('stock_id')

        stock = InventarioStock.objects.select_related('medicamento').get(id=stock_id)
        medicamento = stock.medicamento

        if stock.cantidad_actual > 0:
            return JsonResponse({'status': 'error', 'mensaje': f'No se puede eliminar: el lote tiene {stock.cantidad_actual} unidades. Debe agotar el stock primero o ajustar a 0.'}, status=400)

        stock.delete()

        # Si el medicamento ya no tiene stock en ninguna ubicación, lo eliminamos
        if not InventarioStock.objects.filter(medicamento=medicamento).exists():
            # Verificar que no tenga movimientos asociados
            if not DocumentoDetalle.objects.filter(medicamento=medicamento).exists():
                medicamento.delete()

        return JsonResponse({'status': 'success', 'mensaje': 'Registro de stock eliminado.'})

    except InventarioStock.DoesNotExist:
        return JsonResponse({'status': 'error', 'mensaje': 'Stock no encontrado.'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)


# ==============================================================================
# Eliminar Usuario
# ==============================================================================
@login_required
@require_POST
def api_eliminar_usuario(request):
    """Elimina un usuario del sistema (solo ADMIN, no a sí mismo)"""
    grupos_usuario = request.user.groups.values_list('name', flat=True)
    if 'ADMIN' not in grupos_usuario:
        return JsonResponse({'status': 'error', 'mensaje': 'No autorizado'}, status=403)

    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')

        if int(user_id) == request.user.id:
            return JsonResponse({'status': 'error', 'mensaje': 'No puedes eliminarte a ti mismo.'}, status=400)

        user = User.objects.get(id=user_id)

        # Verificar que no tenga movimientos asociados
        if Documento.objects.filter(usuario=user).exists():
            return JsonResponse({'status': 'error', 'mensaje': 'No se puede eliminar: el usuario tiene movimientos registrados. Desactívelo en su lugar.'}, status=400)

        # Eliminar perfil y usuario
        PerfilUsuario.objects.filter(usuario=user).delete()
        user.delete()

        return JsonResponse({'status': 'success', 'mensaje': 'Usuario eliminado correctamente.'})

    except User.DoesNotExist:
        return JsonResponse({'status': 'error', 'mensaje': 'Usuario no encontrado.'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'mensaje': str(e)}, status=400)