# Reintentos de la extracción inicial SATyS — 2026-07-21

La etapa **1) EXTRAER REGISTROS DESDE SATyS** ahora admite hasta tres intentos
totales: un intento inicial y dos reintentos.

Se reintenta únicamente cuando:

- `extraer_registros_documentos.py` termina con código distinto de cero; o
- el extractor termina con código cero, pero el TXT contiene cero Registros.

Cada intento ejecuta un proceso independiente del extractor. El navegador de
ese proceso se cierra mediante el bloque `finally` existente antes de iniciar el
siguiente intento. En cuanto se obtiene al menos un Registro, se continúa con el
flujo anterior sin cambios: comparación con Excel y procesamiento de Parte 1,
Parte 2, Parte 3 y Parte 4.

Valores predeterminados:

```text
reintentos_extraccion = 2
espera_reintento_extraccion = 30 segundos
```

Opcionalmente pueden agregarse dentro de `procesamiento` en
`config/configuracion_local.json`:

```json
{
  "procesamiento": {
    "reintentos_extraccion": 2,
    "espera_reintento_extraccion": 30
  }
}
```

También existen las opciones de línea de comandos:

```text
--reintentos-extraccion 2
--espera-reintento-extraccion 30
```
