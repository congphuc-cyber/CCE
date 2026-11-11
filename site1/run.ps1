# ==============================================================================
# Khởi chạy máy chủ CCE trực tiếp từ site1
# ==============================================================================
param (
    [string]$Port = "8001"
)

$TargetScript = Join-Path $PSScriptRoot "..\scripts\run.ps1"
if (Test-Path $TargetScript) {
    & (Resolve-Path $TargetScript) -Port $Port
} else {
    python manage.py runserver "127.0.0.1:$Port"
}
