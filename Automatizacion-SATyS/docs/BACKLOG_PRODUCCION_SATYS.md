# Backlog — SATyS: producción, mantenibilidad y API documentada

Este documento es una lista de tareas pensada para pasársela directamente a un
agente de código (Claude Code, Cursor, etc.) con acceso al repositorio. Cada
tarea trae contexto, archivos afectados y criterio de "hecho" para que el
agente pueda ejecutarla sin depender de más preguntas.

**Cómo usar esto:** pega una fase completa (o una tarea individual) como
prompt al agente, dile que trabaje archivo por archivo y que muestre el diff
antes de aplicar cambios grandes. No le des las 6 fases de una vez — el
proyecto es grande (~19k líneas) y es más seguro revisar por fases.

Orden recomendado: **Fase 0 → 1 → 2 → 3 → 5 → 4 → 6** (la 4, modularizar los
archivos gigantes, conviene hacerla después de tener CI y tests corriendo en
automático, para no romper nada sin red de seguridad).

---

## FASE 1 — Seguridad / bloqueantes de producción (P0)

### 1.2 TLS / reverse proxy

- [ ] Agregar un archivo de ejemplo `deploy/nginx-satys.conf` con proxy_pass
  a `127.0.0.1:8082`, terminación TLS
- [ ] Cambiar el ejemplo del README de `uvicorn ... --host 0.0.0.0` a
  `--host 127.0.0.1` y aclarar que el acceso externo pasa siempre por nginx.

### 1.4 Corrección de `.gitignore`

- [ ] Cambiar la regla `*.html` por algo que no capture
  `web/templates/index.html`. Ejemplo: eliminar la regla genérica y, si el
  objetivo era ignorar reportes HTML generados, usar una carpeta específica
  (`debug_html/` o similar) en vez de `*.html`.

---

## FASE 2 — Documentación de mantenimiento (P1)

- [ ] **2.1** Si los README_*.md de Fase 0.3 existen, incorporarlos al repo.
  Si no existen, quitar las referencias rotas del `README.md` principal o
  reescribir ese contenido dentro del propio `README.md`.
- [ ] **2.2** Crear `CONTRIBUTING.md` con: cómo levantar el entorno local,
  cómo correr los tests (`tests/`), convención de commits, cómo se agrega
  una nueva "Parte" al pipeline, y a quién avisar antes de tocar
  `Parte1_descarga.py` o `proceso_lock.py` (son los módulos más sensibles).
- [ ] **2.3** Crear `docs/GLOSARIO.md` con los términos de dominio que hoy
  solo se entienden leyendo código: Folio, Registro, RPC, id_solicitante,
  "sin_operador", Internos IFT, DEPI, columna `1711` de `TrámitesCRT.xlsx`.
