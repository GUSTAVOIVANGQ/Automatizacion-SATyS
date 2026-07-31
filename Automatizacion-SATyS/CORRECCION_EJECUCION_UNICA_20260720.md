# Corrección: una sola corrida diaria de SATyS

## Causa encontrada

El servicio diario era `Type=oneshot`, pero también tenía:

```ini
Restart=on-failure
RestartSec=30min
```

`main_procesar.py` envía el correo antes de devolver su código final. Cuando una
corrida termina con registros fallidos/controlados, devuelve código distinto de
cero. Systemd lo interpretaba como fallo y volvía a ejecutar todo el servicio,
por lo que cada nuevo intento enviaba otro correo.

## Protección aplicada

1. `satys-diario.timer` conserva una sola expresión `OnCalendar` a las 01:00.
2. `satys-diario.service` usa `Restart=no`.
3. `Persistent=false` evita corridas fuera de horario al encender el servidor.
4. `scripts/run_satys_diario.sh` crea un marcador atómico por fecha en
   `runs/daily_guard/YYYY-MM-DD.started`. Todo segundo arranque normal del mismo
   día se omite con código 0 y sin ejecutar Python ni enviar correo.
5. Para soporte técnico existe un override deliberado:

```bash
sudo -u gustavo.garcia env \
  SATYS_FORCE_RUN=1 \
  SATYS_PROJECT_DIR=/data/gustavo.garcia/satys/Automatizacion-SATyS \
  SATYS_PYTHON=/data/gustavo.garcia/satys/venv/bin/python \
  bash /data/gustavo.garcia/satys/Automatizacion-SATyS/scripts/run_satys_diario.sh
```

## Aplicación rápida en el servidor

Después de copiar esta versión del proyecto:

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
sudo bash scripts/aplicar_correccion_ejecucion_unica.sh
```

## Validación

```bash
systemctl cat satys-diario.service | grep -E 'Restart=|SATYS_DAILY_GUARD_DIR'
systemctl cat satys-diario.timer | grep -E 'OnCalendar=|Persistent='
systemctl list-timers --all satys-diario.timer
journalctl -u satys-diario.service --since today --no-pager
find runs/daily_guard -maxdepth 2 -type f -print -exec cat {} \;
```

Resultado esperado:

```text
Restart=no
OnCalendar=*-*-* 01:00:00 America/Mexico_City
Persistent=false
```

Además, revisa que no exista un cron o un timer antiguo que invoque el proyecto.
El script de aplicación imprime los comandos de auditoría porque no elimina
programaciones ajenas automáticamente.
