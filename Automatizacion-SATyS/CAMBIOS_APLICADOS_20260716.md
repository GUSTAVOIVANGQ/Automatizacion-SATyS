# Correcciones aplicadas — 16 de julio de 2026

- Regla única de reintento: `descargas/<REGISTRO>/` debe contener al menos un archivo real, no vacío y no temporal. Los tres JSON de metadata no cuentan.
- Eliminada la validación de `id_solicitante` e `id_representante_legal` para decidir reintentos.
- Worker con `0 OK` ya no aparece como éxito; el código de salida refleja la evidencia real en la carpeta.
- Configuración local centralizada en `config/configuracion_local.json` con permisos 600.
- Credenciales retiradas de `main_procesar.py`, `Parte1_descarga.py`, `extraer_registros_documentos.py`, `login_satys.py` y `notificar_email.py`.
- Riesgo aceptado: credenciales actuales conservadas sin rotación.
- Ruta Windows eliminada. Destino Linux: `/depi/DEI_DATOS/SATyS`.
- Sincronización limitada exclusivamente a `TrámitesCRT.xlsx`, `output/` y `descargas/`.
- `TrámitesCRT.xlsx` se sobrescribe cuando ya existe en DEPI; `output/` y `descargas/` hacen merge, sobrescriben coincidencias y conservan archivos adicionales del destino.
- Nunca se sincronizan `configuracion_local.json` ni `sesion_guardada.json`.
- El cruce de concesionario se conserva sin cambios: `metadata_satys.json.id_solicitante` debe coincidir exactamente con `ID OPERADOR`/`ID CONCESIONARIO` del Excel RPC.
- Pipeline confirmado: Parte 1 → Parte 3 → Parte 4. Parte 2 no se invoca.
- Concurrencia: 10 workers iniciales, timeout 900 s, 2 reintentos, 2 workers en reintentos.
- Eliminados bloques de código comentado de reintentos/fuzzy que no estaban activos.

## Revisión V3 de despliegue

- Se eliminó la última validación duplicada del monitor diario. Ahora toda la
  cadena usa `estado_descargas.registro_esta_completo()`.
- Se alinearon las unidades `systemd` con el servidor documentado: lock local
  `/data/gustavo.garcia/satys/.lock`, Playwright en `/data/.../playwright-browsers`,
  API en puerto `8095` y prevalidación de escritura en `/depi/DEI_DATOS/SATyS`.
