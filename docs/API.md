# API SATyS v1

Versión documentada: `2026.08.17-portable-oci-api-v1-8082`.

Base local: `http://127.0.0.1:8082`. En producción, los consumidores externos deben entrar por el HTTPS institucional publicado por nginx; el proceso Uvicorn no se expone directamente a la red.

La ruta canónica es `/api/v1`. Los endpoints históricos `/api/...` se mantienen como aliases temporales **fuera del esquema OpenAPI** para no romper el panel ni consumidores existentes. `/api/health` permanece además como alias estable del `HEALTHCHECK` de contenedor.

Documentación viva: `/docs` (Swagger UI), `/redoc` (ReDoc) y `/openapi.json`.

> **Autenticación:** el backlog recibido exige que Swagger/ReDoc queden detrás de la autenticación de una “Fase 1.1”, pero esa Fase 1.1 no viene definida en el documento entregado. Esta release no inventa un esquema de autenticación; nginx queda listo para incorporar el mecanismo institucional cuando se especifique.

## Formato uniforme de error

Las excepciones HTTP y los errores de validación de FastAPI usan el mismo shape:

```json
{
  "detail": "Descripción legible del error",
  "code": "not_found"
}
```

Códigos de aplicación habituales: `bad_request`, `unauthorized`, `forbidden`, `not_found`, `conflict`, `validation_error`, `internal_error`, `service_unavailable`.

## Endpoints

### 1. Salud del servicio

**GET `/api/v1/health`** — sin parámetros.

```bash
curl -sS http://127.0.0.1:8082/api/v1/health
```

Respuesta 200:

```json
{"ok":true,"project":"SATyS CRT","project_dir":"/app","logs_dir":"/app/logs","estado_json":"/app/logs/estado_actual.json","manual_allowed":true,"repair_allowed":true,"start_allowed":false,"timer_edit_allowed":false}
```

Errores: 500/503 sólo ante fallo interno extraordinario. Alias: `GET /api/health`.

### 2. Versión desplegada

**GET `/api/v1/version`** — sin parámetros.

```bash
curl -sS http://127.0.0.1:8082/api/v1/version
```

Respuesta 200:

```json
{"version":"2026.08.17-portable-oci-api-v1-8082","git_commit":"<sha-o-unknown>","git_source":"environment|manifest|git|unknown"}
```

Errores: 500 ante fallo interno. Alias: `GET /api/version`.

### 3. Configuración visible del panel

**GET `/api/v1/config`** — no expone contraseña SATyS.

```bash
curl -sS http://127.0.0.1:8082/api/v1/config
```

Respuesta 200, ejemplo abreviado:

```json
{"ok":true,"project":"SATyS CRT","timer_hora":"01:00","workers":6,"headless":true,"manual_allowed":true,"repair_allowed":true,"start_allowed":false,"timer_edit_allowed":false}
```

Errores: 500 si no puede cargar la configuración visible. Alias: `GET /api/config`.

### 4. Estado de la corrida diaria

**GET `/api/v1/estado`** — sin parámetros.

```bash
curl -sS http://127.0.0.1:8082/api/v1/estado
```

Respuesta 200, ejemplo:

```json
{"running":false,"stage":"finalizado","mensaje":"Proceso completado","ok":true}
```

Si aún no existe estado persistido responde 200 con `stage="sin_estado"`; 500 sólo si ocurre un fallo interno inesperado. Alias: `GET /api/estado`.

### 5. Resumen de la última corrida

**GET `/api/v1/resumen/ultimo`** — sin parámetros.

```bash
curl -sS http://127.0.0.1:8082/api/v1/resumen/ultimo
```

Respuesta 200, ejemplo abreviado:

```json
{"ok":true,"mensaje":"Corrida completa","fecha_ejecucion":"2026-08-17"}
```

Errores: 404 si no existe resumen; 500 si no puede leerse. Alias: `GET /api/resumen/ultimo`.

### 6. Estado del scheduler

**GET `/api/v1/systemd`** — en Docker informa el modo de despliegue; en instalación clásica consulta servicio/timer.

```bash
curl -sS http://127.0.0.1:8082/api/v1/systemd
```

