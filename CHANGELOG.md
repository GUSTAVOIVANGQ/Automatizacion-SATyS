# 2026.08.28-definitiva-cierre-seguro-rpc-publico-manual-correos-remitentes-email-post1

- Corrige el correo consolidado: la tarjeta amarilla se renombra a `EN REVISIÓN (X%)` y su cantidad se toma directamente del `TrámitesCRT.xlsx` final. Sólo cuenta filas cuya `Ruta` permanece bajo `_sin_operador`; excluye expresamente `_sin_operador/(correos)` y evita duplicados intermedios de workers/bandejas.
- Los expedientes ya clasificados en `_sin_operador/(correos)` dejan de aparecer como pendientes de revisión en la tabla del correo; se reporta además su cantidad como `Correos clasificados fuera de revisión`.
- Añade `postprocesar_final.py` y el comando `bash scripts/podman_satys.sh postproceso-final`, que ejecuta sin volver a SATyS: completar remitentes desde PDF -> reconciliación global segura -> RPC público + clasificación `(correos)` -> fusión/organización de `output` -> sincronización de `output` y `TrámitesCRT.xlsx` a DEPI -> correo final corregido.
- El postproceso conserva `descargas` intacto, usa el mismo lock global SATyS y tiene timeout global configurable mediante `SATYS_POSTPROCESO_FINAL_TIMEOUT` (7200 s por defecto).
- La sincronización final del postproceso publica únicamente `output/` y `TrámitesCRT.xlsx`, evitando volver a copiar innecesariamente todo `descargas/`.

# 2026.08.28-definitiva-cierre-seguro-rpc-publico-manual-correos-remitentes1

- Antes de la reconciliación global, completa `Solicitante Promovente` y `Representante Legal` sólo cuando estén vacíos o `SIN REMITENTE`, recorriendo todos los PDF del expediente original en `descargas` con la lógica tolerante de `extraer_operador.py`.
- Recorre todos los PDF de cada expediente; si diferentes documentos arrojan valores incompatibles, no inventa ni sobreescribe y deja auditoría para revisión.
- La reconciliación global preserva los valores válidos recién recuperados desde PDF frente a metadata vacío o `SIN REMITENTE`.
- La clasificación `(correos)` reconoce `MEMORANDO`, `MEMORANDUM`, `MEMORÁNDUM`, sufijos y variantes leves del nombre del PDF; `memo.pdf` por sí solo no clasifica.
- Añade el comando manual `bash scripts/podman_satys.sh remitentes-pdf [--dry-run]`.
- Añade `pdfplumber` y sus dependencias fijadas a la imagen OCI.

# 2026.08.28-definitiva-cierre-seguro-rpc-publico-manual-correos1

- Extiende la etapa final/manual `sin-operador-rpc`: después del intento exclusivo con buscador público RPC, vuelve a leer `TrámitesCRT.xlsx` y procesa sólo las filas que todavía mantienen `Ruta` bajo `_sin_operador`.
- Si la fuente original conciliada en `descargas` contiene recursivamente un archivo llamado `MEMORANDUM.pdf` (sin distinguir mayúsculas/minúsculas y tolerando espacios externos en el nombre), clasifica el expediente en `output/_sin_operador/(correos)/<carpeta>`.
- Conserva `descargas` intacto; fusiona archivos históricos sin nombres inventados y después copia desde `descargas`, por lo que la fuente original vigente prevalece ante la misma ruta/nombre. Los JSON siguen excluidos de `output`.
- Actualiza la columna `Ruta` únicamente después de verificar la organización local y, cuando DEPI está habilitado, la publicación en `<SATYS_SHARED_DIR>/output/_sin_operador/(correos)/...`; si DEPI falla, la Ruta y la carpeta anterior permanecen pendientes para reintento.
- La regla es idempotente y se ejecuta dentro de la misma única etapa previa al correo, por lo que también aplica al comando independiente `bash scripts/podman_satys.sh sin-operador-rpc`.
- Añade auditoría `total_memorandum_detectados`, `total_correos_confirmados`, `total_correos_ya_clasificados` y `cambios_excel_correos`.

# 2026.08.28-definitiva-cierre-seguro-rpc-publico-manual1

- Añade comando independiente `scripts/podman_satys.sh sin-operador-rpc` para ejecutar únicamente la reparación final de `Ruta=_sin_operador` con el buscador público RPC, sin ejecutar la corrida diaria completa.
- El comando reutiliza el runtime productivo (`TrámitesCRT.xlsx`, `descargas`, `output`, logs y DEPI), admite `--dry-run` y aplica `SATYS_SIN_OPERADOR_RPC_PUBLICO_TIMEOUT`.
- `resolver_sin_operador_rpc_publico.py` adquiere/hereda `ProcesoLock`, evitando modificar Excel/output en paralelo con una corrida diaria; una colisión de lock termina con código 3.
- Se mantienen las garantías anteriores: RPC público exclusivamente, `descargas` como fuente, fusión sin nombres duplicados, actualización de `Ruta` sólo tras sincronización DEPI verificada y cierre seguro.

# 2026.08.28-definitiva-cierre-seguro-rpc-publico1

