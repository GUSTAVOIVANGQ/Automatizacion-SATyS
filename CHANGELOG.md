## 2026.08.17 - UI2 (documentación sincronizada)

- `/docs` usa el mismo tema claro/oscuro que el panel principal mediante `localStorage["theme"]`.
- Tema claro por defecto; se eliminó la dependencia de `prefers-color-scheme` que podía forzar documentación oscura.
- Se añadió selector de tema en la cabecera de documentación; el cambio también se refleja al volver al panel.
- Swagger UI hereda paleta, superficies, bordes y tipografía compatibles con la interfaz principal.

## 2026.08.17-portable-oci-api-v1-8082-ui1

- Swagger `/docs` recibe una cabecera SATyS, navegación al panel, ReDoc/OpenAPI y estilos responsivos.
- El dashboard añade botón **API Docs** en la cabecera.
- La ruta `/` deja de aparecer como endpoint de API en OpenAPI.
- Documentación agrupada con descripciones por tags.
- Podman rootless permite `SATYS_API_BIND` y `SATYS_API_NETWORK`; perfil RHEL usa `slirp4netns:enable_ipv6=false`.
- Build Podman usa formato Docker para conservar `HEALTHCHECK`.
- Se incorpora el aislamiento correcto del test de `SATYS_API_ALLOW_MANUAL`.

# Changelog

## [2026.08.17-portable-oci-api-v1-8082]

### Added
- Runtime portable configurable por `.env`.
- Docker Compose para laptops/CI y Podman directo para RHEL.
- `bootstrap_portable.sh`, `doctor_portable.sh` y wrappers `satys.sh`.
- Perfil de migración para `srvmbcudaqa01` sin copiar datos operativos.
- Overrides por entorno para credenciales y rutas.

### Changed
- Las rutas institucionales dejan de estar codificadas en el despliegue principal.
- La carpeta compartida dentro de contenedores es estable (`/shared`) y el host la decide.
- API pública local permanece en `127.0.0.1:8082`.


Todos los cambios relevantes de Automatización SATyS se documentan aquí siguiendo el formato de Keep a Changelog.

## [2026.08.17-produccion-api-v1-docker-8082] - 2026-08-17

### Added
- Despliegue estándar con Docker/Compose y `satys-worker` efímero + `satys-api` persistente.
- Reverse proxy nginx TLS a `127.0.0.1:8082`.
- Lock reproducible `requirements-linux.lock.txt`, configuración Ruff y CI con pruebas dentro de la imagen.
- API canónica `/api/v1`, modelos Pydantic, metadatos OpenAPI, errores normalizados y documentación `docs/API.md`.
- Endpoint `/api/v1/version` con `VERSION` y commit de build cuando está disponible.
- Documentación de contribución, glosario, arquitectura y rollback Docker.

### Changed
- El puerto del panel/API queda unificado en `8082`.
- `web/templates/index.html` deja de ser ignorado por `.gitignore`.
- Docker es la ruta principal de instalación; `venv` + `dnf` queda como respaldo.

### Security
- Los datos operativos, sesiones y `config/configuracion_local.json` siguen excluidos de imágenes y releases.
- La API se publica en el host únicamente por `127.0.0.1:8082`; nginx es el punto de entrada TLS.

## [2026.08.14-internos-ift-paralelo] - 2026-08-14

### Added
- Procesamiento paralelo de las seis bandejas de Internos IFT.
- Navegación robusta a `Administración solicitudes +TyS/SIGEDO/Internos IFT` y smoke test específico.

## [2026-07-22]

### Fixed
- Espera robusta de la tabla SATyS ante animaciones/cargas tardías y estados DataTables.

## [2026-07-21]

### Fixed
- Reintentos de extracción y validación de descargas incompletas.

## [2026-07-20]

### Fixed
- Protección de ejecución diaria única para evitar múltiples notificaciones/corridas normales en la misma fecha.

## [2026-07-17]

### Changed
- Optimización del catálogo RPC mediante lectura secuencial con `openpyxl.iter_rows(values_only=True)`.
- Búsqueda RPC por comparación exacta `id_solicitante == ID OPERADOR`.

## [2026-07-16]

### Fixed
- Correcciones de extracción y reconciliación documentadas en el historial de releases del proyecto.
