# SATyS — parche de las tres tareas (29 de julio de 2026)

## Qué corrige

### 1. Corridas ausentes del 28 y 29 de julio

El proyecto recibido todavía tenía tres referencias operativas a la ubicación
anterior `/depi/DEI_DATOS/SATyS`:

- `RequiresMountsFor=/depi/DEI_DATOS`;
- `ExecStartPre` contra `/depi/DEI_DATOS/SATyS`;
- `rutas.carpeta_compartida=/depi/DEI_DATOS/SATyS`.

Con el nuevo recurso `//172.17.47.102/CRT_Recurso_DGP` montado en `/depi/dgp`,
la unidad puede fallar antes de arrancar Python. En ese caso no se crea el log
diario. El diagnóstico incluido permite confirmarlo en el journal.

El parche cambia la precondición a un montaje real en `/depi/dgp`, actualiza la
configuración existente sin tocar credenciales y evita escribir accidentalmente
en un directorio `/depi` local cuando CIFS no está montado.

El timer conserva `Persistent=false`. Por ello, arreglar la ruta no recupera por
sí solo una corrida ya perdida: usa `--run-now` una vez después del despliegue.
La extracción vuelve a consultar todos los Registros actuales y compara contra
el Excel, por lo que procesa los que sigan pendientes.

### 2. Carpeta especial para `CORREO-2408`

Cuando `metadata_satys.json.folio_opc` empieza con `CORREO-2408` y no existe una
coincidencia RPC exacta, los archivos y la columna `Ruta` usan:

```text
sin_operador_CORREO\<Registro-o-identificador>
```

Los demás casos sin operador continúan en `_sin_operador`.

### 3. Reconciliación completa de `TrámitesCRT.xlsx`

Cada corrida diaria ejecuta `reconciliar_metadata_global.py`, incluso cuando no
hay Registros nuevos. El proceso:

1. escanea todos los `metadata_satys.json` y `metadata_tramite_nuevo.json`;
2. cruza exactamente `id_solicitante` con `ID OPERADOR` del catálogo RPC más reciente;
3. recalcula `Ruta` para todos los Registros encontrados;
4. actualiza/sobrescribe los campos automáticos del Excel maestro;
5. conserva columnas manuales y crea un respaldo antes del reemplazo atómico;
6. falla sin reemplazar rutas cuando no existe un catálogo RPC válido.

En el Excel enviado se observaron 1,369 Registros únicos, 305 rutas bajo
`_sin_operador` y ninguna Ruta vacía. Esto indica rutas potencialmente obsoletas,
no celdas vacías. La clasificación `CORREO-2408` solo puede hacerse con los JSON
del servidor, porque el folio OPC no está en ese Excel.

## Contenido del paquete

- `diagnosticar_satys_28_29.sh`: diagnóstico de montaje, systemd, journal y rutas antiguas.
- `aplicar_correcciones_satys_20260729.sh`: instalación transaccional.
- `revertir_correcciones_satys_20260729.sh`: restauración desde respaldo.
- `payload/`: únicamente código y pruebas; no contiene secretos ni datos productivos.
- `CAMBIOS_TAREAS_20260729.diff`: diferencias técnicas.
- `SHA256SUMS`: integridad de los archivos del paquete.

## 1. Diagnóstico previo

```bash
unzip -q parche-satys-3-tareas-20260729.zip -d /tmp/satys-fix-20260729
cd /tmp/satys-fix-20260729/parche-satys-3-tareas-20260729
sha256sum -c SHA256SUMS

sudo bash diagnosticar_satys_28_29.sh \
  | tee diagnostico_satys_20260729.txt
```

Busca especialmente en la salida:

```text
ExecStartPre=.../depi/DEI_DATOS/...
status=1/FAILURE
Failed at step EXEC or ExecStartPre
carpeta_compartida: /depi/DEI_DATOS/SATyS
```

Y confirma el nuevo montaje:

