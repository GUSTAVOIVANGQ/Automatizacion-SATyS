# Estado de implementación del backlog de producción SATyS

Versión: `2026.08.17-portable-oci-api-v1-8082`.

Este documento sirve como trazabilidad entre `BACKLOG_PRODUCCION_SATYS.md` y el código de la release.

## Alcance del backlog recibido

El backlog entregado contiene tareas concretas para las **Fases 1, 2, 3, 5 y 6**. El texto menciona Fase 0 y Fase 4 en el orden recomendado, pero no incluye el cuerpo ni criterios de esas fases. También hace referencia a una **Fase 1.1 de autenticación** que no está definida en el archivo recibido. No se inventó un mecanismo de autenticación ni tareas de modularización ausentes del documento.

## Fase 1 — Seguridad / bloqueantes

- **1.2 TLS / reverse proxy: HECHO.** `deploy/nginx-satys.conf` termina TLS y hace `proxy_pass` a `127.0.0.1:8082`. La ejecución clásica de Uvicorn escucha en `127.0.0.1`; Docker publica el puerto del contenedor exclusivamente en `127.0.0.1:8082` del host.
- **1.4 `.gitignore`: HECHO.** Se eliminó la exclusión genérica `*.html`; los artefactos HTML de diagnóstico se aíslan en `debug_html/`.
- **Referencia a 1.1 auth: SIN ESPECIFICACIÓN.** Swagger/ReDoc quedan habilitados y deben colocarse detrás del mecanismo institucional de autenticación cuando éste sea definido.

## Fase 2 — Documentación

- **2.1: HECHO.** Se incorporaron los README operativos existentes y se actualizaron referencias de puerto a 8082 donde corresponden al despliegue actual.
- **2.2: HECHO.** `CONTRIBUTING.md` documenta entorno, tests, commits, nuevas Partes y módulos sensibles.
- **2.3: HECHO.** `docs/GLOSARIO.md` cubre Folio, Registro, RPC, `id_solicitante`, `sin_operador`, Internos IFT, DEPI y columna 1711.
- **2.4: HECHO.** `CHANGELOG.md` centraliza el historial en formato Keep a Changelog.
- **2.5: HECHO.** `docs/ARQUITECTURA.md` contiene Mermaid con pipeline, API, timer y nodos de datos.

## Fase 3 — Calidad, infraestructura y Docker

### 3A

- **3.1: IMPLEMENTADO.** `pyproject.toml` configura Ruff lint/format y `requirements-dev.txt` fija Ruff. `scripts/calidad.sh` y CI ejecutan las verificaciones. La release fue validada en este entorno con `py_compile`, `compileall` y la suite de tests; el binario Ruff no está disponible en el sandbox de empaquetado, por lo que su ejecución real queda a cargo del CI/entorno de desarrollo que instala `requirements-dev.txt`.
- **3.2: HECHO.** `requirements-linux.in` + `requirements-linux.lock.txt` fijan dependencias; `scripts/actualizar_lock.sh` regenera el lock con `pip-compile`.
- **3.3: HECHO.** `.python-version` = 3.11.

### 3B

- **3.4: HECHO.** `Dockerfile` multi-stage sobre imagen Playwright compatible.
- **3.5: HECHO.** `.dockerignore` excluye datos, secretos, sesiones y runtime.
- **3.7: HECHO.** `HEALTHCHECK` usa `/api/health`, alias estable de salud.

### 3C

- **3.8: HECHO.** `docker-compose.yml` define `satys-api` persistente y `satys-worker` efímero. El host sólo publica `127.0.0.1:8082`.

### 3E

- **3.14: HECHO.** `.github/workflows/ci.yml` ejecuta Ruff, tests, prueba del guard diario, construye imagen Docker y vuelve a ejecutar tests dentro de ella.
- **3.15: IMPLEMENTADO HASTA EL LÍMITE DE INFRAESTRUCTURA.** `scripts/publicar_imagen.sh` publica la etiqueta de `VERSION` al registro indicado en `SATYS_REGISTRY`. El backlog exige confirmar Harbor/Nexus/GitLab del CRT/IFT; esa selección no aparece en los materiales recibidos y no puede fijarse sin infraestructura real.

### 3F

- **3.16: HECHO.** Docker es la ruta principal en `README.md` / `DESPLIEGUE_NUEVO.md`; venv+dnf queda como respaldo.
- **3.17: HECHO.** `scripts/rollback_docker.sh` y `scripts/desplegar_docker.sh` conservan/restauran imagen previa.

## Fase 5 — API documentada

- **5.1: HECHO.** `api_models.py` define modelos Pydantic y las rutas JSON usan `response_model`. Descargas/stream conservan sus tipos de respuesta binario/texto.
- **5.2: HECHO.** Las 26 rutas `/api/v1` tienen `summary`, `description` y `tags`.
- **5.3: HECHO.** `/api/v1/...` es canónico; `/api/...` queda como alias temporal fuera del OpenAPI.
- **5.4: HECHO.** `docs/API.md` documenta las 26 rutas, parámetros, curl, respuesta y errores, más el flujo manual completo.
- **5.5: HECHO.** `ErrorResponse {detail, code}` se aplica a `HTTPException` y errores de validación 422.
- **5.6: PARCIAL POR ESPECIFICACIÓN AUSENTE.** `/docs` y `/redoc` están habilitados y documentados. La parte "detrás de auth de Fase 1.1" no puede materializarse porque el backlog recibido no define Fase 1.1.

## Fase 6

- **6.1: HECHO.** `/api/v1/version` devuelve `VERSION`, hash de commit y fuente del hash; `/api/version` se conserva como alias.

## Mecanismos que se preservaron

- `proceso_lock.py` y liberación en `finally` no fueron removidos.
- `scripts/preparar_release.py` conserva manifest SHA-256, verificación de contenido y exclusión de secretos.
- `config/configuracion_local.json` sigue fuera de la imagen y del release sanitizado; Docker lo monta como archivo de runtime.
