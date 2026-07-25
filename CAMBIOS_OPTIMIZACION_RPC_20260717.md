# Optimización de carga del catálogo RPC — 17 de julio de 2026

## Problema corregido

`buscar_concesionario.cargar_catalogo_desde_excel()` abría el XLSX con
`read_only=True`, pero leía cada valor mediante `ws.cell(fila, columna)`.
Ese acceso no es aleatorio en una hoja de solo lectura y obligaba a recorrer
repetidamente el XML del libro. Con aproximadamente 9,166 filas, la carga podía
consumir cerca de cuatro horas.

## Corrección

- La detección de encabezados ahora usa `ws.iter_rows(values_only=True)`.
- La carga de operadores también usa una única iteración secuencial.
- Solo se leen las columnas necesarias.
- Se conservan la normalización de IDs, deduplicación, filtro de vigencia,
  `_fila_excel_rpc` y `_hoja_excel_rpc`.
- El log informa duración, duplicados y filas vacías/incompletas.
- Se añadió `scripts/validar_catalogo_rpc.py` para medir el archivo real antes
  de ejecutar toda la automatización.

## Validación rápida en el servidor

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS

/data/gustavo.garcia/satys/venv/bin/python \
  scripts/validar_catalogo_rpc.py \
  --esperados 9166
```

El script localiza automáticamente el XLSX más reciente en
`base_de_datos_rpc/`. También puede indicarse explícitamente:

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  scripts/validar_catalogo_rpc.py \
  --excel base_de_datos_rpc/03_concesiones_permisos_autorizaciones_DDMMYY.xlsx \
  --esperados 9166
```

No descarga archivos, no modifica Excel y no abre SATyS.
