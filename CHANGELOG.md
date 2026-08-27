# 2026.08.27-rpc-diario-correo-unico1

- La corrida diaria fuerza la resolución segura en dos niveles: catálogo Excel
  oficial RPC y, si no resuelve, consulta directa a resultados/autocompletado
  del buscador público RPC.
- Los subprocesos de Internos y Oficialía reciben siempre `--sin-email`; sólo
  `automatizar_registros_diario.py` envía un correo consolidado al final.
- El correo se redujo a indicadores clave: total, exitosos, fallidos/revisión
  manual, errores, tipos de expediente y operadores resueltos por Excel o web.
- Todos los expedientes sin operador usan ahora el único destino
  `output/_sin_operador/(correos)/<expediente>`; la consolidación final migra
  carpetas heredadas, preserva conflictos con sufijo `__legacy`, verifica la
  copia y nunca modifica `descargas/`.
- Se mantienen en el correo las rutas de `output`, `descargas`,
  `TrámitesCRT.xlsx`, `Folios_Datos_Completos.xlsx` y
  `Folios_Datos_Completos_Internos.xlsx`.
- Se añadieron pruebas de consolidación, clasificación del correo diario y
  llamada única al módulo de notificación.

# 2026.08.27-auditoria-descargas-unificada1

- Unifica la auditoría de completitud para Internos, registros CRT y el modo
  por Folio, sin importar la bandeja de origen.
- Reencola carpetas inexistentes, vacías, sólo JSON, con metadata ausente o
  corrupta, temporales, archivos vacíos, ZIP pendientes, estados parciales,
  recorridos incompletos, conteos inconsistentes, errores o archivos físicos
  faltantes.
- Conserva y procesa todas las carpetas de expedientes existentes, incluso si
  están vacías o parciales; nunca elimina una carpeta bajo `descargas/`.
- Agrega una protección explícita que rechaza cualquier intento futuro de
  eliminar árboles contenidos en la raíz configurada de `descargas/`.

# 2026.08.27-output-sin-json1

- Mantiene `metadata_satys.json`, `metadata_tramite_nuevo.json` y
  `metadata_completo.json` exclusivamente bajo `descargas/`; ninguna ruta de
  operador, `_sin_operador` o `(correos)` vuelve a publicarlos en `output/`.
- Copia y verifica sólo archivos reales no vacíos, excluyendo JSON, temporales
  y archivos auxiliares, incluso dentro de subcarpetas extraídas.
- Depura JSON heredados de `output/` al iniciar el pipeline y admite rutas
  extendidas de Windows para nombres de concesionarios largos.
- La sincronización a DEPI conserva todos los JSON de `descargas/`, pero
  excluye y retira JSON del `output/` local y compartido.

# 2026.08.27-internos-watchdog-excel1

- Sustituye los hilos no terminables de Internos por procesos aislados con un
  heartbeat por Folio y timeout configurable mediante `--timeout-registro`.
- Termina el árbol completo del worker y Chromium cuando deja de avanzar, y
  reencola únicamente las parejas bandeja/Folio que siguen incompletas.
- Propaga `--timeout-registro` y `--reintentos-registro` desde el pipeline manual
  y el monitor diario; dos reintentos equivalen a tres intentos totales.
- Omite objetivos locales que ya superan la auditoría estricta de metadata y
  archivos, evitando descargas repetidas.
- Genera `Folios_Datos_Completos_Internos.xlsx` de forma atómica, lo reabre para
  validar hojas, encabezados, filas y objetivos, e incluye como `FALTANTE` cada
  pareja solicitada que no produjo metadata completa.

# 2026.08.26-correos-destino-exclusivo1

- Clasifica cualquier `folio_opc` que empiece con `CORREO`, no sólo
  `CORREO-2408`.
- Usa como único destino `output/_sin_operador/(correos)/<expediente>` en los
  flujos de Administración de solicitudes, Trámites Nuevos, Enlace de
  Oficialía de Partes e Internos.
- La clasificación CORREO tiene prioridad sobre una coincidencia RPC y también
  actualiza con esa ruta el Excel y los reportes.
- Fusiona, verifica y retira copias anteriores del mismo expediente ubicadas en
  `_sin_operador`, en el antiguo `sin_operador_CORREO` o en la ruta calculada
  para el operador.

# 2026.08.26-rpc-razones-sociales-multiples1

- Separa y resuelve individualmente las razones sociales completas que SATyS
  enumera en un mismo expediente, conservando su orden original.
