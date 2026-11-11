# ==============================================================================
# Script chuyển tiếp setup khi người dùng đang ở thư mục site1
# ==============================================================================
param (
    [switch]$Demo
)

$TargetScript = Join-Path $PSScriptRoot "..\..\scripts\setup.ps1"
if (Test-Path $TargetScript) {
    & (Resolve-Path $TargetScript) -Demo:$Demo
} else {
    python manage.py bootstrap
}
