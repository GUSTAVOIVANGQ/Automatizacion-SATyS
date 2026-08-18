#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v docker >/dev/null || { echo "ERROR: Docker no está disponible" >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: Docker Compose no está disponible" >&2; exit 2; }
bash scripts/bootstrap_portable.sh
set -a; source .env; set +a
RUNTIME="${SATYS_RUNTIME_DIR:-./runtime}"
SHARED="${SATYS_SHARED_HOST_DIR:-$RUNTIME/shared}"
CONFIG="${SATYS_CONFIG_HOST_FILE:-./config/configuracion_local.json}"
[[ -f "$CONFIG" ]] || { echo "ERROR: falta $CONFIG" >&2; exit 3; }
[[ -f "$RUNTIME/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta $RUNTIME/TrámitesCRT.xlsx" >&2; exit 3; }
[[ -d "$SHARED" && -w "$SHARED" ]] || { echo "ERROR: carpeta compartida/runtime no escribible: $SHARED" >&2; exit 4; }

if [[ "${SATYS_REQUIRE_SHARED_HOST_MOUNT:-0}" == "1" ]]; then
  target="$(findmnt -T "$SHARED" -n -o TARGET 2>/dev/null || true)"
  [[ -n "$target" && "$target" != "/" ]] || { echo "ERROR: $SHARED no está en un montaje independiente" >&2; exit 4; }
fi

python_bin="$(command -v python3 || command -v python || true)"
if [[ -n "$python_bin" ]]; then
  "$python_bin" - "$CONFIG" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); d=json.loads(p.read_text(encoding='utf-8-sig'))
user=str(d.get('satys',{}).get('usuario','')).strip(); pwd=str(d.get('satys',{}).get('password','')).strip()
if not user or 'USUARIO_SATYS' in user: raise SystemExit('ERROR: falta satys.usuario')
if not pwd or 'CAMBIAR' in pwd: raise SystemExit('ERROR: falta satys.password')
w=d.get('procesamiento',{}).get('internos_workers',6)
if not isinstance(w,int) or not 1 <= w <= 6: raise SystemExit(f'ERROR: internos_workers inválido: {w!r}')
print(f'Config: OK (credenciales presentes, no impresas; internos_workers={w})')
PY
fi

docker compose config >/dev/null
printf 'PREFLIGHT DOCKER: OK (runtime=%s, shared=%s, port=%s)\n' "$RUNTIME" "$SHARED" "${SATYS_API_PORT:-8082}"