```bash
findmnt -T /depi/dgp
mountpoint /depi/dgp
test -w /depi/dgp && echo ESCRIBIBLE
```

La fuente esperada es `//172.17.47.102/CRT_Recurso_DGP` y el destino
`/depi/dgp`.

## 2. Despliegue recomendado

El siguiente comando presupone que dentro del recurso debe existir una carpeta
`SATyS`, igual que en la estructura anterior:

```bash
sudo bash aplicar_correcciones_satys_20260729.sh --run-now
```

Equivale a:

```text
punto de montaje = /depi/dgp
sincronización   = /depi/dgp/SATyS
proyecto         = /data/gustavo.garcia/satys/Automatizacion-SATyS
```

Si `TrámitesCRT.xlsx`, `output/` y `descargas/` deben quedar directamente en la
raíz del recurso, usa explícitamente:

```bash
sudo bash aplicar_correcciones_satys_20260729.sh \
  --shared-dir /depi/dgp \
  --run-now
```

El instalador:

- exige que `/depi/dgp` sea un montaje activo y escribible por el usuario SATyS;
- se niega a trabajar durante una corrida activa;
- detiene temporalmente el timer y la API;
- respalda código, configuración, unidad systemd y `TrámitesCRT.xlsx`;
- conserva las credenciales y solo cambia `rutas.carpeta_compartida`;
- instala el código y ejecuta 19 pruebas;
- habilita de nuevo el timer;
- reconstruye el Excel con los JSON del servidor;
- sincroniza el Excel corregido y `sin_operador_CORREO`;
- inicia una corrida de recuperación con `--run-now`.

Una falla de instalación o pruebas revierte automáticamente. Si falla únicamente
la reconciliación real por falta del catálogo RPC o metadata, el código queda
instalado, no se inventan rutas y el comando termina con código `3` para que se
revise el resumen.

## 3. Seguimiento y validación

```bash
systemctl status satys-diario.timer satys-diario.service --no-pager -l
systemctl list-timers satys-diario.timer --all --no-pager
journalctl -fu satys-diario.service
```

Después de la reconciliación:

```bash
cat /data/gustavo.garcia/satys/Automatizacion-SATyS/logs/reconciliacion_global_ultimo.json

find /data/gustavo.garcia/satys/Automatizacion-SATyS/output/sin_operador_CORREO \
  -maxdepth 2 -type f | head
```

El JSON correcto debe contener:

```json
{
  "ok": true,
  "estadisticas": {
    "metadata": 1,
    "rpc_ok": 1,
    "sin_operador_correo": 1
  },
  "reconciliacion": {
    "routes_blank": 0
  }
}
```

Los valores reales serán mayores; lo importante es `ok: true` y
`routes_blank: 0`.

Para comprobar la ruta configurada sin mostrar contraseñas:

```bash
python3 - <<'PY'
import json
p='/data/gustavo.garcia/satys/Automatizacion-SATyS/config/configuracion_local.json'
print(json.load(open(p, encoding='utf-8'))['rutas']['carpeta_compartida'])
PY
```

## 4. Reversión

El instalador imprime la ruta exacta del respaldo. Para revertir el último:

```bash
sudo bash revertir_correcciones_satys_20260729.sh
```

O uno concreto:

```bash
sudo bash revertir_correcciones_satys_20260729.sh \
  /data/gustavo.garcia/satys/Automatizacion-SATyS/respaldos_patch_20260729/AAAAMMDD_HHMMSS
```

La reversión no elimina archivos copiados no destructivamente a
`sin_operador_CORREO`; hacerlo automáticamente podría borrar evidencia o trabajo
posterior.

## Nota v2: registro del timer en servidores con SELinux

La versión v2 instala `satys-diario.timer` con `install` y aplica `restorecon`.
Esto evita que una unidad copiada desde `/tmp` conserve el contexto SELinux de
temporal y sea reportada por systemd como inexistente.
