# Despliegue Linux — automatización diaria y UI

La instalación predeterminada configura:

- automatización diaria a la **01:00**, zona `America/Mexico_City`;
- timer estricto `satys-diario.timer` (`Persistent=false`);
- servicio sin reintentos automáticos (`Restart=no`);
- guarda atómica de una corrida normal por fecha en `runs/daily_guard/`;
- panel web `satys-api.service` en el puerto `8095`;
- Chromium headless de Playwright;
- reconciliación automática de `TrámitesCRT.xlsx` desde
  `output/Folios_Datos_Completos.xlsx`.

## Instalar o actualizar

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
PYTHON_BIN="$(python -c 'import sys; print(sys.executable)')"

sudo env SATYS_PYTHON_BIN="$PYTHON_BIN" \
  bash scripts/instalar_linux_1am.sh \
    --user gustavo.garcia \
    --project-dir /data/gustavo.garcia/satys/Automatizacion-SATyS \
    --skip-python-install
```

Quite `--skip-python-install` cuando sea una instalación nueva o hayan cambiado
las dependencias.

La UI se instala automáticamente. Para no instalarla:

```bash
sudo env SATYS_PYTHON_BIN="$PYTHON_BIN" \
  bash scripts/instalar_linux_1am.sh \
    --user gustavo.garcia \
    --project-dir /data/gustavo.garcia/satys/Automatizacion-SATyS \
    --no-install-api
```

## Verificación

```bash
sudo systemctl is-enabled satys-diario.timer
sudo systemctl is-active satys-diario.timer
sudo systemctl is-enabled satys-api.service
sudo systemctl is-active satys-api.service
systemctl list-timers --all satys-diario.timer
curl --max-time 10 http://127.0.0.1:8095/
```

## Ejecutar sin bloquear la terminal

```bash
sudo systemctl start --no-block satys-diario.service
sudo journalctl -u satys-diario.service -f -o cat
```

## Reparar/reconciliar el Excel manualmente

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  reconciliar_tramites_desde_folios.py \
  --tramites TrámitesCRT.xlsx \
  --folios output/Folios_Datos_Completos.xlsx
```

El comando crea un respaldo con fecha antes de modificar el maestro.


## Aplicar solo la corrección de ejecución única

```bash
sudo bash scripts/aplicar_correccion_ejecucion_unica.sh
```

El script reinstala únicamente las unidades diarias y el runner; no reinstala dependencias.
