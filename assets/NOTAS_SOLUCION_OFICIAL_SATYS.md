# Solución oficial SATyS — timeout, watchdog y reintentos controlados

Archivos modificados:

- `Parte1_descarga.py`
- `main_procesar.py`
- `automatizar_registros_diario.py`

## Qué cambia

1. Cada Registro se procesa en un proceso hijo independiente.
2. Si un Registro excede `--timeout-registro`, el proceso hijo se mata junto con sus Chromium/descendientes.
3. El lote continúa con los demás registros; ya no se congela en `as_completed()` esperando un worker atorado.
4. Se agregan logs finos de descarga directa, click, espera de evento y `save_as`.
5. Se agregan reintentos automáticos solo para registros que sigan incompletos.
6. Los reintentos usan menos workers por defecto.
7. Se genera `registros_fallidos/registros_fallidos_latest.txt` y archivos históricos con los registros que no pudieron completarse.
8. `main_procesar.py` ejecuta Partes 2-4 solo para registros que sí quedaron completos, evitando errores falsos con carpetas vacías.

## Comando recomendado

```powershell
.\python-3.11.9-embed-amd64\python.exe automatizar_registros_diario.py --headless --workers 6 --timeout-registro 900 --reintentos-registro 2 --workers-reintento 2
```

Si desean mantener 7 workers en el primer intento:

```powershell
.\python-3.11.9-embed-amd64\python.exe automatizar_registros_diario.py --headless --workers 7 --timeout-registro 900 --reintentos-registro 2 --workers-reintento 2
```

## Dónde revisar fallidos

- `registros_fallidos/registros_fallidos_latest.txt`
- `registros_fallidos/registros_fallidos_YYYYMMDD_HHMMSS_final.txt`
- `logs/workers_registro/<run_id>/<REGISTRO>.worker.log`
