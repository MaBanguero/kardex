from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Sum
from .models import Documento, DocumentoDetalle, InventarioStock, Ubicacion, Medicamento, PerfilUsuario
from django.contrib.auth.models import User
import io, csv
import datetime
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Font, Border, Side


def procesar_carga_masiva_usuarios(archivo_csv):
    """
    Formato CSV esperado: username, first_name, last_name, email, rol, identificacion, ubicacion_id, password
    """
    decoded_file = archivo_csv.read().decode('utf-8')
    io_string = io.StringIO(decoded_file)
    reader = csv.DictReader(io_string)

    contador = 0
    with transaction.atomic():
        for row in reader:
            if User.objects.filter(username=row['username']).exists():
                continue

            user = User.objects.create_user(
                username=row['username'],
                first_name=row['first_name'],
                last_name=row['last_name'],
                email=row['email'],
                password=row['password']
            )

            ubicacion = Ubicacion.objects.get(id=row['ubicacion_id'])

            # <- Usamos PerfilUsuario y agregamos el numero_identificacion
            PerfilUsuario.objects.create(
                usuario=user,
                rol=row['rol'].upper(),  # Debe ser ADMIN, REGENTE o ENFERMERA
                ubicacion_asignada=ubicacion,
                numero_identificacion=row['identificacion']
            )
            contador += 1
    return contador

def procesar_traslado(usuario_id, ubicacion_origen_id, ubicacion_destino_id, lote, medicamento_id, cantidad_a_mover):
    try:
        with transaction.atomic():
            stock_origen = InventarioStock.objects.select_for_update().get(
                ubicacion_id=ubicacion_origen_id, medicamento_id=medicamento_id, lote=lote
            )
            if stock_origen.cantidad_actual < cantidad_a_mover:
                raise ValidationError("Stock insuficiente.")
            stock_destino, created = InventarioStock.objects.select_for_update().get_or_create(
                ubicacion_id=ubicacion_destino_id, medicamento_id=medicamento_id, lote=lote,
                defaults={'cantidad_actual': 0, 'fecha_vencimiento': stock_origen.fecha_vencimiento}
            )
            stock_origen.cantidad_actual -= cantidad_a_mover
            stock_origen.save()
            stock_destino.cantidad_actual += cantidad_a_mover
            stock_destino.save()
            documento = Documento.objects.create(
                tipo_mov='TRASLADO', origen_id=ubicacion_origen_id, destino_id=ubicacion_destino_id, usuario_id=usuario_id
            )
            DocumentoDetalle.objects.create(documento=documento, medicamento_id=medicamento_id, lote=lote, cantidad=cantidad_a_mover)
        return documento
    except Exception as e:
        raise ValidationError(str(e))


# kardex/services.py

def registrar_devolucion(usuario, doc_salida_id, cantidad_devolver):
    """
    Procesa la devolución parcial o total de medicamentos, reingresándolos al stock
    y vinculándolos con la salida original para auditoría.
    """
    try:
        with transaction.atomic():
            # Bloqueamos la salida original para evitar registros duplicados simultáneos
            salida_original = Documento.objects.select_for_update().get(id=doc_salida_id, tipo_mov='SALIDA')

            # Validación de tiempo (regla de negocio)
            if salida_original.tiempo_agotado_para_devolucion():
                raise ValidationError("El tiempo límite para devoluciones ha expirado.")

            # Obtenemos el detalle del medicamento que se retiró
            detalle_original = salida_original.detalles.first()
            if not detalle_original:
                raise ValidationError("No se encontró el registro de medicamento original.")

            # Validación: No se puede devolver más de lo que salió originalmente
            total_ya_devuelto = DocumentoDetalle.objects.filter(
                documento__documento_referencia=salida_original,
                medicamento=detalle_original.medicamento,
                lote=detalle_original.lote
            ).aggregate(total=Sum('cantidad'))['total'] or 0

            if (total_ya_devuelto + cantidad_devolver) > detalle_original.cantidad:
                raise ValidationError(
                    f"Error: No puedes devolver {cantidad_devolver} unidades. Solo se retiraron {detalle_original.cantidad} y ya has devuelto {total_ya_devuelto}.")

            # 1. Crear el documento de DEVOLUCIÓN
            doc_devolucion = Documento.objects.create(
                tipo_mov='DEVOLUCION',
                usuario=usuario,
                destino=salida_original.origen,  # Regresa a la ubicación de donde salió
                id_paciente=salida_original.id_paciente,
                documento_referencia=salida_original
            )

            # 2. Registrar el detalle del reingreso
            DocumentoDetalle.objects.create(
                documento=doc_devolucion,
                medicamento=detalle_original.medicamento,
                lote=detalle_original.lote,
                cantidad=cantidad_devolver
            )

            # 3. Actualizar el Stock físicamente
            stock, _ = InventarioStock.objects.select_for_update().get_or_create(
                ubicacion=salida_original.origen,
                medicamento=detalle_original.medicamento,
                lote=detalle_original.lote
            )
            stock.cantidad_actual += cantidad_devolver
            stock.save()

            return doc_devolucion

    except Documento.DoesNotExist:
        raise ValidationError("No se encontró el documento de salida original.")


