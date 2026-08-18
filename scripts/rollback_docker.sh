#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PORT="${SATYS_API_PORT:-8082}"
ENGINE="${SATYS_CONTAINER_ENGINE:-docker}"
PREVIOUS="${SATYS_PREVIOUS_IMAGE:-}"
if [[ -z "$PREVIOUS" && -f .satys_previous_image ]]; then PREVIOUS="$(cat .satys_previous_image)"; fi
if [[ -z "$PREVIOUS" ]]; then
  echo "ERROR: define SATYS_PREVIOUS_IMAGE=<tag_o_id> o conserva .satys_previous_image" >&2
  exit 2
fi
export SATYS_IMAGE="$PREVIOUS"
"$ENGINE" compose down
"$ENGINE" compose up -d --no-build satys-api
curl --fail --retry 10 --retry-delay 2 --max-time 5 http://127.0.0.1:$PORT/api/health >/dev/null
echo "ROLLBACK DOCKER: OK -> $PREVIOUS"
