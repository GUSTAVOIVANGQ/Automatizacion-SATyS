#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ENGINE="${SATYS_CONTAINER_ENGINE:-docker}"
REGISTRY="${SATYS_REGISTRY:-}"
[[ -n "$REGISTRY" ]] || { echo "ERROR: define SATYS_REGISTRY con Harbor/Nexus/GitLab Registry interno" >&2; exit 2; }
VERSION="$(tr -d '\r\n' < VERSION)"
LOCAL_IMAGE="${SATYS_IMAGE:-satys-api:${VERSION}}"
REMOTE_IMAGE="${REGISTRY%/}/satys-api:${VERSION}"
"$ENGINE" tag "$LOCAL_IMAGE" "$REMOTE_IMAGE"
"$ENGINE" push "$REMOTE_IMAGE"
echo "$REMOTE_IMAGE"