def registrar_devolucion_agrupada(usuario, nombre_medicamento, cantidad_a_devolver, id_paciente):
    """
    Busca todas las salidas pendientes del paciente y distribuye la devolución
    creando un enlace (documento_referencia) por cada documento afectado.
    """
    ubicacion_id = usuario.perfil.ubicacion_asignada.id

    try:
        with transaction.atomic():
            # 1. Buscar todos los detalles de salida de este paciente para este medicamento
            # Ordenados de la salida más reciente a la más antigua (LIFO para devoluciones)
            detalles_salida = DocumentoDetalle.objects.select_for_update().filter(
                documento__usuario=usuario,
                documento__tipo_mov='SALIDA',
                documento__id_paciente=id_paciente,
                medicamento__principio_activo__iexact=nombre_medicamento.strip()
            ).order_by('-documento__fecha')

            # 2. Calcular saldos globales para validar
            total_salidas = detalles_salida.aggregate(total=Sum('cantidad'))['total'] or 0

            total_ya_devuelto = DocumentoDetalle.objects.filter(
                documento__documento_referencia__in=detalles_salida.values_list('documento', flat=True),
                documento__tipo_mov='DEVOLUCION',
                medicamento__principio_activo__iexact=nombre_medicamento.strip()
            ).aggregate(total=Sum('cantidad'))['total'] or 0

            saldo_disponible_global = total_salidas - total_ya_devuelto

            if cantidad_a_devolver > saldo_disponible_global:
                raise ValidationError(
                    f"Error: No puedes devolver {cantidad_a_devolver}. El saldo pendiente del paciente es de {saldo_disponible_global} unidades.")

            cantidad_pendiente = cantidad_a_devolver

            # 3. Distribuir la devolución creando documentos individuales para mantener el historial
            for detalle in detalles_salida:
                if cantidad_pendiente <= 0:
                    break  # Ya terminamos de devolver todo lo solicitado

                # Calcular cuánto se ha devuelto específicamente de ESTE documento de salida
                ya_devuelto_este_detalle = DocumentoDetalle.objects.filter(
                    documento__documento_referencia=detalle.documento,
                    medicamento=detalle.medicamento,
                    lote=detalle.lote
                ).aggregate(total=Sum('cantidad'))['total'] or 0

                disponible_en_este_doc = detalle.cantidad - ya_devuelto_este_detalle

                if disponible_en_este_doc <= 0:
                    continue  # Esta salida ya fue devuelta en su totalidad, pasamos a la siguiente

                # Tomamos lo que podamos de esta salida
                cantidad_tramo = min(cantidad_pendiente, disponible_en_este_doc)

                # ¡CLAVE!: Creamos el documento de devolución apuntando a la salida específica
                doc_dev = Documento.objects.create(
                    tipo_mov='DEVOLUCION',
                    usuario=usuario,
                    destino_id=ubicacion_id,
                    id_paciente=id_paciente,
                    documento_referencia=detalle.documento  # <- ESTO HACE QUE APAREZCA LA NOTA VERDE EN EL HISTORIAL
                )

                # Creamos el detalle específico
                DocumentoDetalle.objects.create(
                    documento=doc_dev,
                    medicamento=detalle.medicamento,
                    lote=detalle.lote,
                    cantidad=cantidad_tramo
                )

                # Devolvemos el stock físico
                stock = InventarioStock.objects.get(
                    ubicacion_id=ubicacion_id,
                    medicamento=detalle.medicamento,
                    lote=detalle.lote
                )
                stock.cantidad_actual += cantidad_tramo
                stock.save()

                # Restamos a la meta de devolución
                cantidad_pendiente -= cantidad_tramo

            return True  # Finalizó con éxito

    except Exception as e:
        raise ValidationError(str(e))