Respuesta 200, ejemplo:

```json
{"service":"docker:satys-worker","timer":"host-systemd:satys-docker-diario.timer","deployment_mode":"docker"}
```

Errores: normalmente ninguno; los fallos de `systemctl` se reportan dentro del payload. Alias: `GET /api/systemd`.

### 7. Archivos y directorios operativos

**GET `/api/v1/archivos`** — sin parámetros.

```bash
curl -sS http://127.0.0.1:8082/api/v1/archivos
```

Respuesta 200, ejemplo abreviado:

```json
{"excel_control":{"exists":true,"path":"/app/TrámitesCRT.xlsx"},"excel_consolidado":{"exists":true},"output":{"exists":true},"descargas":{"exists":true},"logs":{"exists":true},"registros_diarios":{"exists":true}}
```

Errores: 500 ante fallo al inspeccionar el filesystem. Alias: `GET /api/archivos`.

### 8. Historial de corridas

**GET `/api/v1/historial`** — sin parámetros.

```bash
curl -sS http://127.0.0.1:8082/api/v1/historial
```

Respuesta 200:

```json
{"daily":[],"manual":[]}
```

Errores: 500 ante fallo de lectura inesperado. Alias: `GET /api/historial`.

### 9. Últimas líneas de log

**GET `/api/v1/log/ultimo`**

Parámetros query: `tipo=diario|manual|reparacion` (default `diario`) y `tail=1..5000` (default `300`).

```bash
curl -sS 'http://127.0.0.1:8082/api/v1/log/ultimo?tipo=diario&tail=100'
```

Respuesta 200: `text/plain`, por lo que **no aplica ejemplo JSON**.

Errores JSON: 404 si no hay log; 422 si `tail` queda fuera del rango. Alias: `GET /api/log/ultimo`.

### 10. Descargar log

**GET `/api/v1/log/descargar`** — query `tipo`, default `diario`.

```bash
curl -fOJ 'http://127.0.0.1:8082/api/v1/log/descargar?tipo=diario'
```

Respuesta 200: archivo de log; **no aplica respuesta JSON**.

Errores JSON: 404 si no existe log; 422 para parámetros inválidos. Alias: `GET /api/log/descargar`.

### 11. Descargar resumen

**GET `/api/v1/resumen/descargar`** — sin parámetros.

```bash
curl -fOJ http://127.0.0.1:8082/api/v1/resumen/descargar
```

Respuesta 200: archivo JSON descargable; el contenido corresponde al resumen de la corrida, por ejemplo:

```json
{"ok":true,"fecha_ejecucion":"2026-08-17"}
```

Errores JSON: 404 si no existe resumen. Alias: `GET /api/resumen/descargar`.

### 12. Descargar Excel de control

**GET `/api/v1/download/excel`**

```bash
curl -fOJ http://127.0.0.1:8082/api/v1/download/excel
```

Respuesta 200: archivo `TrámitesCRT.xlsx`; **no aplica respuesta JSON**.

Errores JSON: 404 si no está disponible. Alias: `GET /api/download/excel`.

### 13. Descargar consolidado

**GET `/api/v1/download/consolidado`**

```bash
curl -fOJ http://127.0.0.1:8082/api/v1/download/consolidado
```

Respuesta 200: `Folios_Datos_Completos.xlsx`; **no aplica respuesta JSON**.

Errores JSON: 404 si no existe consolidado. Alias: `GET /api/download/consolidado`.

### 14. Descargar `output/`

**GET `/api/v1/download/output`**

```bash
curl -fOJ http://127.0.0.1:8082/api/v1/download/output
```

Respuesta 200: ZIP generado bajo demanda; **no aplica respuesta JSON**.

Errores JSON: 404 si no hay contenido; 500 si falla la creación del ZIP. Alias: `GET /api/download/output`.

### 15. Descargar `descargas/`

**GET `/api/v1/download/descargas`**

```bash
curl -fOJ http://127.0.0.1:8082/api/v1/download/descargas
```

Respuesta 200: ZIP; **no aplica respuesta JSON**.

Errores JSON: 404 si no hay contenido; 500 si falla la creación del ZIP. Alias: `GET /api/download/descargas`.

