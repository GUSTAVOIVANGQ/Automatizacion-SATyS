#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v podman >/dev/null 2>&1 || { echo "ERROR: podman no está instalado" >&2; exit 2; }
[[ -f .env ]] || cp .env.example .env
set -a; source .env; set +a

VERSION="$(tr -d '\r\n' < VERSION)"
IMAGE="${SATYS_IMAGE:-satys-api:$VERSION}"
PORT="${SATYS_API_PORT:-8082}"
BIND="${SATYS_API_BIND:-127.0.0.1}"
API_NETWORK="${SATYS_API_NETWORK:-slirp4netns:enable_ipv6=false}"
RUNTIME="${SATYS_RUNTIME_DIR:-./runtime}"
SHARED="${SATYS_SHARED_HOST_DIR:-$RUNTIME/shared}"
CONFIG="${SATYS_CONFIG_HOST_FILE:-./config/configuracion_local.json}"
LOCKS="${SATYS_LOCK_HOST_DIR:-$RUNTIME/locks}"
TZV="${SATYS_TZ:-America/Mexico_City}"

abs(){ case "$1" in /*) printf '%s' "$1";; *) printf '%s/%s' "$ROOT" "${1#./}";; esac; }
RUNTIME="$(abs "$RUNTIME")"; SHARED="$(abs "$SHARED")"; CONFIG="$(abs "$CONFIG")"; LOCKS="$(abs "$LOCKS")"

mkdir -p "$RUNTIME" "$RUNTIME/descargas" "$RUNTIME/output" "$RUNTIME/logs" "$RUNTIME/runs" \
  "$RUNTIME/exports" "$RUNTIME/base_de_datos_rpc" "$RUNTIME/registros_diarios" \
  "$RUNTIME/registros_fallidos" "$SHARED" "$LOCKS"
[[ -f "$CONFIG" ]] || { echo "ERROR: falta $CONFIG" >&2; exit 3; }

common=(
  --user 0:0
  --security-opt label=disable
  --shm-size=2g
  -e "TZ=$TZV"
  -e HOME=/tmp
  -e SATYS_PYTHON=/opt/satys-venv/bin/python
  -e SATYS_HEADLESS=1
  -e SATYS_INTERNOS_WORKERS=6
  -e SATYS_API_ALLOW_MANUAL=1
  -e SATYS_API_ALLOW_REPAIR=1
  -e SATYS_API_ALLOW_START=0
  -e SATYS_API_ALLOW_TIMER_EDIT=0
  -e SATYS_LOCK_DIR=/locks
  -e SATYS_DAILY_GUARD_DIR=/app/runs/daily_guard
  -e SATYS_SHARED_DIR=/shared
  -e SATYS_SESION_FILE=/runtime/sesion_guardada.json
  -e SATYS_REQUIRE_SHARED_MOUNT=0
  -v "$CONFIG:/app/config/configuracion_local.json:ro"
  -v "$RUNTIME:/runtime"
  -v "$RUNTIME/TrámitesCRT.xlsx:/app/TrámitesCRT.xlsx"
  -v "$RUNTIME/descargas:/app/descargas"
  -v "$RUNTIME/output:/app/output"
  -v "$RUNTIME/logs:/app/logs"
  -v "$RUNTIME/runs:/app/runs"
  -v "$RUNTIME/exports:/app/exports"
  -v "$RUNTIME/base_de_datos_rpc:/app/base_de_datos_rpc"
  -v "$RUNTIME/registros_diarios:/app/registros_diarios"
  -v "$RUNTIME/registros_fallidos:/app/registros_fallidos"
  -v "$SHARED:/shared"
  -v "$LOCKS:/locks"
)

cmd="${1:-help}"; shift || true
case "$cmd" in
  build)
    exec podman build --format docker \
      --build-arg PLAYWRIGHT_VERSION=1.57.0 \
      --build-arg "SATYS_VERSION=$VERSION" \
      --build-arg "SATYS_GIT_COMMIT=${SATYS_GIT_COMMIT:-unknown}" \
      -t "$IMAGE" .
    ;;
  api-up)
    podman rm -f satys-api >/dev/null 2>&1 || true
    podman run -d --name satys-api "${common[@]}" --network "$API_NETWORK" -p "$BIND:$PORT:8082" \
      "$IMAGE" uvicorn satys_api:app --host 0.0.0.0 --port 8082 --proxy-headers >/dev/null
    for _ in $(seq 1 30); do
      if curl --fail --silent --max-time 3 "http://127.0.0.1:$PORT/api/health" >/dev/null; then
        echo "API SATyS OK en $BIND:$PORT"; exit 0
      fi
      sleep 2
    done
    echo "ERROR: API no respondió" >&2; podman logs --tail 100 satys-api >&2 || true; exit 5
    ;;
  api-down)
    podman rm -f satys-api >/dev/null 2>&1 || true
    echo "API detenida"
    ;;
  status)
    podman ps -a --filter name=satys-api
    ;;
  logs)
    exec podman logs -f --tail 200 satys-api
    ;;
  daily)
    [[ -f "$RUNTIME/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta $RUNTIME/TrámitesCRT.xlsx" >&2; exit 3; }
    exec podman run --rm --name "satys-worker-$(date +%Y%m%d-%H%M%S)" "${common[@]}" \
      "$IMAGE" python automatizar_registros_diario.py --headless --workers 10
    ;;
  smoke)
    exec podman run --rm --name "satys-smoke-$$" "${common[@]}" \
      "$IMAGE" python scripts/smoke_internos.py --workers 6
    ;;
  test)
    exec podman run --rm --name "satys-test-$$" "${common[@]}" \
      "$IMAGE" python -m unittest discover tests
    ;;
  *)
    cat <<EOF
Uso: scripts/podman_satys.sh COMANDO
  build      Construir imagen OCI
  api-up     Levantar API en ${BIND}:${PORT}
  api-down   Detener API
  status     Estado del contenedor API
  logs       Logs en vivo
  smoke      Smoke test SATyS Internos IFT
  daily      Ejecutar worker diario
  test       Ejecutar tests dentro de la imagen
EOF
    ;;
esac
