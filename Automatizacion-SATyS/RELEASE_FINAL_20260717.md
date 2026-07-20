# SATyS — Release final 2026.07.17-final.1

Esta versión consolida en un solo proyecto:

- reconciliación de `TrámitesCRT.xlsx` desde `Folios_Datos_Completos.xlsx`;
- exclusión de carpetas internas de ZIP como trámites independientes;
- una fila independiente por número de Registro, incluso con folios repetidos;
- llenado de `Ruta` para coincidencias RPC y `_sin_operador`;
- reparación manual de `id_solicitante` vacío o `null`, con checkpoint y reanudación;
- controles del reparador en la UI y endpoints backend;
- lectura secuencial optimizada del catálogo RPC mediante `iter_rows(values_only=True)`;
- UI/API levantada por defecto durante la instalación;
- timer diario a las 01:00 `America/Mexico_City`;
- wrappers compatibles con `/data` y SELinux para evitar `203/EXEC`;
- despliegue de reemplazo total del código conservando datos operativos.

## Política del despliegue completo

El script `desplegar_release_completa.sh` reemplaza completamente la carpeta activa de código. No superpone archivos antiguos: renombra la instalación anterior, copia la nueva versión y restaura exclusivamente datos persistentes.

Se conservan:

- `descargas/`
- `output/`
- `logs/`
- `runs/`
- `exports/`
- `registros_diarios/`
- `registros_fallidos/`
- `base_de_datos_rpc/`
- `buscar_concesionario/` si existe como directorio de datos heredado
- `config/configuracion_local.json`
- `TrámitesCRT.xlsx`
- `sesion_guardada.json`
- `registros.txt`

Los archivos `.py`, `.sh`, `.service`, `.timer`, `.html`, `.js`, `.css` y el resto de archivos del programa se reemplazan por los de esta release.
