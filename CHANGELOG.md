# 2026.08.20-portable-oci-api-v1-8082-internos-audit1

- Cambia la unidad de conciliación de Internos de Folio único a
  `(bandeja, folio)`, conservando las apariciones repetidas entre las seis
  bandejas y creando una carpeta auditable por cada pareja.
- Reintenta objetivos con carpeta vacía, metadata parcial, ZIP residual o
  cualquier archivo físico faltante respecto de `metadata_completo.json`.
- Registra y concilia por separado documentos reportados por el portal y
  archivos expandidos desde ZIP; una discrepancia de paginación ya no puede
  terminar como descarga completa.
- Serializa la escritura de `TrámitesCRT.xlsx` entre workers y soporta el
  `EBUSY` de archivos montados individualmente dentro de Linux/OCI sin ocultar
  otros errores de sistema.
- `--no-procesar` genera un inventario rápido y omite la resincronización
  completa de `descargas/` hacia DEPI.
- En la corrida diaria, Internos se procesa antes que Oficialía para que una
  conciliación masiva de varias horas no vuelva a posponer sus descargas.

## 2026.08.18-portable-oci-api-v1-8082-internos12

- Eleva a 12 los navegadores predeterminados de Internos y elimina los topes
  artificiales en CLI, configuración, smoke tests y preflight de servidor.
- Reserva primero dos segmentos por cada bandeja activa y distribuye los slots
  restantes según la carga, sin asignar un mismo Folio a dos navegadores.
- Hace efectivo `SATYS_INTERNOS_WORKERS` dentro de Docker/Podman y parametriza
  la memoria compartida con `SATYS_SHM_SIZE` (6 GB por defecto).
- Evita ciclos infinitos al descomprimir ZIPs: cada archivo fallido se intenta
  una sola vez por proceso y la recursión queda acotada.
- Acorta rutas ZIP largas de forma determinista y portable entre Windows y
  Linux; los ZIPs `121195` y `138146` se validaron completos.
- Reintenta los segmentos de Internos que fallan al cargar el tablero y evita
  aceptar como éxito un segmento vacío con folios asignados.

- Corrige la navegación inicial de Internos IFT en Playwright/RHEL evitando
  `wait_for_function`, cuya inyección fallaba dentro del portal SATyS.
- Los correos de excepción del monitor ahora informan al menos un error y
  dejan de usar el asunto incorrecto "Proceso sin errores - 0 registros".
- Agrega `scripts/satys.sh internos-check` para validar las seis bandejas sin
  descargar documentos ni modificar `TrámitesCRT.xlsx`.
- Activa las seis bandejas por los IDs estables `1` a `6`, evitando que el
  contador pegado al texto, por ejemplo `Recibidos0`, rompa la selección.
- Verifica por separado que SATyS marque el botón como activo, incluso cuando
  el clic provoca que `page.evaluate` devuelva una respuesta vacía.

- Añade modo OCI/Podman `scripts/satys.sh internos` para inventariar exclusivamente Internos IFT y procesar sólo `Folio Internos` nuevos.
- Conserva `daily` sin convertirlo a modo Internos.
- Añade `satys-container-internos.service` para corridas manuales independientes de SSH.
- La API systemd pasa a foreground supervisado (`Restart=on-failure`) en lugar de `active (exited)`.
- Release sanitizada: sin `TrámitesCRT.xlsx`, sesiones, logs, screenshots ni datos operativos.

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
