#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Aplica las correcciones SATyS del 29-07-2026 de forma transaccional.

Uso:
  sudo bash aplicar_correcciones_satys_20260729.sh [opciones]

Opciones:
  --project-dir RUTA   Proyecto en producción.
                       Default: /data/gustavo.garcia/satys/Automatizacion-SATyS
  --mount-dir RUTA     Punto de montaje real del recurso CIFS.
                       Default: /depi/dgp
  --shared-dir RUTA    Carpeta donde se sincronizan Excel/output/descargas/.
                       Default: <mount-dir>/SATyS
  --user USUARIO       Usuario que ejecuta SATyS. Default: dueño del proyecto.
  --python RUTA        Python del venv. Default: <padre proyecto>/venv/bin/python
  --skip-reconcile     Instala el código, pero no reconstruye TrámitesCRT.xlsx ahora.
  --run-now            Al terminar, inicia satys-diario.service para recuperar pendientes.
  -h, --help           Muestra esta ayuda.

El instalador NO sustituye credenciales, sesión ni el Excel por una plantilla.
Crea un respaldo, modifica la configuración existente y revierte automáticamente
si falla la instalación o las pruebas locales. Una falla de la reconciliación
real deja el código instalado y conserva el respaldo para revisión.
USAGE
}

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="$PACKAGE_DIR/payload"
PROJECT_DIR="/data/gustavo.garcia/satys/Automatizacion-SATyS"
MOUNT_DIR="/depi/dgp"
SHARED_DIR=""
APP_USER=""
PYTHON_BIN=""
RUN_RECONCILE=1
RUN_NOW=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir) PROJECT_DIR="${2:?Falta valor para --project-dir}"; shift 2 ;;
    --mount-dir) MOUNT_DIR="${2:?Falta valor para --mount-dir}"; shift 2 ;;
    --shared-dir) SHARED_DIR="${2:?Falta valor para --shared-dir}"; shift 2 ;;
    --user) APP_USER="${2:?Falta valor para --user}"; shift 2 ;;
    --python) PYTHON_BIN="${2:?Falta valor para --python}"; shift 2 ;;
    --skip-reconcile) RUN_RECONCILE=0; shift ;;
    --run-now) RUN_NOW=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: opción desconocida: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "ERROR: ejecuta con sudo." >&2; exit 1; }
[[ -d "$PAYLOAD_DIR" ]] || { echo "ERROR: no existe payload/ junto al instalador." >&2; exit 1; }
[[ -d "$PROJECT_DIR" ]] || { echo "ERROR: no existe el proyecto $PROJECT_DIR" >&2; exit 1; }
[[ -f "$PROJECT_DIR/config/configuracion_local.json" ]] || {
  echo "ERROR: falta $PROJECT_DIR/config/configuracion_local.json; no se crearán credenciales de ejemplo." >&2
  exit 1
}
[[ -f "$PROJECT_DIR/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta $PROJECT_DIR/TrámitesCRT.xlsx" >&2; exit 1; }

PROJECT_DIR="$(readlink -f "$PROJECT_DIR")"
MOUNT_DIR="$(readlink -m "$MOUNT_DIR")"
SHARED_DIR="$(readlink -m "${SHARED_DIR:-$MOUNT_DIR/SATyS}")"
APP_USER="${APP_USER:-$(stat -c '%U' "$PROJECT_DIR")}" 
id "$APP_USER" >/dev/null 2>&1 || { echo "ERROR: usuario inexistente: $APP_USER" >&2; exit 1; }
APP_GROUP="$(id -gn "$APP_USER")"
BASE_DIR="$(dirname "$PROJECT_DIR")"
PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || { echo "ERROR: Python no ejecutable: $PYTHON_BIN" >&2; exit 1; }

if ! command -v systemctl >/dev/null 2>&1; then
  echo "ERROR: este despliegue requiere systemd/systemctl." >&2
  exit 1
fi
if ! mountpoint -q "$MOUNT_DIR"; then
  echo "ERROR: $MOUNT_DIR no es un punto de montaje activo. No se escribirá en /depi local." >&2
  echo "       Revisa primero: findmnt -T '$MOUNT_DIR'" >&2
  exit 1
fi
FSTYPE="$(findmnt -n -o FSTYPE -T "$MOUNT_DIR" 2>/dev/null || true)"
case "$FSTYPE" in
  cifs|smb3) : ;;
  *) echo "ADVERTENCIA: $MOUNT_DIR está montado como '${FSTYPE:-desconocido}', no como cifs/smb3." >&2 ;;
