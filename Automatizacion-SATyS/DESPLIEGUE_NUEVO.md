# Despliegue portable — servidor nuevo o srvmbcudaqa01

Versión: `2026.08.17-portable-oci-api-v1-8082-ui2`

## Principio

La imagen OCI es la unidad reproducible. El runtime productivo queda fuera de la imagen. Esto permite cambiar de servidor o de computadora sin reinstalar manualmente Python, módulos o Chromium.

## srvmbcudaqa01: migración sin copiar 7 GB

El servidor actual mantiene datos en:

`/data/gustavo.garcia/satys/Automatizacion-SATyS`

la carpeta compartida real es:

`/depi/dgp/DEI_DATOS/SATyS`

El perfil `deploy/srvmbcudaqa01.env.example` hace que el contenedor nuevo reutilice esos datos y el mismo lock, sin copiar `descargas/` ni `output/`.

### 1. Instalar motor de contenedores

En RHEL se recomienda Podman. Si `podman --version` no existe, instalar `container-tools` según la política de infraestructura del servidor.

### 2. Preparar la release

```bash
cp deploy/srvmbcudaqa01.env.example .env
bash scripts/bootstrap_portable.sh
bash scripts/doctor_portable.sh
```

La configuración y el Excel se toman de la instalación actual mediante las rutas de `.env`. No se guardan credenciales en la imagen.

### 3. Construir

```bash
bash scripts/podman_satys.sh build
```

### 4. Blue/green del panel

La API actual puede permanecer en 8095. Levantar la nueva en 8082:

```bash
bash scripts/podman_satys.sh api-up
curl -f http://127.0.0.1:8082/api/health
curl -f http://127.0.0.1:8082/api/v1/version
```

### 5. Smoke SATyS

```bash
bash scripts/podman_satys.sh smoke
```

No cambiar el timer hasta obtener `SMOKE_OK`.

### 6. systemd

```bash
sudo bash scripts/instalar_container_systemd.sh
sudo systemctl start satys-container-api.service
```

El instalador NO habilita el nuevo timer por defecto para evitar doble corrida.

Cuando se haya validado la nueva versión:

```bash
sudo systemctl disable --now satys-diario.timer
sudo systemctl enable --now satys-container-diario.timer
```

La API antigua puede detenerse después del cambio de proxy/consumidores hacia 8082.

## Servidor nuevo

En un servidor nuevo:

1. instalar Docker o Podman;
2. extraer la release;
3. copiar `.env.example` a `.env`;
4. crear `config/configuracion_local.json`;
5. colocar el Excel vigente en `runtime/TrámitesCRT.xlsx`;
6. definir `SATYS_SHARED_HOST_DIR` si existe un recurso institucional;
7. `scripts/satys.sh build`;
8. `scripts/satys.sh test`;
9. `scripts/satys.sh smoke` dentro de la red institucional;
10. `scripts/satys.sh api-up`.

No hay rutas de usuario o servidor compiladas en la imagen.
