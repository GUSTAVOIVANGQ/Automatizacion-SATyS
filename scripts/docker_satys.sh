#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker no está instalado" >&2; exit 2; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: docker compose no está disponible" >&2; exit 2; }
[[ -f .env ]] || cp .env.example .env
cmd="${1:-help}"; shift || true
case "$cmd" in
  build) exec docker compose build ;;
  api-up) docker compose up -d satys-api; exec curl --fail --retry 20 --retry-delay 2 "http://127.0.0.1:${SATYS_API_PORT:-8082}/api/health" ;;
  api-down) exec docker compose down ;;
  status) exec docker compose ps ;;
  logs) exec docker compose logs -f --tail=200 satys-api ;;
  smoke) exec docker compose run --rm --entrypoint python satys-worker scripts/smoke_internos.py --workers 6 ;;
  daily) exec docker compose run --rm satys-worker ;;
  test) exec docker compose run --rm --entrypoint python satys-worker -m unittest discover tests ;;
  *) echo "Uso: scripts/docker_satys.sh {build|api-up|api-down|status|logs|smoke|daily|test}" ;;
esac
