#!/usr/bin/env bash
# ==============================================================================
# Script cài đặt & khởi tạo tự động cho Linux / macOS
# Sử dụng: ./scripts/setup.sh [--demo]
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

DEMO_FLAG=""
if [ "$1" == "--demo" ]; then
  DEMO_FLAG="--demo"
fi

echo ">>> [1/4] Kiểm tra môi trường Python..."
if ! command -v python3 &> /dev/null; then
  echo "LỖI: Không tìm thấy python3. Vui lòng cài đặt Python >= 3.10."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo ">>> Tạo môi trường ảo tại .venv..."
  python3 -m venv .venv
fi

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

echo ">>> [2/4] Cài đặt thư viện từ requirements/base.txt..."
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements/base.txt

echo ">>> [3/4] Cấu hình file .env..."
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "    Đã tạo file .env từ .env.example."
else
  echo "    File .env đã tồn tại, giữ nguyên."
fi

echo ">>> [4/4] Khởi tạo hệ thống (bootstrap)..."
cd "$ROOT_DIR/site1"
"$PYTHON_BIN" manage.py bootstrap $DEMO_FLAG

echo ""
echo ">>> CÀI ĐẶT HOÀN TẤT! Chạy dự án bằng lệnh: ./scripts/run.sh"
