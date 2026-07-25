# Aplicar parche de optimización RPC

Este parche corrige la lectura lenta del catálogo RPC. No elimina ni reemplaza
las descargas, los Excel de trabajo, los logs ni la configuración local.

## Ubicación compartida del parche

```text
/depi/DEI_DATOS/SATyS/satys_fullstack_montaje_depi/Automatizacion-SATyS/parche-satys-optimizacion-rpc-20260717.zip
```

## 1. Detener temporalmente la corrida diaria

```bash
sudo systemctl stop satys-diario.timer
sudo systemctl stop satys-diario.service 2>/dev/null || true
```

La UI puede permanecer activa porque el parche no cambia sus endpoints.

## 2. Crear respaldo y aplicar el parche

```bash
cd /data/gustavo.garcia/satys

STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "respaldos_rpc_${STAMP}"

cp -a Automatizacion-SATyS/buscar_concesionario.py \
  "respaldos_rpc_${STAMP}/buscar_concesionario.py"
cp -a Automatizacion-SATyS/README.md \
  "respaldos_rpc_${STAMP}/README.md"

ORIGEN_DEPI=/depi/DEI_DATOS/SATyS/satys_fullstack_montaje_depi/Automatizacion-SATyS
PATCH_ZIP="$ORIGEN_DEPI/parche-satys-optimizacion-rpc-20260717.zip"

rm -rf /tmp/satys-rpc-patch
mkdir -p /tmp/satys-rpc-patch
unzip -q "$PATCH_ZIP" -d /tmp/satys-rpc-patch

cp -a /tmp/satys-rpc-patch/Automatizacion-SATyS/. \
  /data/gustavo.garcia/satys/Automatizacion-SATyS/

sudo chown -R gustavo.garcia:wheel \
  /data/gustavo.garcia/satys/Automatizacion-SATyS
```

## 3. Validar sintaxis

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS

/data/gustavo.garcia/satys/venv/bin/python \
  -m py_compile \
  buscar_concesionario.py \
  scripts/validar_catalogo_rpc.py
```

## 4. Medir el catálogo RPC real

El validador localiza automáticamente el XLSX más reciente:

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  scripts/validar_catalogo_rpc.py
```

Para comprobar el mismo archivo que antes produjo 9,166 operadores:

```bash
/data/gustavo.garcia/satys/venv/bin/python \
  scripts/validar_catalogo_rpc.py \
  --esperados 9166
```

Si el portal publicó un catálogo más reciente, la cantidad puede haber cambiado;
en ese caso ejecuta la validación sin `--esperados` y revisa que termine en
segundos, no en horas.

## 5. Reactivar la programación diaria

```bash
sudo systemctl enable --now satys-diario.timer
systemctl list-timers --all satys-diario.timer
```

No es necesario reinstalar dependencias ni recrear el entorno virtual.

## 6. Prueba manual opcional

```bash
sudo systemctl start --no-block satys-diario.service
sudo journalctl -u satys-diario.service -f -o cat
```

En el log debe aparecer una línea similar a:

```text
Catálogo RPC cargado ... 9166 operadores en N.NN segundos
```