def registrar_salida_paciente(usuario, stock_id, cantidad, id_paciente):
    """
    Resta stock de la ubicación y crea el documento de salida.
    """
    with transaction.atomic():
        # Bloqueamos la fila para evitar que otra enfermera use el mismo stock
        stock = InventarioStock.objects.select_for_update().get(id=stock_id)

        if stock.cantidad_actual < cantidad:
            raise ValidationError(f"Stock insuficiente. Solo hay {stock.cantidad_actual} unidades.")

        # 1. Descontar del inventario
        stock.cantidad_actual -= cantidad
        stock.save()

        # 2. Crear la cabecera del documento
        doc = Documento.objects.create(
            tipo_mov='SALIDA',
            usuario=usuario,
            origen=stock.ubicacion,
            id_paciente=id_paciente
        )

        # 3. Crear el detalle del movimiento
        DocumentoDetalle.objects.create(
            documento=doc,
            medicamento=stock.medicamento,
            lote=stock.lote,
            cantidad=cantidad
        )
        return doc

def registrar_salida_paciente_inteligente(usuario, nombre_medicamento, cantidad_solicitada, id_paciente):
    """
    Descuenta stock automáticamente del lote más próximo a vencer (FEFO).
    """
    ubicacion_id = usuario.perfil.ubicacion_asignada.id

    try:
        with transaction.atomic():
            # 1. Buscamos todos los lotes de este medicamento en esta sede
            # Ordenados por fecha_vencimiento (El más viejo primero)
            stocks_disponibles = InventarioStock.objects.select_for_update().filter(
                ubicacion_id=ubicacion_id,
                medicamento__principio_activo__iexact=nombre_medicamento.strip(),
                cantidad_actual__gt=0
            ).order_by('fecha_vencimiento')

            # 2. Verificamos si la suma de todos los lotes alcanza
            total_disponible = sum(stock.cantidad_actual for stock in stocks_disponibles)
            if total_disponible < cantidad_solicitada:
                raise ValidationError(f"Stock insuficiente. Solo hay {total_disponible} unidades disponibles en total.")

            # 3. Creamos la cabecera del documento
            doc = Documento.objects.create(
                tipo_mov='SALIDA',
                usuario=usuario,
                origen_id=ubicacion_id,
                id_paciente=id_paciente
            )

            # 4. Descontamos lote por lote hasta cumplir la cuota (FEFO)
            cantidad_restante = cantidad_solicitada

            for stock in stocks_disponibles:
                if cantidad_restante <= 0:
                    break # Ya cumplimos con lo solicitado

                # Calculamos cuánto podemos sacarle a este lote
                cantidad_a_descontar = min(stock.cantidad_actual, cantidad_restante)

                # Restamos
                stock.cantidad_actual -= cantidad_a_descontar
                stock.save()

                # Creamos el detalle específico con este lote
                DocumentoDetalle.objects.create(
                    documento=doc,
                    medicamento=stock.medicamento,
                    lote=stock.lote,
                    cantidad=cantidad_a_descontar
                )

                # Actualizamos la meta
                cantidad_restante -= cantidad_a_descontar

            return doc

    except Exception as e:
        raise ValidationError(str(e))


