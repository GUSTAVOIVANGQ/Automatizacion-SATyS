# SATyS CRT — Estado actual del servidor

Este README resume el estado real de instalación y operación del proyecto SATyS CRT en el servidor Linux. Úsalo junto con `README_BACKEND_LINUX.md` y `README_FRONTEND_LINUX.md` cuando continúes el proyecto en otro chat o con otra persona del equipo.

---

## 1. Servidor

```text
Servidor: srvmbcudaqa01
IP: 172.17.42.163
Sistema operativo: Red Hat Enterprise Linux 9.7
Zona horaria: America/Mexico_City
Usuario operativo: gustavo.garcia
UID/GID: uid=1007(gustavo.garcia), gid=10(wheel)
```

La hora del servidor fue validada con `timedatectl` y está correcta para ejecutar la automatización diaria a las 10:00 AM hora de México.

---

## 2. Rutas principales

### Código del proyecto

```text
/data/gustavo.garcia/satys/Automatizacion-SATyS
```

El proyecto debe correr desde `/data`, no desde `/home`, porque al intentar usar el entorno virtual desde `/home` hubo errores de ejecución de librerías binarias como `pydantic_core` y problemas tipo `Permission denied`.

### Entorno virtual Python

```text
/data/gustavo.garcia/satys/venv
```

Python detectado:

```text
Python 3.13.11
```

### Navegadores Playwright

```text
/data/gustavo.garcia/satys/playwright-browsers
```

Se descargó Chromium Headless Shell con Playwright. En RHEL no debe usarse `playwright install-deps chromium`, porque intenta usar `apt-get`. Las dependencias del sistema deben instalarse con `dnf` si Playwright reporta librerías faltantes.

### Lock local

```text
/data/gustavo.garcia/satys/.lock
```

Se decidió usar lock local en `/data`, no en `/depi`, porque el proceso falló intentando crear:

```text
/depi/DEI_DATOS/SATyS/.lock
```

con `PermissionError`.

---

## 3. Montaje que reemplaza `Z:`

El recurso de Windows:

```text
Z:\DEI_DATOS\SATyS
```

queda reemplazado en Linux por:

```text
/depi/DEI_DATOS/SATyS
```

### Resultados reales del proceso

```text
/depi/DEI_DATOS/SATyS/TrámitesCRT.xlsx
/depi/DEI_DATOS/SATyS/descargas
/depi/DEI_DATOS/SATyS/output
/depi/DEI_DATOS/SATyS/backup
```

### Montaje CIFS detectado

```text
//172.17.47.15/CRT_Recurso_DEPI/DEI_DATOS on /depi/DEI_DATOS type cifs
```

Inicialmente estaba montado como `root:root` con `uid=0,gid=0,file_mode=0755,dir_mode=0755`, por lo que `gustavo.garcia` no podía escribir. Se corrigió temporalmente con remount:

```bash
sudo mount -o remount,uid=1007,gid=10,file_mode=0775,dir_mode=0775,noperm /depi/DEI_DATOS
```

Después de eso se validó escritura sin sudo en:

```text
/depi/DEI_DATOS/SATyS
/depi/DEI_DATOS/SATyS/descargas
/depi/DEI_DATOS/SATyS/output
```

---

## 4. Servicio para corregir permisos del montaje

Se creó el servicio:

```text
satys-fix-mount.service
```

Archivo:

```text
/etc/systemd/system/satys-fix-mount.service
```

Contenido esperado:

