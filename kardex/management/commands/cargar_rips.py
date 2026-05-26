"""
Management command para cargar el reporte201 (RIPS) y conciliar contra Kardex.

Uso:
    python manage.py cargar_rips --archivo=/ruta/reporte201.csv
    python manage.py cargar_rips --archivo=/ruta/reporte201.csv --sede="PUERTO TEJADA"
    python manage.py cargar_rips --archivo=/ruta/reporte201.csv --no-conciliar
    python manage.py cargar_rips --ultimo-dia  # Carga el archivo más reciente de Downloads
"""

import os, csv, io, datetime, glob, re
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from kardex.models import (
    CargaRIPS, RegistroRIPS, Conciliacion, DetalleConciliacion,
    MapeoRIPSMedicamento, Medicamento, Documento, DocumentoDetalle
)


class Command(BaseCommand):
    help = "Carga reporte201 RIPS y opcionalmente concilia contra Kardex"

    GRUPOS_MEDICAMENTOS = {'MEDICAMENTO', 'SERVICIO FARMACEUTICO', 'INSUMOS'}

    def add_arguments(self, parser):
        parser.add_argument('--archivo', type=str, help='Ruta al archivo CSV reporte201')
        parser.add_argument('--sede', type=str, default='', help='Filtrar por sede (columna sedehabilitacion)')
        parser.add_argument('--no-conciliar', action='store_true', help='Solo cargar, no conciliar')
        parser.add_argument('--ultimo-dia', action='store_true', help='Buscar el reporte201 más reciente en Downloads')

    def handle(self, *args, **options):
        archivo = options.get('archivo')
        ultimo_dia = options.get('ultimo_dia')
        sede = options.get('sede', '')
        no_conciliar = options.get('no_conciliar', False)

        # Buscar archivo
        if ultimo_dia:
            archivo = self._buscar_ultimo_reporte()
            if not archivo:
                self.stdout.write(self.style.ERROR("No se encontró ningún reporte201 en ~/Downloads"))
                return
        elif not archivo:
            archivo = self._buscar_ultimo_reporte()
            if archivo:
                self.stdout.write(f"Usando: {archivo}")
            else:
                self.stdout.write(self.style.ERROR("Debes especificar --archivo o --ultimo-dia"))
                return

        if not os.path.exists(archivo):
            self.stdout.write(self.style.ERROR(f"Archivo no encontrado: {archivo}"))
            return

        # Determinar periodo desde el nombre del archivo
        periodo_inicio, periodo_fin = self._detectar_periodo(archivo)

        self.stdout.write(f"📂 Cargando: {os.path.basename(archivo)}")
        self.stdout.write(f"📅 Periodo: {periodo_inicio} a {periodo_fin}")
        self.stdout.write(f"🏥 Sede: {sede or 'Todas'}")

        # --- FASE 1: Importar CSV ---
        carga = self._importar_csv(archivo, periodo_inicio, periodo_fin, sede)
        if not carga:
            return

        # --- FASE 2: Mapeo automático de medicamentos ---
        self._mapear_medicamentos(carga)

        # --- FASE 3: Conciliación (opcional) ---
        if not no_conciliar:
            self._conciliar(carga, periodo_inicio, periodo_fin)

    def _buscar_ultimo_reporte(self):
        """Busca el reporte201 más reciente en ~/Downloads"""
        pattern = os.path.expanduser("~/Downloads/reporte201_*.csv")
        archivos = glob.glob(pattern)
        if not archivos:
            return None
        return max(archivos, key=os.path.getmtime)

    def _detectar_periodo(self, archivo):
        """Extrae periodo desde el nombre del archivo: reporte201_2026-05-01_2026-05-25_0.csv"""
        basename = os.path.basename(archivo)
        match = re.search(r'(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})', basename)
        if match:
            return match.group(1), match.group(2)
        # Si no se puede extraer, usar último mes
        hoy = timezone.now().date()
        return hoy.replace(day=1), hoy

    @transaction.atomic
    def _importar_csv(self, archivo, periodo_inicio, periodo_fin, sede_filter):
        """Fase 1: Lee el CSV y guarda en RegistroRIPS"""
        total = 0
        medicamentos = 0

        carga = CargaRIPS.objects.create(
            archivo=os.path.basename(archivo),
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
        )

        registros_batch = []
        batch_size = 1000

        with open(archivo, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            if not reader.fieldnames:
                self.stdout.write(self.style.ERROR("El CSV no tiene cabeceras o el delimitador no es tab"))
                carga.delete()
                return None

            for row in reader:
                total += 1
                gruposervicio = row.get('gruposervicio', '').strip().upper()
                sede = row.get('sedehabilitacion', '').strip()

                # Filtrar por sede si se especificó
                if sede_filter and sede_filter.upper() not in sede.upper():
                    continue

                # Solo guardamos medicamentos e insumos
                if gruposervicio not in self.GRUPOS_MEDICAMENTOS:
                    continue

                # Parsear fecha
                fecha_str = row.get('fechaprocedimiento', '').strip()
                fecha = None
                if fecha_str:
                    try:
                        fecha_naive = datetime.datetime.strptime(fecha_str, '%Y-%m-%d %H:%M:%S')
                        fecha = timezone.make_aware(fecha_naive, timezone=datetime.timezone(datetime.timedelta(hours=-5)))
                    except ValueError:
                        try:
                            fecha = datetime.datetime.strptime(fecha_str, '%Y-%m-%d').date()
                        except ValueError:
                            pass

                registros_batch.append(RegistroRIPS(
                    carga=carga,
                    gruposervicio=gruposervicio,
                    cupscodigo=row.get('cupscodigo', '').strip(),
                    nombreprocedimiento=row.get('nombreprocedimiento', '').strip(),
                    cantidad=int(row.get('cantidad', 0) or 0),
                    valorunitario=self._parse_decimal(row.get('valorunidad', '0')),
                    valortotal=self._parse_decimal(row.get('valortotal', '0')),
                    identificacion_paciente=row.get('identificacion', '').strip(),
                    nombre_paciente=row.get('nombrecompleto', '').strip(),
                    identificacion_profesional=row.get('identificacionprofesional', '').strip(),
                    nombre_profesional=row.get('nombreprofesional', '').strip(),
                    especialidad=row.get('especialidad', '').strip(),
                    fechaprocedimiento=fecha,
                    numerofactura=row.get('numerofactura', '').strip(),
                    sede=sede,
                    admision=row.get('admision', '').strip(),
                    modalidad=row.get('modalidad', '').strip(),
                    diagnostico=row.get('diagnostico', '').strip(),
                    diagnosticonombre=row.get('diagnosticonombre', '').strip(),
                ))
                medicamentos += 1

                if len(registros_batch) >= batch_size:
                    RegistroRIPS.objects.bulk_create(registros_batch, ignore_conflicts=True)
                    registros_batch = []

            # Último batch
            if registros_batch:
                RegistroRIPS.objects.bulk_create(registros_batch, ignore_conflicts=True)

        # Actualizar totales
        carga.total_registros = total
        carga.registros_medicamentos = medicamentos
        if medicamentos == 0:
            carga.estado = 'ERROR'
            carga.save()
            self.stdout.write(self.style.ERROR("No se encontraron registros de medicamentos en el archivo"))
            return None

        carga.save()

        self.stdout.write(self.style.SUCCESS(
            f"✅ Cargados {medicamentos} registros de medicamentos/insumos (de {total} totales)"
        ))
        return carga

    def _mapear_medicamentos(self, carga):
        """Fase 2: Intenta mapear automáticamente los registros RIPS a medicamentos del Kardex"""
        # Primero usar mapeos explícitos existentes
        mapeos = MapeoRIPSMedicamento.objects.filter(activo=True)
        mapeados = 0

        for mapeo in mapeos:
            filtro = {}
            if mapeo.cups_codigo:
                filtro['cupscodigo'] = mapeo.cups_codigo
            if mapeo.nombre_rips:
                filtro['nombreprocedimiento__iexact'] = mapeo.nombre_rips
            if mapeo.gruposervicio:
                filtro['gruposervicio__iexact'] = mapeo.gruposervicio

            if filtro:
                RegistroRIPS.objects.filter(carga=carga, **filtro).update(
                    medicamento_mapeado=mapeo.medicamento
                )
                mapeados += 1

        # Si no hay mapeos explícitos, intentar matching por nombre
        medicamentos = Medicamento.objects.all()
        sin_mapear = RegistroRIPS.objects.filter(carga=carga, medicamento_mapeado__isnull=True)

        for registro in sin_mapear:
            nombre_rips = registro.nombreprocedimiento.upper()
            for med in medicamentos:
                # Buscar coincidencia de principio activo en el nombre
                if med.principio_activo.upper() in nombre_rips:
                    registro.medicamento_mapeado = med
                    registro.save(update_fields=['medicamento_mapeado'])
                    mapeados += 1
                    break

        self.stdout.write(f"🔗 {mapeados} registros mapeados a medicamentos del Kardex")

    def _conciliar(self, carga, periodo_inicio, periodo_fin):
        """Fase 3: Compara SALIDAS del Kardex vs RIPS y genera el reporte de conciliación"""
        self.stdout.write("🔍 Ejecutando conciliación...")

        # Obtener SALIDAS del Kardex en el periodo
        salidas_kardex = Documento.objects.filter(
            tipo_mov='SALIDA',
            fecha__date__gte=periodo_inicio,
            fecha__date__lte=periodo_fin,
        ).select_related('origen', 'usuario').prefetch_related('detalles__medicamento')

        total_salidas = salidas_kardex.count()

        # Contar cantidades totales
        total_cant_kardex = 0
        salidas_dict = {}  # { (paciente_id, fecha_date, med_nombre): [docs] }
        for doc in salidas_kardex:
            for det in doc.detalles.all():
                total_cant_kardex += det.cantidad
                key = (doc.id_paciente or '', doc.fecha.date(), det.medicamento.principio_activo.upper())
                if key not in salidas_dict:
                    salidas_dict[key] = []
                salidas_dict[key].append({
                    'doc': doc,
                    'det': det,
                    'med': det.medicamento,
                    'cant': det.cantidad,
                })

        # Obtener registros RIPS del periodo
        rips_registros = RegistroRIPS.objects.filter(carga=carga)
        total_rips = rips_registros.count()
        total_cant_rips = rips_registros.aggregate(
            total=Sum('cantidad')
        )['total'] or 0

        rips_dict = {}  # { (paciente_id, fecha_date, med_nombre): [registros] }
        for reg in rips_registros:
            fecha = reg.fechaprocedimiento.date() if reg.fechaprocedimiento else periodo_inicio
            # Extraer posible principio activo desde nombre RIPS
            nombre_limpio = reg.nombreprocedimiento.upper().strip()
            key = (reg.identificacion_paciente, fecha, nombre_limpio)
            if key not in rips_dict:
                rips_dict[key] = []
            rips_dict[key].append(reg)

        # Crear conciliación
        conciliacion = Conciliacion.objects.create(
            carga_rips=carga,
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
            total_salidas_kardex=total_salidas,
            total_medicamentos_kardex=total_cant_kardex,
            total_registros_rips=total_rips,
            total_cantidad_rips=total_cant_rips,
        )

        coincidencias = 0
        no_facturados = 0
        no_despachados = 0
        detalles_batch = []

        # --- PASO 1: Buscar cada SALIDA en RIPS ---
        for (paci_id, fecha, med_nombre), items in salidas_dict.items():
            total_kardex = sum(i['cant'] for i in items)
            encontrado = False

            for key_rips, regs in list(rips_dict.items()):
                p_id_rips, fecha_rips, nombre_rips = key_rips

                # Match por paciente
                if paci_id != p_id_rips:
                    continue

                # Match por fecha (mismo día)
                if abs((fecha - fecha_rips).days) > 1:
                    continue

                # Match por nombre de medicamento
                if med_nombre not in nombre_rips and nombre_rips not in med_nombre:
                    continue

                # Encontrado!
                encontrado = True
                total_rips_item = sum(r.cantidad for r in regs)

                for item in items:
                    detalle = DetalleConciliacion(
                        conciliacion=conciliacion,
                        estado='COINCIDE' if item['cant'] == total_rips_item else 'CANTIDAD_DIF',
                        documento_salida=item['doc'],
                        medicamento=item['med'],
                        medicamento_nombre=f"{item['med'].principio_activo} {item['med'].concentracion or ''}".strip(),
                        paciente_identificacion=paci_id,
                        paciente_nombre=regs[0].nombre_paciente if regs else '',
                        cantidad_kardex=item['cant'],
                        cantidad_rips=total_rips_item,
                        fecha=item['doc'].fecha,
                        sede=item['doc'].origen.nombre if item['doc'].origen else '',
                        profesional=item['doc'].usuario.get_full_name() or item['doc'].usuario.username,
                        observacion='OK' if item['cant'] == total_rips_item else f"Kardex: {item['cant']}, RIPS: {total_rips_item}",
                    )
                    detalles_batch.append(detalle)

                if item['cant'] == total_rips_item:
                    coincidencias += 1
                else:
                    no_facturados += 1

                # Remover estos RIPS de la lista para no re-contarlos
                del rips_dict[key_rips]
                break

            if not encontrado:
                # La salida del Kardex NO está en RIPS → No facturado
                for item in items:
                    detalles_batch.append(DetalleConciliacion(
                        conciliacion=conciliacion,
                        estado='NO_FACTURADO',
                        documento_salida=item['doc'],
                        medicamento=item['med'],
                        medicamento_nombre=f"{item['med'].principio_activo} {item['med'].concentracion or ''}".strip(),
                        paciente_identificacion=paci_id,
                        paciente_nombre='',
                        cantidad_kardex=item['cant'],
                        cantidad_rips=0,
                        fecha=item['doc'].fecha,
                        sede=item['doc'].origen.nombre if item['doc'].origen else '',
                        profesional=item['doc'].usuario.get_full_name() or item['doc'].usuario.username,
                        observacion='Dispensado en Kardex pero NO encontrado en RIPS',
                    ))
                no_facturados += len(items)

        # --- PASO 2: Lo que quedó en RIPS sin match en Kardex → No despachado ---
        for key_rips, regs in rips_dict.items():
            for reg in regs:
                detalles_batch.append(DetalleConciliacion(
                    conciliacion=conciliacion,
                    estado='NO_DESPACHADO',
                    registro_rips=reg,
                    medicamento=reg.medicamento_mapeado,
                    medicamento_nombre=reg.nombreprocedimiento,
                    paciente_identificacion=reg.identificacion_paciente,
                    paciente_nombre=reg.nombre_paciente,
                    cantidad_kardex=0,
                    cantidad_rips=reg.cantidad,
                    fecha=reg.fechaprocedimiento or timezone.now(),
                    sede=reg.sede,
                    profesional=reg.nombre_profesional,
                    observacion='Facturado en RIPS pero NO dispensado en Kardex',
                ))
            no_despachados += len(regs)

        # Guardar batch
        if detalles_batch:
            DetalleConciliacion.objects.bulk_create(detalles_batch, batch_size=1000)

        # Actualizar totales
        conciliacion.coincidencias = coincidencias
        conciliacion.no_facturados = no_facturados
        conciliacion.no_despachados = no_despachados
        conciliacion.save()

        carga.estado = 'CONCILIADA'
        carga.save()

        # Resumen
        self.stdout.write(self.style.SUCCESS(
            f"\n{'='*50}\n"
            f"📊 RESUMEN DE CONCILIACIÓN\n"
            f"{'='*50}\n"
            f"📦 Kardex: {total_salidas} documentos ({total_cant_kardex} unidades)\n"
            f"📋 RIPS:   {total_rips} registros ({total_cant_rips} unidades)\n"
            f"✅ Coinciden:       {coincidencias}\n"
            f"❌ No facturados:   {no_facturados} (en Kardex, faltan en RIPS)\n"
            f"⚠️ No despachados:  {no_despachados} (en RIPS, faltan en Kardex)\n"
            f"{'='*50}"
        ))

    def _parse_decimal(self, valor):
        try:
            return float(valor.replace(',', '.'))
        except (ValueError, AttributeError):
            return 0