### 16. Buscar un Registro

**GET `/api/v1/registros/{registro}/buscar`** — path `registro`; query `tipo=auto|output|descargas`.

```bash
curl -sS 'http://127.0.0.1:8082/api/v1/registros/CRT26-027838/buscar?tipo=auto'
```

Respuesta 200:

```json
{"ok":true,"registro":"CRT26-027838","tipo":"auto","total":1,"items":[{"tipo":"output","raiz":"/app/output","path":"/app/output/CRT26-027838","relpath":"CRT26-027838","name":"CRT26-027838","modified_at":"2026-08-17T01:15:00"}]}
```

Errores: 400 para Registro/tipo inválido; 422 por validación. Alias: `GET /api/registros/{registro}/buscar`.

### 17. Descargar un Registro

**GET `/api/v1/registros/{registro}/download`** — path `registro`; query `tipo=auto|output|descargas`.

```bash
curl -fOJ 'http://127.0.0.1:8082/api/v1/registros/CRT26-027838/download?tipo=auto'
```

Respuesta 200: archivo o ZIP; **no aplica respuesta JSON**.

Errores JSON: 400 para datos inválidos; 404 si no se encuentra; 500 al empaquetar; 422 por validación. Alias histórico equivalente disponible.

### 18. Estado del proceso manual

**GET `/api/v1/manual/estado`**

```bash
curl -sS http://127.0.0.1:8082/api/v1/manual/estado
```

Respuesta 200:

```json
{"running":false,"ok":true,"pid":null,"mensaje":"Sin corrida manual activa","run_id":null}
```

Errores: normalmente ninguno; 500 ante fallo de lectura. Alias: `GET /api/manual/estado`.

### 19. Procesar TXT manual

**POST `/api/v1/manual/procesar`** — multipart: `archivo`, `tipo_txt`, `workers`, `headless`.

```bash
curl -sS -X POST http://127.0.0.1:8082/api/v1/manual/procesar \
  -F 'archivo=@registros.txt' \
  -F 'tipo_txt=registros' \
  -F 'workers=6' \
  -F 'headless=true'
```

Respuesta 200, ejemplo:

```json
{"running":true,"ok":true,"pid":12345,"mensaje":"Proceso manual iniciado","run_id":"<id>"}
```

Errores: 400 archivo/tipo inválido; 403 si `SATYS_API_ALLOW_MANUAL=0`; 409 si ya existe proceso; 422 multipart inválido; 500 si no puede arrancar. Alias histórico equivalente disponible.

### 20. Procesar Registros

**POST `/api/v1/registros/procesar`** — multipart: `archivo`, `workers`, `headless`.

```bash
curl -sS -X POST http://127.0.0.1:8082/api/v1/registros/procesar \
  -F 'archivo=@registros.txt' -F 'workers=6' -F 'headless=true'
```

Respuesta 200:

```json
{"running":true,"ok":true,"pid":12345,"mensaje":"Proceso iniciado","run_id":"<id>"}
```

Errores: 400, 403, 409, 422, 500 según validación/permisos/estado. Alias histórico equivalente disponible.

### 21. Estado de reparación de `id_solicitante`

**GET `/api/v1/reparacion-id/estado`**

```bash
curl -sS http://127.0.0.1:8082/api/v1/reparacion-id/estado
```

Respuesta 200:

```json
{"running":false,"ok":true,"status":"idle","summary":null}
```

Errores: 500 ante fallo de lectura. Alias histórico equivalente disponible.

### 22. Iniciar reparación de `id_solicitante`

**POST `/api/v1/reparacion-id/iniciar`** — JSON: `reiniciar_cola`, `actualizar_salidas`, `redescargar_archivos`, `reintentos` (0..10).

```bash
curl -sS -X POST http://127.0.0.1:8082/api/v1/reparacion-id/iniciar \
  -H 'Content-Type: application/json' \
  -d '{"reiniciar_cola":false,"actualizar_salidas":true,"redescargar_archivos":false,"reintentos":2}'
```

Respuesta 200:

```json
{"running":true,"ok":true,"status":"running","pid":12346,"mensaje":"Reparación iniciada"}
```

