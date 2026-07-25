# Despliegue SATyS 2026.07.20-run-once-daily.1

Esta versión corrige las ejecuciones repetidas y los correos múltiples del
proceso diario.

## Causa corregida

La unidad `satys-diario.service` tenía `Restart=on-failure`. El programa enviaba
su correo final y después podía devolver código 1 cuando existían registros con
error. Systemd esperaba 30 minutos y volvía a ejecutar toda la corrida.

La versión corregida aplica tres defensas:

- `Restart=no` en el servicio diario;
- `Persistent=false` para conservar el horario estricto de las 01:00;
- marcador atómico por fecha en `runs/daily_guard/`, que omite cualquier segundo
  arranque normal del mismo día.

## Opción A: reemplazo completo

```bash
rm -rf /tmp/satys-release-corregida
mkdir -p /tmp/satys-release-corregida
unzip -q automatizacion-satys-release-20260720-run-once.zip -d /tmp/satys-release-corregida

sudo env SATYS_PYTHON_BIN=/data/gustavo.garcia/satys/venv/bin/python \
  bash /tmp/satys-release-corregida/desplegar_release_completa.sh
```

El despliegue conserva `descargas/`, `output/`, `logs/`, `runs/`, configuración,
sesión, catálogo RPC y los Excel operativos.

## Opción B: aplicar únicamente la corrección del horario

Después de copiar los archivos corregidos sobre el proyecto activo:

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
sudo bash scripts/aplicar_correccion_ejecucion_unica.sh
```

## Verificación

```bash
systemctl cat satys-diario.service | grep -E 'Restart=|SATYS_DAILY_GUARD_DIR'
systemctl cat satys-diario.timer | grep -E 'OnCalendar=|Persistent='
systemctl list-timers --all satys-diario.timer
```

Debe mostrarse:

```text
Restart=no
OnCalendar=*-*-* 01:00:00 America/Mexico_City
Persistent=false
```

También conviene revisar programaciones antiguas ajenas a systemd:

```bash
crontab -l 2>/dev/null | grep -i satys || true
sudo grep -RIn 'Automatizacion-SATyS\|satys-diario' /etc/cron* /var/spool/cron 2>/dev/null || true
```
