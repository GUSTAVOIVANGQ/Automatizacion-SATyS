#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Revierte el parche SATyS del 29-07-2026 usando el respaldo creado al instalar.

Uso:
  sudo bash revertir_correcciones_satys_20260729.sh [RUTA_RESPALDO]

Sin argumento usa:
  <proyecto>/respaldos_patch_20260729/ULTIMO_RESPALDO

Opciones:
  --project-dir RUTA   Default: /data/gustavo.garcia/satys/Automatizacion-SATyS
  -h, --help

La reversión restaura código, configuración, servicio systemd y Excel maestro.
Los archivos copiados de forma no destructiva a output/sin_operador_CORREO o al
recurso compartido no se eliminan automáticamente.
USAGE
}

PROJECT_DIR="/data/gustavo.garcia/satys/Automatizacion-SATyS"
BACKUP_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir) PROJECT_DIR="${2:?Falta valor}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --*) echo "ERROR: opción desconocida: $1" >&2; exit 2 ;;
    *) [[ -z "$BACKUP_DIR" ]] || { echo "ERROR: solo se admite un respaldo." >&2; exit 2; }; BACKUP_DIR="$1"; shift ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "ERROR: ejecuta con sudo." >&2; exit 1; }
PROJECT_DIR="$(readlink -f "$PROJECT_DIR")"
if [[ -z "$BACKUP_DIR" ]]; then
  POINTER="$PROJECT_DIR/respaldos_patch_20260729/ULTIMO_RESPALDO"
  [[ -f "$POINTER" ]] || { echo "ERROR: no existe $POINTER" >&2; exit 1; }
  BACKUP_DIR="$(cat "$POINTER")"
fi
BACKUP_DIR="$(readlink -f "$BACKUP_DIR")"
[[ -f "$BACKUP_DIR/payload_files.txt" ]] || { echo "ERROR: respaldo inválido: $BACKUP_DIR" >&2; exit 1; }
# shellcheck disable=SC1090
source "$BACKUP_DIR/estado_servicios.env"

if systemctl is-active --quiet satys-diario.service; then
  echo "ERROR: satys-diario.service está activo. Espera a que termine." >&2
  exit 1
fi
systemctl stop satys-diario.timer 2>/dev/null || true
systemctl stop satys-api.service 2>/dev/null || true

while IFS= read -r rel; do
  [[ -n "$rel" ]] || continue
  if grep -Fxq -- "$rel" "$BACKUP_DIR/existing_files.txt"; then
    mkdir -p "$PROJECT_DIR/$(dirname "$rel")"
    cp -a -- "$BACKUP_DIR/project/$rel" "$PROJECT_DIR/$rel"
  else
    rm -f -- "$PROJECT_DIR/$rel"
  fi
done < "$BACKUP_DIR/payload_files.txt"
cp -a -- "$BACKUP_DIR/configuracion_local.json" "$PROJECT_DIR/config/configuracion_local.json"
cp -a -- "$BACKUP_DIR/TrámitesCRT.xlsx" "$PROJECT_DIR/TrámitesCRT.xlsx"
if [[ "$(cat "$BACKUP_DIR/systemd/service_existed")" == "1" ]]; then
  cp -a -- "$BACKUP_DIR/systemd/satys-diario.service" /etc/systemd/system/satys-diario.service
else
  rm -f /etc/systemd/system/satys-diario.service
fi
if [[ "$(cat "$BACKUP_DIR/systemd/timer_existed")" == "1" ]]; then
  cp -a -- "$BACKUP_DIR/systemd/satys-diario.timer" /etc/systemd/system/satys-diario.timer
else
  rm -f /etc/systemd/system/satys-diario.timer
fi
systemctl daemon-reload

case "${TIMER_ENABLED:-}" in
  enabled|enabled-runtime|linked|linked-runtime) systemctl enable satys-diario.timer >/dev/null 2>&1 || true ;;
  disabled) systemctl disable satys-diario.timer >/dev/null 2>&1 || true ;;
esac
[[ "${TIMER_ACTIVE:-}" == "active" ]] && systemctl start satys-diario.timer || true
case "${API_ENABLED:-}" in
  enabled|enabled-runtime|linked|linked-runtime) systemctl enable satys-api.service >/dev/null 2>&1 || true ;;
  disabled) systemctl disable satys-api.service >/dev/null 2>&1 || true ;;
esac
[[ "${API_ACTIVE:-}" == "active" ]] && systemctl start satys-api.service || true

touch "$BACKUP_DIR/REVERTIDO_$(date +%Y%m%d_%H%M%S)"
echo "Reversión terminada desde: $BACKUP_DIR"
echo "No se eliminaron copias no destructivas en sin_operador_CORREO."
