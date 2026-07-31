# SATyS — correcciones del 29 de julio de 2026

## 1. Ejecuciones ausentes del 28 y 29 de julio

El servicio de systemd todavía exigía la ruta anterior:

- `RequiresMountsFor=/depi/DEI_DATOS`
- `ExecStartPre=/usr/bin/test -d /depi/DEI_DATOS/SATyS`
- configuración `carpeta_compartida=/depi/DEI_DATOS/SATyS`

Después del cambio del recurso compartido, el montaje real quedó en `/depi/dgp`.
El fallo ocurre en `ExecStartPre`, antes de que Python y el archivo de log diario
puedan iniciar. Por eso puede no existir `monitor_registros_20260728*.log` o
`monitor_registros_20260729*.log`.

La corrección usa:

- punto de montaje: `/depi/dgp`;
- carpeta SATyS predeterminada: `/depi/dgp/SATyS`;
- comprobación real con `mountpoint -q`, no solo existencia del directorio;
- protección para impedir que la sincronización escriba en `/depi` local cuando
  el CIFS no esté montado.

## 2. Separación de CORREO-2408

Cuando `metadata_satys.json.folio_opc` empieza con `CORREO-2408` y no existe
coincidencia RPC exacta, la salida se guarda en:

`output/sin_operador_CORREO/<identificador>`

Los demás trámites sin operador conservan:

`output/_sin_operador/<identificador>`

La regla se aplica a la copia de archivos, al consolidado JSON y a la columna
`Ruta` del Excel maestro. La reconciliación global también migra de manera no
destructiva los CORREO-2408 ya existentes desde `descargas/`.

## 3. Sobrescritura diaria de TrámitesCRT.xlsx

Se agregó `reconciliar_metadata_global.py`. En cada ejecución diaria, incluso
cuando no hay Registros nuevos:

1. escanea todos los `metadata_satys.json` y `metadata_tramite_nuevo.json`;
2. cruza `id_solicitante` exactamente contra el Excel RPC más reciente;
3. recalcula la ruta del operador o la carpeta de revisión manual;
4. genera `output/Folios_Datos_Completos.xlsx`;
5. sobrescribe los campos automáticos de cada Registro en `TrámitesCRT.xlsx`;
6. conserva las columnas manuales y crea respaldo antes de sustituir el maestro.

Si no existe un catálogo RPC válido, la reconciliación falla sin sustituir rutas
por valores indeterminados.

## Validaciones locales

- compilación Python de los archivos modificados;
- validación de sintaxis de scripts Bash;
- 19 pruebas unitarias;
- prueba integral sintética: una ruta `_sin_operador` se corrigió a una ruta RPC
  por `id_solicitante`, un `CORREO-2408` se movió a `sin_operador_CORREO`, y las
  notas manuales del Excel se conservaron.

La prueba real contra SATyS, el CIFS y el catálogo de producción debe ejecutarse
en el servidor.