- Añade una única etapa final, inmediatamente antes del correo diario, para reintentar las filas de `TrámitesCRT.xlsx` cuya columna `Ruta` aún apunta a `_sin_operador`.
- La llave de conciliación es la columna `1711`; soporta Registros `CRTxx-xxxxxx` y Folios numéricos de Internos, incluso rutas como `_sin_operador\\internos__Fuera_de_tiempo__135531`.
- La etapa toma `nombre_operador` exclusivamente de `descargas/**/metadata_satys.json` y consulta directamente el buscador público RPC (`searchConcesiones` + `searchBP`). No carga ni usa el Excel oficial RPC para esta reparación final.
- Una resolución pública segura crea/reutiliza `output/<ID>_<nombre_normalizado>/01 EN/VE`, fusiona archivos históricos sin generar sufijos `_1/_2/_3`, y copia al final desde `descargas` para que la fuente original vigente prevalezca ante el mismo nombre/ruta.
- Tras verificar la copia, actualiza `Ruta` en `TrámitesCRT.xlsx`, conserva un backup previo y retira únicamente la carpeta `_sin_operador` correspondiente a la fila reparada; `descargas` nunca se elimina ni modifica.
- Replica antes del correo cada destino reparado a `<SATYS_SHARED_DIR>/output` (producción: `/depi/dgp/DEI_DATOS/SATyS/output`), preservando archivos históricos únicos del recurso compartido y retirando sólo la antigua carpeta de revisión ya reparada.
- El correo consolidado refleja como exitosos los registros recuperados por esta etapa.
- Timeout duro independiente `SATYS_SIN_OPERADOR_RPC_PUBLICO_TIMEOUT` (1800 s por defecto): si el RPC público o la etapa se atascan, el monitor continúa al correo y al cierre.
- Genera auditoría `logs/reparacion_sin_operador_rpc_publico_*.json/.csv` y `reparacion_sin_operador_rpc_publico_ultimo.json`.

# 2026.08.28-definitiva-cierre-seguro1

- Corrige el cierre diario que podía permanecer activo durante horas después de
  terminar SATyS/Parte 1: la reconciliación de `TrámitesCRT.xlsx` ya no recorre
  dimensiones fantasma de OpenPyXL ni ejecuta `delete_rows()` sobre cientos de
  miles de filas vacías creadas sólo por formato residual.
- La reconciliación global diaria conserva RPC, rutas, reportes y Excel, pero ya
  no vuelve a copiar ni comparar byte a byte todo el histórico de `descargas`
  contra `output` después de que el pipeline principal ya lo organizó.
- Añade progreso cada 100 metadata durante la reconciliación global para evitar
  periodos largos sin señal visible de avance.
- Añade timeout duro de 1800 segundos a la reconciliación global. Si se excede,
  sólo termina ese subproceso con código `124`; el monitor padre continúa al
  correo consolidado, actualización de estado y cierre en vez de quedar vivo
  indefinidamente.
- Mantiene intactas las reglas de negocio de la release base
  `2026.08.27-definitiva-organizacion-ve1`; no elimina ni mueve `descargas`.

# 2026.08.27-definitiva-organizacion-ve1

- Restaura la ruta documental obligatoria
  `output/<ID>_<nombre_concesionario>/01 EN/VE/` para cualquier bandeja.
- Fusiona en esa misma carpeta los archivos de todos los expedientes del
  concesionario, conserva las subcarpetas internas de cada descarga y no mueve
  ni elimina ningún contenido bajo `descargas/`.
- Si un archivo ya existe en la misma ruta, la versión de `descargas` lo
  reemplaza sin crear `archivo_1`, `archivo_2` u otras copias renombradas.
- Migra al iniciar el procesamiento las antiguas carpetas por Registro y las variantes
  artificiales del operador terminadas en `_1`, `_2`, `_3`, etc.; verifica los
  contenidos byte a byte antes de retirar esos duplicados de `output`.
- Mantiene todos los JSON exclusivamente en `descargas` y depura los heredados
  de la carpeta canónica de salida.
- Evita retirar la carpeta compartida `01 EN/VE` al reclasificar un expediente
  `CORREO`; sólo se consideran copias legacy identificables de ese expediente.

# 2026.08.27-definitiva-sin-operador-separado1

- Revierte la consolidación general de pendientes dentro de `(correos)`.
- Los expedientes sin concesionario permanecen directamente en
  `output/_sin_operador/<expediente>`.
- Sólo los expedientes cuyo `folio_opc` comienza con `CORREO` se organizan en
  `output/_sin_operador/(correos)/<expediente>`.
- Elimina del monitor diario la migración final que mezclaba ambas categorías.
- Si un expediente normal hubiera quedado temporalmente bajo `(correos)`, al
  volver a procesarlo se fusionan y verifican sus documentos en `_sin_operador`
  antes de retirar la copia mal clasificada; `descargas` permanece intacto.
- Conserva la búsqueda RPC Excel→web, el único correo diario consolidado, las
  validaciones de descarga y la política de no publicar JSON en `output`.

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

## 2026.08.28-definitiva-cierre-seguro-rpc-publico-manual-correos-remitentes1

- Antes de la reconciliación global, completa `Solicitante Promovente` y `Representante Legal` sólo cuando estén vacíos o `SIN REMITENTE`, recorriendo todos los PDF del expediente original en `descargas` con la lógica tolerante de `extraer_operador.py`.
- Si distintos PDF producen valores incompatibles, no inventa ni sobreescribe: conserva el pendiente y genera auditoría JSON/CSV.
- La reconciliación de `TrámitesCRT.xlsx` preserva valores válidos ya corregidos desde PDF frente a metadata vacío/`SIN REMITENTE`.
- La regla `(correos)` reconoce nombres PDF `MEMORANDO`, `MEMORANDUM`, `MEMORÁNDUM`, sufijos y variantes leves; `memo.pdf` por sí solo no clasifica.
- Nuevo comando manual `scripts/podman_satys.sh remitentes-pdf [--dry-run]`.
- Se añade `pdfplumber` y dependencias fijadas a la imagen OCI.

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
