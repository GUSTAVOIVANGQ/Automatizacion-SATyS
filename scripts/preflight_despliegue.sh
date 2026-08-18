#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_DIR="/data/gustavo.garcia/satys/Automatizacion-SATyS"
APP_USER="gustavo.garcia"
DEPI_DIR="/depi/DEI_DATOS/SATyS"
PYTHON_BIN="${SATYS_PYTHON_BIN:-python3}"
CHECK_SERVER=0

usage() {
  cat <<'USAGE'
Valida una release SATyS antes de reemplazar la version productiva.

Uso:
  bash scripts/preflight_despliegue.sh [opciones]

Opciones:
  --release-dir RUTA   Raiz de la release a validar.
  --python RUTA        Python 3.11+ usado para compilar y probar Playwright.
  --server             Valida tambien el servidor productivo actual.
  --target-dir RUTA    Proyecto productivo actual.
  --user USUARIO       Usuario operativo. Default: gustavo.garcia.
  --depi-dir RUTA      Destino compartido. Default: /depi/DEI_DATOS/SATyS.
  -h, --help           Muestra esta ayuda.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-dir) RELEASE_DIR="${2:?Falta valor}"; shift 2 ;;
    --python) PYTHON_BIN="${2:?Falta valor}"; shift 2 ;;
    --server) CHECK_SERVER=1; shift ;;
    --target-dir) TARGET_DIR="${2:?Falta valor}"; shift 2 ;;
    --user) APP_USER="${2:?Falta valor}"; shift 2 ;;
    --depi-dir) DEPI_DIR="${2:?Falta valor}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: opcion desconocida: $1" >&2; usage >&2; exit 2 ;;
  esac
done

RELEASE_DIR="$(readlink -f "$RELEASE_DIR")"
TARGET_DIR="$(readlink -m "$TARGET_DIR")"
[[ -d "$RELEASE_DIR" ]] || { echo "ERROR: no existe $RELEASE_DIR" >&2; exit 1; }
command -v bash >/dev/null 2>&1 || { echo "ERROR: bash no disponible" >&2; exit 1; }
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="$(command -v "$PYTHON_BIN" 2>/dev/null || true)"
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || { echo "ERROR: Python no disponible" >&2; exit 1; }
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "ERROR: se requiere Python 3.11 o superior" >&2
  exit 1
}

required=(
  VERSION
  requirements-linux.txt
  configuracion_local.py
  Parte1_descarga.py
  Parte4_excel.py
  extraer_registros_documentos.py
  automatizar_registros_diario.py
  main_procesar.py
  scripts/run_satys_diario.sh
  scripts/run_satys_internos.sh
  scripts/instalar_linux_1am.sh
  scripts/desplegar_release_completa.sh
  scripts/smoke_internos.py
  tests/test_internos_diario.py
  config/configuracion_local.example.json
)
for rel in "${required[@]}"; do
  [[ -f "$RELEASE_DIR/$rel" ]] || { echo "ERROR: falta $rel" >&2; exit 1; }
done

[[ ! -e "$RELEASE_DIR/config/configuracion_local.json" ]] || {
  echo "ERROR: la release contiene config/configuracion_local.json" >&2
  exit 1
}
for session_file in sesion_guardada.json config/sesion_satys.json; do
  [[ ! -e "$RELEASE_DIR/$session_file" ]] || {
    echo "ERROR: la release contiene una sesion productiva: $session_file" >&2
    exit 1
  }
done
if find "$RELEASE_DIR" -type f \( -iname '*.xlsx' -o -iname '*.xls' \) -print -quit | grep -q .; then
  echo "ERROR: la release contiene un Excel; los Excel productivos se preservan en el servidor" >&2
  exit 1
fi

if [[ -f "$RELEASE_DIR/DEPLOYMENT_MANIFEST.json" ]]; then
  "$PYTHON_BIN" - "$RELEASE_DIR" <<'PY_MANIFEST'
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1])
manifest = json.loads((root / "DEPLOYMENT_MANIFEST.json").read_text(encoding="utf-8"))
files = manifest.get("files")
if manifest.get("contains_secrets") is not False or not isinstance(files, list):
    raise SystemExit("DEPLOYMENT_MANIFEST.json es invalido")
if manifest.get("version") != (root / "VERSION").read_text(encoding="utf-8").strip():
    raise SystemExit("La version del manifest no coincide con VERSION")
if manifest.get("file_count") != len(files):
    raise SystemExit("file_count no coincide con la lista del manifest")

expected = set()
for item in files:
    rel = PurePosixPath(str(item.get("path", "")))
    if not rel.parts or rel.is_absolute() or ".." in rel.parts:
        raise SystemExit(f"Ruta insegura en manifest: {rel}")
    rel_text = rel.as_posix()
    if rel_text in expected:
        raise SystemExit(f"Ruta duplicada en manifest: {rel_text}")
    expected.add(rel_text)
    path = root.joinpath(*rel.parts)
    if not path.is_file():
        raise SystemExit(f"Falta archivo declarado en manifest: {rel_text}")
    data = path.read_bytes()
    if len(data) != item.get("bytes") or hashlib.sha256(data).hexdigest() != item.get("sha256"):
        raise SystemExit(f"Hash o tamano incorrecto: {rel_text}")

