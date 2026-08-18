#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
cmd="${1:-help}"; shift || true
case "$cmd" in
  bootstrap) exec bash scripts/bootstrap_portable.sh "$@" ;;
  doctor) exec bash scripts/doctor_portable.sh "$@" ;;
  *)
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
      exec bash scripts/docker_satys.sh "$cmd" "$@"
    elif command -v podman >/dev/null 2>&1; then
      exec bash scripts/podman_satys.sh "$cmd" "$@"
    else
      echo "ERROR: no se encontró Docker Compose ni Podman." >&2
      echo "Ejecuta: scripts/satys.sh doctor" >&2
      exit 2
    fi
    ;;
esac
