# SATyS CRT — Frontend Linux

Este frontend reemplaza Flet para el servidor Linux. No usa React, Node ni build tools: solo FastAPI sirviendo HTML, CSS y JavaScript.

## Objetivo de la interfaz

1. **Automatización diaria** como pantalla principal: monitorea `satys-diario.service`, `satys-diario.timer`, `logs/estado_actual.json`, el resumen diario y el log en vivo.
2. **Procesar**: corrida manual desde un TXT subido por el usuario. Puede ser TXT de registros CRT o TXT de folios SATyS.
3. **Historial**: lista corridas diarias y manuales.
4. **Salidas**: descarga desde el servidor hacia la PC del usuario:
   - `TrámitesCRT.xlsx`
   - `output/Folios_Datos_Completos.xlsx`
   - `output.zip`
   - `descargas.zip`

## Variables importantes

En `systemd/satys-api.service` se agregaron estas variables:

```ini
Environment=SATYS_API_ALLOW_START=1
Environment=SATYS_API_ALLOW_MANUAL=1
Environment=SATYS_API_ALLOW_TIMER_EDIT=0
```

- `SATYS_API_ALLOW_START=1`: permite ejecutar manualmente la tarea diaria con `systemctl start satys-diario.service`.
- `SATYS_API_ALLOW_MANUAL=1`: permite subir un TXT y ejecutar `main_procesar.py` desde la UI.
- `SATYS_API_ALLOW_TIMER_EDIT=0`: guarda la hora en el archivo `systemd/satys-diario.timer`, pero no intenta copiarlo a `/etc/systemd/system` con sudo. Para que el botón aplique la hora automáticamente al sistema, cambia a `1` y configura sudo sin contraseña solo para los comandos necesarios.

## Instalar dependencias

```bash
cd /data/satys/Automatizacion-SATyS
/data/satys/venv/bin/python -m pip install -r requirements-linux.txt
```

`python-multipart` es necesario para recibir el TXT desde el navegador.

## Levantar manualmente para probar

```bash
cd /data/satys/Automatizacion-SATyS
/data/satys/venv/bin/uvicorn satys_api:app --host 0.0.0.0 --port 8080
```

Abrir:

```text
http://172.17.42.163:8080/
```

## Instalar como servicio

```bash
sudo cp systemd/satys-api.service /etc/systemd/system/satys-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now satys-api.service
systemctl status satys-api.service
```

## Permitir acceso por firewall si aplica

```bash
sudo firewall-cmd --add-port=8080/tcp --permanent
sudo firewall-cmd --reload
```

## Aplicar cambio de hora del timer

La UI puede guardar una nueva hora. Si `SATYS_API_ALLOW_TIMER_EDIT=0`, después de guardar la hora ejecuta:

```bash
cd /data/satys/Automatizacion-SATyS
sudo cp systemd/satys-diario.timer /etc/systemd/system/satys-diario.timer
sudo systemctl daemon-reload
sudo systemctl enable --now satys-diario.timer
sudo systemctl restart satys-diario.timer
systemctl list-timers | grep satys
```

## Endpoints principales

```text
GET  /
GET  /api/estado
GET  /api/systemd
GET  /api/resumen/ultimo
GET  /api/historial
GET  /api/archivos
GET  /api/log/stream?tipo=diario
GET  /api/log/stream?tipo=manual
POST /api/proceso/iniciar
POST /api/manual/procesar
POST /api/timer/hora
GET  /api/download/excel
GET  /api/download/consolidado
GET  /api/download/output
GET  /api/download/descargas
```

## Nota de operación

El proceso diario sigue viviendo en `systemd`. El navegador no ejecuta el procesamiento directamente dentro del hilo web. Para corridas manuales, la API lanza `main_procesar.py` en segundo plano y escribe `logs/manual_YYYYMMDD_HHMMSS.log`.

## Endpoints por número de registro

Esta versión agrega una implementación simple para registros únicos:

```text
POST /api/registros/procesar
GET  /api/registros/{registro}/buscar?tipo=auto|descargas|output
GET  /api/registros/{registro}/download?tipo=auto|descargas|output
```

- `POST /api/registros/procesar` recibe un TXT con números de registro y ejecuta `main_procesar.py --archivo-registro`.
- `GET /api/registros/{registro}/buscar` busca coincidencias en nombres de carpetas/archivos y en JSON de metadata dentro de `descargas/` y `output/`.
- `GET /api/registros/{registro}/download` comprime las carpetas encontradas y las descarga como ZIP.

La búsqueda es deliberadamente simple: no usa manifest ni cambia el flujo principal. Funciona bien cuando cada número de registro es único.