- Construye una sola carpeta con todas las parejas `ID_OPERADOR_nombre`; si una
  razón carece de ID verificable la marca `sin_id_nombre` sin inventar datos.
- Registra en la auditoría CSV el arreglo completo de operadores y las razones
  que hayan quedado sin ID.
- Admite rutas conjuntas largas en Windows mediante rutas extendidas durante la
  creación y copia de carpetas.

# 2026.08.26-rpc-resolucion-segura2

- Recupera en Internos el concesionario desde la sexta columna de `texto_fila`
  cuando `metadata_satys.json` no contiene `id_solicitante` ni nombre.
- Corrige descargas legacy que guardaron `folio` y `registro` como el valor fijo
  `100`: usa `folio_tabla_internos` como identificador real del expediente.
- Después del Excel local consulta la sección actual de resultados
  `searchConcesiones` y usa `searchBP` como segundo respaldo, con caché por
  nombre para no repetir peticiones durante una corrida.
- Conserva el Excel RPC local aunque sea antiguo; sólo intenta reemplazarlo con
  `--rebuild-catalogo` o cuando no existe un archivo local.
- Normaliza acentos, puntuación, espacios y mayúsculas; también admite cambios
  de sufijo societario y variantes de similitud muy alta sólo si hay un único
  ID y margen suficiente frente al siguiente candidato.
- Conserva en el CSV la mejor similitud y el margen de los casos rechazados;
  siguen pendientes y nunca se organizan automáticamente con ese dato.
- Agrega `--internos-registros CSV` para procesar exclusivamente las carpetas
  locales de los folios numéricos indicados, sin descargar otras bandejas.
- Al resolver una revisión anterior, fusiona sus archivos con la carpeta final,
  verifica la copia y retira únicamente esa subcarpeta de `_sin_operador`.

# 2026.08.26-rpc-resolucion-segura1

- Mantiene como primera evidencia `id_solicitante == ID OPERADOR` del Excel
  local y agrega nombre exacto normalizado cuando el ID está ausente.
- Usa el endpoint público `searchBP` del RPC como respaldo actual para filas
  ausentes del Excel o catálogos que no puedan abrirse.
- Rechaza coincidencias fuzzy y nombres exactos asociados a varios IDs; los
  conserva en `_sin_operador` con diagnóstico y candidatos.
- Organiza cada expediente en `output/<ID>_<nombre>/<REGISTRO>/`, incluye los
  JSON y evita colisiones entre trámites del mismo operador.
- Genera CSV fechados de auditoría y pendientes, además de un
  `sin_operador_<modo>_ultimo.csv` estable.

# 2026.08.21-portable-oci-api-v1-8082-folio-modal1

- Soporta anexos de Internos cuyo primer botón gris `VER DOCUMENTO` abre la
  ventana morada `Archivo PDF`: detecta el modal, pulsa su segundo
  `VER DOCUMENTO`, captura la pestaña emergente y descarga la URL autenticada.
- Cierra el modal intermedio después de cada intento para que no bloquee el
  siguiente documento ni altere los índices de los botones de la tabla.
- Conserva sin cambios los flujos que descargan directamente o abren el PDF
  con el primer clic; ambos caminos tienen pruebas de regresión.
- El modo puntual `--folio-internos` omite el merge histórico completo hacia
  DEPI después de generar Excel y `output/`, por lo que la terminal termina al
  concluir la validación local; el correo continúa deshabilitado.
- El lanzador PowerShell resuelve `python.exe` a una ruta absoluta cuando no
  existe un entorno virtual ni se definió `SATYS_PYTHON`.

## 2026.08.21-portable-oci-api-v1-8082-folio1

- Agrega `--folio-internos FOLIO` para ejecutar el recorrido completo de un
  único Folio: inventario de seis bandejas, descarga de datos y anexos, RPC,
  organización en `output/` y escritura en la hoja `Internos` del Excel.
- El modo individual conserva todas las parejas `(bandeja, folio)`, fuerza el
  reprocesamiento aunque exista evidencia previa y deshabilita siempre el
  correo electrónico.
- Valida al finalizar que cada aparición del Folio tenga resultado, que
  `TrámitesCRT.xlsx` contenga el Folio y que la carpeta declarada en `output/`
  exista; una salida parcial devuelve código distinto de cero.
- Incluye lanzadores locales para PowerShell/Linux y el comando portable
  `scripts/satys.sh folio NUMERO` para Docker o Podman.

## 2026.08.20-portable-oci-api-v1-8082-internos-audit1

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