```ini
[Unit]
Description=SATyS - ajustar permisos del montaje DEPI
After=remote-fs.target network-online.target
Wants=network-online.target
ConditionPathIsMountPoint=/depi/DEI_DATOS

[Service]
Type=oneshot
ExecStart=/usr/bin/mount -o remount,uid=1007,gid=10,file_mode=0775,dir_mode=0775,noperm /depi/DEI_DATOS
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

Estado esperado:

```text
Active: active (exited)
status=0/SUCCESS
```

Comandos:

```bash
sudo systemctl status satys-fix-mount.service --no-pager -l
sudo systemctl restart satys-fix-mount.service
```

---

## 5. API y UI web

### Servicio

```text
satys-api.service
```

### Puerto

```text
8095
```

Se eligió el puerto 8095 porque el puerto 8080 ya está usado por otra aplicación del servidor.

### URL

Desde la red interna:

```text
http://172.17.42.163:8095/
```

Con túnel SSH desde PowerShell:

```powershell
ssh -L 8095:127.0.0.1:8095 gustavo.garcia@172.17.42.163
```

Después abrir:

```text
http://127.0.0.1:8095/
```

### Health check

```bash
curl http://127.0.0.1:8095/api/health
```

Respuesta válida observada:

```json
{
  "ok": true,
  "project": "SATyS CRT",
  "project_dir": "/data/gustavo.garcia/satys/Automatizacion-SATyS",
  "logs_dir": "/data/gustavo.garcia/satys/Automatizacion-SATyS/logs",
  "estado_json": "/data/gustavo.garcia/satys/Automatizacion-SATyS/logs/estado_actual.json",
  "manual_allowed": true,
  "start_allowed": true,
  "timer_edit_allowed": false
}
```

### Servicio systemd recomendado

Archivo:

```text
/etc/systemd/system/satys-api.service
```

Contenido recomendado:

```ini
[Unit]
Description=SATyS CRT - API y panel web
Wants=network-online.target
After=network-online.target satys-fix-mount.service
Requires=satys-fix-mount.service

[Service]
Type=simple
User=gustavo.garcia
Group=wheel
WorkingDirectory=/data/gustavo.garcia/satys/Automatizacion-SATyS

Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=SATYS_CARPETA_COMPARTIDA=/depi/DEI_DATOS/SATyS
Environment=SATYS_EXCEL_PATH=/depi/DEI_DATOS/SATyS/TrámitesCRT.xlsx
Environment=SATYS_DESCARGAS_DIR=/depi/DEI_DATOS/SATyS/descargas
Environment=SATYS_OUTPUT_DIR=/depi/DEI_DATOS/SATyS/output
Environment=SATYS_DIR=/depi/DEI_DATOS/SATyS/descargas
Environment=SATYS_LOCK_DIR=/data/gustavo.garcia/satys/.lock
Environment=SATYS_API_ALLOW_START=1
Environment=SATYS_API_ALLOW_MANUAL=1
Environment=PLAYWRIGHT_BROWSERS_PATH=/data/gustavo.garcia/satys/playwright-browsers

ExecStart=/bin/bash -lc 'cd /data/gustavo.garcia/satys/Automatizacion-SATyS && source /data/gustavo.garcia/satys/venv/bin/activate && exec python -m uvicorn satys_api:app --host 0.0.0.0 --port 8095'

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Comandos útiles

```bash
sudo systemctl restart satys-api.service
systemctl status satys-api.service --no-pager -l
journalctl -u satys-api.service -n 100 --no-pager
journalctl -u satys-api.service -f
curl http://127.0.0.1:8095/api/health
```

---

## 6. Automatización diaria

### Timer

```text
satys-diario.timer
```

Estado observado:

```text
Tue 2026-07-14 10:00:00 CST ... satys-diario.timer → satys-diario.service
```

Esto confirma que el timer diario quedó programado para las 10:00 AM.

### Servicio

```text
satys-diario.service
```

El primer intento falló por:

```text
PermissionError: [Errno 13] Permission denied: '/depi/DEI_DATOS/SATyS/.lock'
```

Solución: usar `SATYS_LOCK_DIR=/data/gustavo.garcia/satys/.lock`.

### Servicio systemd recomendado

Archivo:

```text
/etc/systemd/system/satys-diario.service
```

Contenido recomendado:

```ini
[Unit]
Description=SATyS CRT - automatización diaria de registros nuevos
Wants=network-online.target
After=network-online.target satys-fix-mount.service
Requires=satys-fix-mount.service

[Service]
Type=oneshot
User=gustavo.garcia
Group=wheel
WorkingDirectory=/data/gustavo.garcia/satys/Automatizacion-SATyS

Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=SATYS_CARPETA_COMPARTIDA=/depi/DEI_DATOS/SATyS
Environment=SATYS_EXCEL_PATH=/depi/DEI_DATOS/SATyS/TrámitesCRT.xlsx
Environment=SATYS_DESCARGAS_DIR=/depi/DEI_DATOS/SATyS/descargas
Environment=SATYS_OUTPUT_DIR=/depi/DEI_DATOS/SATyS/output
Environment=SATYS_DIR=/depi/DEI_DATOS/SATyS/descargas
Environment=SATYS_HEADLESS=True
Environment=SATYS_LOCK_DIR=/data/gustavo.garcia/satys/.lock
Environment=PLAYWRIGHT_BROWSERS_PATH=/data/gustavo.garcia/satys/playwright-browsers

ExecStart=/bin/bash -lc 'cd /data/gustavo.garcia/satys/Automatizacion-SATyS && exec /data/gustavo.garcia/satys/venv/bin/python automatizar_registros_diario.py --python /data/gustavo.garcia/satys/venv/bin/python --headless --workers 6 --timeout-registro 900 --reintentos-registro 2 --workers-reintento 2 --estado-json logs/estado_actual.json --sin-notificacion'
```

### Timer recomendado

Archivo:

```text
/etc/systemd/system/satys-diario.timer
```

Contenido:

```ini
[Unit]
Description=SATyS CRT - ejecutar diario a las 10:00 AM

[Timer]
OnCalendar=*-*-* 10:00:00
Persistent=true
Unit=satys-diario.service

[Install]
WantedBy=timers.target
```

### Comandos

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now satys-diario.timer
systemctl list-timers | grep satys

sudo systemctl start satys-diario.service
systemctl status satys-diario.service --no-pager -l
journalctl -u satys-diario.service -n 200 --no-pager
journalctl -u satys-diario.service -f
```

---

## 7. Flujo funcional del proceso

El flujo confirmado para producción es:

```text
automatizar_registros_diario.py
  → entra a SATyS
  → extrae registros de Documentos en Proceso
  → compara contra TrámitesCRT.xlsx, columna 1711
  → genera registros.txt solo con registros nuevos
  → ejecuta main_procesar.py --archivo-registro registros.txt
  → main_procesar.py usa Parte1, Parte3 y Parte4
  → guarda resultados en /depi/DEI_DATOS/SATyS
```

### Importante

```text
Parte2_extraer.py no se usa en producción.
```

Se dejó por compatibilidad, pero el flujo real usa metadatos SATyS/JSON local, Parte 3 RPC y Parte 4 Excel.

---

## 8. Credenciales SATyS

Archivo esperado:

```text
/home/gustavo.garcia/.satys/credenciales.txt
```

Formato:

```text
usuario_satys
password_satys
```

Permisos recomendados:

```bash
chmod 600 /home/gustavo.garcia/.satys/credenciales.txt
ls -l /home/gustavo.garcia/.satys/credenciales.txt
```

Debe verse:

```text
-rw-------. 1 gustavo.garcia wheel ... credenciales.txt
```

---

## 9. Comandos de diagnóstico rápido

### API/UI

```bash
systemctl status satys-api.service --no-pager -l
journalctl -u satys-api.service -n 100 --no-pager
curl http://127.0.0.1:8095/api/health
sudo ss -ltnp | grep ':8095'
```

### Timer y proceso diario

```bash
systemctl list-timers | grep satys
systemctl status satys-diario.service --no-pager -l
journalctl -u satys-diario.service -n 200 --no-pager
journalctl -u satys-diario.service -f
```

### Montaje

```bash
systemctl status satys-fix-mount.service --no-pager -l
mount | grep -E 'depi|DEI|SATyS'
ls -ld /depi/DEI_DATOS/SATyS
ls -ld /depi/DEI_DATOS/SATyS/descargas /depi/DEI_DATOS/SATyS/output
```

### Escritura en montaje

```bash
echo ok > /depi/DEI_DATOS/SATyS/test_gustavo.tmp
echo ok > /depi/DEI_DATOS/SATyS/descargas/test_gustavo.tmp
echo ok > /depi/DEI_DATOS/SATyS/output/test_gustavo.tmp

