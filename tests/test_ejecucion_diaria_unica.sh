#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/project/scripts" "$TMP/project/logs" "$TMP/project/runs"
cp "$ROOT/scripts/run_satys_diario.sh" "$TMP/project/scripts/"
touch "$TMP/project/automatizar_registros_diario.py"

cat > "$TMP/fake-python" <<'PY'
#!/usr/bin/env bash
COUNT_FILE="${SATYS_TEST_COUNT:?}"
count=0
[[ -f "$COUNT_FILE" ]] && count="$(cat "$COUNT_FILE")"
printf '%s\n' "$((count + 1))" > "$COUNT_FILE"
exit "${SATYS_TEST_RC:-1}"
PY
chmod +x "$TMP/fake-python"

export SATYS_PROJECT_DIR="$TMP/project"
export SATYS_PYTHON="$TMP/fake-python"
export SATYS_DAILY_GUARD_DIR="$TMP/guard"
export SATYS_TEST_COUNT="$TMP/count"
export SATYS_TEST_RC=1
export TZ=America/Mexico_City

set +e
bash "$TMP/project/scripts/run_satys_diario.sh"
first_rc=$?
set -e
[[ "$first_rc" -eq 1 ]]

# El segundo arranque de la misma fecha debe omitirse y devolver éxito para que
# systemd no intente convertir la omisión en otro fallo.
bash "$TMP/project/scripts/run_satys_diario.sh"
[[ "$(cat "$TMP/count")" == "1" ]]

# El override explícito sigue disponible para soporte técnico.
SATYS_FORCE_RUN=1 bash "$TMP/project/scripts/run_satys_diario.sh" || true
[[ "$(cat "$TMP/count")" == "2" ]]

echo "OK: el runner permite una sola corrida normal por fecha y admite override explícito."
