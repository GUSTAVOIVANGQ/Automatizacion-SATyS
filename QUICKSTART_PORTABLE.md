# SATyS — Quickstart portable

Versión: `2026.09.08-trimestres-q3-2026-q4-2027-ruta-excel1`

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

Para ejecutar **sólo** la reparación final de filas `Ruta=_sin_operador`, sin
inventariar SATyS ni ejecutar Internos/Oficialía: primero intenta el buscador
público RPC y después clasifica en `_sin_operador/(correos)` los pendientes
cuya fuente original `descargas` contiene `MEMORANDUM.pdf`:

```bash
bash scripts/podman_satys.sh sin-operador-rpc
```

La ejecución independiente toma el mismo lock global que la corrida diaria,
por lo que se niega a iniciar si SATyS ya está procesando. Usa el Excel,
`descargas`, `output` y DEPI persistentes montados desde el runtime. Para una
auditoría sin modificar archivos ni Excel:

```bash
bash scripts/podman_satys.sh sin-operador-rpc --dry-run
```

Para revisar de principio a fin un único Folio de Internos, sin enviar correo:

```bash
bash scripts/podman_satys.sh folio 148326
```

El comando inventaría las seis bandejas, procesa todas las apariciones del
Folio y verifica `TrámitesCRT.xlsx` y la carpeta final de `output/`. Esta
revisión puntual no sincroniza todo el histórico hacia DEPI, así que termina
al completar las salidas locales. Si un anexo muestra la ventana intermedia
`Archivo PDF`, el segundo botón `VER DOCUMENTO` se procesa automáticamente.

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


### Corrección manual de remitentes desde PDFs

Sin ejecutar la corrida diaria completa:

```bash
bash scripts/podman_satys.sh remitentes-pdf --dry-run
bash scripts/podman_satys.sh remitentes-pdf
```

Sólo modifica `Solicitante Promovente`/`Representante Legal` vacíos o `SIN REMITENTE`; `descargas` es sólo lectura.

## Postproceso final sin volver a SATyS

Para ejecutar únicamente la parte final sobre el Excel/runtime ya existente:

```bash
bash scripts/podman_satys.sh postproceso-final
```

Orden: completa remitentes desde todos los PDF de `descargas`, reconcilia el Excel, reintenta `_sin_operador` con RPC público, clasifica memorandos en `(correos)`, fusiona/organiza `output`, sincroniza `output` + `TrámitesCRT.xlsx` a DEPI y finalmente envía el correo consolidado. La tarjeta `EN REVISIÓN` usa el Excel final y excluye `_sin_operador/(correos)`.

Para ejecutar el mismo postproceso sin mandar correo:

```bash
bash scripts/podman_satys.sh postproceso-final --sin-email
```


## Organización adicional 2026Q3 / 2026Q4

La release incluye una exportación adicional, idempotente y no destructiva,
basada en `TrámitesCRT.xlsx`. El mapeo es **personalizado** y se conserva tal
como fue solicitado (no corresponde a los trimestres calendario habituales):

- `Fecha de creación` entre **01/10/2026 y 15/12/2026** → `output/2026Q3/<Ruta>`.
- `Fecha de creación` entre **01/01/2026 y 31/03/2026** → `output/2026Q4/<Ruta>`.

La columna `1711` localiza la carpeta fuente en `descargas`; la columna `Ruta`
define la jerarquía que se reproduce dentro del bucket. Los archivos se
fusionan sin inventar sufijos y `descargas` conserva siempre los originales.
Los JSON no se publican en `output`. La misma Ruta del bucket se replica a
`<SATYS_SHARED_DIR>/output/2026Q3` o `2026Q4` antes del correo diario.

Auditoría sin copiar:

```bash
bash scripts/podman_satys.sh trimestres-2026 --dry-run
```

Ejecución real de **sólo** esta implementación:

```bash
bash scripts/podman_satys.sh trimestres-2026
```

Para organizar sólo localmente sin DEPI:

```bash
bash scripts/podman_satys.sh trimestres-2026 --sin-sync-depi
```

El timeout de esta etapa se controla con `SATYS_TRIMESTRES_2026_TIMEOUT`
(3600 segundos por defecto). La corrida diaria ejecuta esta etapa después de la
reparación final de `Ruta` (RPC público / `(correos)`) y antes del único correo.
