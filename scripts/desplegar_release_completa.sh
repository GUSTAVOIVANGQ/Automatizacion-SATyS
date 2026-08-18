#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER="gustavo.garcia"
TARGET_DIR="/data/gustavo.garcia/satys/Automatizacion-SATyS"
BASE_DIR="/data/gustavo.garcia/satys"
TIMEZONE="America/Mexico_City"
RUN_HOUR="01:00"
API_PORT="8082"
DEPI_DIR="/depi/DEI_DATOS/SATyS"
RUN_NOW=0
SKIP_DEPS=0
VALIDATE_RPC=1
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'USAGE'
Reemplaza completamente el código de SATyS y despliega UI + timer diario.

Uso:
  sudo env SATYS_PYTHON_BIN=/data/gustavo.garcia/satys/venv/bin/python \
    bash desplegar_release_completa.sh [opciones]

Opciones:
  --source-dir RUTA       Proyecto nuevo. Default: raiz de esta release
  --target-dir RUTA       Proyecto activo. Default: /data/gustavo.garcia/satys/Automatizacion-SATyS
  --user USUARIO          Usuario del servicio. Default: gustavo.garcia
  --hour HH:MM            Hora diaria. Default: 01:00
  --timezone ZONA         Zona horaria. Default: America/Mexico_City
  --api-port PUERTO       Puerto UI/API. Default: 8082
  --depi-dir RUTA         Destino compartido. Default: /depi/DEI_DATOS/SATyS
  --skip-deps             No reinstala requirements ni Chromium.
  --skip-rpc-validation   No mide el catálogo RPC local al finalizar.
  --run-now               Inicia una corrida real después del despliegue.
  -h, --help              Muestra esta ayuda.

El reemplazo conserva únicamente datos operativos y elimina el código anterior
incompatible u obsoleto. La corrida diaria NO se inicia durante el despliegue,
salvo cuando se usa --run-now.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) SOURCE_DIR="${2:?Falta valor}"; shift 2 ;;
    --target-dir) TARGET_DIR="${2:?Falta valor}"; shift 2 ;;
    --user) APP_USER="${2:?Falta valor}"; shift 2 ;;
    --hour) RUN_HOUR="${2:?Falta valor}"; shift 2 ;;
    --timezone) TIMEZONE="${2:?Falta valor}"; shift 2 ;;
    --api-port) API_PORT="${2:?Falta valor}"; shift 2 ;;
    --depi-dir) DEPI_DIR="${2:?Falta valor}"; shift 2 ;;
    --skip-deps) SKIP_DEPS=1; shift ;;
    --skip-rpc-validation) VALIDATE_RPC=0; shift ;;
    --run-now) RUN_NOW=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: opción desconocida: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "ERROR: ejecuta con sudo." >&2; exit 1; }
id "$APP_USER" >/dev/null 2>&1 || { echo "ERROR: no existe el usuario $APP_USER" >&2; exit 1; }
[[ "$RUN_HOUR" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "ERROR: hora inválida" >&2; exit 1; }
[[ "$API_PORT" =~ ^[0-9]+$ ]] || { echo "ERROR: puerto inválido" >&2; exit 1; }

SOURCE_DIR="$(readlink -f "$SOURCE_DIR")"
TARGET_DIR="$(readlink -m "$TARGET_DIR")"
BASE_DIR="$(dirname "$TARGET_DIR")"
[[ -d "$SOURCE_DIR" ]] || { echo "ERROR: no existe $SOURCE_DIR" >&2; exit 1; }
[[ -f "$SOURCE_DIR/scripts/instalar_linux_1am.sh" ]] || { echo "ERROR: release incompleta" >&2; exit 1; }
[[ -f "$SOURCE_DIR/scripts/preflight_despliegue.sh" ]] || { echo "ERROR: falta preflight_despliegue.sh" >&2; exit 1; }
[[ -f "$SOURCE_DIR/VERSION" ]] || { echo "ERROR: falta VERSION en la release" >&2; exit 1; }

if systemctl is-active --quiet satys-diario.service; then
  echo "ERROR: satys-diario.service esta ejecutando una corrida." >&2
  echo "Espera a que termine antes de desplegar; no se interrumpiran descargas activas." >&2
  exit 1
fi

APP_GROUP="$(id -gn "$APP_USER")"
VENV_DIR="$BASE_DIR/venv"
STAMP="$(date +%Y%m%d_%H%M%S)"
OLD_DIR="$BASE_DIR/Automatizacion-SATyS.pre_release_${STAMP}"
META_BACKUP="$BASE_DIR/respaldos_release/${STAMP}"
SYSTEMD_BACKUP="$META_BACKUP/systemd"
ROLLBACK_ARMED=0

mkdir -p "$META_BACKUP" "$SYSTEMD_BACKUP"

# Usa el entorno virtual existente siempre que sea posible. SATYS_PYTHON_BIN
# puede apuntar al Python 3.13 del usuario cuando sudo tiene otro PATH.
PYTHON_BIN="${SATYS_PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" && -x "$VENV_DIR/bin/python" ]]; then
  PYTHON_BIN="$VENV_DIR/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in /usr/local/bin/python3.13 /usr/bin/python3.13 /usr/local/bin/python3.12 /usr/bin/python3.12 /usr/local/bin/python3.11 /usr/bin/python3.11; do
    if [[ -x "$candidate" ]]; then PYTHON_BIN="$candidate"; break; fi
  done
