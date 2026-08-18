# Contribuir a Automatización SATyS

## Entorno local

Recomendado: Python 3.11 (ver `.python-version`).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

La configuración real vive en `config/configuracion_local.json` y no se versiona. Para desarrollo:

```bash
cp config/configuracion_local.example.json config/configuracion_local.json
chmod 600 config/configuracion_local.json
```

## Calidad y pruebas

```bash
ruff check .
ruff format --check .
python -m unittest discover tests/
bash tests/test_ejecucion_diaria_unica.sh
```

Para aplicar formato:

```bash
ruff format .
ruff check --fix .
```

Antes de enviar cambios relacionados con navegador, ejecutar además `scripts/smoke_internos.py` dentro de la red CRT/IFT.

## Convención de commits

Usar mensajes breves en imperativo con prefijo: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `build:` o `chore:`. Un commit debe representar un cambio revisable y no mezclar refactors con cambios funcionales de SATyS.

## Agregar una nueva "Parte" al pipeline

1. Crear un módulo autocontenido `ParteN_<nombre>.py` con una interfaz clara y sin credenciales embebidas.
2. Integrarlo desde `main_procesar.py` en el orden correcto del pipeline.
3. Hacer que acepte rutas desde la configuración existente y respete `proceso_lock.py`; no crear un segundo mecanismo de lock.
4. Persistir estados/reanudación si la Parte puede tardar varios minutos.
5. Agregar pruebas unitarias y actualizar `docs/ARQUITECTURA.md`, `docs/GLOSARIO.md`, `docs/API.md` si aplica, y `CHANGELOG.md`.
6. Verificar que el release generado por `scripts/preparar_release.py` no contenga secretos ni datos operativos.

## Archivos sensibles

Antes de modificar `Parte1_descarga.py` o `proceso_lock.py`, avisar al **responsable técnico de la automatización SATyS de la Dirección Ejecutiva de Indicadores (DEI)** y coordinar una ventana de smoke test en la red CRT/IFT. `Parte1_descarga.py` concentra la navegación del portal y `proceso_lock.py` evita ejecuciones concurrentes; ambos pueden afectar producción de forma amplia.

## Pull requests / revisión

El cambio se considera listo cuando CI pasa, el diff no agrega secretos, los tests del área modificada están incluidos y el mecanismo de rollback continúa funcionando.
