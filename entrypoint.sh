#!/bin/sh
# =============================================================================
# KARDEX — Entrypoint: run migrations, then start Gunicorn
# =============================================================================
set -e

echo "→ Running database migrations..."
python manage.py migrate --noinput

echo "→ Cargando datos de demostración (se salta si ya existen)..."
python manage.py cargar_sample_data --sede=FarmaciaSede1 2>&1 || echo "  (aviso menor ignorado)"

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