esac

mkdir -p "$SHARED_DIR"
TEST_FILE="$SHARED_DIR/.satys_write_test_$$"
if ! runuser -u "$APP_USER" -- bash -c 'set -e; : > "$1"; rm -f "$1"' _ "$TEST_FILE"; then
  echo "ERROR: $APP_USER no puede escribir en $SHARED_DIR." >&2
  exit 1
fi

# No se parchea durante una corrida activa.
if systemctl is-active --quiet satys-diario.service; then
  echo "ERROR: satys-diario.service está activo. Espera a que termine." >&2
  exit 1
fi
if pgrep -u "$APP_USER" -f '(automatizar_registros_diario|main_procesar|extraer_registros_documentos)\.py' >/dev/null 2>&1; then
  echo "ERROR: hay un proceso SATyS activo para $APP_USER:" >&2
  pgrep -a -u "$APP_USER" -f '(automatizar_registros_diario|main_procesar|extraer_registros_documentos)\.py' >&2 || true
  exit 1
fi

unit_state() {
  local unit="$1" field="$2"
  case "$field" in
    active) systemctl is-active "$unit" 2>/dev/null || true ;;
    enabled) systemctl is-enabled "$unit" 2>/dev/null || true ;;
  esac
}

TIMER_ACTIVE="$(unit_state satys-diario.timer active)"
TIMER_ENABLED="$(unit_state satys-diario.timer enabled)"
API_ACTIVE="$(unit_state satys-api.service active)"
API_ENABLED="$(unit_state satys-api.service enabled)"

INSTALL_COMMITTED=0
ROLLBACK_RUNNING=0
BACKUP_READY=0
SERVICES_PAUSED=0
BACKUP_DIR=""

restore_unit_state() {
  local unit="$1" enabled="$2" active="$3"
  case "$enabled" in
    enabled|enabled-runtime|linked|linked-runtime) systemctl enable "$unit" >/dev/null 2>&1 || true ;;
    disabled) systemctl disable "$unit" >/dev/null 2>&1 || true ;;
  esac
  [[ "$active" == "active" ]] && systemctl start "$unit" >/dev/null 2>&1 || true
}

rollback() {
  local rc="${1:-1}"
  (( ROLLBACK_RUNNING == 0 )) || return 0
  ROLLBACK_RUNNING=1
  echo >&2
  if (( BACKUP_READY == 1 )); then
    echo "ERROR: instalación incompleta (código $rc). Restaurando $BACKUP_DIR ..." >&2
    while IFS= read -r rel; do
      [[ -n "$rel" ]] || continue
      if grep -Fxq -- "$rel" "$BACKUP_DIR/existing_files.txt"; then
        mkdir -p "$PROJECT_DIR/$(dirname "$rel")"
        cp -a -- "$BACKUP_DIR/project/$rel" "$PROJECT_DIR/$rel"
      else
        rm -f -- "$PROJECT_DIR/$rel"
      fi
    done < "$BACKUP_DIR/payload_files.txt"
    cp -a -- "$BACKUP_DIR/configuracion_local.json" "$PROJECT_DIR/config/configuracion_local.json"
    cp -a -- "$BACKUP_DIR/TrámitesCRT.xlsx" "$PROJECT_DIR/TrámitesCRT.xlsx"
    if [[ "$(cat "$BACKUP_DIR/systemd/service_existed")" == "1" ]]; then
      cp -a -- "$BACKUP_DIR/systemd/satys-diario.service" /etc/systemd/system/satys-diario.service
    else
      rm -f /etc/systemd/system/satys-diario.service
    fi
    if [[ "$(cat "$BACKUP_DIR/systemd/timer_existed")" == "1" ]]; then
      cp -a -- "$BACKUP_DIR/systemd/satys-diario.timer" /etc/systemd/system/satys-diario.timer
    else
      rm -f /etc/systemd/system/satys-diario.timer
    fi
  else
    echo "ERROR: instalación interrumpida antes de completar el respaldo (código $rc)." >&2
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  if (( SERVICES_PAUSED == 1 )); then
    restore_unit_state satys-diario.timer "$TIMER_ENABLED" "$TIMER_ACTIVE"
    restore_unit_state satys-api.service "$API_ENABLED" "$API_ACTIVE"
  fi
  [[ -n "$BACKUP_DIR" ]] && echo "Respaldo: $BACKUP_DIR" >&2 || true
}
trap 'rc=$?; if (( rc != 0 && INSTALL_COMMITTED == 0 )); then rollback "$rc"; fi' EXIT

