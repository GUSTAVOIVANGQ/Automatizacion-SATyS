#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${1:-/data/gustavo.garcia/satys/Automatizacion-SATyS}"
[[ $EUID -eq 0 ]] || { echo "ERROR: ejecuta con sudo." >&2; exit 1; }
PROJECT_DIR="$(readlink -f "$PROJECT_DIR")"
[[ -f "$PROJECT_DIR/systemd/satys-diario.service" ]] || { echo "ERROR: proyecto no válido: $PROJECT_DIR" >&2; exit 1; }

install -m 0644 "$PROJECT_DIR/systemd/satys-diario.service" /etc/systemd/system/satys-diario.service
install -m 0644 "$PROJECT_DIR/systemd/satys-diario.timer" /etc/systemd/system/satys-diario.timer
chmod 0755 "$PROJECT_DIR/scripts/run_satys_diario.sh"
mkdir -p "$PROJECT_DIR/runs/daily_guard"
chown -R "$(stat -c '%U:%G' "$PROJECT_DIR")" "$PROJECT_DIR/runs/daily_guard"

# Eliminar drop-ins heredados que podrían volver a activar Restart=on-failure.
rm -f /etc/systemd/system/satys-diario.service.d/override.conf
rmdir /etc/systemd/system/satys-diario.service.d 2>/dev/null || true

systemctl stop satys-diario.service >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl reset-failed satys-diario.service || true
systemctl enable --now satys-diario.timer
systemctl restart satys-diario.timer
if systemctl is-active --quiet satys-api.service; then
  systemctl restart satys-api.service
fi

echo
echo "Corrección aplicada."
systemctl cat satys-diario.service
systemctl list-timers --all satys-diario.timer

echo
echo "Comprueba que no haya otra programación heredada:"
echo "  crontab -l 2>/dev/null | grep -i satys || true"
echo "  sudo grep -RIn --include='*cron*' --include='*satys*' 'Automatizacion-SATyS\|satys-diario' /etc/cron* /var/spool/cron 2>/dev/null || true"
