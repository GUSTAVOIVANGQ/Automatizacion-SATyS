# Portabilidad y reproducibilidad

## Contrato del host

El host sólo aporta:

- Docker Compose **o** Podman;
- acceso de red a SATyS para las pruebas/corridas reales;
- un archivo de configuración local;
- el Excel operativo;
- almacenamiento persistente.

Python, dependencias, Playwright y Chromium pertenecen a la imagen OCI.

## Variables de host

| Variable | Default | Propósito |
|---|---|---|
| `SATYS_API_PORT` | `8082` | Puerto local del panel |
| `SATYS_RUNTIME_DIR` | `./runtime` | Datos persistentes |
| `SATYS_SHARED_HOST_DIR` | `./runtime/shared` | Recurso compartido del host |
| `SATYS_LOCK_HOST_DIR` | `./runtime/locks` | Lock compartido con otras instancias |
| `SATYS_CONFIG_HOST_FILE` | `./config/configuracion_local.json` | Configuración/secretos |
| `SATYS_IMAGE` | versión actual | Etiqueta de imagen |

Dentro del contenedor, la compartida siempre es `/shared`; por eso el código no conoce la ruta del host.

## Compatibilidad

- Windows/macOS: Docker Desktop + Compose.
- Linux genérico: Docker Engine + Compose.
- RHEL: Podman mediante `scripts/podman_satys.sh` sin dependencia de Compose.
- Legacy: `venv + systemd` permanece disponible como mecanismo de recuperación.
