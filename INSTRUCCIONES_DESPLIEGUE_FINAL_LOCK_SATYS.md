# Despliegue final SATyS Linux — RPC exacto, Excel consolidado, email y lock seguro

Este paquete incluye los ajustes finales para producción:

1. RPC por comparación exacta `id_solicitante == ID OPERADOR`.
2. Exactitud RPC solo `100%` si existe el ID en el Excel oficial, o `0%` si no existe.
3. Generación de `output/Folios_Datos_Completos.xlsx` con todos los campos de `metadata_satys.json` y `metadata_tramite_nuevo.json`.
4. Notificación final por correo con configuración hardcodeada en `notificar_email.py`.
5. Lock compartido liberado explícitamente al terminar `main_procesar.py` y `automatizar_registros_diario.py`.
6. `automatizar_registros_diario.py` toma el lock externo y llama `main_procesar.py --sin-lock`.

---

## 1. Respaldar versión actual

```bash
sudo systemctl stop satys-diario.service 2>/dev/null || true
sudo systemctl stop satys-api.service 2>/dev/null || true

TS=$(date +%Y%m%d_%H%M%S)
mkdir -p /data/gustavo.garcia/satys/backups
cp -a /data/gustavo.garcia/satys/Automatizacion-SATyS \
      /data/gustavo.garcia/satys/backups/Automatizacion-SATyS_$TS
```

---

## 2. Descomprimir paquete nuevo

Copia el ZIP al servidor, por ejemplo a:

```text
/data/gustavo.garcia/satys/satys_fullstack_registro_download_linux_final_lock.zip
```

Luego:

```bash
cd /data/gustavo.garcia/satys
rm -rf satys_fullstack_registro_download_final
mkdir -p satys_fullstack_registro_download_final
unzip -q satys_fullstack_registro_download_linux_final_lock.zip -d satys_fullstack_registro_download_final
```

El backend queda en:

```text
/data/gustavo.garcia/satys/satys_fullstack_registro_download_final/satys_fullstack_registro_download/satys_backend_linux
```

---

## 3. Reemplazar código operativo sin tocar datos reales

```bash
cd /data/gustavo.garcia/satys

rsync -av --delete \
  --exclude 'descargas/' \
  --exclude 'output/' \
  --exclude 'logs/' \
  --exclude 'runs/' \
  --exclude 'exports/' \
  --exclude 'base_de_datos_rpc/' \
  --exclude 'TrámitesCRT.xlsx' \
  satys_fullstack_registro_download_final/satys_fullstack_registro_download/satys_backend_linux/ \
  Automatizacion-SATyS/
```

Si el Excel maestro está en `/depi/DEI_DATOS/SATyS/TrámitesCRT.xlsx`, no lo reemplaces con el de plantilla.

---

## 4. Validar permisos y variables necesarias

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
source /data/gustavo.garcia/satys/venv/bin/activate

export PLAYWRIGHT_BROWSERS_PATH=/data/gustavo.garcia/satys/playwright-browsers
export SATYS_LOCK_DIR=/data/gustavo.garcia/satys/.lock

mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" "$SATYS_LOCK_DIR"
chmod 775 "$SATYS_LOCK_DIR"
```

Validar escritura:

```bash
echo ok > /depi/DEI_DATOS/SATyS/descargas/test_lock_perm.tmp
rm -f /depi/DEI_DATOS/SATyS/descargas/test_lock_perm.tmp
```

---

## 5. Validar sintaxis

```bash
python -m py_compile \
  main_procesar.py \
  automatizar_registros_diario.py \
  proceso_lock.py \
  buscar_concesionario.py \
  generar_excel_metadata_json.py \
  notificar_email.py \
  descargar_concesiones_rpc.py \
  Parte3_rpc.py \
  Parte4_excel.py
```

---

## 6. Probar lock manualmente

```bash
rm -f /data/gustavo.garcia/satys/.lock/satys_proceso.lock

python main_procesar.py \
  --archivo-registro /data/gustavo.garcia/satys/Automatizacion-SATyS/archivo.txt \
  --workers 1 \
  --timeout-registro 900 \
  --reintentos-registro 0 \
  --workers-reintento 1 \
  --headless \
  --sin-email

ls -la /data/gustavo.garcia/satys/.lock
```

Al terminar, no debe quedar `satys_proceso.lock`.

---

## 7. Probar correo

```bash
python notificar_email.py --test
```

---

## 8. Probar corrida manual pequeña con correo

```bash
python main_procesar.py \
  --archivo-registro /data/gustavo.garcia/satys/Automatizacion-SATyS/archivo.txt \
  --workers 2 \
  --timeout-registro 900 \
  --reintentos-registro 0 \
  --workers-reintento 1 \
  --headless
```

Debe generar o actualizar:

```text
output/Folios_Datos_Completos.xlsx
output/
descargas/
TrámitesCRT.xlsx
```

y enviar el correo final.

---

## 9. Reiniciar servicios

```bash
sudo systemctl daemon-reload
sudo systemctl restart satys-api.service
sudo systemctl reset-failed satys-diario.service
sudo systemctl start satys-diario.service
```

Ver logs:

```bash
journalctl -u satys-diario.service -f
```

---

## 10. Liberación de lock

- `main_procesar.py` libera el lock en `finally` al terminar, fallar, recibir `Ctrl+C` o `systemctl stop`.
- `automatizar_registros_diario.py` libera el lock en `finally` al terminar, fallar, recibir `Ctrl+C` o `systemctl stop`.
- La corrida diaria llama `main_procesar.py --sin-lock` porque el lock ya lo tomó el monitor diario.
- Si ocurre apagón, `kill -9` o reinicio forzado, usa:

```bash
rm -f /data/gustavo.garcia/satys/.lock/satys_proceso.lock
```

solo después de confirmar que no hay procesos vivos:

```bash
ps -ef | grep -E 'main_procesar|automatizar_registros_diario|Parte1_descarga|chromium|playwright' | grep -v grep
```
