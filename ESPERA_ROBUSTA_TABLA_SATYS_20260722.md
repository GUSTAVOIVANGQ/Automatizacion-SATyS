# SATyS — espera robusta de tabla y reintentos por año (2026-07-22)

## Comportamiento nuevo

La etapa inicial de extracción espera hasta **120 segundos** a que aparezca por lo menos un Registro válido en cada año y en cada página.

- El estado temporal `Mostrando 0 a 0 de 0 trámites` no se acepta como resultado final.
- En cuanto aparece un Registro, comienza la lectura completa de todas las páginas.
- Si no aparece ningún Registro en 120 segundos, falla únicamente el intento del año actual.
- El año se reintenta inmediatamente hasta 3 intentos totales.
- Los años ya completados se conservan en memoria y no se vuelven a extraer por un fallo posterior.
- Antes de aceptar un cambio de año se confirma el valor seleccionado y una actualización real de la tabla.
- Después de pulsar `Siguiente` se confirma que el rango de paginación avanzó; las páginas repetidas no se contabilizan.
- Se mantienen los 3 intentos generales del navegador ya instalados como última protección.

## Sin cambios

No se modifica el procesamiento posterior (Parte 1, Parte 2, Parte 3, Parte 4), los reintentos por Registro, el timer de las 01:00, el `daily_guard`, el Excel, las credenciales, las descargas ni el correo.

## Parámetros efectivos

- `--timeout-tabla 120`
- `--intentos-anio 3`
- `--intentos-pagina 3`
- reintentos generales: 3 intentos totales, inmediatos
