param(
    [int]$Port = 8080,
    [string]$HostName = "0.0.0.0",
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "============================================================"
Write-Host "  Market Prediction Web 启动"
Write-Host "============================================================"
Write-Host "  端口: $Port"
Write-Host "  前端: http://localhost:$Port/"
Write-Host "  API:  http://localhost:$Port/docs"
Write-Host "============================================================"

if (-not (Test-Path ".env")) {
    Write-Warning ".env 文件不存在，请复制 .env.example 并填写 API Key"
    Copy-Item ".env.example" ".env"
    Write-Host "已创建 .env 模板，请编辑后重新运行"
    exit 1
}

$pythonCandidates = @(
    "python",
    "D:\anaconda\python.exe"
)

$python = $null
foreach ($candidate in $pythonCandidates) {
    try {
        & $candidate --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $python = $candidate
            break
        }
    } catch {
        continue
    }
}

if (-not $python) {
    throw "未找到可用 Python。请激活虚拟环境后重试。"
}

$argsList = @("api_server.py", "--host", $HostName, "--port", "$Port")
if ($Reload) {
    $argsList += "--reload"
}

& $python @argsList
