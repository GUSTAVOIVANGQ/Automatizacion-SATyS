# systemd

Para el despliegue portable **no copies las unidades de esta carpeta a mano**.

- Contenedores: `sudo bash scripts/instalar_container_systemd.sh` genera unidades con el usuario y la ruta reales del host.
- Legacy `venv`: `sudo bash scripts/instalar_linux_1am.sh ...` genera las unidades nativas con las rutas indicadas.

Los archivos `.service` históricos de esta carpeta se conservan como referencia/compatibilidad y pueden contener valores de despliegues anteriores. Los instaladores son la fuente de verdad.
