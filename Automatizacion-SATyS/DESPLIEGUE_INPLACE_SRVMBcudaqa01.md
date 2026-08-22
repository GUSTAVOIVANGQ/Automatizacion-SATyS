# Despliegue in-place en srvmbcudaqa01

Este paquete mantiene el código final en `/data/gustavo.garcia/satys/Automatizacion-SATyS` y reutiliza el estado productivo existente.

Estado persistente preservado: `TrámitesCRT.xlsx`, `sesion_guardada.json`, `descargas/`, `output/`, `logs/`, `runs/`, `exports/`, `base_de_datos_rpc/`, `registros_diarios/`, `registros_fallidos/`, configuración local y lock compartido.

La imagen OCI no contiene credenciales ni datos. El perfil `deploy/srvmbcudaqa01.env.example` apunta al runtime productivo existente y a `/depi/dgp/DEI_DATOS/SATyS`.
