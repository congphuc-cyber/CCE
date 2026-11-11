#!/usr/bin/env bash
set -e

echo ">>> [Docker] Đang chuẩn bị cơ sở dữ liệu và tài khoản..."
cd /app/site1

python manage.py bootstrap ${BOOTSTRAP_EXTRA_ARGS:-}

echo ">>> [Docker] Khởi động máy chủ CCE trên cổng ${PORT:-8001}..."
exec python manage.py runserver "0.0.0.0:${PORT:-8001}"
