param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidatePattern('^\d{1,15}$')]
    [string]$Folio,

    [switch]$Visible,

    [ValidateScript({ $_ -ge 1 })]
    [int]$InternosWorkers = 1
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
    $ResolvedPython = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $ResolvedPython) {
        $ResolvedPython = Get-Command python3 -CommandType Application -ErrorAction SilentlyContinue
    }
    if ($null -eq $ResolvedPython) {
        throw "No se encontró python.exe. Define SATYS_PYTHON con la ruta absoluta al intérprete."
    }
    $PythonBin = $ResolvedPython.Source
}

$CommandArgs = @(
    "automatizar_registros_diario.py",
    "--python", "$PythonBin",
    "--folio-internos", "$Folio",
    "--internos-workers", "$InternosWorkers",
    "--sin-email",
    "--sin-notificacion",
    "--estado-json", (Join-Path $ProjectDir "logs\estado_actual.json")
)
if ($Visible) {
    $CommandArgs += "--visible"
}
else {
    $CommandArgs += "--headless"
}

Push-Location $ProjectDir
try {
    Write-Host "SATyS: procesamiento completo del Folio Internos $Folio (correo deshabilitado)."
    & $PythonBin @CommandArgs
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $ExitCode
