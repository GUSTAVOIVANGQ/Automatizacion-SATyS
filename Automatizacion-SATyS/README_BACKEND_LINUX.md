> **Compatibilidad legacy:** para instalaciones nuevas usa `QUICKSTART_PORTABLE.md`. Este documento describe el flujo nativo `venv + systemd`.

# SATyS CRT — Backend Linux listo para despliegue

Este paquete deja el backend separado de la UI. El proceso diario corre por `systemd` a la 01:00 AM (America/Mexico_City) y escribe estado vivo en `logs/estado_actual.json` para que después el frontend FastAPI/HTML lo consulte.

## Qué cambió

- Se conserva el flujo central: SATyS → comparar contra `TrámitesCRT.xlsx` columna `1711` → generar `registros.txt` → ejecutar `main_procesar.py`.
- Se reemplazó el Python embebido de Windows por `sys.executable` / `SATYS_PYTHON`.
- Se eliminó la ruta `Z:\...`; la sincronización usa `/depi/dgp/DEI_DATOS/SATyS` desde `config/configuracion_local.json`.
- Parte2_extraer.py queda fuera del flujo de producción; el procesamiento usa metadatos SATyS/JSON local, Parte 3 RPC y Parte 4 Excel.
- Se agregó `estado_ejecucion.py`, que escribe `logs/estado_actual.json` durante la ejecución.
- Se agregó `satys_api.py`, backend FastAPI mínimo para el futuro frontend.
- Se agregaron unidades `systemd` para proceso diario y API de monitoreo.

## Estructura sugerida en servidor

```text
/data/gustavo.garcia/satys/
  venv/
  Automatizacion-SATyS/
    automatizar_registros_diario.py
    main_procesar.py
    Parte1_descarga.py
    Parte2_extraer.py              # compatibilidad; no se llama en producción
    Parte3_rpc.py
    Parte4_excel.py
    TrámitesCRT.xlsx
    logs/
    descargas/
    output/
    registros_diarios/
```

## Instalación base en RHEL

```bash
sudo mkdir -p /data/gustavo.garcia/satys
sudo chown -R gustavo.garcia:gustavo.garcia /data/gustavo.garcia/satys
cd /data/gustavo.garcia/satys
python3.11 -m venv venv
source /data/gustavo.garcia/satys/venv/bin/activate
python -m pip install --upgrade pip
pip install -r /data/gustavo.garcia/satys/Automatizacion-SATyS/requirements-linux.txt
python -m playwright install chromium
```

Si Playwright reporta librerías faltantes de sistema, instalar dependencias de Chromium con el procedimiento permitido por TI/Red Hat.

## Prueba manual

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
export SATYS_PYTHON=/data/gustavo.garcia/satys/venv/bin/python
export SATYS_LOCK_DIR=/data/gustavo.garcia/satys/Automatizacion-SATyS/.lock
./scripts/run_satys_diario.sh
```

Para probar solo comparación y generación de TXT sin procesar:

```bash
/data/gustavo.garcia/satys/venv/bin/python automatizar_registros_diario.py \
  --python /data/gustavo.garcia/satys/venv/bin/python \
  --headless \
  --workers 10 \
  --timeout-registro 900 \
  --reintentos-registro 2 \
  --workers-reintento 2 \
  --no-procesar \
  --sin-notificacion
```

## Instalar timer diario 01:00 AM

```bash
sudo cp systemd/satys-diario.service /etc/systemd/system/
sudo cp systemd/satys-diario.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now satys-diario.timer
systemctl list-timers | grep satys
```

Ejecutar manualmente:

```bash
sudo systemctl start satys-diario.service
```

Ver logs en vivo:

```bash
journalctl -u satys-diario.service -f
```

Ver estado rápido:

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
./scripts/estado_satys.sh
cat logs/estado_actual.json
```

## API de monitoreo FastAPI opcional

Instalar servicio:

```bash
sudo cp systemd/satys-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now satys-api.service
```

Endpoints:

```text
GET  /api/v1/health
GET  /api/v1/estado
GET  /api/v1/resumen/ultimo
GET  /api/log/ultimo?tail=300
POST /api/v1/proceso/iniciar   # deshabilitado salvo SATYS_API_ALLOW_START=1
```

Esta API no incluye frontend. El frontend se puede construir después con HTML/Bootstrap/JS consumiendo esos endpoints.

## Configuración local

Credenciales, rutas y concurrencia se leen desde:

```text
config/configuracion_local.json
```

La configuración de producción es `workers=10`, `timeout_registro=900`, `reintentos_registro=2` y `workers_reintento=2`. La carpeta compartida es `/depi/dgp/DEI_DATOS/SATyS`.

Las variables `SATYS_PYTHON` y `SATYS_LOCK_DIR` se mantienen únicamente para localizar el entorno virtual y el lock de proceso.


## Monitoreo operacional

Durante la ejecución el estado vivo se actualiza en:

```text
logs/estado_actual.json
```

Ejemplo de campos:

```json
{
  "running": true,
  "stage": "procesando_registros_nuevos",
  "pid": 12345,
  "hostname": "srvmbcudaqa01",
  "total_registros_satys": 1200,
  "total_nuevos": 25,
  "updated_at": "2026-07-13T10:18:44",
  "log": "logs/monitor_registros_20260713_100001.log"
}
```

Si `running=true` pero `updated_at` no cambia por mucho tiempo, revisar:

```bash
journalctl -u satys-diario.service -f
ps -ef | grep -i satys
ps -ef | grep -i chromium
```

---

## Frontend web de monitoreo

Este paquete incluye un frontend ligero en:

```text
web/templates/index.html
web/static/styles.css
web/static/app.js
```

Se sirve desde `satys_api.py` en la ruta `/`.

Prueba manual:

```bash
/data/gustavo.garcia/satys/venv/bin/uvicorn satys_api:app --host 127.0.0.1 --port 8080
```

Abrir:

```text
http://172.17.42.163:8080/
```

Más detalles en `README_FRONTEND_LINUX.md`.
