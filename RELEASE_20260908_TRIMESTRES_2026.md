# Organización adicional Q3/Q4 — 2026.09.08-trimestres-q3-2026-q4-2027-ruta-excel1

Esta revisión corrige la ventana de negocio y hace que la organización trimestral sea parte del estado final del Excel.

## Ventanas vigentes

- 01-oct-2026 a 15-dic-2026 → `output/2026Q3/<Ruta base>`.
- 01-ene-2027 a 31-mar-2027 → `output/2026Q4/<Ruta base>`.
- Enero-marzo de 2026 ya **no** pertenece a `2026Q4`.

Los nombres `2026Q3` y `2026Q4` son etiquetas de negocio solicitadas y no trimestres calendario estándar.

## Doble organización

Para cada fila seleccionada, `descargas` sigue siendo la fuente de verdad. Los archivos quedan en ambos lugares:

1. `output/<Ruta base>`
2. `output/<bucket>/<Ruta base>`

La misma estructura se replica a DEPI. Un archivo de `descargas` sobrescribe el mismo pathname; archivos históricos distintos se conservan y JSON no se publica en `output`.

## Columna Ruta

Después de confirmar la copia local y, cuando está habilitado, la publicación en DEPI, `TrámitesCRT.xlsx` guarda `Ruta` como una ruta relativa a `output`:

- `2026Q3\<Ruta base>` o
- `2026Q4\<Ruta base>`.

No se guarda el literal `output\` dentro de la celda porque toda `Ruta` del sistema es relativa a la carpeta `output`. La lógica es idempotente: si la celda ya empieza por Q3/Q4, el prefijo se retira para derivar la Ruta base y no se duplica.

El Excel actualizado también se replica a `/depi/dgp/DEI_DATOS/SATyS/TrámitesCRT.xlsx` en ejecución con DEPI.

## Ejecución independiente

```bash
bash scripts/podman_satys.sh trimestres-2026 --dry-run
bash scripts/podman_satys.sh trimestres-2026
```

La misma etapa se ejecuta automáticamente al final de la corrida diaria, después de RPC/(correos) y antes del correo.
