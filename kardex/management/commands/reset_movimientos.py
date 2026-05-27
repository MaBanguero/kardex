#!/usr/bin/env python3
"""
Management command para reiniciar todos los movimientos del sistema
al iniciar producción. Conserva datos maestros (usuarios, sedes, medicamentos).

Elimina:
  - DocumentoDetalle, Documento
  - TurnoEnfermera
  - SolicitudStock
  - Conciliacion, DetalleConciliacion
  - CargaRIPS, RegistroRIPS
  - MapeoRIPSMedicamento
  - Resetea InventarioStock.cantidad_actual a 0

Conserva:
  - Ubicacion (sedes/bodegas)
  - Medicamento (catálogo)
  - User + PerfilUsuario
  - ConfiguracionSistema
  - Groups (roles)
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = 'Reinicia todos los movimientos para salida a producción'

    MODELS_TO_CLEAR = [
        'DocumentoDetalle',
        'Documento',
        'TurnoEnfermera',
        'SolicitudStock',
        'DetalleConciliacion',
        'Conciliacion',
        'RegistroRIPS',
        'CargaRIPS',
        'MapeoRIPSMedicamento',
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Ejecutar sin confirmación interactiva'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Mostrar lo que se eliminaría sin hacer cambios'
        )

    def handle(self, *args, **options):
        force = options['force']
        dry_run = options['dry_run']

        from django.apps import apps
        from kardex.models import InventarioStock

        self.stdout.write(self.style.WARNING('=' * 55))
        self.stdout.write(self.style.WARNING('  ⚠️  REINICIO DE MOVIMIENTOS'))
        self.stdout.write(self.style.WARNING('=' * 55))
        self.stdout.write('')

        # Contar registros a eliminar
        total_eliminar = 0
        tabla_info = []
        for nombre_modelo in self.MODELS_TO_CLEAR:
            try:
                modelo = apps.get_model('kardex', nombre_modelo)
                count = modelo.objects.count()
                if count > 0:
                    tabla_info.append((nombre_modelo, count))
                    total_eliminar += count
            except LookupError:
                pass

        # Stock a resetear
        stocks_con_cantidad = InventarioStock.objects.filter(cantidad_actual__gt=0).count()
        if stocks_con_cantidad > 0:
            tabla_info.append(('InventarioStock (cantidad>0 → 0)', stocks_con_cantidad))
            total_eliminar += stocks_con_cantidad

        if not tabla_info:
            self.stdout.write(self.style.SUCCESS('✅ No hay movimientos que limpiar. El sistema ya está limpio.'))
            return

        self.stdout.write(f'Se {"eliminarían" if dry_run else "eliminarán"} los siguientes registros:\n')
        self.stdout.write(f'{"MODELO":<35} {"CANTIDAD":<10}')
        self.stdout.write('-' * 45)
        for nombre, count in tabla_info:
            self.stdout.write(f'{nombre:<35} {count:<10}')
        self.stdout.write('-' * 45)
        self.stdout.write(f'{"TOTAL":<35} {total_eliminar:<10}')
        self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 MODO DRY-RUN — No se realizaron cambios.'))
            return

        # Confirmación
        if not force:
            confirm = input('¿Estás seguro? Esta operación NO es reversible. Escribe "REINICIAR" para confirmar: ')
            if confirm.strip() != 'REINICIAR':
                self.stdout.write(self.style.ERROR('❌ Operación cancelada.'))
                return

        # Ejecutar limpieza
        with transaction.atomic():
            eliminados = {}

            # 1. Nullificar referencias protegidas antes de borrar
            Documento = apps.get_model('kardex', 'Documento')
            Documento.objects.all().update(documento_referencia=None)

            for nombre_modelo in self.MODELS_TO_CLEAR:
                try:
                    modelo = apps.get_model('kardex', nombre_modelo)
                    count, _ = modelo.objects.all().delete()
                    eliminados[nombre_modelo] = count
                    self.stdout.write(f'  🗑️  {nombre_modelo}: {count} eliminados')
                except LookupError:
                    pass

            # Resetear stock
            stock_count = InventarioStock.objects.filter(cantidad_actual__gt=0).update(cantidad_actual=0)
            if stock_count:
                self.stdout.write(f'  🔄 InventarioStock: {stock_count} registros reseteados a 0')
                eliminados['InventarioStock'] = stock_count

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 55))
        self.stdout.write(self.style.SUCCESS('  ✅ REINICIO COMPLETADO'))
        self.stdout.write(self.style.SUCCESS(f'  Se procesaron {sum(eliminados.values())} registros'))
        self.stdout.write(self.style.SUCCESS('=' * 55))
        self.stdout.write('')
        self.stdout.write('📌 Datos maestros conservados:')
        self.stdout.write('  • Usuarios y perfiles')
        self.stdout.write('  • Sedes/ubicaciones')
        self.stdout.write('  • Catálogo de medicamentos')
        self.stdout.write('  • Configuración del sistema')
        self.stdout.write('  • Roles y grupos')
