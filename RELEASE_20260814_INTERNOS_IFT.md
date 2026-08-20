# Release 2026.08.14 — Internos IFT en paralelo + API 8082

## Corrección de publicación del panel/API

- Puerto interno definitivo: `8082`.
- Uvicorn escucha sólo en `127.0.0.1`.
- El acceso desde otras máquinas debe entrar por nginx con TLS/HTTPS.
- Ejemplo de configuración: `deploy/nginx-satys.conf`.
- `web/templates/index.html` deja de quedar oculto por la regla genérica `*.html` de `.gitignore`.


# Release SATyS 2026.08.14 - Internos IFT paralelo

Versión: `2026.08.14-internos-ift-paralelo-api8082`

Esta es una release completa para actualizar el servidor RHEL 9.7. No es un
parche parcial: incluye el inventario diario de Internos IFT, descarga y
procesamiento de folios, hoja `Internos`, concurrencia por bandeja y limpieza
de pestañas auxiliares de descarga.

## Datos que se conservan

El despliegue preserva desde la instalación productiva anterior:

- `config/configuracion_local.json` y sus credenciales;
- `sesion_guardada.json`;
- todos los archivos `.xlsx` de la raíz, incluido `TrámitesCRT.xlsx`;
- `descargas/`, `output/`, `logs/`, `runs/` y datos RPC;
- registros históricos y fallidos.

La migración usa `procesamiento.internos_workers: 12` para instalaciones nuevas
y actualiza únicamente el antiguo valor predeterminado `6`. Cualquier otro
valor configurado por el operador se conserva sin imponer un máximo. También
alinea `rutas.carpeta_compartida` con el `--depi-dir` indicado y no reemplaza
credenciales ni otros valores de configuración.

## 1. Construir el paquete en Windows

Desde la raíz del proyecto:

```powershell
python .\scripts\preparar_release.py
```

Se generan, bajo `releases/`:

```text
Automatizacion-SATyS-2026.08.14-internos-ift-paralelo-api8082.tar.gz
Automatizacion-SATyS-2026.08.14-internos-ift-paralelo-api8082.tar.gz.sha256
```

El generador rechaza credenciales, sesiones, Excel, descargas, logs y otros
datos operativos. También vuelve a abrir el `.tar.gz` y valida su contenido.

## 2. Transferir al servidor

```powershell
ssh gustavo.garcia@srvmbcudaqa01 "mkdir -p /data/gustavo.garcia/satys/releases/2026.08.14"

scp .\releases\Automatizacion-SATyS-2026.08.14-internos-ift-paralelo-api8082.tar.gz* `
  gustavo.garcia@srvmbcudaqa01:/data/gustavo.garcia/satys/releases/2026.08.14/
```

## 3. Verificar y extraer en RHEL

```bash
cd /data/gustavo.garcia/satys/releases/2026.08.14
sha256sum -c Automatizacion-SATyS-2026.08.14-internos-ift-paralelo-api8082.tar.gz.sha256
tar -xzf Automatizacion-SATyS-2026.08.14-internos-ift-paralelo-api8082.tar.gz
```

## 4. Preflight del servidor

Ejecutar cuando `satys-diario.service` no tenga una corrida activa:

```bash
cd /data/gustavo.garcia/satys/releases/2026.08.14

sudo bash Automatizacion-SATyS/scripts/preflight_despliegue.sh \
  --server \
  --python /data/gustavo.garcia/satys/venv/bin/python \
  --target-dir /data/gustavo.garcia/satys/Automatizacion-SATyS \
  --user gustavo.garcia \
  --depi-dir /depi/DEI_DATOS/SATyS
```

El resultado correcto termina con:

```text
PREFLIGHT COMPLETO: servidor listo para desplegar
```

## 5. Desplegar sin iniciar una corrida completa

```bash
cd /data/gustavo.garcia/satys/releases/2026.08.14

sudo env SATYS_PYTHON_BIN=/data/gustavo.garcia/satys/venv/bin/python \
  bash Automatizacion-SATyS/scripts/desplegar_release_completa.sh \
    --source-dir /data/gustavo.garcia/satys/releases/2026.08.14/Automatizacion-SATyS \
    --target-dir /data/gustavo.garcia/satys/Automatizacion-SATyS \
    --user gustavo.garcia \
    --depi-dir /depi/DEI_DATOS/SATyS \
    --hour 01:00 \
    --timezone America/Mexico_City \
    --api-port 8082 \
    --skip-deps
```

No usar `--run-now` durante la instalación. El script:

1. aborta si la corrida diaria está activa;
2. repite el preflight completo del servidor y valida los SHA-256 del manifest;
3. detiene temporalmente timer y API;
4. mueve el código anterior a un respaldo con timestamp;
5. instala la nueva release y restaura los datos productivos;
6. reinstala las unidades systemd;
7. valida timer, API, puerto y catálogo RPC;
8. restaura automáticamente la versión anterior si falla un paso.

## 6. Smoke test sin descargas

Después del despliegue:

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS

sudo -u gustavo.garcia env \
  PLAYWRIGHT_BROWSERS_PATH=/data/gustavo.garcia/satys/playwright-browsers \
  /data/gustavo.garcia/satys/venv/bin/python scripts/smoke_internos.py --workers 6
```

El smoke abre seis Chromium headless, navega cada bandeja y compara contador
contra paginación. No abre detalles ni descarga archivos. Debe terminar con:

```text
SMOKE_OK
```

## 7. Validación posterior

```bash
cat /data/gustavo.garcia/satys/Automatizacion-SATyS/VERSION
systemctl list-timers --all satys-diario.timer
systemctl status satys-diario.timer --no-pager -l
systemctl status satys-api.service --no-pager -l
curl --fail --max-time 10 http://127.0.0.1:8082/api/health
```

Verificar la configuración sin mostrar credenciales:

```bash
/data/gustavo.garcia/satys/venv/bin/python - <<'PY'
import json
from pathlib import Path

p = Path('/data/gustavo.garcia/satys/Automatizacion-SATyS/config/configuracion_local.json')
d = json.loads(p.read_text(encoding='utf-8'))
print('internos_workers =', d.get('procesamiento', {}).get('internos_workers'))
print('carpeta_compartida =', d.get('rutas', {}).get('carpeta_compartida'))
PY
```

## 8. Respaldo y rollback

El código anterior queda en una ruta similar a:

```text
/data/gustavo.garcia/satys/Automatizacion-SATyS.pre_release_YYYYMMDD_HHMMSS
```

El reporte y respaldo de unidades systemd queda bajo:

```text
/data/gustavo.garcia/satys/respaldos_release/YYYYMMDD_HHMMSS
```

Si el instalador falla, el rollback es automático. No borres esos directorios
hasta confirmar al menos una corrida diaria completa y la sincronización DEPI.
