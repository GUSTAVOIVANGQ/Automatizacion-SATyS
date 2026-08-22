# Despliegue Internos IFT sobre SATyS 8082

Versión: `2026.08.18-portable-oci-api-v1-8082-internos12`.

## Modos

- `bash scripts/satys.sh daily`: conserva la corrida diaria completa. Inventaría Oficialía e Internos y mantiene el flujo alternativo por **Trámites Nuevos** cuando un trámite lo requiere.
- `bash scripts/satys.sh internos`: omite Oficialía, revisa únicamente las seis bandejas de **Administración solicitudes +TyS/SIGEDO/Internos IFT > +TyS/SIGEDO/Internos IFT**, compara `Folio Internos` contra la hoja `Internos` de `TrámitesCRT.xlsx` y procesa sólo Folios pendientes.
- `bash scripts/satys.sh internos-check`: valida el acceso, inventaría las seis bandejas y compara contra la hoja `Internos`, pero no procesa Folios ni envía correo.

Ambos modos usan el mismo lock compartido. No ejecutar `internos` cerca de la 01:00; si ocupa el lock cuando dispara el timer, la corrida diaria se omitirá por seguridad.

## Ejecución manual desacoplada de SSH

Tras instalar systemd:

```bash
sudo systemctl start --no-block satys-container-internos.service
sudo journalctl -fu satys-container-internos.service
```

La terminal y la laptop pueden cerrarse después del `start --no-block`; el proceso continúa en el servidor.

## Release `internos12`

La imagen `internos12` conserva las correcciones de navegación de `internos4`
y usa 12 navegadores por defecto. Cada bandeja activa recibe primero dos
segmentos; los restantes se asignan según la carga. `SATYS_INTERNOS_WORKERS`
acepta cualquier entero positivo y `SATYS_SHM_SIZE` controla `/dev/shm`.
Después de copiar y extraer la release:

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
sed -i 's|^SATYS_IMAGE=.*|SATYS_IMAGE=satys-api:2026.08.18-portable-oci-api-v1-8082-internos12|' .env
sed -i 's|^SATYS_INTERNOS_WORKERS=.*|SATYS_INTERNOS_WORKERS=12|' .env
sed -i 's|^SATYS_SHM_SIZE=.*|SATYS_SHM_SIZE=6gb|' .env
bash scripts/preflight_despliegue.sh
bash scripts/satys.sh build
bash scripts/satys.sh internos-check
```

El check correcto debe terminar con extracción `COMPLETO`, integridad
`VALIDADA` y seis bandejas auditadas. Sólo entonces ejecutar:

```bash
bash scripts/satys.sh internos
```

Para que la API también reporte la nueva imagen:

```bash
sudo systemctl restart satys-container-api.service
curl -sS http://127.0.0.1:8082/api/v1/version | python3 -m json.tool
```