def generar_excel_kardex(mes, anio, ubicacion_id):
    wb = Workbook()
    ws = wb.active
    ws.title = "KARDEX"

    # 0. Obtener Contexto
    hoy = datetime.datetime.now()
    ubicacion = Ubicacion.objects.get(id=ubicacion_id)
    unidad_nombre = ubicacion.nombre

    # Estilos
    fuente_negrita = Font(bold=True, size=9, name='Arial')
    fuente_normal = Font(size=9, name='Arial')
    alineacion_centro = Alignment(horizontal="center", vertical="center", wrap_text=True)
    alineacion_izq = Alignment(horizontal="left", vertical="center", wrap_text=True)
    borde_fino = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'),
                        bottom=Side(style='thin'))
    fondo_gris = PatternFill(start_color="E7E6E6", end_color="E7E6E6", fill_type="solid")

    def estilizar_rango(rango, fuente, alineacion, fondo, borde):
        for fila in ws[rango]:
            for celda in fila:
                if fuente: celda.font = fuente
                if alineacion: celda.alignment = alineacion
                if fondo: celda.fill = fondo
                if borde: celda.border = borde

    # 1. Preparar Datos y Calcular Columnas Dinámicas
    stocks = InventarioStock.objects.filter(ubicacion_id=ubicacion_id).select_related('medicamento')

    datos_kardex = []
    max_ingresos = 1
    max_egresos = 1

    for stock in stocks:
        # Obtenemos los movimientos del mes iterando con el ORM de Django
        ingresos_qs = DocumentoDetalle.objects.filter(
            documento__tipo_mov__in=['ENTRADA', 'DEVOLUCION'],
            documento__destino_id=ubicacion_id,
            medicamento=stock.medicamento,
            lote=stock.lote,
            documento__fecha__month=mes,
            documento__fecha__year=anio
        ).order_by('documento__fecha')

        egresos_qs = DocumentoDetalle.objects.filter(
            documento__tipo_mov__in=['SALIDA', 'TRASLADO'],
            documento__origen_id=ubicacion_id,
            medicamento=stock.medicamento,
            lote=stock.lote,
            documento__fecha__month=mes,
            documento__fecha__year=anio
        ).order_by('documento__fecha')

        ingresos_list = list(ingresos_qs)
        egresos_list = list(egresos_qs)

        if len(ingresos_list) > max_ingresos: max_ingresos = len(ingresos_list)
        if len(egresos_list) > max_egresos: max_egresos = len(egresos_list)

        total_ingresos = sum(mov.cantidad for mov in ingresos_list)
        total_egresos = sum(mov.cantidad for mov in egresos_list)

        # En Django nuestro stock es en tiempo real (Saldo Final)
        # Por ende, calculamos el inicial hacia atrás
        saldo_final = stock.cantidad_actual
        saldo_inicial = saldo_final - total_ingresos + total_egresos

        datos_kardex.append({
            'stock': stock,
            'ingresos': ingresos_list,
            'egresos': egresos_list,
            'total_ingresos': total_ingresos,
            'total_egresos': total_egresos,
            'saldo_inicial': saldo_inicial,
            'saldo_final': saldo_final
        })

    # Matemáticas de tus columnas
    col_saldo_ini = 12
    col_ingresos_start = 13
    col_ingresos_end = col_ingresos_start + (max_ingresos * 2) - 1
    col_total_ingresos = col_ingresos_end + 1
    col_egresos_start = col_total_ingresos + 1
    col_egresos_end = col_egresos_start + (max_egresos * 2) - 1
    col_total_egresos = col_egresos_end + 1
    col_saldo_final = col_total_egresos + 1
    col_verificacion = col_saldo_final + 1

    # 2. Encabezados y Metadatos
    meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
             "NOVIEMBRE", "DICIEMBRE"]
    ws['K1'] = "Código:";
    ws['L1'] = "PM-SF-FR12"
    ws['K2'] = "Versión:";
    ws['L2'] = "1"
    ws['K3'] = "Fecha Act.:";
    ws['L3'] = hoy.strftime("%d.%m.%Y")
    for row in range(1, 4):
        ws[f'K{row}'].font = fuente_negrita;
        ws[f'K{row}'].alignment = Alignment(horizontal="right")
        ws[f'L{row}'].font = fuente_normal

    ws.merge_cells('D1:H3')
    ws['D1'] = "KARDEX DE MEDICAMENTOS"
    ws['D1'].font = Font(bold=True, size=14, name='Arial');
    ws['D1'].alignment = alineacion_centro

    ws['A5'] = "FECHA:";
    ws['B5'] = f"{hoy.day} DE {meses[hoy.month - 1]} DE {hoy.year}"
    ws['D5'] = "ÁREA/SERVICIO:";
    ws['E5'] = "HOSPITALIZACIÓN"  # O hacerlo dinámico según tu lógica
    ws['G5'] = "UBICACIÓN:";
    ws['H5'] = unidad_nombre
    ws['J5'] = "UNIDAD DE ATENCIÓN:";
    ws['K5'] = "PUERTO TEJADA"

    for celda in ['A5', 'D5', 'G5', 'J5']:
        ws[celda].font = fuente_negrita;
        ws[celda].alignment = Alignment(horizontal="right", vertical="center")
    for celda in ['B5', 'E5', 'H5', 'K5']:
        ws[celda].font = fuente_normal

    ws.row_dimensions[7].height = 25
    ws.row_dimensions[8].height = 40

    # 3. Dibujar Tabla Dinámica
    columnas_base = [
        ("A", "ITEM"), ("B", "MEDICAMENTOS (PRINCIPIO ACTIVO)"), ("C", "FORMA FARMACÉUTICA"),
        ("D", "CONCENTRACIÓN"), ("E", "LOTE"), ("F", "FECHA DE VENCIMIENTO"),
        ("G", "UNIDAD DE MEDIDA"), ("H", "REGISTRO INVIMA"), ("I", "VIDA UTIL"),
        ("J", "SEMAFORIZACIÓN"), ("K", "MEDICAMENTO LASA")
    ]
    for letra, titulo in columnas_base:
        rango = f"{letra}7:{letra}8"
        ws.merge_cells(rango)
        ws[f"{letra}7"] = titulo
        estilizar_rango(rango, fuente_negrita, alineacion_centro, fondo_gris, borde_fino)

    grupos = [
        (f"{get_column_letter(col_saldo_ini)}7:{get_column_letter(col_saldo_ini)}8",
         "SALDOS INICIO DEL PERIODO\nCANTIDAD"),
        (f"{get_column_letter(col_ingresos_start)}7:{get_column_letter(col_ingresos_end)}7", "INGRESOS"),
        (f"{get_column_letter(col_total_ingresos)}7:{get_column_letter(col_total_ingresos)}8", "TOTAL\nINGRESOS"),
        (f"{get_column_letter(col_egresos_start)}7:{get_column_letter(col_egresos_end)}7", "EGRESOS"),
        (f"{get_column_letter(col_total_egresos)}7:{get_column_letter(col_total_egresos)}8", "TOTAL\nEGRESOS"),
        (f"{get_column_letter(col_saldo_final)}7:{get_column_letter(col_saldo_final)}8",
         "SALDOS AL FINAL\nDEL PERIODO"),
        (f"{get_column_letter(col_verificacion)}7:{get_column_letter(col_verificacion)}8",
         "VERIFICACION EXISTENCIAS\nEN FISICO")
    ]
    for rango, texto in grupos:
        ws.merge_cells(rango)
        ws[rango.split(':')[0]] = texto
        estilizar_rango(rango, fuente_negrita, alineacion_centro, fondo_gris, borde_fino)

    for col in range(col_ingresos_start, col_ingresos_end + 1, 2):
        ws.cell(row=8, column=col, value="FECHA");
        ws.cell(row=8, column=col + 1, value="CANT.")
    for col in range(col_egresos_start, col_egresos_end + 1, 2):
        ws.cell(row=8, column=col, value="FECHA");
        ws.cell(row=8, column=col + 1, value="CANT.")

    estilizar_rango(f"{get_column_letter(col_ingresos_start)}8:{get_column_letter(col_ingresos_end)}8", fuente_negrita,
                    alineacion_centro, fondo_gris, borde_fino)
    estilizar_rango(f"{get_column_letter(col_egresos_start)}8:{get_column_letter(col_egresos_end)}8", fuente_negrita,
                    alineacion_centro, fondo_gris, borde_fino)

    anchos_base = {'A': 5, 'B': 30, 'C': 18, 'D': 15, 'E': 12, 'F': 12, 'G': 10, 'H': 15, 'I': 10, 'J': 12, 'K': 12}
    for letra, ancho in anchos_base.items(): ws.column_dimensions[letra].width = ancho
    ws.column_dimensions[get_column_letter(col_saldo_ini)].width = 12
    ws.column_dimensions[get_column_letter(col_total_ingresos)].width = 11
    ws.column_dimensions[get_column_letter(col_total_egresos)].width = 11
    ws.column_dimensions[get_column_letter(col_saldo_final)].width = 12
    ws.column_dimensions[get_column_letter(col_verificacion)].width = 14

    # 4. Insertar Datos
    fila_actual = 9
    for idx, data in enumerate(datos_kardex, start=1):
        ws.row_dimensions[fila_actual].height = 20
        m = data['stock'].medicamento
        stock = data['stock']

        # Usamos getattr para evitar errores si aún no has agregado campos como 'concentracion' al modelo
        datos_fila = [
            idx,
            m.principio_activo,
            m.forma_farmaceutica,
            getattr(m, 'concentracion', ''),
            stock.lote,
            stock.fecha_vencimiento.strftime("%d/%m/%Y"),
            getattr(m, 'unidad_medida', ''),
            m.registro_invima,
            getattr(m, 'vida_util', ''),
            getattr(m, 'semaforizacion', ''),
            getattr(m, 'medicamento_lasa', '')
        ]

        for col_idx, valor in enumerate(datos_fila, start=1):
            celda = ws.cell(row=fila_actual, column=col_idx, value=valor)
            celda.font = fuente_normal
            celda.alignment = alineacion_centro if col_idx not in [2, 3] else alineacion_izq

        ws.cell(row=fila_actual, column=col_saldo_ini, value=data['saldo_inicial']).alignment = alineacion_centro

        # Poblar ingresos dinámicos
        c_ing = col_ingresos_start
        for mov in data['ingresos']:
            ws.cell(row=fila_actual, column=c_ing,
                    value=mov.documento.fecha.strftime("%d/%m")).alignment = alineacion_centro
            ws.cell(row=fila_actual, column=c_ing + 1, value=mov.cantidad).alignment = alineacion_centro
            c_ing += 2
        ws.cell(row=fila_actual, column=col_total_ingresos, value=data['total_ingresos']).alignment = alineacion_centro

        # Poblar egresos dinámicos
        c_eg = col_egresos_start
        for mov in data['egresos']:
            ws.cell(row=fila_actual, column=c_eg,
                    value=mov.documento.fecha.strftime("%d/%m")).alignment = alineacion_centro
            ws.cell(row=fila_actual, column=c_eg + 1, value=mov.cantidad).alignment = alineacion_centro
            c_eg += 2
        ws.cell(row=fila_actual, column=col_total_egresos, value=data['total_egresos']).alignment = alineacion_centro

        celda_saldo = ws.cell(row=fila_actual, column=col_saldo_final, value=data['saldo_final'])
        celda_saldo.font = fuente_negrita;
        celda_saldo.alignment = alineacion_centro

        estilizar_rango(f"A{fila_actual}:{get_column_letter(col_verificacion)}{fila_actual}", None, None, None,
                        borde_fino)
        fila_actual += 1

    ws.freeze_panes = 'A9'

    # Retornamos el Workbook para que views.py lo descargue
    return wb


