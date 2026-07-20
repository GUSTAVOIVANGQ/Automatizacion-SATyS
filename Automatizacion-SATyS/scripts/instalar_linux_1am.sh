#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Instala SATyS como tarea diaria de systemd a la 01:00 y levanta la UI/API.

Uso:
  sudo env SATYS_PYTHON_BIN="/ruta/python3.11+" \
    bash scripts/instalar_linux_1am.sh [opciones]

Opciones:
  --user USUARIO           Usuario Linux que ejecutará SATyS.
                           Default: SUDO_USER o dueño del proyecto.
  --project-dir RUTA       Ruta de Automatizacion-SATyS.
                           Default: directorio padre de este script.
  --venv-dir RUTA          Entorno virtual. Default: <base>/venv.
  --browsers-dir RUTA      Chromium Playwright. Default: <base>/playwright-browsers.
  --lock-dir RUTA          Lock local. Default: <base>/.lock.
  --depi-dir RUTA          Destino compartido. Default: /depi/DEI_DATOS/SATyS.
  --timezone ZONA          Zona del timer. Default: America/Mexico_City.
  --hour HH:MM             Hora diaria. Default: 01:00.
  --install-api            Instala la UI/API (valor predeterminado).
  --no-install-api         No instala ni levanta la UI/API.
  --api-port PUERTO        Puerto del panel. Default: 8095.
  --run-now                Inicia la automatización al terminar, sin bloquear.
  --skip-python-install    No instala requirements ni Chromium.
  -h, --help               Muestra esta ayuda.

Ejemplo:
  PYTHON_BIN="$(python -c 'import sys; print(sys.executable)')"
  sudo env SATYS_PYTHON_BIN="$PYTHON_BIN" \
    bash scripts/instalar_linux_1am.sh \
      --user gustavo.garcia \
      --project-dir /data/gustavo.garcia/satys/Automatizacion-SATyS
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$DEFAULT_PROJECT_DIR"
APP_USER="${SUDO_USER:-}"
VENV_DIR=""
BROWSERS_DIR=""
LOCK_DIR=""
DEPI_DIR="/depi/DEI_DATOS/SATyS"
TIMEZONE="America/Mexico_City"
RUN_HOUR="01:00"
INSTALL_API=1
API_PORT=8095
RUN_NOW=0
SKIP_PYTHON_INSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) APP_USER="${2:?Falta valor para --user}"; shift 2 ;;
    --project-dir) PROJECT_DIR="${2:?Falta valor para --project-dir}"; shift 2 ;;
    --venv-dir) VENV_DIR="${2:?Falta valor para --venv-dir}"; shift 2 ;;
    --browsers-dir) BROWSERS_DIR="${2:?Falta valor para --browsers-dir}"; shift 2 ;;
    --lock-dir) LOCK_DIR="${2:?Falta valor para --lock-dir}"; shift 2 ;;
    --depi-dir) DEPI_DIR="${2:?Falta valor para --depi-dir}"; shift 2 ;;
    --timezone) TIMEZONE="${2:?Falta valor para --timezone}"; shift 2 ;;
    --hour) RUN_HOUR="${2:?Falta valor para --hour}"; shift 2 ;;
    --install-api) INSTALL_API=1; shift ;;
    --no-install-api) INSTALL_API=0; shift ;;
    --api-port) API_PORT="${2:?Falta valor para --api-port}"; shift 2 ;;
    --run-now) RUN_NOW=1; shift ;;
    --skip-python-install) SKIP_PYTHON_INSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: ejecuta este instalador con sudo." >&2
  exit 1
fi

PROJECT_DIR="$(readlink -f "$PROJECT_DIR")"
[[ -d "$PROJECT_DIR" ]] || { echo "ERROR: no existe $PROJECT_DIR" >&2; exit 1; }
[[ -f "$PROJECT_DIR/requirements-linux.txt" ]] || { echo "ERROR: no parece ser el proyecto SATyS: $PROJECT_DIR" >&2; exit 1; }

