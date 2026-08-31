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
INTERNOS_WORKERS="${SATYS_INTERNOS_WORKERS:-12}"
INTERNOS_WORKER_REINTENTOS="${SATYS_INTERNOS_WORKER_REINTENTOS:-2}"
INTERNOS_WORKER_ESPERA="${SATYS_INTERNOS_WORKER_ESPERA:-2}"
ZIP_MAX_ITERACIONES="${SATYS_ZIP_MAX_ITERACIONES:-32}"
ZIP_RUTA_RELATIVA_MAX="${SATYS_ZIP_RUTA_RELATIVA_MAX:-140}"
REMITENTES_PDF_TIMEOUT="${SATYS_REMITENTES_PDF_TIMEOUT:-1800}"
RECONCILIACION_GLOBAL_TIMEOUT="${SATYS_RECONCILIACION_GLOBAL_TIMEOUT:-1800}"
SIN_OPERADOR_RPC_PUBLICO_TIMEOUT="${SATYS_SIN_OPERADOR_RPC_PUBLICO_TIMEOUT:-1800}"
POSTPROCESO_FINAL_TIMEOUT="${SATYS_POSTPROCESO_FINAL_TIMEOUT:-7200}"
SHM_SIZE="${SATYS_SHM_SIZE:-6g}"

