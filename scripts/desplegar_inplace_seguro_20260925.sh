#!/usr/bin/env bash
set -euo pipefail

# Despliegue limpio de código SATyS conservando TODO el runtime existente en:
#   /data/gustavo.garcia/satys/Automatizacion-SATyS
#
# Ejecutar DESDE una release extraída y verificada, por ejemplo:
#   bash /data/gustavo.garcia/satys/stage/Automatizacion-SATyS/scripts/desplegar_inplace_seguro_20260925.sh
#
# No ejecuta smoke ni dispara la corrida diaria.

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${SATYS_TARGET_DIR:-/data/gustavo.garcia/satys/Automatizacion-SATyS}"
VERSION="$(tr -d '\r\n' < "$SOURCE_ROOT/VERSION")"
API_SERVICE="satys-container-api.service"
DAILY_SERVICE="satys-container-diario.service"
TIMER="satys-container-diario.timer"

if [[ "$SOURCE_ROOT" == "$TARGET" ]]; then
  echo "ERROR: este script debe ejecutarse desde la release extraída en staging, no desde el proyecto activo." >&2
  exit 2
fi

[[ -d "$TARGET" ]] || { echo "ERROR: no existe el runtime activo: $TARGET" >&2; exit 2; }
[[ -f "$SOURCE_ROOT/DEPLOYMENT_MANIFEST.json" ]] || { echo "ERROR: falta DEPLOYMENT_MANIFEST.json en staging" >&2; exit 2; }
[[ -f "$TARGET/.env" ]] || { echo "ERROR: falta $TARGET/.env; no se modifica el servidor" >&2; exit 2; }
[[ -f "$TARGET/config/configuracion_local.json" ]] || { echo "ERROR: falta configuración productiva; no se modifica el servidor" >&2; exit 2; }
[[ -f "$TARGET/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta TrámitesCRT.xlsx; no se modifica el servidor" >&2; exit 2; }

# Las unidades deben apuntar a la ruta persistente acordada. Si no, aborta antes
# de copiar para evitar que el timer siga arrancando una release antigua.
for svc in "$API_SERVICE" "$DAILY_SERVICE"; do
  wd="$(systemctl show "$svc" -p WorkingDirectory --value 2>/dev/null || true)"
  exec_cfg="$(systemctl show "$svc" -p ExecStart --value 2>/dev/null || true)"
  [[ "$wd" == "$TARGET" ]] || {
    echo "ERROR: $svc WorkingDirectory=$wd; debe ser $TARGET" >&2
    exit 2
  }
  grep -Fq "$TARGET/scripts/satys.sh" <<<"$exec_cfg" || {
    echo "ERROR: $svc ExecStart no apunta a $TARGET/scripts/satys.sh" >&2
    exit 2
  }
done

case "$VERSION" in
  2026.09.25-email4203-rpc-crt-1) ;;
  *) echo "ERROR: versión inesperada: $VERSION" >&2; exit 2 ;;
esac

# La release limpia debe pasar su propio preflight sin secretos/runtime.
(
  cd "$SOURCE_ROOT"
  bash scripts/preflight_despliegue.sh
)

# Nunca tocar una corrida que esté activa.
state="$(systemctl is-active "$DAILY_SERVICE" 2>/dev/null || true)"
if [[ "$state" == "active" || "$state" == "activating" ]]; then
  echo "ERROR: $DAILY_SERVICE está $state. Espera a que termine antes de desplegar." >&2
  exit 3
fi

# Pausa únicamente nuevas corridas y la API mientras se reconstruye la imagen.
sudo systemctl stop "$TIMER" || true
sudo systemctl stop "$API_SERVICE" || true

if podman ps --format '{{.Names}}' | grep -Eq '^satys-worker'; then
  echo "ERROR: existe un worker SATyS activo. No se copiará código." >&2
  exit 3
fi

# Overlay deliberadamente SIN --delete: la release no contiene secretos ni runtime,
# de modo que config, sesión, Excel, descargas, output, logs, etc. permanecen intactos.
rsync -a "$SOURCE_ROOT/" "$TARGET/"

# Actualiza sólo parámetros operativos no secretos. Nunca imprime credenciales.
upsert_env() {
  local key="$1" value="$2" file="$TARGET/.env"
  if grep -qE "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

upsert_env SATYS_IMAGE "satys-api:${VERSION}"
upsert_env SATYS_RPC_BASE_URL "https://rpc.crt.gob.mx/vrpc"
upsert_env SATYS_INTERNOS_WORKERS "10"
upsert_env SATYS_RECONCILIACION_GLOBAL_TIMEOUT "3600"
upsert_env SATYS_SIN_OPERADOR_RPC_PUBLICO_TIMEOUT "3600"
upsert_env SATYS_TRIMESTRES_2026_TIMEOUT "3600"
upsert_env SATYS_POSTPROCESO_FINAL_TIMEOUT "10800"
upsert_env SATYS_RUNTIME_DIR "$TARGET"
upsert_env SATYS_CONFIG_HOST_FILE "$TARGET/config/configuracion_local.json"
upsert_env SATYS_SHARED_HOST_DIR "/depi/dgp/DEI_DATOS/SATyS"
upsert_env SATYS_LOCK_HOST_DIR "/data/gustavo.garcia/satys/.lock"

chmod 600 "$TARGET/config/configuracion_local.json"
[[ ! -f "$TARGET/sesion_guardada.json" ]] || chmod 600 "$TARGET/sesion_guardada.json"

# La imagen se construye desde la release limpia de staging, no desde el runtime
# activo. Así, aunque el directorio persistente conserve archivos históricos,
# éstos no entran en la imagen.
cd "$SOURCE_ROOT"
podman build \
  --build-arg "SATYS_VERSION=$VERSION" \
  --build-arg "SATYS_GIT_COMMIT=unknown" \
  -t "satys-api:$VERSION" .

sudo systemctl daemon-reload
sudo systemctl start "$API_SERVICE"
sleep 5

api_version="$(curl -fsS http://127.0.0.1:8082/api/v1/version)"
echo "API: $api_version"
curl -fsS -o /dev/null http://127.0.0.1:8082/
echo "UI: HTTP 200"

if ! grep -Fq "$VERSION" <<<"$api_version"; then
  echo "ERROR: la API no reporta la versión esperada; el timer permanecerá detenido." >&2
  exit 4
fi

# La activación del timer es el último paso, después de validar API/UI.
sudo systemctl reset-failed "$DAILY_SERVICE" || true
sudo systemctl enable --now "$TIMER"

echo
echo "OK: despliegue terminado."
echo "Proyecto activo: $TARGET"
echo "Versión: $VERSION"
echo "Internos: 10 workers"
echo "No se ejecutó smoke ni se inició manualmente la corrida diaria."
systemctl list-timers "$TIMER" --all --no-pager || true
