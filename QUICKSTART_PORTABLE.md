# SATyS — Quickstart portable

Versión: `2026.08.18-portable-oci-api-v1-8082-internos12`

## Objetivo

El código no depende de un usuario, una ruta `/data/...`, una unidad Windows ni un montaje `/depi/...` concretos. El entorno reproducible contiene Python, dependencias y Chromium/Playwright. Los datos, credenciales y rutas de cada instalación viven fuera de la imagen.

## Opción A — Docker Compose (Windows, macOS y Linux)

Requisitos del host: Docker Engine/Desktop con Compose.

```bash
cp .env.example .env
bash scripts/bootstrap_portable.sh
# editar config/configuracion_local.json
bash scripts/satys.sh build
bash scripts/satys.sh api-up
```

Validar:

```bash
curl http://127.0.0.1:8082/api/health
curl http://127.0.0.1:8082/api/v1/version
```

Pruebas:

```bash
bash scripts/satys.sh test
bash scripts/satys.sh smoke
```

La prueba `smoke` necesita conectividad real a `satys.ift.org.mx`.

En Windows PowerShell también se puede usar `./scripts/satys.ps1 bootstrap`, `build`, `api-up`, `test`, etc.

## Opción B — Podman en RHEL

Requisitos del host: Podman. No se necesita `podman-compose`; el proyecto incluye `scripts/podman_satys.sh`, que usa la CLI OCI directamente.

```bash
cp .env.example .env
bash scripts/bootstrap_portable.sh
bash scripts/podman_satys.sh build
bash scripts/podman_satys.sh api-up
```

Para una corrida real:

```bash
bash scripts/podman_satys.sh daily
```

## Datos persistentes

Por defecto viven en `./runtime/`:

- `runtime/TrámitesCRT.xlsx`
- `runtime/descargas/`
- `runtime/output/`
- `runtime/logs/`
- `runtime/runs/`
- `runtime/base_de_datos_rpc/`
- `runtime/registros_diarios/`
- `runtime/shared/`

Se cambian con `.env`; no se modifica el código.

El procesamiento de Internos usa 12 Chromium por defecto y 6 GB de memoria
compartida. Se puede ajustar sin editar código mediante
`SATYS_INTERNOS_WORKERS` y `SATYS_SHM_SIZE`; no existe un máximo artificial de
workers, aunque el host y el portal SATyS siguen siendo los límites reales.

## Credenciales

Copiar `config/configuracion_local.example.json` a `config/configuracion_local.json` o usar las variables `SATYS_USUARIO`, `SATYS_PASSWORD`, `SATYS_EMAIL_*`. El archivo real se excluye de la imagen y de las releases.

## Playwright

La imagen y el paquete Python están fijados en `1.57.0`. Una actualización de Playwright debe cambiar ambos valores juntos y pasar tests + smoke contra SATyS antes de producción.

## Equipo nuevo fuera de la red institucional

Puede construir la imagen, iniciar la API, ejecutar tests unitarios y desarrollar el panel. El login/extracción real sólo funciona desde una red con acceso a `https://satys.ift.org.mx/`.
