param(
    [switch]$Visible,
    [switch]$SinEmail,
    [ValidateRange(0, 6)]
    [int]$Workers = 6
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
    "main_procesar.py",
    "--todos-internos",
    "--internos-workers",
    "$Workers"
)
if (-not $Visible) {
    $CommandArgs += "--headless"
}
if ($SinEmail) {
    $CommandArgs += "--sin-email"
}

Push-Location $ProjectDir
try {
    $WorkersLabel = if ($Workers -eq 0) { "uno por bandeja" } else { "$Workers" }
    Write-Host "SATyS Internos: seis bandejas, navegadores paralelos: $WorkersLabel."
    & $PythonBin @CommandArgs
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $ExitCode
