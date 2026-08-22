# Despliegue SATyS Linux V3

Esta versión está aprobada para despliegue controlado. La aprobación final de
producción requiere una corrida pequeña real contra SATyS y el montaje DEPI.

## Rutas del servidor

- Aplicación: `/data/gustavo.garcia/satys/Automatizacion-SATyS`
- Entorno virtual: `/data/gustavo.garcia/satys/venv`
- Chromium: `/data/gustavo.garcia/satys/playwright-browsers`
- Lock: `/data/gustavo.garcia/satys/.lock`
- DEPI: `/depi/DEI_DATOS/SATyS`

## Parámetros productivos

- Workers iniciales: 10
- Timeout por registro: 900 segundos
- Reintentos adicionales: 2
- Workers de reintento: 2

## Salidas sincronizadas

Solo se copian `TrámitesCRT.xlsx`, `output/` y `descargas/`. Se sobrescriben
coincidencias y no se elimina ningún archivo exclusivo del destino.