def procesar_carga_masiva_productos(usuario, archivo_csv):
    """
    Lee un CSV y actualiza o crea stock masivamente.
    Formato esperado: principio_activo, forma, lote, vencimiento(YYYY-MM-DD), cantidad
    """
    if not usuario.is_staff and usuario.perfil.rol != 'ADMIN':
        raise PermissionError("No tienes permisos para realizar cargas masivas.")

    decoded_file = archivo_csv.read().decode('utf-8')
    io_string = io.StringIO(decoded_file)
    reader = csv.DictReader(io_string)

    contador = 0
    with transaction.atomic():
        for row in reader:
            # 1. Buscar o crear el Medicamento (base de catálogo)
            medicamento, _ = Medicamento.objects.get_or_create(
                principio_activo=row['principio_activo'].strip(),
                forma_farmaceutica=row['forma'].strip()
            )

            medicamento.codigo = row.get('codigo', '')
            medicamento.concentracion = row.get('concentracion', '')
            medicamento.presentacion = row.get('presentacion', '')
            medicamento.laboratorio = row.get('laboratorio', '')
            medicamento.save()

            # 2. Actualizar o crear el Stock en la sede del admin
            stock, created = InventarioStock.objects.get_or_create(
                ubicacion=usuario.perfil.ubicacion_asignada,
                medicamento=medicamento,
                lote=row['lote'].strip(),
                defaults={'fecha_vencimiento': row['vencimiento'], 'cantidad_actual': 0}
            )

            stock.cantidad_actual += int(row['cantidad'])
            stock.save()
            contador += 1

    return contador


def registrar_solicitud_reabastecimiento(usuario, nombre_med, cantidad):
    """Crea un documento de tipo SOLICITUD para que el admin lo vea"""
    return Documento.objects.create(
        tipo_mov='SOLICITUD',
        usuario=usuario,
        origen=usuario.perfil.ubicacion_asignada,
        id_paciente=f"REQ-{nombre_med[:3].upper()}",  # Tag de seguimiento
        # En los detalles guardamos qué se pidió
    )