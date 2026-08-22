#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v pip-compile >/dev/null 2>&1 || {
  echo "ERROR: instala requirements-dev.txt para disponer de pip-compile" >&2
  exit 2
}
pip-compile \
  --resolver=backtracking \
  --strip-extras \
  --output-file=requirements-linux.lock.txt \
  requirements-linux.in
printf 'Lock actualizado: %s\n' "$(pwd)/requirements-linux.lock.txt"
