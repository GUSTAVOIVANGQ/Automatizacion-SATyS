#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker no está instalado" >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: docker compose no está disponible" >&2; exit 2; }
[[ -f .env ]] || cp .env.example .env
set -a
# shellcheck disable=SC1091
source .env
set +a
INTERNOS_WORKERS="${SATYS_INTERNOS_WORKERS:-12}"
[[ "$INTERNOS_WORKERS" =~ ^[1-9][0-9]*$ ]] || {
  echo "ERROR: SATYS_INTERNOS_WORKERS debe ser un entero positivo" >&2
  exit 2
}
cmd="${1:-help}"; shift || true
case "$cmd" in
  build) exec docker compose build ;;
  api-up) docker compose up -d satys-api; exec curl --fail --retry 20 --retry-delay 2 "http://127.0.0.1:${SATYS_API_PORT:-8082}/api/health" ;;
  api-down) exec docker compose down ;;
  status) exec docker compose ps ;;
  logs) exec docker compose logs -f --tail=200 satys-api ;;
  smoke) exec docker compose run --rm --entrypoint python satys-worker scripts/smoke_internos.py --workers "$INTERNOS_WORKERS" ;;
  daily) exec docker compose run --rm satys-worker ;;
  internos) exec docker compose run --rm --entrypoint python satys-worker automatizar_registros_diario.py --solo-internos --headless ;;
  internos-check) exec docker compose run --rm --entrypoint python satys-worker automatizar_registros_diario.py --solo-internos --no-procesar --sin-email --headless ;;
  folio)
    folio="${1:-}"
    [[ "$folio" =~ ^[0-9]{1,15}$ ]] || { echo "Uso: scripts/docker_satys.sh folio NUMERO" >&2; exit 2; }
    exec docker compose run --rm --entrypoint python satys-worker automatizar_registros_diario.py \
      --folio-internos "$folio" --internos-workers "$INTERNOS_WORKERS" --sin-email --headless
    ;;
  postproceso-final)
    exec docker compose run --rm --entrypoint python satys-worker \
      postprocesar_final.py "$@"
    ;;
  sin-operador-rpc)
    exec docker compose run --rm --entrypoint python satys-worker \
      resolver_sin_operador_rpc_publico.py "$@"
    ;;
  test) exec docker compose run --rm --entrypoint python satys-worker -m unittest discover tests ;;
  *) echo "Uso: scripts/docker_satys.sh {build|api-up|api-down|status|logs|smoke|daily|internos|internos-check|folio NUMERO|postproceso-final|sin-operador-rpc|test}" ;;
esac