[[ "$INTERNOS_WORKERS" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: SATYS_INTERNOS_WORKERS debe ser un entero positivo" >&2
  exit 2
}
[[ "$REMITENTES_PDF_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: SATYS_REMITENTES_PDF_TIMEOUT debe ser un entero positivo" >&2
  exit 2
}
[[ "$SIN_OPERADOR_RPC_PUBLICO_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: SATYS_SIN_OPERADOR_RPC_PUBLICO_TIMEOUT debe ser un entero positivo" >&2
  exit 2
}
[[ "$POSTPROCESO_FINAL_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: SATYS_POSTPROCESO_FINAL_TIMEOUT debe ser un entero positivo" >&2
  exit 2
}

abs(){ case "$1" in /*) printf '%s' "$1";; *) printf '%s/%s' "$ROOT" "${1#./}";; esac; }
RUNTIME="$(abs "$RUNTIME")"; SHARED="$(abs "$SHARED")"; CONFIG="$(abs "$CONFIG")"; LOCKS="$(abs "$LOCKS")"

mkdir -p "$RUNTIME" "$RUNTIME/descargas" "$RUNTIME/output" "$RUNTIME/logs" "$RUNTIME/runs" \
  "$RUNTIME/exports" "$RUNTIME/base_de_datos_rpc" "$RUNTIME/registros_diarios" \
  "$RUNTIME/registros_fallidos" "$SHARED" "$LOCKS"
[[ -f "$CONFIG" ]] || { echo "ERROR: falta $CONFIG" >&2; exit 3; }

common=(
  --user 0:0
  --security-opt label=disable
  --shm-size="$SHM_SIZE"
  -e "TZ=$TZV"
  -e HOME=/tmp
  -e SATYS_PYTHON=/opt/satys-venv/bin/python
  -e SATYS_HEADLESS=1
  -e "SATYS_INTERNOS_WORKERS=$INTERNOS_WORKERS"
  -e "SATYS_INTERNOS_WORKER_REINTENTOS=$INTERNOS_WORKER_REINTENTOS"
  -e "SATYS_INTERNOS_WORKER_ESPERA=$INTERNOS_WORKER_ESPERA"
  -e "SATYS_ZIP_MAX_ITERACIONES=$ZIP_MAX_ITERACIONES"
  -e "SATYS_ZIP_RUTA_RELATIVA_MAX=$ZIP_RUTA_RELATIVA_MAX"
  -e "SATYS_REMITENTES_PDF_TIMEOUT=$REMITENTES_PDF_TIMEOUT"
  -e "SATYS_RECONCILIACION_GLOBAL_TIMEOUT=$RECONCILIACION_GLOBAL_TIMEOUT"
  -e "SATYS_SIN_OPERADOR_RPC_PUBLICO_TIMEOUT=$SIN_OPERADOR_RPC_PUBLICO_TIMEOUT"
  -e "SATYS_POSTPROCESO_FINAL_TIMEOUT=$POSTPROCESO_FINAL_TIMEOUT"
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
  api-run)
    # Modo foreground para que systemd supervise el proceso real del contenedor.
    podman rm -f satys-api >/dev/null 2>&1 || true
    exec podman run --rm --name satys-api "${common[@]}" --network "$API_NETWORK" -p "$BIND:$PORT:8082" \
      "$IMAGE" uvicorn satys_api:app --host 0.0.0.0 --port 8082 --proxy-headers
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
  internos)
    [[ -f "$RUNTIME/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta $RUNTIME/TrámitesCRT.xlsx" >&2; exit 3; }
    exec podman run --rm --name "satys-internos-$(date +%Y%m%d-%H%M%S)" "${common[@]}" \
      "$IMAGE" python automatizar_registros_diario.py --solo-internos --headless
    ;;
  internos-check)
    [[ -f "$RUNTIME/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta $RUNTIME/TrámitesCRT.xlsx" >&2; exit 3; }
    exec podman run --rm --name "satys-internos-check-$(date +%Y%m%d-%H%M%S)" "${common[@]}" \
      "$IMAGE" python automatizar_registros_diario.py --solo-internos --no-procesar --sin-email --headless
    ;;
  folio)
    [[ -f "$RUNTIME/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta $RUNTIME/TrámitesCRT.xlsx" >&2; exit 3; }
    folio="${1:-}"
    [[ "$folio" =~ ^[0-9]{1,15}$ ]] || { echo "Uso: scripts/podman_satys.sh folio NUMERO" >&2; exit 2; }
    exec podman run --rm --name "satys-folio-${folio}-$(date +%Y%m%d-%H%M%S)" "${common[@]}" \
      "$IMAGE" python automatizar_registros_diario.py --folio-internos "$folio" \
      --internos-workers "$INTERNOS_WORKERS" --sin-email --headless
    ;;
  remitentes-pdf)
    [[ -f "$RUNTIME/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta $RUNTIME/TrámitesCRT.xlsx" >&2; exit 3; }
    name="satys-remitentes-pdf-$(date +%Y%m%d-%H%M%S)"
    echo "Ejecutando sólo corrección Solicitante/Representante desde todos los PDF de descargas (timeout ${REMITENTES_PDF_TIMEOUT}s)..."
    set +e
    timeout --signal=TERM --kill-after=30s "${REMITENTES_PDF_TIMEOUT}s" \
      podman run --rm --name "$name" "${common[@]}" \
      "$IMAGE" python completar_remitentes_desde_pdfs.py "$@"
    rc=$?
    set -e
    if [[ $rc -eq 124 || $rc -eq 137 ]]; then
      podman rm -f "$name" >/dev/null 2>&1 || true
      echo "ERROR: completar remitentes PDF excedió ${REMITENTES_PDF_TIMEOUT}s (rc=$rc)." >&2
    fi
    exit "$rc"
    ;;
  postproceso-final)
    [[ -f "$RUNTIME/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta $RUNTIME/TrámitesCRT.xlsx" >&2; exit 3; }
    name="satys-postproceso-final-$(date +%Y%m%d-%H%M%S)"
    echo "Ejecutando postproceso final: remitentes PDF -> reconciliación -> RPC público/(correos) -> output+Excel a DEPI -> correo (timeout global ${POSTPROCESO_FINAL_TIMEOUT}s)..."
    set +e
    timeout --signal=TERM --kill-after=30s "${POSTPROCESO_FINAL_TIMEOUT}s" \
      podman run --rm --name "$name" "${common[@]}" \
      "$IMAGE" python postprocesar_final.py "$@"
    rc=$?
    set -e
    if [[ $rc -eq 124 || $rc -eq 137 ]]; then
      podman rm -f "$name" >/dev/null 2>&1 || true
      echo "ERROR: postproceso final excedió ${POSTPROCESO_FINAL_TIMEOUT}s (rc=$rc)." >&2
    fi
    exit "$rc"
    ;;
  sin-operador-rpc)
    [[ -f "$RUNTIME/TrámitesCRT.xlsx" ]] || { echo "ERROR: falta $RUNTIME/TrámitesCRT.xlsx" >&2; exit 3; }
    name="satys-sin-operador-rpc-$(date +%Y%m%d-%H%M%S)"
    echo "Ejecutando reparación _sin_operador: RPC público + clasificación MEMORANDUM.pdf en (correos) (timeout global ${SIN_OPERADOR_RPC_PUBLICO_TIMEOUT}s)..."
    set +e
    timeout --signal=TERM --kill-after=30s "${SIN_OPERADOR_RPC_PUBLICO_TIMEOUT}s" \
      podman run --rm --name "$name" "${common[@]}" \
      "$IMAGE" python resolver_sin_operador_rpc_publico.py "$@"
    rc=$?
    set -e
    if [[ $rc -eq 124 || $rc -eq 137 ]]; then
      podman rm -f "$name" >/dev/null 2>&1 || true
      echo "ERROR: reparación _sin_operador excedió ${SIN_OPERADOR_RPC_PUBLICO_TIMEOUT}s (rc=$rc)." >&2
    fi
    exit "$rc"
    ;;
  smoke)
    exec podman run --rm --name "satys-smoke-$$" "${common[@]}" \
      "$IMAGE" python scripts/smoke_internos.py --workers "$INTERNOS_WORKERS"
    ;;
  test)
    exec podman run --rm --name "satys-test-$$" "${common[@]}" \
      "$IMAGE" python -m unittest discover tests
    ;;
  *)
    cat <<EOF
Uso: scripts/podman_satys.sh COMANDO
  build      Construir imagen OCI
  api-run    Ejecutar API en foreground (uso de systemd)
  api-up     Levantar API en ${BIND}:${PORT}
  api-down   Detener API
  status     Estado del contenedor API
  logs       Logs en vivo
  smoke      Smoke test SATyS Internos IFT
  daily      Ejecutar worker diario
  internos   Inventariar seis bandejas y procesar solo Folios Internos nuevos
  internos-check  Validar acceso, inventario y comparación sin procesar Folios
  folio NUMERO     Procesar de principio a fin un Folio Internos, sin correo
  remitentes-pdf    Completar Solicitante/Representante desde todos los PDF de descargas
                    (acepta --dry-run; no ejecuta la corrida diaria)
  postproceso-final Ejecutar desde el Excel final: remitentes PDF -> reconciliación -> RPC público/(correos)
                    -> fusionar output -> sincronizar output+TrámitesCRT.xlsx a DEPI -> correo corregido
  sin-operador-rpc  Ejecutar reparación _sin_operador: RPC público + PDF tipo MEMORANDO/MEMORANDUM -> (correos)
                    (acepta args del módulo, por ejemplo --dry-run)
  test       Ejecutar tests dentro de la imagen
EOF
    ;;
esac
