"""
Management command para consolidar medicamentos duplicados en la BD.

Problema: El campo `concentracion` se guardaba como string vacio ('')
en vez de NULL, rompiendo el `get_or_create` que buscaba con 
`concentracion IS NULL`. Esto generaba registros duplicados de Medicamento
cada vez que se cargaba el mismo producto sin concentracion.

Este comando:
1. Normaliza concentracion='' a NULL
2. Identifica grupos de Medicamento duplicados (mismo PA + FF + conc)
3. Consolida todo el stock y referencias en un solo registro canonico
4. Elimina los duplicados sobrantes

Uso:
    python manage.py consolidar_medicamentos [--dry-run]
    
Con --dry-run solo muestra que se va a hacer sin modificar nada.
"""
import logging
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import models, transaction

from kardex.models import (
    Medicamento, InventarioStock, SolicitudStock,
    DocumentoDetalle, DetalleConciliacion, MapeoRIPSMedicamento,
)

logger = logging.getLogger(__name__)

# Modelos que referencian a Medicamento como FK, ordenados por importancia
FK_REFS = [
    ('InventarioStock', InventarioStock, 'medicamento'),
    ('SolicitudStock', SolicitudStock, 'medicamento'),
    ('DocumentoDetalle', DocumentoDetalle, 'medicamento'),
    ('DetalleConciliacion', DetalleConciliacion, 'medicamento'),
    ('MapeoRIPSMedicamento', MapeoRIPSMedicamento, 'medicamento'),
]


class Command(BaseCommand):
    help = 'Consolida medicamentos duplicados y normaliza concentracion'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar que se hara sin modificar la BD',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🧪 MODO DRY RUN — No se modificara nada\n'))
        else:
            self.stdout.write(self.style.WARNING('⚠️  MODO REAL — Se modificara la base de datos\n'))
            confirm = input('Escribe "CONSOLIDAR" para continuar: ')
            if confirm != 'CONSOLIDAR':
                self.stdout.write(self.style.ERROR('Cancelado.'))
                return

        # Paso 1: Normalizar concentracion = '' a NULL
        self._normalizar_concentracion(dry_run)

        # Paso 2: Consolidar duplicados
        self._consolidar_duplicados(dry_run)

        self.stdout.write(self.style.SUCCESS('\n✅ Comando completado.'))

    def _normalizar_concentracion(self, dry_run):
        """Convierte concentracion='' a NULL en toda la tabla."""
        
        affected = Medicamento.objects.filter(concentracion='').count()
        if affected == 0:
            self.stdout.write('✓ No hay registros con concentracion vacia.')
            return

        self.stdout.write(
            f'→ Normalizando {affected} registros con concentracion="" a NULL...'
        )

        if not dry_run:
            updated = Medicamento.objects.filter(concentracion='').update(concentracion=None)
            self.stdout.write(self.style.SUCCESS(f'  ✓ {updated} registros actualizados.'))
        else:
            ids = list(Medicamento.objects.filter(concentracion='').values_list('id', flat=True)[:20])
            self.stdout.write(f'  🔍 Se actualizarian {affected} registros (ids ej: {ids}...)')

    def _consolidar_duplicados(self, dry_run):
        """
        Encuentra grupos de medicamentos duplicados y consolida
        todo el stock/referencias en el registro canonico.
        
        Estrategia: 
        - Por cada grupo (PA, FF, conc), el canonico es el que tiene 
          MAS registros de InventarioStock (mayor cantidad de stock).
        - Si hay empate, se queda el de menor ID (mas antiguo).
        """
        # Todos los medicamentos agrupados
        all_meds = Medicamento.objects.all().order_by('principio_activo', 'forma_farmaceutica', 'concentracion', 'id')

        # Agrupar manualmente (COALESCE no funciona bien con NULL en el ORM)
        groups = defaultdict(list)
        for m in all_meds:
            key = (m.principio_activo, m.forma_farmaceutica, m.concentracion)
            groups[key].append(m)

        total_duplicates = 0
        total_consolidated = 0

        for key, members in groups.items():
            if len(members) <= 1:
                continue  # No hay duplicados

            pa, ff, conc = key
            duplicates = members[1:]  # Todos menos el primero son duplicados potenciales

            # Elegir canonico: el que tenga MAS entradas de InventarioStock
            # (mayor cantidad de stock total)
            def stock_score(m):
                entries = InventarioStock.objects.filter(medicamento=m)
                return (entries.count(), m.id)

            members_sorted = sorted(members, key=stock_score, reverse=True)
            canonical = members_sorted[0]
            to_delete = [m for m in members_sorted[1:]]

            total_duplicates += len(to_delete)

            self.stdout.write(
                f'\n{"─"*60}'
                f'\n📦 {pa[:60]}'
                f'\n   Forma: {ff} | Conc: {conc or "NULL"}'
                f'\n   Canonico: ID {canonical.id} ({canonical.codigo or "sin codigo"})'
                f'\n   Duplicados a eliminar: {[m.id for m in to_delete]}'
            )

            # Contar stock que se migrara
            for dup in to_delete:
                stock_count = InventarioStock.objects.filter(medicamento=dup).count()
                if stock_count > 0:
                    self.stdout.write(f'     → ID {dup.id}: {stock_count} registros de stock a migrar')

            if dry_run:
                continue

            # CONSOLIDAR: migrar referencias de duplicados al canonico
            with transaction.atomic():
                for dup in to_delete:
                    # 1. Migrar InventarioStock
                    for stock in InventarioStock.objects.filter(medicamento=dup):
                        # Si ya existe en canonico con mismo lote y ubicacion, sumar cantidad
                        existing = InventarioStock.objects.filter(
                            ubicacion=stock.ubicacion,
                            medicamento=canonical,
                            lote=stock.lote,
                        ).first()
                        if existing:
                            existing.cantidad_actual += stock.cantidad_actual
                            existing.save()
                            stock.delete()
                        else:
                            # Reasignar el stock al canonico
                            InventarioStock.objects.filter(id=stock.id).update(
                                medicamento=canonical
                            )

                    # 2. Migrar SolicitudStock
                    SolicitudStock.objects.filter(medicamento=dup).update(
                        medicamento=canonical
                    )

                    # 3. Migrar DocumentoDetalle
                    DocumentoDetalle.objects.filter(medicamento=dup).update(
                        medicamento=canonical
                    )

                    # 4. Migrar DetalleConciliacion
                    DetalleConciliacion.objects.filter(medicamento=dup).update(
                        medicamento=canonical
                    )

                    # 5. Migrar MapeoRIPSMedicamento
                    MapeoRIPSMedicamento.objects.filter(medicamento=dup).update(
                        medicamento=canonical
                    )

                    # 6. Eliminar duplicado
                    dup.delete()
                    total_consolidated += 1

        if dry_run:
            self.stdout.write(
                f'\n{"="*60}'
                f'\n🔍 DRY RUN: Se consolidarian {total_duplicates} registros duplicados'
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n{"="*60}'
                    f'\n✅ Consolidados: {total_consolidated} registros duplicados eliminados'
                )
            )
