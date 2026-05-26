"""
Management command para cargar el reporte201 (RIPS) y conciliar contra Kardex.

Uso:
    python manage.py cargar_rips --archivo=/ruta/reporte201.csv
    python manage.py cargar_rips --ultimo-dia
"""

import os, glob, re
from django.core.management.base import BaseCommand
from django.utils import timezone
from kardex.services import procesar_importacion_rips, ejecutar_conciliacion


class Command(BaseCommand):
    help = "Carga reporte201 RIPS y opcionalmente concilia contra Kardex"

    def add_arguments(self, parser):
        parser.add_argument('--archivo', type=str, help='Ruta al archivo CSV reporte201')
        parser.add_argument('--sede', type=str, default='', help='Filtrar por sede')
        parser.add_argument('--no-conciliar', action='store_true', help='Solo cargar, no conciliar')
        parser.add_argument('--ultimo-dia', action='store_true', help='Buscar reporte más reciente en Downloads')

    def handle(self, *args, **options):
        archivo = options.get('archivo')
        ultimo_dia = options.get('ultimo_dia')
        sede = options.get('sede', '')
        no_conciliar = options.get('no_conciliar', False)

        if ultimo_dia or not archivo:
            pattern = os.path.expanduser("~/Downloads/reporte201_*.csv")
            archivos = glob.glob(pattern)
            if not archivos:
                self.stdout.write(self.style.ERROR("No se encontró reporte201 en ~/Downloads"))
                return
            archivo = max(archivos, key=os.path.getmtime)

        if not os.path.exists(archivo):
            self.stdout.write(self.style.ERROR(f"Archivo no encontrado: {archivo}"))
            return

        self.stdout.write(f"📂 Procesando: {os.path.basename(archivo)}")

        with open(archivo, 'rb') as f:
            carga, mensaje, success = procesar_importacion_rips(f, sede_filter=sede)

        if not success:
            self.stdout.write(self.style.ERROR(mensaje))
            return

        self.stdout.write(self.style.SUCCESS(f"✅ {mensaje}"))

        if not no_conciliar:
            self.stdout.write("🔍 Ejecutando conciliación...")
            conciliacion = ejecutar_conciliacion(carga)
            self.stdout.write(self.style.SUCCESS(
                f"📊 Conciliación completada:\n"
                f"  ✅ Coinciden:     {conciliacion.coincidencias}\n"
                f"  ❌ No facturados: {conciliacion.no_facturados} (en Kardex, faltan en RIPS)\n"
                f"  ⚠️ No despachados: {conciliacion.no_despachados} (en RIPS, faltan en Kardex)"
            ))
