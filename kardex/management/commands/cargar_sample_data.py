"""
Management command para cargar datos de demostración en el sistema Kardex.

Uso:
    python manage.py cargar_sample_data --sede=FarmaciaSede1
    python manage.py cargar_sample_data --sede=FarmaciaSede1 --clear   # Limpia datos existentes
    python manage.py cargar_sample_data --sede=FarmaciaSede1 --force   # Omite confirmación
"""

from datetime import date, timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.auth.models import User, Group
from django.utils import timezone

from kardex.models import (
    Ubicacion, Medicamento, InventarioStock, Documento,
    DocumentoDetalle, SolicitudStock, PerfilUsuario, ConfiguracionSistema
)


class Command(BaseCommand):
    help = "Carga datos de demostración (medicamentos, dispositivos, movimientos)"

    def add_arguments(self, parser):
        parser.add_argument('--sede', type=str, required=True,
                            help='Nombre de la sede/ubicación para los datos')
        parser.add_argument('--clear', action='store_true',
                            help='Limpiar datos existentes antes de cargar')
        parser.add_argument('--force', action='store_true',
                            help='Omitir confirmación al limpiar datos')

    def _confirmar(self, mensaje):
        """Pregunta al usuario antes de continuar."""
        respuesta = input(f"{mensaje} (s/N): ").strip().lower()
        return respuesta == 's'

    def _limpiar_datos(self, ubicacion):
        """Elimina datos existentes en orden inverso a las dependencias."""
        self.stdout.write(self.style.WARNING("Limpiando datos existentes..."))
        DocumentoDetalle.objects.filter(
            documento__origen=ubicacion
        ).delete()
        DocumentoDetalle.objects.filter(
            documento__destino=ubicacion
        ).delete()
        Documento.objects.filter(origen=ubicacion).delete()
        Documento.objects.filter(destino=ubicacion).delete()
        SolicitudStock.objects.filter(sede_solicitante=ubicacion).delete()
        InventarioStock.objects.filter(ubicacion=ubicacion).delete()
        # Eliminar medicamentos que solo existían en esta sede
        ids_med_stock = set(InventarioStock.objects.values_list('medicamento_id', flat=True))
        ids_med_docs = set(DocumentoDetalle.objects.values_list('medicamento_id', flat=True))
        ids_usados = ids_med_stock | ids_med_docs
        Medicamento.objects.exclude(id__in=ids_usados).delete()
        self.stdout.write(self.style.SUCCESS("Datos limpiados correctamente."))

    @transaction.atomic
    def handle(self, *args, **options):
        sede_nombre = options['sede']
        hacer_clear = options['clear']
        force = options['force']

        # ---------- Obtener/Crear Ubicación ----------
        ubicacion, creada = Ubicacion.objects.get_or_create(
            nombre=sede_nombre,
            defaults={'es_bodega_principal': False}
        )
        if creada:
            self.stdout.write(self.style.SUCCESS(f"Sede '{sede_nombre}' creada."))
        else:
            self.stdout.write(f"Sede '{sede_nombre}' encontrada.")

        # ---------- Verificar si ya hay datos ----------
        stock_existente = InventarioStock.objects.filter(ubicacion=ubicacion).count()
        if stock_existente > 0 and not hacer_clear and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"Ya hay {stock_existente} registros de stock en '{sede_nombre}'."
                )
            )
            if not self._confirmar("¿Deseas agregar datos de muestra adicionales?"):
                self.stdout.write("Operación cancelada.")
                return

        if hacer_clear:
            if not force:
                if not self._confirmar(
                    f"¿Estás seguro de limpiar todos los datos de '{sede_nombre}'?"
                ):
                    self.stdout.write("Operación cancelada.")
                    return
            self._limpiar_datos(ubicacion)

        # ---------- Asegurar Bodega Central ----------
        bodega_central, _ = Ubicacion.objects.get_or_create(
            nombre="Bodega Central",
            defaults={'es_bodega_principal': True}
        )
        if bodega_central.es_bodega_principal is False:
            Ubicacion.objects.filter(es_bodega_principal=True).update(es_bodega_principal=False)
            bodega_central.es_bodega_principal = True
            bodega_central.save()

        # ---------- Asegurar Configuración ----------
        ConfiguracionSistema.objects.get_or_create(
            pk=1, defaults={'horas_limite_devolucion': 2}
        )

        # ---------- Asegurar Grupos ----------
        for nombre_grupo in ['ADMIN', 'REGENTE', 'ENFERMERA']:
            Group.objects.get_or_create(name=nombre_grupo)

        # ---------- Medicamentos ----------
        medicamentos_data = [
            # (principio_activo, concentracion, forma_farmaceutica, presentacion, laboratorio, codigo),
            ('ACETAMINOFEN', '500mg', 'TABLETA', 'Caja x 100', 'GENFAR', '770123456'),
            ('IBUPROFENO', '400mg', 'TABLETA', 'Caja x 50', 'MK', '770123457'),
            ('AMOXICILINA', '500mg', 'CAPSULA', 'Caja x 30', 'FARMA', '770123458'),
            ('OMEPRAZOL', '20mg', 'CAPSULA', 'Caja x 30', 'TECNOQUIMICAS', '770123459'),
            ('LOSARTAN', '50mg', 'TABLETA', 'Caja x 30', 'GENFAR', '770123460'),
            ('METFORMINA', '850mg', 'TABLETA', 'Caja x 60', 'SIGMA', '770123461'),
            ('ENALAPRIL', '10mg', 'TABLETA', 'Caja x 30', 'MK', '770123462'),
            ('SALBUTAMOL', '100mcg', 'INHALADOR', 'Frasco x 200 dosis', 'GSK', '770123463'),
            ('DEXAMETASONA', '4mg/2ml', 'AMPOLLA', 'Caja x 10', 'MK', '770123464'),
            ('RANITIDINA', '50mg/2ml', 'AMPOLLA', 'Caja x 10', 'SIGMA', '770123465'),
            ('CEFTRIAXONA', '1g', 'POLVO', 'Frasco x 1', 'FARMA', '770123466'),
            ('DICLOFENACO', '75mg/3ml', 'AMPOLLA', 'Caja x 6', 'GENFAR', '770123467'),
            ('MORFINA', '10mg/ml', 'AMPOLLA', 'Caja x 5', 'MK', '770123468'),
        ]

        medicamentos_lotes = [
            # (indice, lote, fecha_vencimiento, cantidad, stock_minimo)
            (0, 'L2026001', date(2027, 6, 30), 200, 20),
            (1, 'L2026002', date(2027, 5, 31), 150, 20),
            (2, 'L2026003', date(2027, 4, 30), 100, 15),
            (3, 'L2026004', date(2027, 8, 31), 80, 15),
            (4, 'L2026005', date(2027, 7, 31), 120, 15),
            (5, 'L2026006', date(2027, 9, 30), 90, 15),
            (6, 'L2026007', date(2027, 6, 30), 75, 10),
            (7, 'L2026008', date(2027, 3, 31), 50, 10),
            (8, 'L2026009', date(2027, 5, 31), 60, 10),
            (9, 'L2026010', date(2027, 8, 31), 40, 10),
            (10, 'L2026011', date(2027, 4, 30), 30, 10),
            (11, 'L2026012', date(2027, 7, 31), 45, 10),
            (12, 'L2026013', date(2027, 2, 28), 25, 5),
        ]

        meds_creados = []
        for idx, (pa, conc, ff, pres, lab, cod) in enumerate(medicamentos_data):
            med, created = Medicamento.objects.get_or_create(
                principio_activo=pa,
                forma_farmaceutica=ff,
                defaults={
                    'tipo': 'MEDICAMENTO',
                    'concentracion': conc,
                    'presentacion': pres,
                    'laboratorio': lab,
                    'codigo': cod,
                }
            )
            if not created:
                # Actualizar campos en caso de que ya exista
                med.codigo = cod
                med.concentracion = conc
                med.presentacion = pres
                med.laboratorio = lab
                med.save()
            meds_creados.append(med)

        self.stdout.write(
            self.style.SUCCESS(f"✓ {len(meds_creados)} medicamentos asegurados.")
        )

        # ---------- Dispositivos ----------
        dispositivos_data = [
            ('JERINGA 5ML', None, 'DISPOSITIVO', 'NO APLICA', 'Caja x 100', 'LIFE CARE', 'D770001'),
            ('GUANTES LATEX TALLA M', None, 'DISPOSITIVO', 'NO APLICA', 'Caja x 100', 'MEDIGLOVES', 'D770002'),
            ('ALGODON 500G', None, 'DISPOSITIVO', 'NO APLICA', 'Bolsa x 500g', 'SUNMAX', 'D770003'),
        ]

        dispositivos_lotes = [
            (0, 'D2026001', date(2028, 1, 31), 500, 50),
            (1, 'D2026002', date(2027, 12, 31), 1000, 100),
            (2, 'D2026003', date(2028, 3, 31), 200, 30),
        ]

        disp_creados = []
        for pa, conc, tipo, ff, pres, lab, cod in dispositivos_data:
            med, created = Medicamento.objects.get_or_create(
                principio_activo=pa,
                forma_farmaceutica=ff,
                defaults={
                    'tipo': tipo,
                    'presentacion': pres,
                    'laboratorio': lab,
                    'codigo': cod,
                }
            )
            if not created:
                med.tipo = tipo
                med.codigo = cod
                med.presentacion = pres
                med.laboratorio = lab
                med.save()
            disp_creados.append(med)

        self.stdout.write(
            self.style.SUCCESS(f"✓ {len(disp_creados)} dispositivos asegurados.")
        )

        # ---------- Crear Stock (solo si no existe el lote) ----------
        stock_creado = 0
        for idx, lote, venc, cantidad, stock_min in medicamentos_lotes:
            med = meds_creados[idx]
            _, created = InventarioStock.objects.get_or_create(
                ubicacion=ubicacion,
                medicamento=med,
                lote=lote,
                defaults={
                    'fecha_vencimiento': venc,
                    'cantidad_actual': cantidad,
                    'stock_minimo': stock_min,
                }
            )
            if created:
                stock_creado += 1

        for idx, lote, venc, cantidad, stock_min in dispositivos_lotes:
            med = disp_creados[idx]
            _, created = InventarioStock.objects.get_or_create(
                ubicacion=ubicacion,
                medicamento=med,
                lote=lote,
                defaults={
                    'fecha_vencimiento': venc,
                    'cantidad_actual': cantidad,
                    'stock_minimo': stock_min,
                }
            )
            if created:
                stock_creado += 1

        self.stdout.write(
            self.style.SUCCESS(f"✓ {stock_creado} lotes de stock creados.")
        )

        # ---------- Asegurar usuario admin demo ----------
        admin_user, creado = User.objects.get_or_create(
            username='admin',
            defaults={
                'first_name': 'Admin',
                'last_name': 'Sistema',
                'email': 'admin@farmacia.com',
                'is_staff': True,
            }
        )
        if creado:
            admin_user.set_password('admin123')
            admin_user.save()
            PerfilUsuario.objects.create(
                usuario=admin_user,
                ubicacion_asignada=ubicacion,
                numero_identificacion='000000000'
            )
            grupo_admin = Group.objects.get(name='ADMIN')
            admin_user.groups.add(grupo_admin)
            self.stdout.write(self.style.SUCCESS("✓ Usuario admin creado (admin / admin123)"))

        # ---------- Crear movimientos de ejemplo (últimos 7 días) ----------
        pacientes = [
            ('1012345678', 'Pérez, Juan'),
            ('1023456789', 'García, María'),
            ('1034567890', 'Rodríguez, Carlos'),
            ('1045678901', 'López, Ana'),
        ]

        movimientos_data = [
            # (dias_atras, med_idx, cantidad, paciente_idx, tipo)
            (7, 0, 10, 0, 'SALIDA'),
            (7, 1, 8, 1, 'SALIDA'),
            (6, 2, 15, 2, 'SALIDA'),
            (6, 3, 5, 3, 'SALIDA'),
            (5, 4, 12, 0, 'SALIDA'),
            (5, 7, 3, 1, 'SALIDA'),
            (4, 8, 6, 2, 'SALIDA'),
            (3, 0, 20, 3, 'SALIDA'),
            (2, 5, 10, 0, 'SALIDA'),
            (1, 1, 15, 1, 'SALIDA'),
            # Una entrada de bodega central
            (3, 6, 30, 'CENTRAL', 'ENTRADA'),
            # Una devolución
            (1, 0, 3, 0, 'DEVOLUCION'),
        ]

        docs_creados = 0
        for dias_atras, med_idx, cantidad, paciente_ref, tipo in movimientos_data:
            stock_item = InventarioStock.objects.filter(
                ubicacion=ubicacion,
                medicamento=meds_creados[med_idx]
            ).first()
            if not stock_item:
                continue

            fecha_mov = timezone.now() - timedelta(days=dias_atras)

            if tipo == 'SALIDA':
                # Verificar que haya suficiente stock
                if stock_item.cantidad_actual >= cantidad:
                    doc = Documento.objects.create(
                        tipo_mov='SALIDA',
                        fecha=fecha_mov,
                        usuario=admin_user,
                        origen=ubicacion,
                        id_paciente=pacientes[paciente_ref][0],
                    )
                    DocumentoDetalle.objects.create(
                        documento=doc,
                        medicamento=stock_item.medicamento,
                        lote=stock_item.lote,
                        cantidad=cantidad,
                    )
                    stock_item.cantidad_actual -= cantidad
                    stock_item.save()
                    docs_creados += 1

            elif tipo == 'ENTRADA':
                doc = Documento.objects.create(
                    tipo_mov='ENTRADA',
                    fecha=fecha_mov,
                    usuario=admin_user,
                    destino=ubicacion,
                    origen=bodega_central,
                    id_paciente=f"REPOSICION-SAMPLE",
                )
                DocumentoDetalle.objects.create(
                    documento=doc,
                    medicamento=stock_item.medicamento,
                    lote=stock_item.lote,
                    cantidad=cantidad,
                )
                stock_item.cantidad_actual += cantidad
                stock_item.save()
                docs_creados += 1

            elif tipo == 'DEVOLUCION':
                # Buscar un documento de salida reciente del mismo paciente y medicamento
                doc_salida = Documento.objects.filter(
                    tipo_mov='SALIDA',
                    origen=ubicacion,
                    id_paciente=pacientes[paciente_ref][0],
                    detalles__medicamento=stock_item.medicamento,
                ).first()
                if doc_salida:
                    doc = Documento.objects.create(
                        tipo_mov='DEVOLUCION',
                        fecha=fecha_mov,
                        usuario=admin_user,
                        destino=ubicacion,
                        origen=ubicacion,
                        id_paciente=pacientes[paciente_ref][0],
                        documento_referencia=doc_salida,
                    )
                    DocumentoDetalle.objects.create(
                        documento=doc,
                        medicamento=stock_item.medicamento,
                        lote=stock_item.lote,
                        cantidad=cantidad,
                    )
                    stock_item.cantidad_actual += cantidad
                    stock_item.save()
                    docs_creados += 1

        self.stdout.write(
            self.style.SUCCESS(f"✓ {docs_creados} movimientos de ejemplo creados.")
        )

        # ---------- Resumen ----------
        total_meds = Medicamento.objects.filter(tipo='MEDICAMENTO').count()
        total_disp = Medicamento.objects.filter(tipo='DISPOSITIVO').count()
        total_stock = InventarioStock.objects.filter(ubicacion=ubicacion).count()
        from django.db.models import Q as models_Q
        total_docs = Documento.objects.filter(
            models_Q(origen=ubicacion) | models_Q(destino=ubicacion)
        ).count()

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS("  ✅ CARGA DE DATOS COMPLETADA"))
        self.stdout.write("=" * 50)
        self.stdout.write(f"  Sede:          {sede_nombre}")
        self.stdout.write(f"  Medicamentos:  {total_meds}")
        self.stdout.write(f"  Dispositivos:  {total_disp}")
        self.stdout.write(f"  Stock (lotes): {total_stock}")
        self.stdout.write(f"  Movimientos:   {total_docs}")
        self.stdout.write(f"  Usuario admin: admin / admin123")
        self.stdout.write("=" * 50)