if [[ -z "$APP_USER" ]]; then
  APP_USER="$(stat -c '%U' "$PROJECT_DIR")"
fi
id "$APP_USER" >/dev/null 2>&1 || { echo "ERROR: no existe el usuario Linux '$APP_USER'." >&2; exit 1; }
APP_GROUP="$(id -gn "$APP_USER")"

BASE_DIR="$(dirname "$PROJECT_DIR")"
VENV_DIR="${VENV_DIR:-$BASE_DIR/venv}"
BROWSERS_DIR="${BROWSERS_DIR:-$BASE_DIR/playwright-browsers}"
LOCK_DIR="${LOCK_DIR:-$BASE_DIR/.lock}"

[[ "$RUN_HOUR" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || {
  echo "ERROR: --hour debe usar HH:MM, por ejemplo 01:00." >&2; exit 1;
}
[[ "$API_PORT" =~ ^[0-9]+$ ]] && (( API_PORT >= 1 && API_PORT <= 65535 )) || {
  echo "ERROR: puerto inválido: $API_PORT" >&2; exit 1;
}

# SATYS_PYTHON_BIN resuelve el caso donde sudo usa un PATH distinto al usuario.
PYTHON_SYS="${SATYS_PYTHON_BIN:-}"
if [[ -n "$PYTHON_SYS" ]]; then
  [[ -x "$PYTHON_SYS" ]] || { echo "ERROR: SATYS_PYTHON_BIN no es ejecutable: $PYTHON_SYS" >&2; exit 1; }
else
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_SYS="$(command -v "$candidate")"
      if "$PYTHON_SYS" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
        break
      fi
      PYTHON_SYS=""
    fi
  done
fi
[[ -n "$PYTHON_SYS" ]] || { echo "ERROR: instala o indica Python 3.11 o superior." >&2; exit 1; }
"$PYTHON_SYS" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "ERROR: SATyS requiere Python 3.11 o superior. Detectado: $($PYTHON_SYS --version 2>&1)" >&2; exit 1;
}

echo "Python seleccionado: $PYTHON_SYS ($($PYTHON_SYS --version 2>&1))"

mkdir -p "$BASE_DIR" "$BROWSERS_DIR" "$LOCK_DIR" \
  "$PROJECT_DIR/logs" "$PROJECT_DIR/descargas" "$PROJECT_DIR/output" \
  "$PROJECT_DIR/registros_diarios" "$PROJECT_DIR/base_de_datos_rpc" \
  "$PROJECT_DIR/runs/daily_guard" "$PROJECT_DIR/systemd"
chown -R "$APP_USER:$APP_GROUP" "$BASE_DIR"
chmod +x "$PROJECT_DIR/scripts/"*.sh
chmod +x "$PROJECT_DIR/reconciliar_tramites_desde_folios.py" 2>/dev/null || true
chmod +x "$PROJECT_DIR/reparar_id_solicitante.py" 2>/dev/null || true

if [[ -f "$PROJECT_DIR/config/configuracion_local.json" ]]; then
  chown "$APP_USER:$APP_GROUP" "$PROJECT_DIR/config/configuracion_local.json"
  chmod 600 "$PROJECT_DIR/config/configuracion_local.json"
else
  echo "ERROR: falta $PROJECT_DIR/config/configuracion_local.json" >&2
  exit 1
fi

run_as_app() { runuser -u "$APP_USER" -- "$@"; }

if (( SKIP_PYTHON_INSTALL == 0 )); then
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "Creando entorno virtual en $VENV_DIR"
    run_as_app "$PYTHON_SYS" -m venv "$VENV_DIR"
  fi

  echo "Instalando dependencias Python"
  run_as_app "$VENV_DIR/bin/python" -m pip install --upgrade pip
  run_as_app "$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements-linux.txt"

  echo "Instalando Chromium de Playwright en $BROWSERS_DIR"
  runuser -u "$APP_USER" -- env PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR" \
    "$VENV_DIR/bin/python" -m playwright install chromium

  echo "Validando que Chromium pueda iniciar en modo headless"
  runuser -u "$APP_USER" -- env PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR" \
    "$VENV_DIR/bin/python" -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()'
fi

[[ -x "$VENV_DIR/bin/python" ]] || { echo "ERROR: no existe $VENV_DIR/bin/python" >&2; exit 1; }
[[ -f "$PROJECT_DIR/scripts/run_satys_diario.sh" ]] || { echo "ERROR: falta run_satys_diario.sh" >&2; exit 1; }

DEPI_PARENT="$(dirname "$DEPI_DIR")"

cat > /etc/systemd/system/satys-diario.service <<EOF_SERVICE
[Unit]
Description=SATyS CRT - revisión y procesamiento diario (máximo una corrida por fecha)
After=network-online.target remote-fs.target
Wants=network-online.target remote-fs.target
RequiresMountsFor=$DEPI_PARENT

[Service]
Type=oneshot
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=TZ=$TIMEZONE
Environment=SATYS_PROJECT_DIR=$PROJECT_DIR
Environment=SATYS_PYTHON=$VENV_DIR/bin/python
Environment=SATYS_LOCK_DIR=$LOCK_DIR
Environment=SATYS_DAILY_GUARD_DIR=$PROJECT_DIR/runs/daily_guard
Environment=PLAYWRIGHT_BROWSERS_PATH=$BROWSERS_DIR
Environment=SATYS_SYNC_EXCEL_CADA_FILA=0
ExecStartPre=/usr/bin/test -d $DEPI_DIR
ExecStartPre=/usr/bin/test -w $DEPI_DIR
ExecStart=/usr/bin/bash $PROJECT_DIR/scripts/run_satys_diario.sh
TimeoutStartSec=infinity
Restart=no
KillMode=control-group
UMask=0077
StandardOutput=journal
StandardError=journal
EOF_SERVICE

cat > /etc/systemd/system/satys-diario.timer <<EOF_TIMER
[Unit]
Description=Ejecuta SATyS CRT diariamente a las $RUN_HOUR ($TIMEZONE)

[Timer]
OnCalendar=*-*-* $RUN_HOUR:00 $TIMEZONE
Persistent=false
AccuracySec=1min
RandomizedDelaySec=0
Unit=satys-diario.service

[Install]
WantedBy=timers.target
EOF_TIMER

# El wrapper en /usr/local/sbin evita 203/EXEC al lanzar directamente un
# ejecutable del venv ubicado bajo /data en servidores con SELinux.
if (( INSTALL_API == 1 )); then
  cat > /usr/local/sbin/satys-api-start <<EOF_API_WRAPPER
#!/usr/bin/bash
set -euo pipefail
cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export TZ="$TIMEZONE"
export PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR"
exec "$VENV_DIR/bin/python" -m uvicorn satys_api:app --host 0.0.0.0 --port "$API_PORT"
EOF_API_WRAPPER
  chmod 755 /usr/local/sbin/satys-api-start
  restorecon -v /usr/local/sbin/satys-api-start >/dev/null 2>&1 || true

  cat > /etc/systemd/system/satys-api.service <<EOF_API
[Unit]
Description=SATyS CRT - panel web FastAPI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Environment=TZ=$TIMEZONE
Environment=SATYS_TIMER_HORA=$RUN_HOUR
Environment=SATYS_ESTADO_JSON=$PROJECT_DIR/logs/estado_actual.json
Environment=SATYS_SYSTEMD_SERVICE=satys-diario.service
Environment=SATYS_SYSTEMD_TIMER=satys-diario.timer
Environment=SATYS_API_ALLOW_START=1
Environment=SATYS_API_ALLOW_MANUAL=1
Environment=SATYS_API_ALLOW_REPAIR=1
Environment=SATYS_API_ALLOW_TIMER_EDIT=0
Environment=SATYS_LOCK_DIR=$LOCK_DIR
Environment=SATYS_DAILY_GUARD_DIR=$PROJECT_DIR/runs/daily_guard
Environment=PLAYWRIGHT_BROWSERS_PATH=$BROWSERS_DIR
ExecStart=/usr/local/sbin/satys-api-start
Restart=on-failure
RestartSec=5
UMask=0077
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF_API
fi

# Elimina overrides creados para corregir versiones anteriores; las unidades
# nuevas ya contienen los ExecStart compatibles con /data y SELinux.
rm -f /etc/systemd/system/satys-diario.service.d/override.conf
rmdir /etc/systemd/system/satys-diario.service.d 2>/dev/null || true
if (( INSTALL_API == 1 )); then
  rm -f /etc/systemd/system/satys-api.service.d/override.conf
  rmdir /etc/systemd/system/satys-api.service.d 2>/dev/null || true
fi

cp /etc/systemd/system/satys-diario.service "$PROJECT_DIR/systemd/satys-diario.service"
cp /etc/systemd/system/satys-diario.timer "$PROJECT_DIR/systemd/satys-diario.timer"
if (( INSTALL_API == 1 )); then
  cp /etc/systemd/system/satys-api.service "$PROJECT_DIR/systemd/satys-api.service"
fi
chown "$APP_USER:$APP_GROUP" "$PROJECT_DIR/systemd/"*.service "$PROJECT_DIR/systemd/"*.timer 2>/dev/null || true

systemctl daemon-reload
systemctl enable --now satys-diario.timer

if (( INSTALL_API == 1 )); then
  systemctl enable --now satys-api.service
  # Esperar brevemente a que uvicorn abra el puerto y mostrar el error real si no lo hace.
  api_ok=0
  for _ in {1..20}; do
    if runuser -u "$APP_USER" -- "$VENV_DIR/bin/python" - "$API_PORT" <<'PY' >/dev/null 2>&1
import socket, sys
port = int(sys.argv[1])
with socket.create_connection(("127.0.0.1", port), timeout=1):
    pass
PY
    then
      api_ok=1
      break
    fi
    sleep 1
  done
  if (( api_ok == 0 )); then
    echo "ERROR: satys-api.service no abrió el puerto $API_PORT." >&2
    systemctl status satys-api.service --no-pager -l >&2 || true
    journalctl -u satys-api.service -n 80 --no-pager >&2 || true
    exit 1
  fi
else
  systemctl disable --now satys-api.service >/dev/null 2>&1 || true
fi

if command -v systemd-analyze >/dev/null 2>&1; then
  units=(/etc/systemd/system/satys-diario.service /etc/systemd/system/satys-diario.timer)
  (( INSTALL_API == 1 )) && units+=(/etc/systemd/system/satys-api.service)
  systemd-analyze verify "${units[@]}"
  systemd-analyze calendar "*-*-* $RUN_HOUR:00 $TIMEZONE"
fi

cat <<EOF_DONE

Instalación terminada.

Proyecto:       $PROJECT_DIR
Usuario:        $APP_USER:$APP_GROUP
Python:         $VENV_DIR/bin/python
Chromium:       $BROWSERS_DIR
Horario:        $RUN_HOUR $TIMEZONE
Política:       una corrida por fecha, sin reintentos automáticos
Timer:          satys-diario.timer
Destino DEPI:   $DEPI_DIR
UI/API:         $([[ $INSTALL_API -eq 1 ]] && echo "activa en http://IP_SERVIDOR:$API_PORT/" || echo "no instalada")

Ver estado:
  systemctl list-timers --all satys-diario.timer
  systemctl status satys-diario.timer --no-pager
  systemctl status satys-diario.service --no-pager
  systemctl status satys-api.service --no-pager
  journalctl -u satys-diario.service -f
EOF_DONE

if (( RUN_NOW == 1 )); then
  echo "Iniciando corrida real sin bloquear esta terminal..."
  systemctl start --no-block satys-diario.service
fi
