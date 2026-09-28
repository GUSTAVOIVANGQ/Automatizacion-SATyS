# SATyS 2026.09.25-email4203-rpc-crt-1

Correcciones posteriores a la corrida del 25/09/2026:

- El correo consolidado usa siempre el total final del Excel maestro (`Turnados recibidos` + `Internos`), no sólo los resultados de workers de la corrida.
- El correo muestra el desglose del Excel maestro y mantiene `EXITO + EN REVISION + ERRORES = TOTAL`.
- El RPC público usa por defecto el portal vigente `https://rpc.crt.gob.mx/vrpc`, configurable con `SATYS_RPC_BASE_URL`.
- Se detectan redirecciones fuera de `/vrpc` para no confundir un redirect del portal viejo con una búsqueda válida sin resultados.
- El formulario de resultados envía ambos nombres de campo observados (`strConcesionario` y `txtBPConcesionario`).
- El parser de resultados tolera cambios de plantilla HTML y el parser de autocomplete reconoce variantes de campos.
- La selección sigue siendo conservadora: igualdad normalizada, igualdad de nombre base sin sufijo legal o similitud de alta confianza con margen. No existe tabla hardcodeada de operadores.
- La reparación `_sin_operador` conserva el mecanismo existente de mover/fusionar archivos, actualizar `Ruta` en el Excel y sincronizar DEPI sólo después de resolver un ID RPC único.
