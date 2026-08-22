#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(id -un)}"
GROUP_NAME="$(id -gn "$USER_NAME" 2>/dev/null || echo "$USER_NAME")"
API_SERVICE="satys-container-api.service"
DAILY_SERVICE="satys-container-diario.service"
TIMER="satys-container-diario.timer"
INTERNOS_SERVICE="satys-container-internos.service"

if [[ $EUID -ne 0 ]]; then
  echo "Uso: sudo bash scripts/instalar_container_systemd.sh" >&2
  exit 2
fi

USER_UID="$(id -u "$USER_NAME")"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[[ -n "$USER_HOME" ]] || USER_HOME="/home/$USER_NAME"

# Rootless Podman necesita el runtime del usuario disponible también sin sesión SSH.
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$USER_NAME" || true
fi

cat > "/etc/systemd/system/$API_SERVICE" <<EOF
[Unit]
Description=SATyS CRT - API portable en contenedor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
Group=$GROUP_NAME
WorkingDirectory=$ROOT
Environment=HOME=$USER_HOME
Environment=XDG_RUNTIME_DIR=/run/user/$USER_UID
ExecStart=/usr/bin/bash $ROOT/scripts/satys.sh api-run
ExecStop=/usr/bin/bash $ROOT/scripts/satys.sh api-down
Restart=on-failure
RestartSec=10
TimeoutStartSec=180
TimeoutStopSec=60
KillMode=control-group

[Install]
WantedBy=multi-user.target
EOF

cat > "/etc/systemd/system/$DAILY_SERVICE" <<EOF
[Unit]
Description=SATyS CRT - worker diario portable en contenedor
After=network-online.target $API_SERVICE
Wants=network-online.target

[Service]
Type=oneshot
User=$USER_NAME
Group=$GROUP_NAME
WorkingDirectory=$ROOT
Environment=HOME=$USER_HOME
Environment=XDG_RUNTIME_DIR=/run/user/$USER_UID
ExecStart=/usr/bin/bash $ROOT/scripts/satys.sh daily
TimeoutStartSec=infinity
KillMode=control-group
UMask=0077
StandardOutput=journal
StandardError=journal
EOF

cat > "/etc/systemd/system/$INTERNOS_SERVICE" <<EOF
[Unit]
Description=SATyS CRT - corrida manual solo Internos IFT
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
Group=$GROUP_NAME
WorkingDirectory=$ROOT
Environment=HOME=$USER_HOME
Environment=XDG_RUNTIME_DIR=/run/user/$USER_UID
ExecStart=/usr/bin/bash $ROOT/scripts/satys.sh internos
TimeoutStartSec=infinity
KillMode=control-group
UMask=0077
StandardOutput=journal
StandardError=journal
EOF

cat > "/etc/systemd/system/$TIMER" <<EOF
[Unit]
Description=SATyS CRT - timer diario portable a la 01:00

[Timer]
OnCalendar=*-*-* 01:00:00 America/Mexico_City
Persistent=false
AccuracySec=1min
RandomizedDelaySec=0
Unit=$DAILY_SERVICE

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable "$API_SERVICE"
# El timer se habilita deliberadamente sólo con --enable-timer para evitar dos
# corridas diarias mientras el timer legado siga activo.
if [[ "${1:-}" == "--enable-timer" ]]; then
  systemctl enable --now "$TIMER"
fi

echo "Instalados: $API_SERVICE, $DAILY_SERVICE, $INTERNOS_SERVICE, $TIMER"
echo "API: sudo systemctl start $API_SERVICE"
echo "Internos manual (desacoplado de SSH): sudo systemctl start --no-block $INTERNOS_SERVICE"
echo "Timer: sudo systemctl enable --now $TIMER (sólo tras desactivar el timer legado)"
