#!/usr/bin/env bash
set -e

echo ">>> Preparing database..."
cd /app/site1

echo ">>> Running migrations..."
python manage.py migrate --noinput

echo ">>> Creating cache table if needed..."
python -c "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'site1.settings.prod'); django.setup(); from django.core.management import call_command; from django.db import connection; call_command('createcachetable', 'django_cache') if 'django_cache' not in connection.introspection.table_names() else print('Cache table already exists')"

echo ">>> Collecting static files..."
python manage.py collectstatic --noinput

echo ">>> Starting CCE with Gunicorn..."
exec python -m gunicorn site1.wsgi:application \
    --bind 0.0.0.0:${PORT:-8001} \
    --workers 2 \
    --timeout 120
