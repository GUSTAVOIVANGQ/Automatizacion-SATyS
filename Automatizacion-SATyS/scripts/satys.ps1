param(
    [Parameter(Position=0)]
    [ValidateSet('bootstrap','doctor','build','api-up','api-down','status','logs','smoke','daily','internos','internos-check','folio','test','help')]
    [string]$Command = 'help',

    [Parameter(Position=1)]
    [string]$Folio = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Bootstrap-Satys {
    if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
    if (-not (Test-Path 'config/configuracion_local.json')) {
        Copy-Item 'config/configuracion_local.example.json' 'config/configuracion_local.json'
        Write-Host 'Se creó config/configuracion_local.json; completa las credenciales.'
    }
    $dirs = @('runtime','runtime/descargas','runtime/output','runtime/logs','runtime/runs','runtime/exports','runtime/base_de_datos_rpc','runtime/registros_diarios','runtime/shared','runtime/locks')
    foreach ($d in $dirs) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    if ((Test-Path 'TrámitesCRT.xlsx') -and -not (Test-Path 'runtime/TrámitesCRT.xlsx')) {
        Copy-Item 'TrámitesCRT.xlsx' 'runtime/TrámitesCRT.xlsx'
    }
    Write-Host 'Runtime portable preparado.'
}

if ($Command -eq 'bootstrap') { Bootstrap-Satys; exit 0 }
if ($Command -eq 'doctor') {
    Bootstrap-Satys
    docker --version
    docker compose version
    if (Test-Path 'runtime/TrámitesCRT.xlsx') { Write-Host 'Excel runtime: OK' } else { Write-Warning 'Falta runtime/TrámitesCRT.xlsx' }
    exit 0
}

if ($Command -eq 'help') {
    Write-Host 'Uso: .\scripts\satys.ps1 bootstrap|doctor|build|api-up|api-down|status|logs|smoke|daily|internos|internos-check|folio NUMERO|test'
    exit 0
}

Bootstrap-Satys
switch ($Command) {
    'build'    { docker compose build }
    'api-up'   { docker compose up -d satys-api }
    'api-down' { docker compose down }
    'status'   { docker compose ps }
    'logs'     { docker compose logs -f --tail=200 satys-api }
    'smoke'    { docker compose run --rm --entrypoint python satys-worker scripts/smoke_internos.py --workers 6 }
    'daily'    { docker compose run --rm satys-worker }
    'internos' { docker compose run --rm --entrypoint python satys-worker automatizar_registros_diario.py --solo-internos --headless }
    'internos-check' { docker compose run --rm --entrypoint python satys-worker automatizar_registros_diario.py --solo-internos --no-procesar --sin-email --headless }
    'folio' {
        if ($Folio -notmatch '^\d{1,15}$') { throw 'Uso: .\scripts\satys.ps1 folio NUMERO' }
        docker compose run --rm --entrypoint python satys-worker automatizar_registros_diario.py --folio-internos $Folio --sin-email --headless
    }
    'test'     { docker compose run --rm --entrypoint python satys-worker -m unittest discover tests }
}
