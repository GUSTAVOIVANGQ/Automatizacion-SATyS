# Despliegue SATyS — Notificación por correo hardcodeada

Esta versión deja la notificación final integrada en `main_procesar.py` y usa configuración hardcodeada en `notificar_email.py`.

## Qué corrige

1. El correo se envía al terminar cualquier corrida de `main_procesar.py`:
   - Corrida manual con `--archivo-registro`.
   - Corrida por folios.
   - Corrida diaria, porque `automatizar_registros_diario.py` invoca `main_procesar.py`.

2. El correo ya no depende de variables de entorno ni de `config_notificacion_email.json`.

3. El correo incluye las salidas principales:
   - `output/Folios_Datos_Completos.xlsx`
   - `output/`
   - `descargas/`
   - `TrámitesCRT.xlsx`

4. El correo muestra resultados con conteos consistentes:
   - Total procesados.
   - Exitosos.
   - Revisión manual / sin operador.
   - Errores.

5. La tabla de registros incluye:
   - Registro/Folio.
   - Estado.
   - Operador.
   - ID solicitante.
   - RPC 100% o 0% por `id_solicitante == ID OPERADOR`.
   - Ruta de output.

## Archivos principales

Reemplazar en producción:

```bash
cp main_procesar.py /data/gustavo.garcia/satys/Automatizacion-SATyS/main_procesar.py
cp notificar_email.py /data/gustavo.garcia/satys/Automatizacion-SATyS/notificar_email.py
```

Si usas el ZIP completo, copia todo el contenido del backend Linux como en el despliegue anterior.

## Configuración de correo

Editar únicamente este bloque dentro de `notificar_email.py`:

```python
EMAIL_ENABLED = True
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
GMAIL_REMITENTE = "..."
GMAIL_APP_PASSWORD = "..."
DESTINATARIOS = [
    "...",
]
```

No se requiere agregar `SATYS_EMAIL_*` en systemd.

## Probar correo únicamente

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
source /data/gustavo.garcia/satys/venv/bin/activate
python notificar_email.py --test
```

Para probar con un destinatario específico sin cambiar el archivo:

```bash
python notificar_email.py --test --to gustavo.garcia@crt.gob.mx
```

## Probar corrida manual con correo

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
source /data/gustavo.garcia/satys/venv/bin/activate
export PLAYWRIGHT_BROWSERS_PATH=/data/gustavo.garcia/satys/playwright-browsers

python main_procesar.py \
  --archivo-registro /data/gustavo.garcia/satys/Automatizacion-SATyS/archivo.txt \
  --workers 2 \
  --timeout-registro 900 \
  --reintentos-registro 0 \
  --workers-reintento 1 \
  --headless
```

## Correr sin correo excepcionalmente

```bash
python main_procesar.py --archivo-registro archivo.txt --sin-email
```

## Seguridad operativa

Como la contraseña queda hardcodeada en `notificar_email.py`, restringir permisos del archivo en Linux:

```bash
chmod 600 /data/gustavo.garcia/satys/Automatizacion-SATyS/notificar_email.py
```
