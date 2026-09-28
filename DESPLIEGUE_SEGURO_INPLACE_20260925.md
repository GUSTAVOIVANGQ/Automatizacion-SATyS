# Despliegue seguro in-place — SATyS 2026.09.25

Versión: `2026.09.25-redeploy-seguro-rutas-q3q4-2`

## Objetivo

Mantener como única ruta activa y persistente:

`/data/gustavo.garcia/satys/Automatizacion-SATyS`

La release reemplaza/actualiza **código**, pero conserva en esa misma ruta:

- `.env`
- `config/configuracion_local.json`
- `sesion_guardada.json`
- `TrámitesCRT.xlsx`
- `descargas/`
- `output/`
- `logs/`
- `runs/`
- `exports/`
- `base_de_datos_rpc/`
- `registros_diarios/`
- `registros_fallidos/`
- `debug/` y `Screenshots/` si existen

La release no lleva credenciales ni Excel productivo. CI/CD bajo `.github/` se incluye como referencia, pero no es necesario para producción.

## Qué corrige esta release

1. **Internos diarios:** si el monitor genera 45 objetivos, Partes 3–4 sólo pueden procesar esos pares exactos `bandeja + folio`; ya no recorren miles de carpetas históricas locales.
2. **Turnados diarios:** Partes 3–4 sólo pueden procesar los `1711` incluidos en el archivo de registros de esa corrida.
3. **Excel conservador:** una fila existente sólo completa campos vacíos. No reemplaza solicitante, representante, asunto, tipo, fechas ni marcas ya validadas. Las marcas R001–R027 sólo se agregan si existe evidencia; nunca se limpian masivamente.
4. **Ruta protegida:** una Ruta canónica existente nunca se degrada a `_sin_operador` por falla temporal del RPC. Sólo se permite promoción de `_sin_operador` a una Ruta canónica segura. La excepción explícita `CORREO` conserva su clasificación bajo `_sin_operador/(correos)`.
5. **RPC:** un XLSX RPC nuevo corrupto se ignora y se usa el catálogo local válido anterior. Si no hay catálogo válido, se preservan las Rutas canónicas ya existentes.
6. **Reconciliación global:** deja de reconstruir masivamente el maestro y sólo intenta promover Rutas pendientes de forma conservadora.
7. **Timeouts:** reconciliación y reparación RPC pública usan 3600 s por defecto/perfil del servidor.
8. **Internos:** 10 workers en defaults, `.env`, contenedor y diaria.
9. **Q3/Q4:** mantiene las reglas acordadas:
   - `01-oct-2026 .. 15-dic-2026` -> `2026Q3`
   - `01-ene-2027 .. 31-mar-2027` -> `2026Q4`
   - enero–marzo 2026 no participa
   - conserva `output/<Ruta base>` y crea además `output/2026Q3|2026Q4/<Ruta base>`
   - `Ruta` del Excel se prefija únicamente después de publicación DEPI satisfactoria
   - conserva las excepciones `_sin_operador` y `(correos)`
   - JSON permanece sólo en `descargas`, no se publica en `output`

## Red

`satys.ift.org.mx` sólo necesita ser accesible desde el entorno donde corre la automatización. En este caso los procesos Playwright corren en `srvmbcudaqa01`, dentro de la red del trabajo. No es necesario que el sitio SATyS sea accesible desde la red de casa para la ejecución automática; sí necesitas la red/VPN corporativa para administrar el servidor si SSH no está publicado fuera de ella.

## Antes de desplegar

No ejecutes una corrida diaria manual ni `smoke`. Verifica:

```bash
systemctl is-active satys-container-diario.service
systemctl is-active satys-container-diario.timer
systemctl is-active satys-container-api.service
podman ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

Si `satys-container-diario.service` está `active` o `activating`, espera a que termine.

## Transferencia desde Windows

Desde PowerShell, dentro de la red del trabajo:

```powershell
scp .\Automatizacion-SATyS-2026.09.25-redeploy-seguro-rutas-q3q4-2.tar.gz gustavo.garcia@172.17.42.163:/data/gustavo.garcia/satys/
scp .\Automatizacion-SATyS-2026.09.25-redeploy-seguro-rutas-q3q4-2.tar.gz.sha256 gustavo.garcia@172.17.42.163:/data/gustavo.garcia/satys/
```

Si vas a restaurar el Excel recuperado, transfiérelo también pero **no lo pongas todavía como maestro**:

```powershell
scp .\TrámitesCRT_recuperado_seguro_20260925.xlsx gustavo.garcia@172.17.42.163:/data/gustavo.garcia/satys/
```

## Verificar y extraer staging

```bash
cd /data/gustavo.garcia/satys
sha256sum -c Automatizacion-SATyS-2026.09.25-redeploy-seguro-rutas-q3q4-2.tar.gz.sha256
rm -rf stage-20260925
mkdir stage-20260925
tar -xzf Automatizacion-SATyS-2026.09.25-redeploy-seguro-rutas-q3q4-2.tar.gz -C stage-20260925
cd stage-20260925/Automatizacion-SATyS
bash scripts/preflight_despliegue.sh
```

El preflight debe terminar en `OK` y mostrar 10 workers.

## Restauración recomendada del Excel dañado del 25-sep

El archivo recuperado entregado junto con esta release se construyó de forma conservadora a partir del maestro anterior del 08-sep y del maestro actual del 25-sep: restaura las filas históricas preexistentes y conserva los registros nuevos detectados entre ambos archivos. **Revísalo antes de producción**, especialmente si hubo ediciones manuales legítimas posteriores al 08-sep que no estén reflejadas en los archivos proporcionados.

Con timer y API detenidos:

```bash
ROOT=/data/gustavo.garcia/satys/Automatizacion-SATyS
STAMP=$(date +%Y%m%d_%H%M%S)
cp -a "$ROOT/TrámitesCRT.xlsx" "$ROOT/TrámitesCRT.pre-recuperacion-$STAMP.xlsx"
cp -a /data/gustavo.garcia/satys/TrámitesCRT_recuperado_seguro_20260925.xlsx "$ROOT/TrámitesCRT.xlsx"
```

No copies el Excel recuperado a DEPI hasta verificarlo. Después de validarlo, la siguiente sincronización diaria lo publicará; si necesitas publicación inmediata, haz primero un respaldo independiente del Excel compartido.

## Despliegue del código

Desde el staging extraído:

```bash
cd /data/gustavo.garcia/satys/stage-20260925/Automatizacion-SATyS
bash scripts/desplegar_inplace_seguro_20260925.sh
```

El script:

- valida la release limpia;
- aborta si la diaria está en ejecución;
- detiene timer y API;
- comprueba que API y diaria de systemd ya apuntan a `/data/gustavo.garcia/satys/Automatizacion-SATyS`;
- copia código al directorio activo **sin `--delete`**;
- conserva runtime, secretos, Excel y datos;
- fija 10 workers y timeouts de 3600 s;
- construye `satys-api:2026.09.25-redeploy-seguro-rutas-q3q4-2` **desde el staging limpio**, no desde el runtime persistente;
- levanta API/UI y comprueba versión;
- sólo entonces reactiva el timer de las 01:00;
- no ejecuta smoke ni inicia Internos por separado.

## Verificación final

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
cat VERSION
grep -E '^SATYS_(IMAGE|INTERNOS_WORKERS|RECONCILIACION_GLOBAL_TIMEOUT|SIN_OPERADOR_RPC_PUBLICO_TIMEOUT|TRIMESTRES_2026_TIMEOUT|RUNTIME_DIR|SHARED_HOST_DIR)=' .env
curl -fsS http://127.0.0.1:8082/api/v1/version; echo
curl -sS -o /dev/null -w 'UI_HTTP=%{http_code}\n' http://127.0.0.1:8082/
systemctl list-timers satys-container-diario.timer --all --no-pager
podman inspect satys-api --format '{{range .Mounts}}{{println .Source " -> " .Destination}}{{end}}'
```

Debe aparecer:

- versión `2026.09.25-redeploy-seguro-rutas-q3q4-2`;
- `SATYS_INTERNOS_WORKERS=10`;
- runtime `/data/gustavo.garcia/satys/Automatizacion-SATyS`;
- shared `/depi/dgp/DEI_DATOS/SATyS -> /shared`;
- API activa, timer activo, diaria inactiva hasta las 01:00.

## Primera corrida automática

No la lances manualmente. Después de la primera corrida de las 01:00 revisa:

```bash
systemctl status satys-container-diario.service --no-pager -l
LATEST=$(ls -1t /data/gustavo.garcia/satys/Automatizacion-SATyS/logs/monitor_registros_*.log | head -1)
echo "$LATEST"
grep -E 'Filtro seguro|Objetivos Internos nuevos|Partes 3-4 procesarán|RECONCILIAR|REPARAR _SIN_OPERADOR|ORGANIZAR VENTANAS|return_code=' "$LATEST" | tail -100
```

En Internos debe verse un mensaje del tipo:

`Filtro seguro --internos-objetivos: Partes 3-4 procesarán sólo N carpeta(s) objetivo; histórico excluido.`

Nunca debe volver a aparecer una línea que diga que Partes 3–4 conservarán miles de carpetas Internos locales durante una corrida con pocos objetivos.