rm -f /depi/DEI_DATOS/SATyS/test_gustavo.tmp
rm -f /depi/DEI_DATOS/SATyS/descargas/test_gustavo.tmp
rm -f /depi/DEI_DATOS/SATyS/output/test_gustavo.tmp
```

### Estado vivo

```bash
cat /data/gustavo.garcia/satys/Automatizacion-SATyS/logs/estado_actual.json
ls -lah /data/gustavo.garcia/satys/Automatizacion-SATyS/logs
```

Si `estado_actual.json` no existe, normalmente significa que el proceso diario no ha iniciado correctamente o falló antes de escribir estado.

---

## 10. Probar ejecución manual sin systemd

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
source /data/gustavo.garcia/satys/venv/bin/activate

SATYS_CARPETA_COMPARTIDA=/depi/DEI_DATOS/SATyS \
SATYS_EXCEL_PATH=/depi/DEI_DATOS/SATyS/TrámitesCRT.xlsx \
SATYS_DESCARGAS_DIR=/depi/DEI_DATOS/SATyS/descargas \
SATYS_OUTPUT_DIR=/depi/DEI_DATOS/SATyS/output \
SATYS_DIR=/depi/DEI_DATOS/SATyS/descargas \
SATYS_HEADLESS=True \
SATYS_LOCK_DIR=/data/gustavo.garcia/satys/.lock \
PLAYWRIGHT_BROWSERS_PATH=/data/gustavo.garcia/satys/playwright-browsers \
/data/gustavo.garcia/satys/venv/bin/python automatizar_registros_diario.py \
  --python /data/gustavo.garcia/satys/venv/bin/python \
  --headless \
  --workers 1 \
  --timeout-registro 900 \
  --reintentos-registro 2 \
  --workers-reintento 2 \
  --estado-json logs/estado_actual.json \
  --sin-notificacion
```

Para producción, el servicio usa `--workers 6`.

---

## 11. Dependencias Playwright en RHEL

Se descargó Chromium en:

```text
/data/gustavo.garcia/satys/playwright-browsers
```

No usar en RHEL:

```bash
python -m playwright install-deps chromium
```

porque intenta usar `apt-get`.

Si faltan librerías de Chromium, instalar con `dnf`, por ejemplo:

```bash
sudo dnf install -y \
  nss nspr atk at-spi2-atk cups-libs libdrm \
  libXcomposite libXdamage libXrandr mesa-libgbm pango \
  alsa-lib libxshmfence libXtst libX11 libxcb libXext \
  libXi libXrender libXfixes libXcursor libXinerama \
  fontconfig freetype liberation-fonts
```

---

## 12. Notas importantes para continuar en otro chat

- El código y el entorno virtual ya no deben usarse desde `/home`.
- El proyecto operativo vive en `/data/gustavo.garcia/satys/Automatizacion-SATyS`.
- El montaje `/depi/DEI_DATOS/SATyS` solo debe usarse para datos/resultados.
- El lock debe ser local en `/data/gustavo.garcia/satys/.lock`.
- La UI usa puerto 8095 porque 8080 ya estaba ocupado.
- La automatización diaria está con `systemd timer` a las 10:00 AM.
- Si falla el proceso diario, revisar primero `journalctl -u satys-diario.service -n 200 --no-pager`.
- Si falla la UI, revisar primero `journalctl -u satys-api.service -n 100 --no-pager`.
- `estado_actual.json` aparece solo después de que el proceso diario inicia correctamente.
