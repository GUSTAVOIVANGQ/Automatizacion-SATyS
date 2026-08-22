#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say(){ printf '%s\n' "$*"; }
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 2; }

if [[ ! -f .env ]]; then
  cp .env.example .env
  say "Creado .env desde .env.example"
fi

# Cargar sólo variables simples del .env para preparar paths del host.
set -a
# shellcheck disable=SC1091
source .env
set +a

RUNTIME="${SATYS_RUNTIME_DIR:-./runtime}"
SHARED="${SATYS_SHARED_HOST_DIR:-$RUNTIME/shared}"
CONFIG="${SATYS_CONFIG_HOST_FILE:-./config/configuracion_local.json}"
LOCKS="${SATYS_LOCK_HOST_DIR:-$RUNTIME/locks}"

mkdir -p "$RUNTIME" "$RUNTIME/descargas" "$RUNTIME/output" "$RUNTIME/logs" \
  "$RUNTIME/runs" "$RUNTIME/exports" "$RUNTIME/base_de_datos_rpc" \
  "$RUNTIME/registros_diarios" "$RUNTIME/registros_fallidos" "$SHARED" "$LOCKS"

if [[ ! -f "$CONFIG" ]]; then
  mkdir -p "$(dirname "$CONFIG")"
  cp config/configuracion_local.example.json "$CONFIG"
  chmod 600 "$CONFIG" 2>/dev/null || true
  say "Creado $CONFIG. Completa credenciales antes de una corrida real."
fi

# Compatibilidad con una instalación anterior: si el Excel está en la raíz,
# copiarlo al runtime portable sin modificar el original.
if [[ ! -f "$RUNTIME/TrámitesCRT.xlsx" && -f TrámitesCRT.xlsx ]]; then
  cp -p TrámitesCRT.xlsx "$RUNTIME/TrámitesCRT.xlsx"
  say "Copiado TrámitesCRT.xlsx existente al runtime portable."
fi

# También puede recuperarse desde la carpeta compartida configurada.
if [[ ! -f "$RUNTIME/TrámitesCRT.xlsx" && -f "$SHARED/TrámitesCRT.xlsx" ]]; then
  cp -p "$SHARED/TrámitesCRT.xlsx" "$RUNTIME/TrámitesCRT.xlsx"
  say "Recuperado TrámitesCRT.xlsx desde $SHARED."
fi

if command -v id >/dev/null 2>&1; then
  uid="$(id -u)"; gid="$(id -g)"
  python_cmd="$(command -v python3 || command -v python || true)"
  if [[ -n "$python_cmd" ]]; then
    "$python_cmd" - "$uid" "$gid" <<'PY'
from pathlib import Path
import sys
p=Path('.env')
text=p.read_text(encoding='utf-8')
values={'SATYS_UID':sys.argv[1], 'SATYS_GID':sys.argv[2]}
lines=text.splitlines()
out=[]; seen=set()
for line in lines:
    key=line.split('=',1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
    if key in values:
        out.append(f'{key}={values[key]}'); seen.add(key)
    else:
        out.append(line)
for key,val in values.items():
    if key not in seen: out.append(f'{key}={val}')
p.write_text('\n'.join(out)+'\n',encoding='utf-8')
PY
  fi
fi

say "Runtime listo: $RUNTIME"
say "Compartida host: $SHARED"
say "Config host: $CONFIG"
say "Lock host: $LOCKS"
if [[ -f "$RUNTIME/TrámitesCRT.xlsx" ]]; then
  say "Excel: OK ($RUNTIME/TrámitesCRT.xlsx)"
else
  say "AVISO: falta $RUNTIME/TrámitesCRT.xlsx; la API puede iniciar, pero el worker real no."
fi
