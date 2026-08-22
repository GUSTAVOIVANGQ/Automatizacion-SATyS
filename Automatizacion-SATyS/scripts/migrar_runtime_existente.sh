#!/usr/bin/env bash
set -euo pipefail
usage(){
cat <<'TXT'
Prepara una release portable para reutilizar un runtime SATyS existente sin
copiar los directorios grandes.

Uso:
  bash scripts/migrar_runtime_existente.sh /ruta/proyecto/actual [--shared /ruta/compartida] [--lock /ruta/lock]

Genera/actualiza .env apuntando al Excel, config, descargas, output y logs del
runtime existente. El código de la release nueva permanece separado.
TXT
}
[[ $# -ge 1 ]] || { usage >&2; exit 2; }
OLD="$(readlink -f "$1")"; shift
SHARED=""; LOCK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --shared) SHARED="$2"; shift 2;;
    --lock) LOCK="$2"; shift 2;;
    *) echo "ERROR: argumento desconocido $1" >&2; exit 2;;
  esac
done
[[ -d "$OLD" ]] || { echo "ERROR: no existe $OLD" >&2; exit 2; }
[[ -f "$OLD/config/configuracion_local.json" ]] || { echo "ERROR: falta config productiva en $OLD" >&2; exit 3; }
[[ -f "$OLD/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta TrámitesCRT.xlsx en $OLD" >&2; exit 3; }

if [[ -z "$SHARED" ]]; then
  py="$(command -v python3 || command -v python || true)"
  if [[ -n "$py" ]]; then
    SHARED="$($py - "$OLD/config/configuracion_local.json" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); d=json.loads(p.read_text(encoding='utf-8-sig'))
print(str(d.get('rutas',{}).get('carpeta_compartida','')).strip())
PY
)"
  fi
fi
[[ -n "$SHARED" ]] || SHARED="$OLD/shared"
[[ -n "$LOCK" ]] || LOCK="$(dirname "$OLD")/.lock"
[[ -f .env ]] || cp .env.example .env
python3 - "$OLD" "$SHARED" "$LOCK" <<'PY'
from pathlib import Path
import sys
p=Path('.env')
updates={
 'SATYS_RUNTIME_DIR':sys.argv[1],
 'SATYS_CONFIG_HOST_FILE':str(Path(sys.argv[1])/'config/configuracion_local.json'),
 'SATYS_SHARED_HOST_DIR':sys.argv[2],
 'SATYS_LOCK_HOST_DIR':sys.argv[3],
}
lines=p.read_text(encoding='utf-8').splitlines(); out=[]; seen=set()
for line in lines:
    key=line.split('=',1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else ''
    if key in updates:
        out.append(f'{key}={updates[key]}'); seen.add(key)
    else: out.append(line)
for key,val in updates.items():
    if key not in seen: out.append(f'{key}={val}')
p.write_text('\n'.join(out)+'\n',encoding='utf-8')
PY
printf 'Runtime existente configurado:\n  runtime=%s\n  shared=%s\n  lock=%s\n' "$OLD" "$SHARED" "$LOCK"