systemctl stop satys-diario.timer 2>/dev/null || true
systemctl stop satys-api.service 2>/dev/null || true
SERVICES_PAUSED=1

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_ROOT="$PROJECT_DIR/respaldos_patch_20260729"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"
mkdir -p "$BACKUP_DIR/project" "$BACKUP_DIR/systemd"
chmod 700 "$BACKUP_DIR"

find "$PAYLOAD_DIR" -type f -printf '%P\n' | sort > "$BACKUP_DIR/payload_files.txt"
: > "$BACKUP_DIR/existing_files.txt"
while IFS= read -r rel; do
  [[ -n "$rel" ]] || continue
  if [[ -e "$PROJECT_DIR/$rel" || -L "$PROJECT_DIR/$rel" ]]; then
    echo "$rel" >> "$BACKUP_DIR/existing_files.txt"
    mkdir -p "$BACKUP_DIR/project/$(dirname "$rel")"
    cp -a -- "$PROJECT_DIR/$rel" "$BACKUP_DIR/project/$rel"
  fi
done < "$BACKUP_DIR/payload_files.txt"

cp -a -- "$PROJECT_DIR/config/configuracion_local.json" "$BACKUP_DIR/configuracion_local.json"
cp -a -- "$PROJECT_DIR/TrámitesCRT.xlsx" "$BACKUP_DIR/TrámitesCRT.xlsx"
if [[ -e /etc/systemd/system/satys-diario.service ]]; then
  cp -a /etc/systemd/system/satys-diario.service "$BACKUP_DIR/systemd/satys-diario.service"
  echo 1 > "$BACKUP_DIR/systemd/service_existed"
else
  echo 0 > "$BACKUP_DIR/systemd/service_existed"
fi
if [[ -e /etc/systemd/system/satys-diario.timer ]]; then
  cp -a /etc/systemd/system/satys-diario.timer "$BACKUP_DIR/systemd/satys-diario.timer"
  echo 1 > "$BACKUP_DIR/systemd/timer_existed"
else
  echo 0 > "$BACKUP_DIR/systemd/timer_existed"
fi
cat > "$BACKUP_DIR/estado_servicios.env" <<EOF_STATE
TIMER_ACTIVE='$TIMER_ACTIVE'
TIMER_ENABLED='$TIMER_ENABLED'
API_ACTIVE='$API_ACTIVE'
API_ENABLED='$API_ENABLED'
APP_USER='$APP_USER'
APP_GROUP='$APP_GROUP'
PROJECT_DIR='$PROJECT_DIR'
MOUNT_DIR='$MOUNT_DIR'
SHARED_DIR='$SHARED_DIR'
PYTHON_BIN='$PYTHON_BIN'
EOF_STATE
mkdir -p "$BACKUP_ROOT"
printf '%s\n' "$BACKUP_DIR" > "$BACKUP_ROOT/ULTIMO_RESPALDO"
BACKUP_READY=1

# Copiar únicamente archivos de código/documentación incluidos. No contiene
# configuracion_local.json, sesión, Excel maestro ni datos operativos.
(
  cd "$PAYLOAD_DIR"
  tar -cf - .
) | (
  cd "$PROJECT_DIR"
  tar -xf -
)

# Modificar la configuración real en sitio, conservando todos los secretos.
"$PYTHON_BIN" - "$PROJECT_DIR/config/configuracion_local.json" "$SHARED_DIR" <<'PY'
import json, os, sys
from pathlib import Path
p = Path(sys.argv[1])
shared = sys.argv[2]
data = json.loads(p.read_text(encoding="utf-8-sig"))
if not isinstance(data, dict):
    raise SystemExit("configuracion_local.json no contiene un objeto JSON")
