#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ENGINE="${SATYS_CONTAINER_ENGINE:-docker}"
VERSION="$(tr -d '\r\n' < VERSION)"
IMAGE="${SATYS_IMAGE:-satys-api:${VERSION}}"
PORT="${SATYS_API_PORT:-8082}"
PREVIOUS_FILE=".satys_previous_image"

bash scripts/docker_preflight.sh

current="$($ENGINE compose images -q satys-api 2>/dev/null | head -n1 || true)"
if [[ -n "$current" ]]; then
  printf '%s\n' "$current" > "$PREVIOUS_FILE"
fi

export SATYS_IMAGE="$IMAGE"
export SATYS_GIT_COMMIT="${SATYS_GIT_COMMIT:-$(git rev-parse HEAD 2>/dev/null || echo unknown)}"

echo "Construyendo $IMAGE"
"$ENGINE" compose build satys-api satys-worker
"$ENGINE" compose up -d satys-api

for _ in $(seq 1 30); do
  if curl --fail --silent --max-time 3 http://127.0.0.1:$PORT/api/health >/dev/null; then
    echo "DESPLIEGUE DOCKER: OK ($IMAGE)"
    exit 0
  fi
  sleep 2
done

echo "ERROR: healthcheck no respondió; intentando rollback" >&2
bash scripts/rollback_docker.sh || true
exit 5
