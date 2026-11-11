# ==============================================================================
# Script cài đặt & khởi tạo tự động cho Windows (PowerShell)
# Sử dụng: .\scripts\setup.ps1 [-Demo]
# ==============================================================================

param (
    [switch]$Demo
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

Write-Host ">>> [1/4] Kiểm tra môi trường Python..." -ForegroundColor Cyan
if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Không tìm thấy Python. Vui lòng cài đặt Python >= 3.10."
}

# Tạo .venv nếu chưa có
if (!(Test-Path "$RootDir\.venv")) {
    Write-Host ">>> Tạo môi trường ảo tại .venv..." -ForegroundColor Yellow
    python -m venv .venv
}

$PythonExe = "$RootDir\.venv\Scripts\python.exe"

# Cài đặt thư viện
Write-Host ">>> [2/4] Cài đặt các gói thư viện từ requirements/base.txt..." -ForegroundColor Cyan
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r requirements/base.txt

# Tạo file .env nếu chưa có
Write-Host ">>> [3/4] Cấu hình file .env..." -ForegroundColor Cyan
if (!(Test-Path "$RootDir\.env")) {
    Copy-Item "$RootDir\.env.example" "$RootDir\.env"
    Write-Host "    Đã tạo file .env từ .env.example." -ForegroundColor Green
} else {
    Write-Host "    File .env đã tồn tại, giữ nguyên cấu hình." -ForegroundColor Gray
}

# Chạy bootstrap
Write-Host ">>> [4/4] Chạy lệnh bootstrap khởi tạo..." -ForegroundColor Cyan
Set-Location "$RootDir\site1"

$BootstrapArgs = @("manage.py", "bootstrap")
if ($Demo) {
    $BootstrapArgs += "--demo"
}

& $PythonExe @BootstrapArgs

Set-Location $RootDir
Write-Host "`n>>> CÀI ĐẶT HOÀN TẤT! Chạy dự án bằng lệnh: .\scripts\run.ps1" -ForegroundColor Green
