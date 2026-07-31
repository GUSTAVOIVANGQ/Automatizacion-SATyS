#!/usr/bin/env bash
set -u

PROJECT_DIR="${SATYS_PROJECT_DIR:-/data/gustavo.garcia/satys/Automatizacion-SATyS}"
MOUNT_DIR="${SATYS_MOUNT_DIR:-/depi/dgp}"
SHARED_DIR="${SATYS_SHARED_DIR:-/depi/dgp/SATyS}"

echo "=== Fecha y host ==="
date --iso-8601=seconds 2>/dev/null || date
hostname

echo
echo "=== Montaje compartido ==="
findmnt -T "$MOUNT_DIR" 2>&1 || true
mountpoint "$MOUNT_DIR" 2>&1 || true
df -h "$MOUNT_DIR" 2>&1 || true
ls -ld "$MOUNT_DIR" "$SHARED_DIR" 2>&1 || true

echo
echo "=== Configuración SATyS ==="
python3 - "$PROJECT_DIR/config/configuracion_local.json" <<'PY' 2>&1 || true
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
print("archivo:", p)
if p.exists():
    data=json.loads(p.read_text(encoding="utf-8"))
    print("carpeta_compartida:", data.get("rutas",{}).get("carpeta_compartida"))
else:
    print("NO EXISTE")
PY

echo
echo "=== Unidad y timer ==="
systemctl cat satys-diario.service 2>&1 || true
systemctl status satys-diario.timer satys-diario.service --no-pager -l 2>&1 || true
systemctl list-timers satys-diario.timer --all --no-pager 2>&1 || true

echo
echo "=== Journal 27-29 julio ==="
journalctl -u satys-diario.service --since '2026-07-27 00:00:00' --until '2026-07-30 00:00:00' --no-pager -o short-iso 2>&1 || true

echo
echo "=== Logs y marcadores diarios ==="
find "$PROJECT_DIR/logs" -maxdepth 1 -type f \( -name '*20260728*' -o -name '*20260729*' \) -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' 2>/dev/null | sort || true
find "$PROJECT_DIR/runs/daily_guard" -maxdepth 2 -type f -printf '%p\n' 2>/dev/null | sort | tail -30 || true

echo
echo "=== Referencias a la ruta anterior ==="
grep -RIn --exclude='*.md' --exclude='*.log' --exclude='*.xlsx' '/depi/DEI_DATOS\|CRT_Recurso_DEPI' \
  "$PROJECT_DIR/config" "$PROJECT_DIR/systemd" "$PROJECT_DIR/scripts" "$PROJECT_DIR"/*.py 2>/dev/null || true
