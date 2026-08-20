param(
    [switch]$Visible,
    [switch]$SinEmail,
    [switch]$NoProcesar,
    [ValidateRange(0, 100000)]
    [int]$MaxFoliosInternos = 0,
    [ValidateScript({ $_ -ge 1 })]
    [int]$InternosWorkers = 12,
    [ValidateRange(1, 32)]
    [int]$Workers = 10
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonBin = $env:SATYS_PYTHON

if ([string]::IsNullOrWhiteSpace($PythonBin)) {
    $Candidates = @(
        (Join-Path $ProjectDir ".venv\Scripts\python.exe"),
        (Join-Path (Split-Path -Parent $ProjectDir) "venv\Scripts\python.exe")
    )
    $PythonBin = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($PythonBin)) {
    $PythonBin = "python"
}

$CommandArgs = @(
    "automatizar_registros_diario.py",
    "--python", "$PythonBin",
    "--solo-internos",
    "--workers", "$Workers",
    "--internos-workers", "$InternosWorkers",
    "--estado-json", (Join-Path $ProjectDir "logs\estado_actual.json"),
    "--sin-notificacion"
)
if ($MaxFoliosInternos -gt 0) {
    $CommandArgs += @("--max-folios-internos", "$MaxFoliosInternos")
}
if ($Visible) {
    $CommandArgs += "--visible"
}
else {
    $CommandArgs += "--headless"
}
if ($SinEmail) {
    $CommandArgs += "--sin-email"
}
if ($NoProcesar) {
    $CommandArgs += "--no-procesar"
}

Push-Location $ProjectDir
try {
    Write-Host "SATyS Internos nuevos: inventario de seis bandejas y procesamiento de pendientes."
    & $PythonBin @CommandArgs
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $ExitCode
