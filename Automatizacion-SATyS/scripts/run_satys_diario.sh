#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SATYS_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON_BIN="${SATYS_PYTHON:-$(cd "$PROJECT_DIR/.." && pwd)/venv/bin/python}"
TIMEZONE="${TZ:-America/Mexico_City}"
GUARD_ROOT="${SATYS_DAILY_GUARD_DIR:-$PROJECT_DIR/runs/daily_guard}"
FORCE_RUN="${SATYS_FORCE_RUN:-0}"

cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export SATYS_HEADLESS=True
export SATYS_LOCK_DIR="${SATYS_LOCK_DIR:-$PROJECT_DIR/.lock}"
export TZ="$TIMEZONE"

mkdir -p "$GUARD_ROOT"
RUN_DATE="$(date +%F)"
RUN_STAMP="$(date --iso-8601=seconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')"
GUARD_DIR="$GUARD_ROOT/$RUN_DATE.started"

# Defensa adicional a systemd: la creación de un directorio es atómica. Aunque
# alguien pulse el botón de la UI, ejecute systemctl start dos veces o exista un
# intento de reinicio heredado, solo el primer arranque de la fecha continúa.
if [[ "$FORCE_RUN" != "1" ]]; then
  if ! mkdir "$GUARD_DIR" 2>/dev/null; then
    echo "[$RUN_STAMP] SATyS diario omitido: ya existe una corrida iniciada para $RUN_DATE ($TIMEZONE)."
    echo "Marcador: $GUARD_DIR"
    exit 0
  fi

  cat > "$GUARD_DIR/inicio.txt" <<EOF_MARKER
fecha=$RUN_STAMP
fecha_calendario=$RUN_DATE
zona_horaria=$TIMEZONE
host=$(hostname 2>/dev/null || echo desconocido)
pid=$$
servicio=${SYSTEMD_UNIT:-satys-diario.service}
EOF_MARKER
else
  echo "[$RUN_STAMP] SATYS_FORCE_RUN=1: se omite la protección de una corrida por día."
fi

# Limpiar marcadores antiguos sin afectar la evidencia reciente.
find "$GUARD_ROOT" -mindepth 1 -maxdepth 1 -type d -name '*.started' -mtime +45 -exec rm -rf -- {} + 2>/dev/null || true

# Workers, timeout y reintentos se leen de config/configuracion_local.json.
set +e
"$PYTHON_BIN" automatizar_registros_diario.py \
  --python "$PYTHON_BIN" \
  --headless \
  --estado-json "$PROJECT_DIR/logs/estado_actual.json" \
  --sin-notificacion
RC=$?
set -e

FIN_STAMP="$(date --iso-8601=seconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')"
if [[ "$FORCE_RUN" != "1" && -d "$GUARD_DIR" ]]; then
  cat > "$GUARD_DIR/fin.txt" <<EOF_FIN
fecha=$FIN_STAMP
return_code=$RC
EOF_FIN
fi

echo "[$FIN_STAMP] SATyS diario terminó con código $RC. No se programarán reintentos automáticos del servicio."
exit "$RC"
