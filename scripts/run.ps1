# ==============================================================================
# Script khởi chạy máy chủ phát triển trên Windows (PowerShell)
# Sử dụng: .\scripts\run.ps1 [PORT]
# ==============================================================================

param (
    [string]$Port = "8001"
)

$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location "$RootDir\site1"

$PythonExe = "$RootDir\.venv\Scripts\python.exe"
if (!(Test-Path $PythonExe)) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonExe = "python"
    } else {
        Write-Host "Chưa tìm thấy môi trường ảo .venv. Đang chạy cài đặt tự động..." -ForegroundColor Yellow
        & "$RootDir\scripts\setup.ps1"
        $PythonExe = "$RootDir\.venv\Scripts\python.exe"
    }
}

Write-Host ">>> Khởi chạy máy chủ CCE tại http://127.0.0.1:$Port/ ..." -ForegroundColor Green
Write-Host "    - Trang chủ:          http://127.0.0.1:$Port/" -ForegroundColor Cyan
Write-Host "    - Đăng nhập:          http://127.0.0.1:$Port/login/" -ForegroundColor Cyan
Write-Host "    - Quản trị (Admin):   http://127.0.0.1:$Port/admin/" -ForegroundColor Cyan
Write-Host "    - Kiểm tra giao diện: http://127.0.0.1:$Port/ui-demo/" -ForegroundColor Cyan
Write-Host "    (Nhấn Ctrl+C để dừng máy chủ)`n" -ForegroundColor Gray

& $PythonExe manage.py runserver "127.0.0.1:$Port"