- [ ] **2.4** Crear `CHANGELOG.md` en formato Keep a Changelog, migrando el
  historial disperso que hoy vive como secciones sueltas al final del
  `README.md` ("Optimización del catálogo RPC", "Corrección de múltiples
  correos", etc.).
- [ ] **2.5** Agregar un diagrama de arquitectura (puede ser Mermaid dentro
  de un `.md`) que muestre: SATyS (externo) → Parte1 → Parte3 (RPC) →
  Parte4 (Excel) → panel FastAPI → systemd timer, con las carpetas
  `descargas/`, `output/`, `logs/` como nodos de datos.

---



## FASE 3 — Calidad de código, infraestructura y migración a Docker (P1)

Decisión ya tomada: el proyecto migra a Docker como método estándar de
despliegue. El flujo actual con `venv` + `dnf` queda documentado como
alternativa de respaldo hasta que el despliegue en contenedores esté
validado en el servidor real, no se borra de inmediato.

**Nota técnica:** todo lo que se construye aquí (`Dockerfile`,
`docker-compose.yml`) funciona igual con Podman si en algún momento el
equipo de IT del CRT/IFT lo prefiere sobre Docker Engine en RHEL — es el
mismo archivo, solo cambia el binario que lo ejecuta. No es necesario
decidirlo ahora.

### 3A. Preparación (antes de escribir el Dockerfile)

- [ ] **3.1** Crear `pyproject.toml` con configuración de `ruff` (lint) y
  `black` o `ruff format` (formato). Correrlo sobre todo el repo una vez y
  arreglar lo que salga.
- [ ] **3.2** Congelar dependencias: generar un lockfile real (`pip-compile`
  de `pip-tools`, o exportar `pip freeze` a `requirements-linux.lock.txt`).
  Esto alimenta directamente el `Dockerfile` (instalar desde el lockfile,
  no desde rangos `>=`), así la imagen es reproducible bit a bit.
- [ ] **3.3 (opcional, solo para desarrollo sin Docker)** Agregar
  `.python-version` con `3.11` para quien siga trabajando con `venv` local.

### 3B. Imagen Docker

- [ ] **3.4** Crear `Dockerfile` en la raíz, multi-stage
- [ ] **3.5** Crear `.dockerignore` que excluya `descargas/`, `output/`,
  `logs/`, `runs/`, `base_de_datos_rpc/`, `registros_diarios/`,
  `config/configuracion_local.json`, `.git/` — nada de datos ni secretos
  debe quedar horneado en la imagen.
- [ ] **3.7** Agregar `HEALTHCHECK` en el Dockerfile del servicio API
  apuntando a `/api/health` (coordinar con la Fase 1: esa ruta debe seguir
  siendo la única sin autenticación).

### 3C. Orquestación y volúmenes

- [ ] **3.8** Crear `docker-compose.yml` con dos servicios:
  - `satys-worker`: para la corrida diaria/manual, pensado para invocarse
    como `docker compose run --rm satys-worker ...` (no queda corriendo
    todo el tiempo, igual que hoy con el `oneshot` de systemd).
  - `satys-api`: el panel FastAPI, servicio de larga duración
    (`restart: unless-stopped`), expuesto solo en `127.0.0.1:808` (nunca
    `0.0.0.0` — coordinar con la Fase 1.2 de TLS/nginx).

### 3E. CI/CD

- [ ] **3.14** Crear `.github/workflows/ci.yml` (o el CI interno que
  usen) que en cada push/PR: corra `ruff check`, corra
  `python -m unittest discover tests/`, y además **construya la imagen
  Docker** y corra los tests dentro de ella (`docker run --rm <imagen> python -m unittest discover tests/`) para detectar diferencias entre el
  entorno de CI y el de la imagen real.
- [ ] **3.15** Publicar la imagen en un registro interno (Harbor, Nexus,
  GitLab Container Registry — confirmar cuál tiene disponible el CRT/IFT;
  no hay registro público accesible desde la red interna, así que este
  paso necesita decisión de infraestructura antes de automatizarse) y
  etiquetarla con la misma versión que ya usa `VERSION` /
  `scripts/preparar_release.py` (ej. `satys-api:2026.08.14-internos-ift-paralelo`).

### 3F. Documentación y ruta de reversión

- [ ] **3.16** Actualizar la sección "📦 Instalación" del `README.md`
  para que el flujo con Docker sea el camino principal
  (`docker compose up -d`), dejando el flujo `venv` + `dnf` como sección
  aparte "Instalación sin Docker (alternativa)".
- [ ] **3.17** Definir cómo se hace rollback de la imagen si un despliegue
  falla — hoy `preparar_release.py` restaura automáticamente la versión
  anterior de archivos; con Docker el equivalente es conservar la etiqueta
  de imagen previa y documentar
  `docker compose down && docker compose up -d --no-build` apuntando a la
  etiqueta anterior en caso de fallo.

---

## FASE 5 — Documentar y exponer el panel como servicio API (P1/P2)

Esta fase es la que responde directamente a "que otros programadores lo
entiendan y usen como servicio API".

- [ ] **5.1** Agregar modelos `pydantic.BaseModel` para las respuestas de
  cada endpoint de `satys_api.py` (hoy todos devuelven `dict`/`Any` sueltos)
  y usarlos con `response_model=` en cada ruta. Empezar por los endpoints
  de solo lectura (`/api/health`, `/api/estado`, `/api/archivos`,
  `/api/historial`) antes que los que disparan procesos.
- [ ] **5.2** Agregar `summary=`, `description=` y `tags=` a cada decorador
  `@app.get`/`@app.post` en `satys_api.py` (hay 26 rutas listadas en el
  archivo — agrupar en tags como `estado`, `descargas`, `corridas`,
  `reparación`, `timer`).
- [ ] **5.3** Versionar las rutas: mover todo de `/api/...` a `/api/v1/...`,
  dejando un redirect o alias temporal desde las rutas viejas si algo ya
  las consume.
- [ ] **5.4** Crear `docs/API.md` con, para cada endpoint: método, ruta,
  parámetros, ejemplo de request con `curl`, ejemplo de response en JSON,
  y qué códigos de error puede devolver. Incluir un flujo completo de
  ejemplo: "cómo lanzar una corrida manual desde cero usando la API".
- [ ] **5.5** Estandarizar el formato de error: definir un modelo
  `ErrorResponse {detail: str, code: str}` y usarlo de forma consistente
  en todos los `HTTPException` (hoy varía el shape del `detail`).
- [ ] **5.6** Confirmar que `/docs` (Swagger) y `/redoc` quedan accesibles
  (detrás de la auth de la Fase 1.1) y agregar el link en el `README.md`
  como la referencia "viva" de la API.

---

## FASE 6 — Extras / deuda ya reconocida en el README

- [ ] **6.1** Agregar endpoint `/api/version` que combine `VERSION` +
  hash de commit git actual.

---

## Nota para el agente

El proyecto ya tiene buenas prácticas que **no** hay que romper: el lock
compartido (`proceso_lock.py`) libera siempre en `finally`, el proceso de
release (`scripts/preparar_release.py`) genera manifest con SHA-256 y hace
rollback automático si falla, y las credenciales ya están fuera del código
en `config/configuracion_local.json`. Cualquier cambio debe preservar esos
tres mecanismos tal como están.