rutas = data.setdefault("rutas", {})
if not isinstance(rutas, dict):
    raise SystemExit("configuracion_local.json: 'rutas' no es un objeto")
rutas["carpeta_compartida"] = shared
tmp = p.with_name(f".{p.name}.tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.replace(tmp, p)
print(f"carpeta_compartida actualizada: {shared}")
PY

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/output" "$PROJECT_DIR/descargas" \
  "$PROJECT_DIR/base_de_datos_rpc" "$PROJECT_DIR/runs/daily_guard"
chmod +x "$PROJECT_DIR/scripts/"*.sh "$PROJECT_DIR/reconciliar_metadata_global.py" \
  "$PROJECT_DIR/reconciliar_tramites_desde_folios.py" 2>/dev/null || true
chown "$APP_USER:$APP_GROUP" "$PROJECT_DIR/config/configuracion_local.json"
chmod 600 "$PROJECT_DIR/config/configuracion_local.json"
while IFS= read -r rel; do
  [[ -e "$PROJECT_DIR/$rel" ]] && chown "$APP_USER:$APP_GROUP" "$PROJECT_DIR/$rel" || true
done < "$BACKUP_DIR/payload_files.txt"

cat > /etc/systemd/system/satys-diario.service <<EOF_SERVICE
[Unit]
Description=SATyS CRT - revisión y procesamiento diario (máximo una corrida por fecha)
After=network-online.target remote-fs.target
Wants=network-online.target remote-fs.target
RequiresMountsFor=$MOUNT_DIR

[Service]
Type=oneshot
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=TZ=America/Mexico_City
Environment=SATYS_PROJECT_DIR=$PROJECT_DIR
Environment=SATYS_PYTHON=$PYTHON_BIN
Environment=SATYS_LOCK_DIR=$BASE_DIR/.lock
Environment=SATYS_DAILY_GUARD_DIR=$PROJECT_DIR/runs/daily_guard
Environment=PLAYWRIGHT_BROWSERS_PATH=$BASE_DIR/playwright-browsers
Environment=SATYS_SYNC_EXCEL_CADA_FILA=0
ExecStartPre=/usr/bin/mountpoint -q $MOUNT_DIR
ExecStartPre=/usr/bin/test -w $SHARED_DIR
ExecStart=/usr/bin/bash $PROJECT_DIR/scripts/run_satys_diario.sh
TimeoutStartSec=infinity
Restart=no
KillMode=control-group
UMask=0077
StandardOutput=journal
StandardError=journal
EOF_SERVICE
install -o root -g root -m 0644 \
  "$PAYLOAD_DIR/systemd/satys-diario.timer" \
  /etc/systemd/system/satys-diario.timer
# En RHEL/CentOS con SELinux, copiar con cp -a desde /tmp puede conservar
# el contexto user_tmp_t y hacer que systemd informe que la unidad no existe.
if command -v restorecon >/dev/null 2>&1; then
  restorecon -F /etc/systemd/system/satys-diario.service \
    /etc/systemd/system/satys-diario.timer || true
fi

# Validaciones antes de confirmar el despliegue.
bash -n "$PROJECT_DIR/scripts/run_satys_diario.sh" "$PROJECT_DIR/scripts/instalar_linux_1am.sh"
"$PYTHON_BIN" -m compileall -q -f \
  "$PROJECT_DIR/extraer_registros_documentos.py" \
  "$PROJECT_DIR/automatizar_registros_diario.py" \
  "$PROJECT_DIR/main_procesar.py" \
  "$PROJECT_DIR/generar_excel_metadata_json.py" \
  "$PROJECT_DIR/reconciliar_metadata_global.py" \
  "$PROJECT_DIR/reconciliar_tramites_desde_folios.py" \
  "$PROJECT_DIR/rutas_salida.py" \
  "$PROJECT_DIR/sincronizacion_depi.py"
(
  cd "$PROJECT_DIR"
  "$PYTHON_BIN" -m unittest discover -s tests -p 'test_*.py' -v
)
"$PYTHON_BIN" - "$PROJECT_DIR/config/configuracion_local.json" "$SHARED_DIR" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
expected = sys.argv[2]
actual = json.loads(p.read_text(encoding="utf-8-sig")).get("rutas", {}).get("carpeta_compartida")
if actual != expected:
    raise SystemExit(f"Ruta compartida no quedó configurada: actual={actual!r}, esperada={expected!r}")
print("Configuración validada:", actual)
PY

systemctl daemon-reload
systemctl enable --now satys-diario.timer
if [[ "$API_ACTIVE" == "active" || "$API_ENABLED" =~ ^enabled ]]; then
  systemctl restart satys-api.service || {
    echo "ADVERTENCIA: no se pudo reiniciar satys-api.service; el parche principal sí quedó instalado." >&2
  }
fi
INSTALL_COMMITTED=1
trap - EXIT

echo
echo "Código instalado y pruebas superadas. Respaldo: $BACKUP_DIR"

LIVE_RC=0
if (( RUN_RECONCILE == 1 )); then
  echo
  echo "Reconstruyendo TrámitesCRT.xlsx desde todos los metadata JSON..."
  set +e
  runuser -u "$APP_USER" -- env \
    PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 TZ=America/Mexico_City \
    "$PYTHON_BIN" "$PROJECT_DIR/reconciliar_metadata_global.py" \
      --excel "$PROJECT_DIR/TrámitesCRT.xlsx" \
      --resumen-json "$PROJECT_DIR/logs/reconciliacion_global_ultimo.json"
  RECONCILE_RC=$?
  set -e
  if (( RECONCILE_RC != 0 )); then
    echo "ERROR: la reconciliación real terminó con código $RECONCILE_RC." >&2
    echo "       El código permanece instalado. Revisa logs/reconciliacion_global_ultimo.json." >&2
    LIVE_RC=3
  else
    echo "Sincronizando el Excel corregido y sin_operador_CORREO hacia $SHARED_DIR ..."
    runuser -u "$APP_USER" -- cp -f -- "$PROJECT_DIR/TrámitesCRT.xlsx" "$SHARED_DIR/TrámitesCRT.xlsx"
    if [[ -d "$PROJECT_DIR/output/sin_operador_CORREO" ]]; then
      mkdir -p "$SHARED_DIR/output/sin_operador_CORREO"
      chown "$APP_USER:$APP_GROUP" "$SHARED_DIR/output/sin_operador_CORREO" 2>/dev/null || true
      if command -v rsync >/dev/null 2>&1; then
        runuser -u "$APP_USER" -- rsync -a "$PROJECT_DIR/output/sin_operador_CORREO/" \
          "$SHARED_DIR/output/sin_operador_CORREO/"
      else
        runuser -u "$APP_USER" -- cp -a "$PROJECT_DIR/output/sin_operador_CORREO/." \
          "$SHARED_DIR/output/sin_operador_CORREO/"
      fi
    fi
  fi
else
  echo "Reconciliación inmediata omitida por --skip-reconcile. La corrida diaria la ejecutará siempre."
fi

if (( RUN_NOW == 1 )); then
  echo
  echo "Iniciando corrida de recuperación mediante satys-diario.service..."
  if ! systemctl start --no-block satys-diario.service; then
    echo "ERROR: no se pudo iniciar satys-diario.service." >&2
    LIVE_RC=4
  else
    echo "La corrida quedó en segundo plano. Sigue el avance con:"
    echo "  journalctl -fu satys-diario.service"
  fi
fi

echo
echo "Verificación recomendada:"
echo "  systemctl status satys-diario.timer satys-diario.service --no-pager -l"
echo "  systemctl list-timers satys-diario.timer --all --no-pager"
echo "  cat '$PROJECT_DIR/logs/reconciliacion_global_ultimo.json'"
echo "  find '$PROJECT_DIR/output/sin_operador_CORREO' -maxdepth 2 -type f | head"
echo "Reversión disponible con:"
echo "  sudo bash '$PACKAGE_DIR/revertir_correcciones_satys_20260729.sh' '$BACKUP_DIR'"

exit "$LIVE_RC"