fi
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || {
  echo "ERROR: indica Python 3.11+ mediante SATYS_PYTHON_BIN." >&2
  exit 1
}
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' || {
  echo "ERROR: se requiere Python 3.11 o superior: $($PYTHON_BIN --version 2>&1)" >&2
  exit 1
}

backup_path() {
  local src="$1" name="$2"
  if [[ -e "$src" ]]; then
    cp -a "$src" "$SYSTEMD_BACKUP/$name"
    touch "$SYSTEMD_BACKUP/$name.present"
  else
    touch "$SYSTEMD_BACKUP/$name.absent"
  fi
}

restore_path() {
  local dst="$1" name="$2"
  rm -rf "$dst"
  if [[ -f "$SYSTEMD_BACKUP/$name.present" ]]; then
    cp -a "$SYSTEMD_BACKUP/$name" "$dst"
  fi
}

OLD_TIMER_ENABLED="$(systemctl is-enabled satys-diario.timer 2>/dev/null || true)"
OLD_TIMER_ACTIVE="$(systemctl is-active satys-diario.timer 2>/dev/null || true)"
OLD_API_ENABLED="$(systemctl is-enabled satys-api.service 2>/dev/null || true)"
OLD_API_ACTIVE="$(systemctl is-active satys-api.service 2>/dev/null || true)"

backup_path /etc/systemd/system/satys-diario.service satys-diario.service
backup_path /etc/systemd/system/satys-diario.timer satys-diario.timer
backup_path /etc/systemd/system/satys-api.service satys-api.service
backup_path /usr/local/sbin/satys-api-start satys-api-start
backup_path /etc/systemd/system/satys-diario.service.d satys-diario.service.d
backup_path /etc/systemd/system/satys-api.service.d satys-api.service.d

PERSIST_DIRS=(
  descargas output logs runs exports registros_diarios registros_fallidos
  base_de_datos_rpc buscar_concesionario
)
PERSIST_FILES=(
  config/configuracion_local.json
  sesion_guardada.json
  registros.txt
)

rollback() {
  local rc=$?
  set +e
  if (( ROLLBACK_ARMED == 0 )); then
    exit "$rc"
  fi
  echo >&2
  echo "ERROR: falló el despliegue. Restaurando la versión anterior..." >&2
  systemctl stop satys-diario.timer satys-diario.service satys-api.service >/dev/null 2>&1 || true

  if [[ -d "$OLD_DIR" ]]; then
    for rel in "${PERSIST_DIRS[@]}"; do
      if [[ -e "$TARGET_DIR/$rel" && ! -e "$OLD_DIR/$rel" ]]; then
        mkdir -p "$(dirname "$OLD_DIR/$rel")"
        mv "$TARGET_DIR/$rel" "$OLD_DIR/$rel" || true
      fi
    done
    rm -rf "$TARGET_DIR"
    mv "$OLD_DIR" "$TARGET_DIR"
  fi

  restore_path /etc/systemd/system/satys-diario.service satys-diario.service
  restore_path /etc/systemd/system/satys-diario.timer satys-diario.timer
  restore_path /etc/systemd/system/satys-api.service satys-api.service
  restore_path /usr/local/sbin/satys-api-start satys-api-start
  restore_path /etc/systemd/system/satys-diario.service.d satys-diario.service.d
  restore_path /etc/systemd/system/satys-api.service.d satys-api.service.d
  systemctl daemon-reload || true

  [[ "$OLD_TIMER_ENABLED" == "enabled" ]] && systemctl enable satys-diario.timer >/dev/null 2>&1 || true
  [[ "$OLD_API_ENABLED" == "enabled" ]] && systemctl enable satys-api.service >/dev/null 2>&1 || true
  [[ "$OLD_TIMER_ACTIVE" == "active" ]] && systemctl start satys-diario.timer >/dev/null 2>&1 || true
  [[ "$OLD_API_ACTIVE" == "active" ]] && systemctl start satys-api.service >/dev/null 2>&1 || true

  echo "Versión anterior restaurada en: $TARGET_DIR" >&2
  echo "Diagnóstico del intento: $META_BACKUP" >&2
  exit "$rc"
}
trap rollback ERR INT TERM

