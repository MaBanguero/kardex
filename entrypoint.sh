#!/bin/sh
# =============================================================================
# KARDEX — Entrypoint: run migrations, then start Gunicorn
# =============================================================================
set -e

echo "→ Running database migrations..."
python manage.py migrate --noinput

# ============================================================
# RESET DE DATOS PARA PRODUCCIÓN (solo si RESET_DB=true)
# Elimina movimientos, medicamentos y stock.
# Una vez ejecutado, quitar la variable RESET_DB del entorno.
# ============================================================
if [ "$RESET_DB" = "true" ]; then
    echo "→ RESET_DB activado. Limpiando todos los datos del sistema..."
    python manage.py shell -c "
from kardex.models import (
    Documento, DocumentoDetalle, TurnoEnfermera, SolicitudStock,
    Conciliacion, DetalleConciliacion, CargaRIPS, RegistroRIPS,
    MapeoRIPSMedicamento, InventarioStock, Medicamento
)
Documento.objects.all().update(documento_referencia=None)
DocumentoDetalle.objects.all().delete()
Documento.objects.all().delete()
TurnoEnfermera.objects.all().delete()
SolicitudStock.objects.all().delete()
DetalleConciliacion.objects.all().delete()
Conciliacion.objects.all().delete()
RegistroRIPS.objects.all().delete()
CargaRIPS.objects.all().delete()
MapeoRIPSMedicamento.objects.all().delete()
InventarioStock.objects.all().delete()
count, _ = Medicamento.objects.all().delete()
print(f'    ✅ {count} medicamentos y todos los datos eliminados')
print('    🔄 Una vez verificado, quita RESET_DB del entorno para evitar reinicios.')
"
fi

echo "→ Collecting static files..."
python manage.py collectstatic --noinput --clear 2>/dev/null || python manage.py collectstatic --noinput

echo "→ Starting Gunicorn..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers ${GUNICORN_WORKERS:-4} \
    --threads ${GUNICORN_THREADS:-2} \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --log-level info
