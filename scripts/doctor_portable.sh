#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] || cp .env.example .env
set -a; source .env; set +a
RUNTIME="${SATYS_RUNTIME_DIR:-./runtime}"
SHARED="${SATYS_SHARED_HOST_DIR:-$RUNTIME/shared}"
CONFIG="${SATYS_CONFIG_HOST_FILE:-./config/configuracion_local.json}"
errors=0
ok(){ echo "OK: $*"; }
warn(){ echo "WARN: $*"; }
bad(){ echo "ERROR: $*"; errors=$((errors+1)); }

if command -v docker >/dev/null 2>&1; then
  ok "Docker: $(docker --version 2>/dev/null || true)"
  docker compose version >/dev/null 2>&1 && ok "Docker Compose disponible" || warn "Docker Compose no disponible"
elif command -v podman >/dev/null 2>&1; then
  ok "Podman: $(podman --version 2>/dev/null || true)"
else
  bad "No hay Docker ni Podman"
fi

[[ -f "$CONFIG" ]] && ok "Configuración: $CONFIG" || bad "Falta configuración: $CONFIG"
[[ -f "$RUNTIME/TrámitesCRT.xlsx" ]] && ok "Excel runtime" || warn "Falta $RUNTIME/TrámitesCRT.xlsx"
[[ -d "$SHARED" ]] && ok "Carpeta compartida/runtime: $SHARED" || warn "No existe $SHARED"

if [[ -f "$CONFIG" ]]; then
  py="$(command -v python3 || command -v python || true)"
  if [[ -n "$py" ]]; then
    "$py" - "$CONFIG" <<'PY' || errors=$((errors+1))
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
d=json.loads(p.read_text(encoding='utf-8-sig'))
user=str(d.get('satys',{}).get('usuario','')).strip()
pwd=str(d.get('satys',{}).get('password','')).strip()
print(f"Config SATyS: usuario={'OK' if user and 'USUARIO_SATYS' not in user else 'PENDIENTE'}, password={'OK' if pwd and 'CAMBIAR' not in pwd else 'PENDIENTE'}")
PY
  fi
fi

if getent hosts satys.ift.org.mx >/dev/null 2>&1; then ok "DNS SATyS"; else warn "SATyS no resuelve desde esta red"; fi
if command -v curl >/dev/null 2>&1; then
  curl -kIsS --max-time 8 https://satys.ift.org.mx/ >/dev/null 2>&1 && ok "HTTPS SATyS accesible" || warn "HTTPS SATyS no accesible (normal fuera de la red institucional)"
fi

if (( errors > 0 )); then
  echo "DOCTOR: $errors error(es) bloqueante(s)"
  exit 2
fi
echo "DOCTOR: OK"