printf 'Release:       %s\n' "$(cat "$SOURCE_DIR/VERSION")"
printf 'Origen:        %s\n' "$SOURCE_DIR"
printf 'Destino:       %s\n' "$TARGET_DIR"
printf 'Usuario:       %s:%s\n' "$APP_USER" "$APP_GROUP"
printf 'Python:        %s\n' "$PYTHON_BIN"
printf 'Horario:       %s %s\n' "$RUN_HOUR" "$TIMEZONE"
printf 'Destino DEPI:  %s\n' "$DEPI_DIR"

# Validacion previa: no toca el servidor activo.
bash "$SOURCE_DIR/scripts/preflight_despliegue.sh" \
  --release-dir "$SOURCE_DIR" \
  --python "$PYTHON_BIN" \
  --server \
  --target-dir "$TARGET_DIR" \
  --user "$APP_USER" \
  --depi-dir "$DEPI_DIR"

# Detener entradas automáticas y procesos manuales conocidos.
systemctl stop satys-diario.timer >/dev/null 2>&1 || true
systemctl stop satys-diario.service >/dev/null 2>&1 || true
systemctl stop satys-api.service >/dev/null 2>&1 || true

mapfile -t satys_pids < <(pgrep -u "$APP_USER" -f "$TARGET_DIR" 2>/dev/null || true)
if (( ${#satys_pids[@]} > 0 )); then
  echo "Deteniendo procesos SATyS todavía activos: ${satys_pids[*]}"
  kill -TERM "${satys_pids[@]}" 2>/dev/null || true
  for _ in {1..30}; do
    sleep 1
    vivos=()
    for pid in "${satys_pids[@]}"; do kill -0 "$pid" 2>/dev/null && vivos+=("$pid"); done
    (( ${#vivos[@]} == 0 )) && break
  done
  for pid in "${satys_pids[@]}"; do kill -KILL "$pid" 2>/dev/null || true; done
fi

if [[ -e "$TARGET_DIR" ]]; then
  [[ ! -e "$OLD_DIR" ]] || { echo "ERROR: ya existe $OLD_DIR" >&2; exit 1; }
  mv "$TARGET_DIR" "$OLD_DIR"
fi
ROLLBACK_ARMED=1

mkdir -p "$TARGET_DIR"
cp -a "$SOURCE_DIR/." "$TARGET_DIR/"

# Restaurar directorios operativos grandes mediante rename en el mismo filesystem.
if [[ -d "$OLD_DIR" ]]; then
  for rel in "${PERSIST_DIRS[@]}"; do
    if [[ -e "$OLD_DIR/$rel" ]]; then
      rm -rf "$TARGET_DIR/$rel"
      mkdir -p "$(dirname "$TARGET_DIR/$rel")"
      mv "$OLD_DIR/$rel" "$TARGET_DIR/$rel"
    fi
  done

  # Restaurar archivos pequeños de configuración/estado.
  for rel in "${PERSIST_FILES[@]}"; do
    if [[ -f "$OLD_DIR/$rel" ]]; then
      mkdir -p "$(dirname "$TARGET_DIR/$rel")"
      cp -a "$OLD_DIR/$rel" "$TARGET_DIR/$rel"
    fi
  done

  # Conservar todos los Excel raíz del servidor; TrámitesCRT.xlsx prevalece
  # sobre el archivo de ejemplo incluido en la release.
  while IFS= read -r -d '' excel; do cp -a "$excel" "$TARGET_DIR/"; done \
    < <(find "$OLD_DIR" -maxdepth 1 -type f -name '*.xlsx' -print0)
fi

CONFIG_PATH="$TARGET_DIR/config/configuracion_local.json"
[[ -f "$CONFIG_PATH" ]] || {
  echo "ERROR: no se encontro la configuracion productiva preservada: $CONFIG_PATH" >&2
  exit 1
}
"$PYTHON_BIN" - "$CONFIG_PATH" "$DEPI_DIR" <<'PY_CONFIG'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
depi_dir = sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    raise SystemExit("La configuracion productiva no es un objeto JSON")
procesamiento = data.setdefault("procesamiento", {})
if not isinstance(procesamiento, dict):
    raise SystemExit("La seccion procesamiento no es un objeto JSON")
procesamiento.setdefault("internos_workers", 6)
rutas = data.setdefault("rutas", {})
if not isinstance(rutas, dict):
    raise SystemExit("La seccion rutas no es un objeto JSON")
rutas["carpeta_compartida"] = depi_dir
temporal = path.with_suffix(path.suffix + ".tmp")
temporal.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.replace(temporal, path)
PY_CONFIG

mkdir -p "$TARGET_DIR"/{descargas,output,logs,runs,exports,registros_diarios,registros_fallidos,base_de_datos_rpc}
chown -R "$APP_USER:$APP_GROUP" "$TARGET_DIR"
chmod 600 "$TARGET_DIR/config/configuracion_local.json" 2>/dev/null || true

INSTALL_ARGS=(
  --user "$APP_USER"
  --project-dir "$TARGET_DIR"
  --timezone "$TIMEZONE"
  --hour "$RUN_HOUR"
  --api-port "$API_PORT"
  --depi-dir "$DEPI_DIR"
  --install-api
)
(( SKIP_DEPS == 1 )) && INSTALL_ARGS+=(--skip-python-install)
(( RUN_NOW == 1 )) && INSTALL_ARGS+=(--run-now)

env SATYS_PYTHON_BIN="$PYTHON_BIN" \
  bash "$TARGET_DIR/scripts/instalar_linux_1am.sh" "${INSTALL_ARGS[@]}"

# Verificaciones finales del despliegue.
systemctl is-enabled --quiet satys-diario.timer
systemctl is-active --quiet satys-diario.timer
systemctl is-enabled --quiet satys-api.service
systemctl is-active --quiet satys-api.service

runuser -u "$APP_USER" -- "$VENV_DIR/bin/python" - "$API_PORT" <<'PY'
import socket, sys
with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=5):
    pass
print("UI/API: puerto local disponible")
PY

if (( VALIDATE_RPC == 1 )); then
  if find "$TARGET_DIR/base_de_datos_rpc" -maxdepth 1 -type f -name '03_concesiones_permisos_autorizaciones_*.xlsx' -print -quit | grep -q .; then
    echo "Midiendo catálogo RPC local..."
    runuser -u "$APP_USER" -- "$VENV_DIR/bin/python" "$TARGET_DIR/scripts/validar_catalogo_rpc.py"
  else
    echo "Catálogo RPC local no encontrado; la validación se hará en la próxima corrida."
  fi
fi

cat > "$META_BACKUP/despliegue.txt" <<EOF_REPORT
release=$(cat "$TARGET_DIR/VERSION")
fecha=$(date --iso-8601=seconds)
destino=$TARGET_DIR
respaldo_codigo_anterior=$OLD_DIR
timer=satys-diario.timer
timer_hora=$RUN_HOUR $TIMEZONE
api=satys-api.service
api_puerto=$API_PORT
depi_dir=$DEPI_DIR
corrida_iniciada=$RUN_NOW
EOF_REPORT

ROLLBACK_ARMED=0
trap - ERR INT TERM

echo
echo "DESPLIEGUE COMPLETO CORRECTO"
echo "Código activo:       $TARGET_DIR"
echo "Código anterior:     $OLD_DIR"
echo "Reporte:             $META_BACKUP/despliegue.txt"
echo "UI:                  http://IP_SERVIDOR:$API_PORT/"
echo "Destino DEPI:        $DEPI_DIR"
echo "Timer diario:        $RUN_HOUR $TIMEZONE"
echo "Corrida iniciada:    $([[ $RUN_NOW -eq 1 ]] && echo sí || echo no)"
echo
echo "Verificación:"
echo "  systemctl status satys-api.service --no-pager -l"
echo "  systemctl list-timers --all satys-diario.timer"
echo "  curl --max-time 10 http://127.0.0.1:$API_PORT/"
