# Despliegue UI2 en srvmbcudaqa01

Versión: `2026.08.18-portable-oci-api-v1-8082-internos12`

Objetivo: publicar SATyS directamente en `http://172.17.42.163:8082`, sin modificar nginx ni la aplicación de Conectividad en México.

## Perfil del servidor

Copiar `deploy/srvmbcudaqa01.env.example` a `.env`. Deben mantenerse:

```text
SATYS_API_PORT=8082
SATYS_API_BIND=0.0.0.0
SATYS_API_NETWORK=slirp4netns:enable_ipv6=false
SATYS_RUNTIME_DIR=/data/gustavo.garcia/satys/Automatizacion-SATyS
SATYS_SHARED_HOST_DIR=/depi/dgp/DEI_DATOS/SATyS
```

La release reutiliza el runtime productivo existente. No copiar ni reinicializar `TrámitesCRT.xlsx`, `descargas/`, `output/`, `logs/`, `runs/`, `registros_diarios/`, `registros_fallidos/`, `base_de_datos_rpc/` ni `sesion_guardada.json`.

## Validación previa

```bash
bash scripts/satys.sh build
bash scripts/satys.sh test
bash scripts/satys.sh api-up
curl -f http://127.0.0.1:8082/api/health
```

Validar desde otra PC:

```text
http://172.17.42.163:8082/
http://172.17.42.163:8082/docs
```

No ejecutar `daily` durante la validación paralela.

## Tema de documentación

El panel y `/docs` usan la misma clave de navegador: `localStorage["theme"]`. El valor por defecto es claro. El selector de tema de cualquiera de las dos páginas se refleja en la otra al navegar entre ellas.

## Producción

Una vez validado el contenedor, copiar sólo código sobre `/data/gustavo.garcia/satys/Automatizacion-SATyS`, preservando todos los directorios y archivos de runtime. Instalar las unidades con:

```bash
sudo bash scripts/instalar_container_systemd.sh
sudo systemctl enable --now satys-container-api.service
```

Después de verificar `8082`, deshabilitar el API legado `8095`. El timer contenedor sólo debe habilitarse después de desactivar el timer legado.

**No modificar, recargar ni reiniciar nginx como parte de este despliegue.**
