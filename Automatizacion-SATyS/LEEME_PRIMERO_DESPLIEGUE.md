# Léeme primero — SATyS portable

Versión: `2026.08.17-portable-oci-api-v1-8082`

El método recomendado es una imagen OCI reproducible. El proyecto funciona con:

- Docker Compose en Windows/macOS/Linux;
- Podman directo en RHEL mediante `scripts/podman_satys.sh`;
- `venv + systemd` sólo como compatibilidad/rollback.

No se incluyen credenciales, sesiones, Excel ni datos operativos en la release.

Inicio rápido:

```bash
cp .env.example .env
bash scripts/bootstrap_portable.sh
bash scripts/doctor_portable.sh
```

En `srvmbcudaqa01`, usar en cambio:

```bash
cp deploy/srvmbcudaqa01.env.example .env
bash scripts/bootstrap_portable.sh
bash scripts/doctor_portable.sh
```

Ese perfil reutiliza el runtime productivo existente, incluida la ruta real
`/depi/dgp/DEI_DATOS/SATyS`, sin codificarla dentro de la imagen.

Lee `QUICKSTART_PORTABLE.md` y `DESPLIEGUE_NUEVO.md` antes del cutover.
