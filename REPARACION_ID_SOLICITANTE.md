# Reparación manual de `id_solicitante` y reanudación por checkpoint

## Comportamiento de una corrida normal

Una nueva corrida de `main_procesar.py` **no vuelve a descargar todos los registros**.
La descarga se omite cuando `descargas/<REGISTRO>/` contiene al menos un archivo real
(no cuentan los JSON de metadata, archivos temporales ni archivos vacíos).

Los registros ya descargados sí pueden volver a pasar por RPC, actualización de
`TrámitesCRT.xlsx` y organización de `output/`. Esto permite reconstruir las salidas
sin consumir otra descarga desde SATyS.

`id_solicitante` vacío no convertía por sí solo un registro en pendiente. Por eso se
agregó el reparador específico.

## Programa nuevo

Archivo:

```bash
reparar_id_solicitante.py
```

El programa:

1. Busca recursivamente todos los `metadata_satys.json`.
2. Selecciona registros cuyo `id_solicitante` está ausente, vacío o `null`.
3. Reabre cada Registro en SATyS y vuelve a extraer **solo metadata**.
4. De forma predeterminada no descarga nuevamente los documentos asociados.
5. Puede forzar la descarga completa con `--redescargar-archivos` o desde la casilla de la UI.
6. Conserva los valores anteriores si SATyS devuelve metadata parcial.
7. Valida el JSON después de cada intento.
8. Guarda un checkpoint atómico después de cada Registro.
9. Al terminar actualiza RPC, `TrámitesCRT.xlsx` y `output/` usando los archivos ya existentes.

Algunos trámites migrados no muestran un ID en el DOM de SATyS. Después de los
intentos configurados quedan registrados como `unresolved`, sin repetirse para siempre.

## Reanudación después de apagón

Checkpoint:

```text
logs/reparacion_id_estado.json
```

Si el servidor se apaga durante un Registro:

- Los registros ya completados permanecen marcados.
- El Registro que estaba en curso no se marca como completado.
- Al ejecutar nuevamente el programa, ese Registro se intenta otra vez.
- Después continúa con el siguiente elemento de la cola.

## Backend

Iniciar o reanudar:

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  reparar_id_solicitante.py \
  --reintentos 2 \
  --headless
```

Crear una cola nueva descartando el checkpoint anterior:

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  reparar_id_solicitante.py \
  --reiniciar-cola \
  --reintentos 2 \
  --headless
```

Solo analizar y crear la cola, sin abrir SATyS:

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  reparar_id_solicitante.py \
  --reiniciar-cola \
  --solo-analizar
```

Forzar también la descarga de documentos asociados:

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  reparar_id_solicitante.py \
  --redescargar-archivos
```

Esta opción normalmente no es necesaria y puede generar archivos con sufijos `(1)` si el portal entrega nuevamente el mismo nombre.

No actualizar Excel/output al final:

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  reparar_id_solicitante.py \
  --no-actualizar-salidas
```

## UI

La sección **Reparar IDs** muestra:

- Registros detectados.
- Procesados.
- Resueltos.
- Sin resolver.
- Pendientes.
- Registro en curso.
- Log en vivo.

Acciones:

- **Iniciar / reanudar**: usa el checkpoint existente.
- **Nueva revisión**: vuelve a escanear todos los JSON y crea una cola nueva.
- **Detener**: solicita cierre controlado y conserva el checkpoint.
- **Volver a descargar documentos asociados**: fuerza descarga completa; por defecto solo se actualiza metadata.

## API

```text
GET  /api/reparacion-id/estado
POST /api/reparacion-id/iniciar
POST /api/reparacion-id/detener
GET  /api/log/stream?tipo=reparacion
GET  /api/log/descargar?tipo=reparacion
```

Ejemplo para iniciar/reanudar:

```bash
curl -X POST http://127.0.0.1:8095/api/reparacion-id/iniciar \
  -H 'Content-Type: application/json' \
  -d '{"reiniciar_cola":false,"actualizar_salidas":true,"redescargar_archivos":false,"reintentos":2}'
```

## Exclusión mutua

El reparador usa el mismo `ProcesoLock` que la automatización diaria. No permite que
la reparación, la corrida diaria y otra corrida manual escriban simultáneamente en
Excel/output.
