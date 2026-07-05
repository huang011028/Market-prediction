param(
    [string]$Python = "python",
    [switch]$IncludeSlow,
    [switch]$IncludeNetwork,
    [switch]$IncludeLLM
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$markers = @()
if (-not $IncludeSlow) { $markers += "not slow" }
if (-not $IncludeNetwork) { $markers += "not network" }
if (-not $IncludeLLM) { $markers += "not llm" }

$markerExpr = if ($markers.Count -gt 0) { $markers -join " and " } else { "" }

Write-Host "== Python =="
& $Python --version

Write-Host "== Compile key entrypoints =="
& $Python -m py_compile `
    api_server.py `
    config\settings.py `
    config\weight_manager.py `
    scripts\run_analysis.py `
    scripts\run_backtest.py `
    scripts\show_stats.py `
    scripts\track_predictions.py

Write-Host "== Pytest =="
if ($markerExpr) {
    & $Python -m pytest -q -m $markerExpr
} else {
    & $Python -m pytest -q
}
