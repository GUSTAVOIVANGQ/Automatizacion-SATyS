# Release 2026.09.25-redeploy-seguro-rutas-q3q4-2 — redeploy seguro de Rutas y Q3/Q4

Esta release corrige el fallo observado en la corrida diaria del 25-sep-2026.

## Cambios de seguridad de datos

- La corrida diaria de **Turnados recibidos** procesa en Partes 3–4 solamente los Registros contenidos en el archivo diario; ya no recorre todo `descargas/`.
- La corrida diaria de **Internos** procesa solamente los pares exactos `bandeja + folio` del JSON de objetivos; ya no reprocesa el histórico local completo.
- Una indisponibilidad temporal del catálogo/RPC **nunca degrada** una Ruta canónica existente a `_sin_operador`.
- `Parte4_excel.py` es conservador en filas existentes: completa vacíos, promueve `_sin_operador -> Ruta canónica`, conserva datos ya validados y no limpia marcas R001–R027.
- La reconciliación global diaria dejó de reconstruir masivamente el Excel: sólo intenta promover Rutas pendientes; no reordena ni sobrescribe filas históricas.
- El cargador RPC ignora un XLSX nuevo corrupto y cae al catálogo local válido anterior.
- Internos queda estandarizado en **10 workers** en runtime, ejemplos y validadores.
- Los timeouts de reconciliación/RPC público de los perfiles de despliegue suben a 3600 s.

## Organización Q3/Q4 preservada

- 01-oct-2026 a 15-dic-2026 -> `2026Q3`.
- 01-ene-2027 a 31-mar-2027 -> `2026Q4`.
- Ene–mar 2026 queda fuera.
- Se conserva la copia normal en `output/<Ruta base>` y además la copia del bucket.
- La columna `Ruta` sólo adopta el prefijo Q3/Q4 cuando la publicación requerida en DEPI termina correctamente.
- Se mantienen las excepciones `_sin_operador` y `(correos)`.

## Importante

La release no contiene `.env`, `config/configuracion_local.json`, `sesion_guardada.json`, Excel operativo ni credenciales.

## Despliegue in-place definitivo

- El proyecto activo permanece en `/data/gustavo.garcia/satys/Automatizacion-SATyS`.
- `scripts/desplegar_inplace_seguro_20260925.sh` superpone únicamente código de una release ya verificada y conserva `.env`, credenciales, sesión, Excel y todos los directorios de runtime.
- El script no ejecuta smoke ni dispara la corrida diaria. Reactiva el timer únicamente después de validar API/UI.
- CI/CD (`.github/`) es opcional; no participa en la ejecución productiva del servidor.
