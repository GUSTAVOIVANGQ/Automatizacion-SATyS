# Reparación final de `_sin_operador` con RPC público

La corrida diaria ejecuta esta etapa una sola vez después de consolidar Excel/output y antes de enviar el único correo final.

1. Lee `TrámitesCRT.xlsx` (hojas `Turnados recibidos` e `Internos`) y selecciona únicamente filas cuyo `Ruta` contiene `_sin_operador`.
2. Usa `1711` como llave para localizar la carpeta de revisión en `output` y uno o más expedientes originales bajo `descargas` que contienen `metadata_satys.json`.
3. Obtiene `nombre_operador` de `metadata_satys.json` y consulta **exclusivamente** el buscador público RPC. Esta etapa no usa el Excel oficial RPC.
4. Si la resolución pública es segura y unívoca, construye `<ID>_<nombre>/01 EN/VE`.
5. Fusiona archivos históricos de `_sin_operador` sólo cuando no pisan una copia canónica; después copia desde `descargas`, que siempre gana si existe el mismo nombre/ruta. No se crean nombres alternos.
6. Verifica byte a byte los documentos esperados desde `descargas`, actualiza `Ruta` en Excel y retira únicamente la carpeta de revisión reparada.
7. Replica el destino reparado a `SATYS_SHARED_DIR/output` antes del correo final. En el servidor, `SATYS_SHARED_DIR=/shared` está montado sobre `/depi/dgp/DEI_DATOS/SATyS`.

Si el RPC no ofrece una coincidencia segura, la fila y su carpeta permanecen en `_sin_operador`. El proceso genera JSON/CSV de auditoría y continúa.


## Ejecución independiente

En RHEL/Podman puede ejecutarse únicamente esta etapa, sin hacer la corrida
diaria completa:

```bash
bash scripts/podman_satys.sh sin-operador-rpc
```

Modo de diagnóstico sin modificar Excel/output/DEPI:

```bash
bash scripts/podman_satys.sh sin-operador-rpc --dry-run
```

La ejecución independiente adquiere el mismo `ProcesoLock` global que el
monitor diario. Si existe una corrida SATyS activa, termina con código 3 y no
toca `TrámitesCRT.xlsx`, `descargas`, `output` ni DEPI. El wrapper Podman aplica
también `SATYS_SIN_OPERADOR_RPC_PUBLICO_TIMEOUT` (1800 s por defecto).
