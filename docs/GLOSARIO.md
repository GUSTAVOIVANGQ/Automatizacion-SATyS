# Glosario de dominio SATyS

- **Folio:** identificador numérico/operativo mostrado por SATyS para un trámite. En algunos flujos se usa el Folio OPC; en Internos IFT se conserva además `Folio Internos` como inventario dedicado.
- **Registro:** identificador CRT del trámite, por ejemplo `CRT26-027838`. Es la clave principal usada para detectar trámites nuevos de Oficialía y reconciliar resultados.
- **RPC:** Registro Público de Concesiones. El pipeline consulta su catálogo para relacionar el solicitante con el operador mediante comparación exacta.
- **`id_solicitante`:** identificador obtenido de los metadatos SATyS que se compara exactamente con el `ID OPERADOR` del catálogo RPC.
- **`sin_operador`:** destino de salida para trámites que no pudieron asociarse de forma válida a un operador RPC. Requiere revisión posterior.
- **Internos IFT:** flujo SATyS de `Administración solicitudes +TyS/SIGEDO/Internos IFT`, procesado por seis bandejas en paralelo y controlado en la hoja `Internos` del Excel.
- **DEPI:** recurso compartido institucional utilizado como destino de sincronización. La ruta depende del host. En `srvmbcudaqa01` actualmente es `/depi/dgp/DEI_DATOS/SATyS`; dentro del contenedor se expone como `/shared`.
- **Columna `1711` de `TrámitesCRT.xlsx`:** encabezado de la columna que contiene la evidencia de números de Registro ya procesados en la hoja `Turnados recibidos`; el monitor diario la usa para comparar contra los Registros observados en SATyS. El código mantiene un fallback controlado a la columna D para el formato conocido del libro.
