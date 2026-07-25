#!/usr/bin/env bash
set -euo pipefail
SERVICE="${1:-satys-diario.service}"
echo "== systemd =="
systemctl status "$SERVICE" --no-pager || true
echo
echo "== timers =="
systemctl list-timers --all | grep -i satys || true
echo
echo "== estado_actual.json =="
python3 scripts/health_satys.py logs/estado_actual.json || true
echo
echo "Para seguir logs en vivo: journalctl -u $SERVICE -f"