Errores: 403 si reparación está deshabilitada; 409 si ya corre; 422 body inválido; 500 si no arranca. Alias histórico equivalente disponible.

### 23. Detener reparación

**POST `/api/v1/reparacion-id/detener`** — sin body.

```bash
curl -sS -X POST http://127.0.0.1:8082/api/v1/reparacion-id/detener
```

Respuesta 200, ejemplo:

```json
{"running":false,"ok":true,"status":"stopping","mensaje":"Se solicitó detener la reparación"}
```

Si no existe reparación activa responde 200 con el estado actual. Error 500 sólo si no puede señalizar un proceso activo. Alias histórico equivalente disponible.

### 24. Cambiar hora del timer

**POST `/api/v1/timer/hora`** — JSON `{"hora":"HH:MM"}`.

```bash
curl -sS -X POST http://127.0.0.1:8082/api/v1/timer/hora \
  -H 'Content-Type: application/json' \
  -d '{"hora":"01:00"}'
```

Respuesta 200, ejemplo:

```json
{"ok":true,"hora":"01:00","install":{"ok":true},"systemd":{"service":"satys-diario.service","timer":"satys-diario.timer"}}
```

Con `SATYS_API_ALLOW_TIMER_EDIT=0` responde 200 y guarda el archivo sin instalarlo, indicando `installed=false`. Errores: 422 si la hora no cumple `HH:MM`; 500 ante fallo interno. En Docker se recomienda editar el timer del host, no este endpoint. Alias histórico equivalente disponible.

### 25. Iniciar corrida diaria

**POST `/api/v1/proceso/iniciar`** — sin body. En Docker está deshabilitado por defecto (`SATYS_API_ALLOW_START=0`) para que el scheduler del host sea la única autoridad.

```bash
curl -sS -X POST http://127.0.0.1:8082/api/v1/proceso/iniciar
```

Respuesta 200 en instalación que lo habilite:

```json
{"ok":true,"service":"satys-diario.service","estado":{"running":true}}
```

Errores: 403 si está deshabilitado; 409 si existe corrida activa; 500/503 si no puede iniciar el servicio. Alias histórico equivalente disponible.

### 26. Stream de log

**GET `/api/v1/log/stream`** — query `tipo`, default `diario`.

```bash
curl -N 'http://127.0.0.1:8082/api/v1/log/stream?tipo=diario'
```

Respuesta 200: stream `text/event-stream`; **no aplica respuesta JSON**.

Errores JSON: 422 para parámetros inválidos; errores de lectura se notifican en el stream cuando es posible. Alias histórico equivalente disponible.

## Flujo completo: corrida manual desde cero usando la API

1. Confirmar que el servicio responde:

   ```bash
   curl -f http://127.0.0.1:8082/api/v1/health
   ```

2. Verificar que no existe una corrida manual activa:

   ```bash
   curl -f http://127.0.0.1:8082/api/v1/manual/estado
   ```

3. Crear `registros.txt`, un Registro por línea.

4. Lanzar el proceso:

   ```bash
   curl -f -X POST http://127.0.0.1:8082/api/v1/manual/procesar \
     -F 'archivo=@registros.txt' \
     -F 'tipo_txt=registros' \
     -F 'workers=6' \
     -F 'headless=true'
   ```

5. Observar log vivo:

   ```bash
   curl -N 'http://127.0.0.1:8082/api/v1/log/stream?tipo=manual'
   ```

6. Consultar estado final y salidas:

   ```bash
   curl -f http://127.0.0.1:8082/api/v1/manual/estado
   curl -f http://127.0.0.1:8082/api/v1/archivos
   ```

7. Descargar consolidado cuando corresponda:

   ```bash
   curl -fOJ http://127.0.0.1:8082/api/v1/download/consolidado
   ```

## Ejecución diaria en Docker

La corrida programada no se dispara desde el API. El host ejecuta:

```bash
docker compose run --rm satys-worker
```

mediante `systemd/satys-docker-diario.service` + `.timer`. Esto conserva el modelo `oneshot`, el lock compartido y la garantía de una sola corrida diaria.