actual = {
    path.relative_to(root).as_posix()
    for path in root.rglob("*")
    if path.is_file() and path.name != "DEPLOYMENT_MANIFEST.json"
}
if actual != expected:
    missing = sorted(expected - actual)
    extras = sorted(actual - expected)
    raise SystemExit(f"Contenido distinto al manifest; faltantes={missing}, extras={extras}")
print(f"OK manifest SHA-256: {len(expected)} archivos")
PY_MANIFEST
fi

grep -q 'def descargar_internos_ift' "$RELEASE_DIR/Parte1_descarga.py"
grep -q 'internos_workers' "$RELEASE_DIR/Parte1_descarga.py"
grep -q 'def _cerrar_paginas_emergentes' "$RELEASE_DIR/Parte1_descarga.py"
grep -q -- '--internos-workers' "$RELEASE_DIR/main_procesar.py"

TMP_CACHE="$(mktemp -d)"
trap 'rm -rf "$TMP_CACHE"' EXIT
PYTHONPYCACHEPREFIX="$TMP_CACHE" "$PYTHON_BIN" -m compileall -q \
  -x '/(descargas|output|logs|debug|runs|releases)/' "$RELEASE_DIR"
while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find "$RELEASE_DIR" -type f -name '*.sh' -print0)

"$PYTHON_BIN" - "$RELEASE_DIR/config/configuracion_local.example.json" <<'PY_RELEASE'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
workers = data.get("procesamiento", {}).get("internos_workers")
if workers != 6:
    raise SystemExit(f"internos_workers esperado=6, recibido={workers!r}")
PY_RELEASE

echo "OK release: $(cat "$RELEASE_DIR/VERSION")"
echo "OK sintaxis Python, Bash, manifest y configuracion de seis workers Internos"

if (( CHECK_SERVER == 0 )); then
  exit 0
fi

[[ $EUID -eq 0 ]] || { echo "ERROR: usa sudo para --server" >&2; exit 1; }
id "$APP_USER" >/dev/null 2>&1 || { echo "ERROR: no existe $APP_USER" >&2; exit 1; }
[[ -d "$TARGET_DIR" ]] || { echo "ERROR: no existe el proyecto productivo $TARGET_DIR" >&2; exit 1; }
[[ -f "$TARGET_DIR/config/configuracion_local.json" ]] || { echo "ERROR: falta configuracion productiva" >&2; exit 1; }

if systemctl is-active --quiet satys-diario.service; then
  echo "ERROR: satys-diario.service esta activo; espera a que termine" >&2
  exit 1
fi

DEPI_PARENT="$(dirname "$DEPI_DIR")"
mountpoint -q "$DEPI_PARENT" || { echo "ERROR: $DEPI_PARENT no esta montado" >&2; exit 1; }
runuser -u "$APP_USER" -- test -w "$DEPI_DIR" || {
  echo "ERROR: $APP_USER no puede escribir en $DEPI_DIR" >&2
  exit 1
}

"$PYTHON_BIN" - "$TARGET_DIR/config/configuracion_local.json" "$TARGET_DIR" <<'PY_CONFIG'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
project_dir = Path(sys.argv[2])
data = json.loads(config_path.read_text(encoding="utf-8"))
satys = data.get("satys", {})
if not str(satys.get("usuario", "")).strip() or not str(satys.get("password", "")).strip():
    raise SystemExit("Faltan credenciales SATyS en la configuracion productiva")
excel = Path(str(data.get("rutas", {}).get("excel", "TramitesCRT.xlsx")))
if not excel.is_absolute():
    excel = project_dir / excel
if not excel.exists():
    raise SystemExit(f"No existe el Excel productivo configurado: {excel}")
workers = data.get("procesamiento", {}).get("internos_workers", 6)
if not isinstance(workers, int) or not 0 <= workers <= 6:
    raise SystemExit(f"internos_workers invalido: {workers!r}")
print(f"OK configuracion productiva; internos_workers efectivo={workers}")
PY_CONFIG

BROWSERS_DIR="$(dirname "$TARGET_DIR")/playwright-browsers"
runuser -u "$APP_USER" -- env PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR" \
  "$PYTHON_BIN" - <<'PY_BROWSER'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("<title>SATyS preflight</title>")
    assert page.title() == "SATyS preflight"
    browser.close()
print("OK Chromium headless")
PY_BROWSER

echo "OK montaje DEPI, permisos, Excel, credenciales y Chromium"
echo "PREFLIGHT COMPLETO: servidor listo para desplegar"
