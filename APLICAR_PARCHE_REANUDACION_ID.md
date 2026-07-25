# Aplicar parche: reparación y reanudación de `id_solicitante`

## Ruta compartida canónica

En este documento, el marcador `/ruta/` nunca debe escribirse literalmente. El origen compartido de despliegue es siempre:

```text
/depi/DEI_DATOS/SATyS/satys_fullstack_montaje_depi/Automatizacion-SATyS
```

Por tanto, el parche se encuentra en:

```text
/depi/DEI_DATOS/SATyS/satys_fullstack_montaje_depi/Automatizacion-SATyS/parche-satys-reanudacion-id-20260717.zip
```

La instalación activa del servidor permanece en:

```text
/data/gustavo.garcia/satys/Automatizacion-SATyS
```

## 1. Detener temporalmente servicios

```bash
sudo systemctl stop satys-api.service
sudo systemctl stop satys-diario.service 2>/dev/null || true
```

El timer puede permanecer habilitado, pero conviene aplicar el parche fuera de la 1:00 a. m.

## 2. Respaldo del código y datos actuales

```bash
cd /data/gustavo.garcia/satys
STAMP="$(date +%Y%m%d_%H%M%S)"
cp -a Automatizacion-SATyS "Automatizacion-SATyS.backup_${STAMP}"
```

Si ya hubo una corrida y los documentos están descargados, **no borres** las carpetas `descargas/`, `output/`, `logs/`, el archivo `TrámitesCRT.xlsx` ni `config/configuracion_local.json`. Este parche debe superponerse a la instalación actual; no requiere volver a descargar todos los documentos.

## 3. Superponer el parche desde DEPI

```bash
ORIGEN_DEPI=/depi/DEI_DATOS/SATyS/satys_fullstack_montaje_depi/Automatizacion-SATyS
PATCH_ZIP="$ORIGEN_DEPI/parche-satys-reanudacion-id-20260717.zip"

test -f "$PATCH_ZIP" || {
  echo "No existe el parche: $PATCH_ZIP" >&2
  exit 1
}

rm -rf /tmp/satys-patch
mkdir -p /tmp/satys-patch
unzip -q "$PATCH_ZIP" -d /tmp/satys-patch

cp -a /tmp/satys-patch/Automatizacion-SATyS/. \
  /data/gustavo.garcia/satys/Automatizacion-SATyS/
```

## 4. Reinstalar/actualizar unidades y levantar UI

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
PYTHON_BIN="$(python -c 'import sys; print(sys.executable)')"

sudo env SATYS_PYTHON_BIN="$PYTHON_BIN" \
  bash scripts/instalar_linux_1am.sh \
  --user gustavo.garcia \
  --project-dir /data/gustavo.garcia/satys/Automatizacion-SATyS \
  --skip-python-install
```

Esto conserva la automatización de la 1:00 a. m., levanta la UI y agrega `SATYS_API_ALLOW_REPAIR=1` al servicio.

## 5. Verificación de UI y timer

```bash
sudo systemctl is-active satys-api.service
sudo systemctl is-enabled satys-diario.timer
sudo systemctl is-active satys-diario.timer
systemctl list-timers --all satys-diario.timer
curl -s http://127.0.0.1:8095/api/health | python -m json.tool
curl -s http://127.0.0.1:8095/api/reparacion-id/estado | python -m json.tool
```

La UI mostrará una nueva sección llamada **Reparar IDs**.

## 6. Recomendación cuando ya existe una corrida descargada

Primero crea la cola sin abrir SATyS ni modificar documentos:

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS

/data/gustavo.garcia/satys/venv/bin/python \
  reparar_id_solicitante.py \
  --reiniciar-cola \
  --solo-analizar
```

Revisa el resumen:

```bash
python -m json.tool logs/reparacion_id_estado.json
```

Después inicia o reanuda la reparación predeterminada:

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  reparar_id_solicitante.py \
  --reintentos 2 \
  --headless
```

Esta ejecución:

- solo selecciona registros cuyo `metadata_satys.json` tiene `id_solicitante` vacío o `null`;
- vuelve a consultar los metadatos de esos registros;
- reutiliza los archivos ya descargados;
- guarda un checkpoint después de cada registro;
- reanuda la cola después de un apagón;
- al terminar actualiza RPC, `TrámitesCRT.xlsx` y `output/` sin ejecutar nuevamente la descarga documental.

No marques **Redescargar documentos** en la UI y no uses `--redescargar-archivos` en la primera reparación. Esa opción se reserva para casos donde la consulta de metadatos no sea suficiente y puede crear archivos duplicados.

## 7. Automático frente a manual

La configuración instalada tiene comportamientos separados:

1. **Corrida diaria:** `satys-diario.timer` la activa una vez por calendario cada día a las `01:00:00 America/Mexico_City`.
2. **Horario estricto:** la corrección 2026-07-20 usa `Persistent=false`, por lo que no inicia corridas tardías al encender el servidor.
3. **Sin reintentos de corrida completa:** `satys-diario.service` usa `Restart=no` y el runner limita el inicio normal a una vez por fecha. Consulta `CORRECCION_EJECUCION_UNICA_20260720.md`.
4. **Inicio manual de la corrida diaria:** está permitido desde la UI o con:

   ```bash
   sudo systemctl start --no-block satys-diario.service
   ```

5. **Reparación de IDs:** `reparar_id_solicitante.py` solo se ejecuta manualmente desde la UI o backend. No existe un timer para este módulo.
6. **Concurrencia:** el lock del proyecto evita que estos flujos trabajen simultáneamente sobre el Excel y las carpetas.

Para confirmar el calendario efectivo del servidor:

```bash
systemctl cat satys-diario.timer
systemctl list-timers --all satys-diario.timer
systemd-analyze calendar '*-*-* 01:00:00 America/Mexico_City'
```
