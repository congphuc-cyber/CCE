#!/usr/bin/env bash
# ==============================================================================
# Script khởi chạy máy chủ phát triển cho Linux / macOS
# Sử dụng: ./scripts/run.sh [PORT]
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PORT="${1:-8001}"

cd "$ROOT_DIR/site1"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
  echo "Chưa tìm thấy .venv. Đang chạy cài đặt ban đầu..."
  bash "$ROOT_DIR/scripts/setup.sh"
fi

echo ">>> Khởi chạy máy chủ CCE tại http://127.0.0.1:$PORT/ ..."
echo "    - Trang chủ:          http://127.0.0.1:$PORT/"
echo "    - Đăng nhập:          http://127.0.0.1:$PORT/login/"
echo "    - Quản trị (Admin):   http://127.0.0.1:$PORT/admin/"
echo "    - Kiểm tra giao diện: http://127.0.0.1:$PORT/ui-demo/"
echo "    (Nhấn Ctrl+C để dừng máy chủ)"
echo ""

"$PYTHON_BIN" manage.py runserver "127.0.0.1:$PORT"
