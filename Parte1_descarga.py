r"""
=============================================================
  PROYECTO SATyS -- PARTE 1: DESCARGA AUTOMATICA DE ARCHIVOS
=============================================================
Flujo:
  1. Login en https://satys.ift.org.mx/
  2. Navega: Enlace/SIGEDO -> Enlace Oficialia de Partes
  3. Por cada folio -> busca en tabla -> Ver detalle -> descarga archivos
  4. Guarda en carpeta  descargas/<folio>/

Uso:
  .\python_portable\python.exe Parte1_descarga.py                   # usa FOLIOS por defecto
  .\python_portable\python.exe Parte1_descarga.py 6802 6801         # folios por argumento

Autor: Automatizacion IFT
Version: 4.0 - SPA corregido, encoding Windows OK
"""

import sys
import io
import os
import re
import time
import json
import zipfile
import shutil
import logging
import argparse
import hashlib
import threading
import concurrent.futures
import subprocess
import tempfile
import signal
import uuid
import unicodedata
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_result, before_sleep_log
from logging.handlers import RotatingFileHandler

from urllib.parse import urljoin, urlparse
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from configuracion_local import configuracion_procesamiento, credenciales_satys, ruta_configurada
from estado_descargas import registro_esta_completo, slug_bandeja_internos

# --- Cargar .env manualmente (para no depender de dotenv) ---
env_path = Path(".env")
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip("'\"")


# Forzar UTF-8 en consola Windows (evita UnicodeEncodeError)
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, 'encoding', '') != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ============================================================
#  CONFIGURACION  -- edita solo esta seccion
# ============================================================
# Credenciales SATyS: lectura exclusiva desde config/configuracion_local.json.
USUARIO, PASSWORD = credenciales_satys()
BASE_URL      = os.getenv("SATYS_BASE_URL", "https://satys.ift.org.mx/")
DESCARGA_BASE = ruta_configurada("descargas", "descargas")
SESION_FILE   = Path(os.getenv("SATYS_SESION_FILE", str(Path(__file__).resolve().parent / "sesion_guardada.json")))

# False = ver el navegador (recomendado para depurar)
# True  = sin ventana (modo produccion)
HEADLESS = os.getenv("SATYS_HEADLESS", "False").lower() in ("true", "1", "yes")
SOLO_METADATOS_ID = False

TIMEOUT_NAV   = int(os.getenv("SATYS_TIMEOUT_NAV", "60000"))   # ms -- carga de paginas (red intranet IFT)
TIMEOUT_CORTO = int(os.getenv("SATYS_TIMEOUT_CORTO", "10000"))   # ms -- esperas de elementos
TIMEOUT_DL    = int(os.getenv("SATYS_TIMEOUT_DL", "90000"))   # ms -- espera de descarga
TIMEOUT_DETALLE = int(os.getenv("SATYS_TIMEOUT_DETALLE", "120000"))  # ms -- espera de carga en Ver detalle

# Watchdog profesional por Registro.
# Un Registro completo corre en un proceso hijo independiente; si excede este
# límite, el proceso hijo se mata junto con sus Chromium sin congelar el lote.
_PROCESAMIENTO_CFG = configuracion_procesamiento()
TIMEOUT_REGISTRO = int(_PROCESAMIENTO_CFG.get("timeout_registro", 900))
REINTENTOS_REGISTRO = int(_PROCESAMIENTO_CFG.get("reintentos_registro", 2))  # 2 = 3 intentos totales
WORKERS_REINTENTO_REGISTRO = int(_PROCESAMIENTO_CFG.get("workers_reintento", 2))
INTERNOS_WORKERS_DEFAULT = int(_PROCESAMIENTO_CFG.get("internos_workers", 12))
INTERNOS_WORKER_REINTENTOS = max(
    0,
    int(os.getenv("SATYS_INTERNOS_WORKER_REINTENTOS", str(REINTENTOS_REGISTRO))),
)
INTERNOS_WORKER_ESPERA = max(
    0.0,
    float(os.getenv("SATYS_INTERNOS_WORKER_ESPERA", "2")),
)
REGISTROS_FALLIDOS_DIR = Path(__file__).resolve().parent / "registros_fallidos"

# Retransmisión de logs descriptivos de cada proceso hijo al log principal.
# Mantiene la protección anti-congelamiento por subproceso, pero vuelve a mostrar
# en monitor_registros_*.log los mensajes detallados que antes salían en vivo
# ([REG-BUSCAR], [REG-VIEW], [WEB], [FILES], Descargando, etc.).
RELAY_WORKER_LOGS = os.getenv("SATYS_RELAY_WORKER_LOGS", "True").lower() in ("true", "1", "yes", "si", "sí")
MAX_RELAY_LINE_CHARS = int(os.getenv("SATYS_MAX_RELAY_LINE_CHARS", "900"))

# API discovery y descarga directa (experimental)
API_DISCOVERY = os.getenv("SATYS_API_DISCOVERY", "True").lower() in ("true", "1", "yes")
DIRECT_DOWNLOAD = os.getenv("SATYS_DIRECT_DOWNLOAD", "True").lower() in ("true", "1", "yes")
API_LOG_PATH = Path("debug") / "api_log.jsonl"

# Folios a procesar (se normalizan automaticamente)
FOLIOS_DEFAULT = ["6407", "6801", "6802"]

# Bandejas del flujo "Administracion solicitudes +TyS/SIGEDO/Internos IFT".
# Se procesan en orden y se guardan aisladas bajo descargas/internos/.
BANDEJAS_INTERNOS_DEFAULT = [
    "Recibidos",
    "En proceso",
    "Copias Marcadas",
    "Atendidos",
    "Ultimos Movimientos",
    "Fuera de tiempo",
]
INTERNOS_BANDEJA_IDS = {
    "recibidos": "1",
    "en proceso": "2",
    "copias marcadas": "3",
    "atendidos": "4",
    "ultimos movimientos": "5",
    "fuera de tiempo": "6",
}
# ============================================================

# Configurar Logging con RotatingFileHandler
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
log_file = log_dir / "satys_execution.log"

logger_format = "%(asctime)s  %(levelname)-8s  %(message)s"
logging.basicConfig(level=logging.INFO, format=logger_format, datefmt="%H:%M:%S")

log = logging.getLogger("SATyS-P1")
log.propagate = False
if not log.handlers:
    # Handler para consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(logger_format, datefmt="%H:%M:%S"))
    log.addHandler(console_handler)
    
    # Handler rotativo para archivo (Max 5 MB, hasta 5 respaldos)
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(logger_format, datefmt="%H:%M:%S"))
    log.addHandler(file_handler)

# URL del tablero (se asigna durante la navegacion)
URL_TABLERO = ""

# ============================================================
#  LOCKS DE THREAD-SAFETY (Fase 3 -- Concurrencia)
# ============================================================
_sesion_lock = threading.Lock()   # Protege escrituras a sesion_guardada.json
_api_log_lock = threading.Lock()  # Protege escrituras a api_log.jsonl


# ------------------------------------------------------------
#  AUXILIARES
# ------------------------------------------------------------
def _retry_if_false(result):
    return result is False

def normalizar_folio(folio: str) -> str:

    """'006407' -> '6407',  'CRT26-009493' -> '9493'."""
    m = re.search(r"(\d+)$", str(folio).strip())
    return str(int(m.group(1))) if m else str(folio).strip()


def _fila_contiene_folio(texto: str, folio: str) -> bool:
    return re.search(rf"(?:^|\D)0*{re.escape(folio)}(?:\D|$)", texto) is not None


def crear_carpeta(folio: str) -> Path:
    p = DESCARGA_BASE / folio
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sanitizar_nombre_carpeta(nombre: str) -> str:
    """Limpia un nombre para que sea valido como carpeta en Windows/Linux."""
    nombre = (nombre or "SIN_REGISTRO").strip()
    nombre = re.sub(r'[<>:"/\\|?*]', "_", nombre)
    return nombre or "SIN_REGISTRO"


def carpeta_para_registro(carpeta_base: Path, indice_registro: int, registro: str) -> Path:
    """
    Calcula la carpeta de descarga correcta cuando un mismo folio coincide
    con VARIOS tramites/registros distintos en SATyS (ej. folio 1660 con
    registros CRT26-020606 y CRT26-002483).

    - 1er registro encontrado (indice_registro=0): usa la carpeta base SIN
      cambios -- descargas/<folio>/ -- exactamente como siempre. Esto
      preserva 100% de compatibilidad para el caso normal (un folio = un
      registro), que es la inmensa mayoria.
    - 2do registro en adelante: recibe su PROPIA carpeta, para que sus
      archivos y metadatos no se mezclen ni se sobreescriban con los del
      primero:
          descargas/<folio>_1/<registro>/
          descargas/<folio>_2/<registro>/
          ...
    """
    if indice_registro == 0:
        return carpeta_base

    nombre_pseudo_folio = f"{carpeta_base.name}_{indice_registro}"
    nombre_registro = _sanitizar_nombre_carpeta(registro)
    carpeta_extra = carpeta_base.parent / nombre_pseudo_folio / nombre_registro
    carpeta_extra.mkdir(parents=True, exist_ok=True)
    return carpeta_extra


ZIP_MAX_ITERACIONES = max(1, int(os.getenv("SATYS_ZIP_MAX_ITERACIONES", "32")))
ZIP_RUTA_RELATIVA_MAX = max(80, int(os.getenv("SATYS_ZIP_RUTA_RELATIVA_MAX", "140")))
ZIP_RUTA_WINDOWS_MAX = max(180, int(os.getenv("SATYS_ZIP_RUTA_WINDOWS_MAX", "235")))


def _limpiar_componente_zip(nombre: str) -> str:
    """Convierte un componente ZIP en un nombre portable Windows/Linux."""
    limpio = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nombre).rstrip(" .")
    limpio = limpio or "_"
    reservados = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if limpio.split(".", 1)[0].upper() in reservados:
        limpio = f"_{limpio}"
    return limpio


def _acortar_componente_zip(nombre: str, maximo: int, clave: str, es_archivo: bool) -> str:
    """Acorta un nombre conservando extensión y una huella estable anticolisión."""
    digest = hashlib.sha1(clave.encode("utf-8", errors="replace")).hexdigest()[:10]
    sufijo = Path(nombre).suffix if es_archivo else ""
    if len(sufijo) > 16 or len(sufijo.encode("utf-8")) > 32:
        sufijo = ""
    marca = f"~{digest}"
    espacio = max(1, maximo - len(marca) - len(sufijo))
    prefijo = nombre[:-len(sufijo)] if sufijo else nombre
    prefijo = prefijo[:espacio].rstrip(" ._-") or "_"
    return f"{prefijo}{marca}{sufijo}"


def _destino_seguro_zip(carpeta: Path, nombre_miembro: str) -> tuple[Path, bool]:
    """Resuelve un miembro ZIP sin traversal y dentro de presupuestos de ruta."""
    nombre_normalizado = (nombre_miembro or "").replace("\\", "/")
    if nombre_normalizado.startswith("/") or re.match(r"^[A-Za-z]:", nombre_normalizado):
        raise ValueError(f"ruta absoluta no permitida en ZIP: {nombre_miembro!r}")

    originales = []
    for parte in nombre_normalizado.split("/"):
        if parte in ("", "."):
            continue
        if parte == "..":
            raise ValueError(f"salida de carpeta no permitida en ZIP: {nombre_miembro!r}")
        originales.append(parte)
    if not originales:
        raise ValueError("miembro ZIP sin nombre utilizable")

    partes = [_limpiar_componente_zip(parte) for parte in originales]
    cambiado = partes != originales

    # Un componente excesivo también falla en Linux (NAME_MAX suele ser 255 bytes).
    for indice, parte in enumerate(list(partes)):
        if len(parte.encode("utf-8")) > 220:
            maximo = min(180, len(parte))
            while True:
                candidato = _acortar_componente_zip(
                    parte,
                    maximo,
                    "/".join(originales[:indice + 1]),
                    indice == len(partes) - 1,
                )
                if len(candidato.encode("utf-8")) <= 220 or maximo <= 24:
                    partes[indice] = candidato
                    break
                maximo -= 1
            cambiado = True

    def ruta_actual() -> Path:
        return carpeta.joinpath(*partes)

    # El límite relativo hace que el resultado sea portable y estable al mover
    # el proyecto entre Windows y el contenedor Linux.
    def exceso_ruta() -> int:
        exceso_relativo = len(str(Path(*partes))) - ZIP_RUTA_RELATIVA_MAX
        exceso_windows = 0
        if os.name == "nt":
            # Reservar espacio para el sufijo temporal ".part".
            exceso_windows = len(str(ruta_actual().absolute())) + len(".part") - ZIP_RUTA_WINDOWS_MAX
        return max(exceso_relativo, exceso_windows, 0)

    while exceso_ruta() > 0:
        candidatos = [i for i, parte in enumerate(partes) if len(parte) > 24]
        if not candidatos:
            digest = hashlib.sha1(nombre_normalizado.encode("utf-8", errors="replace")).hexdigest()[:16]
            sufijo = Path(partes[-1]).suffix
            partes = ["_rutas_largas", f"{digest}{sufijo}"]
            cambiado = True
            break
        indice = max(candidatos, key=lambda i: len(partes[i]))
        nuevo_maximo = max(24, len(partes[indice]) - exceso_ruta())
        partes[indice] = _acortar_componente_zip(
            partes[indice],
            nuevo_maximo,
            "/".join(originales[:indice + 1]),
            indice == len(partes) - 1,
        )
        cambiado = True

    return ruta_actual(), cambiado


def _extraer_contenido_zip(archivo: Path, carpeta: Path) -> tuple[list[Path], list[str]]:
    """Extrae un ZIP de forma segura y devuelve (archivos, errores)."""
    archivos_extraidos: list[Path] = []
    errores: list[str] = []
    try:
        with zipfile.ZipFile(archivo, "r") as zip_ref:
            for member in zip_ref.infolist():
                if member.is_dir():
                    continue
                try:
                    # No materializar enlaces simbólicos contenidos en un ZIP.
                    if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                        raise ValueError("enlace simbolico no permitido")
                    destino, acortado = _destino_seguro_zip(carpeta, member.filename)
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    temporal = destino.with_name(f"{destino.name}.part")
                    try:
                        with zip_ref.open(member, "r") as origen, open(temporal, "wb") as salida:
                            shutil.copyfileobj(origen, salida, length=1024 * 1024)
                        os.replace(temporal, destino)
                    finally:
                        temporal.unlink(missing_ok=True)
                    archivos_extraidos.append(destino)
                    if acortado:
                        log.warning(
                            "[ZIP-PATH] Ruta acortada para portabilidad: %s -> %s",
                            member.filename,
                            destino.relative_to(carpeta),
                        )
                except Exception as exc:
                    errores.append(f"{member.filename}: {exc}")
    except Exception as exc:
        errores.append(str(exc))
    return archivos_extraidos, errores


def extraer_zip_si_aplica(archivo: Path, carpeta: Path, _profundidad: int = 0) -> list:
    archivos_extraidos: list[Path] = []
    if archivo.suffix.lower() == ".zip":
        log.info("[ZIP] Archivo ZIP detectado, descomprimiendo: %s", archivo.name)
        archivos_extraidos, errores = _extraer_contenido_zip(archivo, carpeta)
        if errores:
            log.error(
                "[ZIP] Error descomprimiendo %s (%d miembro(s)): %s",
                archivo.name,
                len(errores),
                " | ".join(errores[:3]),
            )
        else:
            log.info("[ZIP] Descomprimido correctamente: %s. Eliminando ZIP original.", archivo.name)
            archivo.unlink(missing_ok=True)
            # Convertir ZIPs anidados exitosos en sus hojas finales antes de
            # construir metadata. Así metadata_completo.json nunca apunta a un
            # ZIP intermedio que ya fue eliminado.
            finales: list[Path] = []
            for extraido in archivos_extraidos:
                if extraido.suffix.lower() != ".zip":
                    finales.append(extraido)
                    continue
                if _profundidad + 1 >= ZIP_MAX_ITERACIONES:
                    log.error(
                        "[ZIP] Profundidad maxima (%d) alcanzada en %s; se conserva para diagnostico.",
                        ZIP_MAX_ITERACIONES,
                        extraido,
                    )
                    finales.append(extraido)
                    continue
                anidados = extraer_zip_si_aplica(extraido, extraido.parent, _profundidad + 1)
                if anidados:
                    finales.extend(anidados)
                elif extraido.exists():
                    finales.append(extraido)
            archivos_extraidos = finales
    return archivos_extraidos


def descomprimir_todos_zips_en_carpeta(carpeta: Path) -> int:
    """Descomprime TODOS los archivos ZIP presentes en la carpeta del folio,
    incluyendo ZIPs dentro de ZIPs (recursivo por iteracion).
    Retorna el total de archivos extraidos.
    """
    total_extraidos = 0
    intentados: set[tuple[str, int, int]] = set()
    for iteracion in range(1, ZIP_MAX_ITERACIONES + 1):
        # La firma impide reintentar indefinidamente un ZIP que permanece en
        # disco tras fallar; un archivo realmente reemplazado sí puede entrar.
        zips_encontrados: list[tuple[Path, tuple[str, int, int]]] = []
        for zip_path in carpeta.rglob("*.zip"):
            try:
                stat_zip = zip_path.stat()
                firma = (os.path.normcase(str(zip_path.absolute())), stat_zip.st_size, stat_zip.st_mtime_ns)
            except OSError as exc:
                log.error("[ZIP-ALL] No se pudo inspeccionar %s: %s", zip_path, exc)
                continue
            if firma not in intentados:
                zips_encontrados.append((zip_path, firma))
        if not zips_encontrados:
            break
        log.info("[ZIP-ALL] Iteracion %d: encontrados %d ZIP(s) en carpeta %s",
                 iteracion, len(zips_encontrados), carpeta.name)
        for zip_path, firma in zips_encontrados:
            intentados.add(firma)
            # Extraer en la misma carpeta donde esta el ZIP (preserva estructura)
            carpeta_destino = zip_path.parent
            extraidos, errores = _extraer_contenido_zip(zip_path, carpeta_destino)
            if errores:
                log.error(
                    "[ZIP-ALL] Error descomprimiendo %s (%d miembro(s)); "
                    "se conserva el ZIP y no se reintentara en este proceso: %s",
                    zip_path.name,
                    len(errores),
                    " | ".join(errores[:3]),
                )
            else:
                log.info("[ZIP-ALL] Extraidos %d archivos de: %s — eliminando ZIP.",
                         len(extraidos), zip_path.name)
                zip_path.unlink(missing_ok=True)
                total_extraidos += len(extraidos)
    else:
        pendientes_nuevos = []
        for zip_path in carpeta.rglob("*.zip"):
            try:
                stat_zip = zip_path.stat()
                firma = (os.path.normcase(str(zip_path.absolute())), stat_zip.st_size, stat_zip.st_mtime_ns)
            except OSError:
                continue
            if firma not in intentados:
                pendientes_nuevos.append(zip_path)
        if pendientes_nuevos:
            log.error(
                "[ZIP-ALL] Se alcanzo el limite de %d iteraciones en %s; "
                "se detiene la recursion con %d ZIP(s) nuevo(s) pendiente(s).",
                ZIP_MAX_ITERACIONES,
                carpeta,
                len(pendientes_nuevos),
            )

    zips_pendientes = list(carpeta.rglob("*.zip"))
    if zips_pendientes:
        log.warning(
            "[ZIP-ALL] Quedaron %d ZIP(s) sin eliminar en %s para diagnostico; "
            "no se repetiran en bucle.",
            len(zips_pendientes),
            carpeta,
        )
    if total_extraidos > 0:
        log.info("[ZIP-ALL] Total archivos extraidos de todos los ZIPs: %d", total_extraidos)
    return total_extraidos


def screenshot(page, nombre: str):
    d = Path("debug")
    d.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    path = d / f"{ts}_{nombre}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        log.debug("Screenshot: %s", path)
    except Exception:
        pass


def _write_jsonl(path: Path, data: dict):
    path.parent.mkdir(exist_ok=True)
    with _api_log_lock:
        with open(path, "a", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=True)
            f.write("\n")


def _url_relevante(url: str) -> bool:
    u = url.lower()
    for k in ("sarccontroller", "sigedo", "descarg", "download", "archivo", "documento", "tramite"):
        if k in u:
            return True
    return False


def habilitar_api_discovery(context):
    if not API_DISCOVERY:
        return

    def _on_request(req):
        if req.resource_type in ("xhr", "fetch") or _url_relevante(req.url):
            _write_jsonl(API_LOG_PATH, {
                "type": "request",
                "ts": datetime.now().isoformat(),
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
            })

    def _on_response(res):
        req = res.request
        if req.resource_type in ("xhr", "fetch") or _url_relevante(res.url):
            headers = res.headers
            _write_jsonl(API_LOG_PATH, {
                "type": "response",
                "ts": datetime.now().isoformat(),
                "url": res.url,
                "status": res.status,
                "resource_type": req.resource_type,
                "content_type": headers.get("content-type", ""),
                "content_disposition": headers.get("content-disposition", ""),
            })

    context.on("request", _on_request)
    context.on("response", _on_response)
    log.info("[API] Discovery activo: %s", API_LOG_PATH)


def esperar_detalle(page, folio: str) -> bool:
    """Espera a que cargue la vista de detalle; evita bloqueos largos."""
    inicio = time.time()
    timeout_s = TIMEOUT_DETALLE / 1000
    selectores = [
        "text=Visualizacion del documento",
        "text=Visualizaci",
        "text=Datos Oficialia de Partes",
        "text=DATOS DEL SISTEMA",
        "text=ARCHIVOS ASOCIADOS",
    ]

    while (time.time() - inicio) < timeout_s:
        for sel in selectores:
            try:
                if page.locator(sel).count() > 0:
                    return True
            except Exception:
                pass
        try:
            page.evaluate("window.scrollBy(0, 800)")
        except Exception:
            pass
        page.wait_for_timeout(1_000)

    screenshot(page, f"detalle_timeout_{folio}")
    return False


def _absolutizar_url(page, url: str) -> str:
    return urljoin(page.url, url)


def _extraer_url_from_onclick(onclick: str) -> str:
    if not onclick:
        return ""
    m = re.search(r"'(.*?)'", onclick)
    return m.group(1) if m else ""

def _return_false(retry_state):
    return False

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_result(_retry_if_false), retry_error_callback=_return_false)
def _descargar_directo(context, page, url: str, dest: Path) -> bool:
    if not DIRECT_DOWNLOAD or not url:
        return False
    req_ctx = getattr(context, "request", None)
    if req_ctx is None:
        return False
    try:
        log.info("     [DL-DIRECTO] GET inicio: %s -> %s", url, dest.name)
        resp = req_ctx.get(url, timeout=TIMEOUT_DL)
        log.info("     [DL-DIRECTO] GET respuesta: status_ok=%s archivo=%s", resp.ok, dest.name)
        if not resp.ok:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = resp.body()
        dest.write_bytes(body)
        log.info("     [DL-DIRECTO] Escrito: %s (%.1f KB)", dest.name, len(body) / 1024)
        return True
    except Exception as exc:
        log.warning("     [DL-DIRECTO] Falló descarga directa de %s: %s", dest.name, exc)
        return False

def _retry_if_both_none(res):
    return res[0] is None and res[1] is None

def _return_both_none(retry_state):
    return None, None


def _encontrar_boton_documento_modal(page):
    """Retorna el VER DOCUMENTO visible de la ventana intermedia, si existe."""
    selectores = (
        ".modal:visible button:has-text('VER DOCUMENTO'), "
        ".modal:visible a:has-text('VER DOCUMENTO'), "
        ".modal:visible input[value='VER DOCUMENTO']",
        "[role='dialog']:visible button:has-text('VER DOCUMENTO'), "
        "[role='dialog']:visible a:has-text('VER DOCUMENTO'), "
        "[role='dialog']:visible input[value='VER DOCUMENTO']",
    )
    for selector in selectores:
        try:
            candidatos = page.locator(selector)
            for indice in range(candidatos.count()):
                candidato = candidatos.nth(indice)
                if not candidato.is_visible():
                    continue
                try:
                    if not candidato.is_enabled():
                        continue
                except Exception:
                    pass
                return candidato
        except Exception:
            continue
    return None


def _cerrar_modal_documento(page) -> bool:
    """Cierra la ventana intermedia de Archivo PDF antes del siguiente anexo."""
    selectores = (
        ".modal:visible button:has-text('CERRAR')",
        ".modal:visible [data-dismiss='modal']",
        ".modal:visible [data-bs-dismiss='modal']",
        ".modal:visible button.close",
        "[role='dialog']:visible button:has-text('CERRAR')",
    )
    for selector in selectores:
        try:
            botones = page.locator(selector)
            if botones.count() == 0:
                continue
            boton = botones.last
            if not boton.is_visible():
                continue
            try:
                boton.click(timeout=3_000)
            except Exception:
                boton.click(force=True, timeout=3_000)
            page.wait_for_timeout(200)
            log.info("[MODAL-PDF] Ventana intermedia cerrada.")
            return True
        except Exception:
            continue
    return False


def _cerrar_paginas_emergentes(context, page_principal, motivo: str = "descarga") -> int:
    """Cierra pestañas auxiliares sin tocar la página principal del worker."""
    cerradas = 0
    try:
        paginas = list(context.pages)
    except Exception:
        return 0

    for pagina in paginas:
        if pagina is page_principal:
            continue
        try:
            if pagina.is_closed():
                continue
            pagina.close(run_before_unload=False)
            cerradas += 1
        except Exception as exc:
            log.debug("[POPUP-CLOSE] No se pudo cerrar una pestaña auxiliar: %s", exc)

    if cerradas:
        log.info("[POPUP-CLOSE] %d pestaña(s) auxiliar(es) cerrada(s) tras %s.", cerradas, motivo)
    return cerradas


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_result(_retry_if_both_none), retry_error_callback=_return_both_none)
def _click_y_esperar_descarga(page, context, boton_ver_doc):
    dl_obj = None
    np_obj = None
    modal_pulsado = False

    def on_dl(d): nonlocal dl_obj; dl_obj = d
    def on_pg(p): nonlocal np_obj; np_obj = p

    page.on("download", on_dl)
    context.on("page", on_pg)
    try:
        # Si un intento anterior dejo abierto el modal, continuar desde el
        # boton morado sin volver a pulsar el boton gris de la tabla.
        boton_modal = _encontrar_boton_documento_modal(page)
        if boton_modal is not None:
            log.info("  [MODAL-PDF] Ventana intermedia ya abierta; continuando con VER DOCUMENTO.")
            try:
                boton_modal.click(timeout=3_000)
            except Exception:
                boton_modal.click(force=True, timeout=3_000)
            modal_pulsado = True
        else:
            try:
                boton_ver_doc.click()
            except Exception:
                boton_ver_doc.click(force=True)

        import time
        start_t = time.time()
        while time.time() - start_t < (TIMEOUT_DL / 1000.0):
            if dl_obj or np_obj:
                break

            # Algunos anexos muestran primero el modal morado "Archivo PDF"
            # y exigen un segundo clic. Resolverlo dentro del mismo timeout
            # conserva tambien los flujos de descarga directa ya existentes.
            if not modal_pulsado:
                boton_modal = _encontrar_boton_documento_modal(page)
                if boton_modal is not None:
                    log.info("  [MODAL-PDF] Ventana Archivo PDF detectada; pulsando VER DOCUMENTO.")
                    try:
                        boton_modal.click(timeout=3_000)
                    except Exception:
                        try:
                            boton_modal.click(force=True, timeout=3_000)
                        except Exception as exc:
                            log.debug("  [MODAL-PDF] El boton intermedio aun no esta listo: %s", exc)
                            page.wait_for_timeout(200)
                            continue
                    modal_pulsado = True
                    continue

            page.wait_for_timeout(200)
    finally:
        page.remove_listener("download", on_dl)
        context.remove_listener("page", on_pg)
    return dl_obj, np_obj


def _click_menu_text(root, pattern, timeout=TIMEOUT_CORTO) -> bool:
    try:
        loc = root.locator("a, button").filter(has_text=pattern).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.scroll_into_view_if_needed()
        loc.click()
        return True
    except Exception:
        return False


def _click_onclick(page, snippet: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                (snippet) => {
                  const sel = "[onclick*='" + snippet + "']";
                  const el = document.querySelector(sel);
                  if (!el) return false;
                  el.click();
                  return true;
                }
                """,
                snippet,
            )
        )
    except Exception:
        return False


def _parsear_paginacion(page, scope=None):
    """
    Parsea el texto 'Mostrando X a Y de Z tramites' de un DataTable.
    scope: Playwright locator para limitar la busqueda.
    Retorna (mostrado_hasta, total) o (0, 0) si no se encuentra.
    """
    root = scope if scope else page
    try:
        info_els = root.locator(".dataTables_info, [id*='_info']")
        for i in range(info_els.count()):
            texto = info_els.nth(i).inner_text()
            m = re.search(r'Mostrando\s+(\d+)\s+a\s+(\d+)\s+de\s+(\d+)', texto)
            if m:
                return (int(m.group(2)), int(m.group(3)))
        # Fallback: buscar en texto completo del scope
        texto_all = root.inner_text()
        m = re.search(r'Mostrando\s+(\d+)\s+a\s+(\d+)\s+de\s+(\d+)', texto_all)
        if m:
            return (int(m.group(2)), int(m.group(3)))
    except Exception:
        pass
    return (0, 0)


def _avanzar_pagina_datatables(page, scope=None):
    """
    Hace click en 'Siguiente' de un DataTable dentro del scope dado.
    Retorna True si se avanzo, False si es la ultima pagina.
    """
    root = scope if scope else page
    try:
        btn_sig = root.locator(
            ".paginate_button.next, li.next a, a:has-text('Siguiente')"
        ).last
        if btn_sig.count() == 0:
            return False
        clases = btn_sig.get_attribute("class") or ""
        try:
            padre_clases = btn_sig.locator("xpath=..").get_attribute("class") or ""
        except Exception:
            padre_clases = ""
        if "disabled" in clases or "disabled" in padre_clases:
            return False
        try:
            if btn_sig.is_disabled():
                return False
        except Exception:
            pass
        btn_sig.click()
        # Esperar que DataTables actualice la página (más rápido que tiempo fijo)
        try:
            page.wait_for_function(
                "() => { const p = document.querySelector('.dataTables_processing'); "
                "return !p || p.style.display === 'none' || p.style.display === ''; }",
                timeout=6_000
            )
        except Exception:
            page.wait_for_timeout(800)
        return True
    except Exception:
        return False


# ------------------------------------------------------------
#  V-05: Paginacion mejorada -- doble verificacion mostrado_hasta vs total
# ------------------------------------------------------------
def _avanzar_pagina_datatables_v2(page, mostrado_hasta: int, total: int, scope=None) -> bool:
    """
    Avanza pagina en DataTable. Verifica AMBAS condiciones antes de parar:
    - clase 'disabled' del boton
    - mostrado_hasta >= total (V-05: evita parar prematuramente)
    """
    if total > 0 and mostrado_hasta >= total:
        return False  # Ya vimos todos

    root = scope if scope else page
    for _intento in range(3):  # hasta 3 intentos de avance
        try:
            btn_sig = root.locator(
                ".paginate_button.next, li.next a, a:has-text('Siguiente')"
            ).last
            if btn_sig.count() == 0:
                return False
            clases = btn_sig.get_attribute("class") or ""
            try:
                padre_clases = btn_sig.locator("xpath=..").get_attribute("class") or ""
            except Exception:
                padre_clases = ""
            if "disabled" in clases or "disabled" in padre_clases:
                return False
            try:
                if btn_sig.is_disabled():
                    return False
            except Exception:
                pass
            btn_sig.click()
            # Esperar que DataTables actualice la página (más rápido que tiempo fijo)
            try:
                page.wait_for_function(
                    "() => { const p = document.querySelector('.dataTables_processing'); "
                    "return !p || p.style.display === 'none' || p.style.display === ''; }",
                    timeout=6_000
                )
            except Exception:
                page.wait_for_timeout(800)
            return True
        except Exception:
            page.wait_for_timeout(500)
    return False


# ------------------------------------------------------------
#  V-09: Spinner -- esperar que desaparezca el overlay de carga
# ------------------------------------------------------------
def _esperar_sin_spinner(page, timeout_ms: int = 30_000) -> bool:
    """
    V-09: Espera a que desaparezcan los spinners/overlays de carga de la SPA.
    Retorna True cuando no hay spinner, False si agoto el tiempo.
    """
    selectores_spinner = [
        ".loading-overlay",
        ".overlay-loading",
        "[class*='loading'][class*='show']",
        "[class*='spinner'][style*='display: block']",
        "#loadingModal[style*='display: block']",
        ".modal-backdrop",
    ]
    patrones_texto = [
        re.compile(r"^\s*Cargando\.{0,3}\s*$", re.I),
        re.compile(r"Cargando\.{0,3}", re.I),
    ]
    inicio = time.time()
    limite = timeout_ms / 1000

    while (time.time() - inicio) < limite:
        hay_spinner = False
        # Internos usa #pantalla-carga y reemplaza el Map nativo de JavaScript,
        # lo que rompe los selectores de texto de Playwright en esta vista.
        try:
            estado_dom = page.evaluate(
                """() => {
                    const visibles = [
                        document.querySelector('#pantalla-carga'),
                        document.querySelector('.dataTables_processing')
                    ].filter(Boolean).some(el => {
                        const s = getComputedStyle(el);
                        return s.display !== 'none' && s.visibility !== 'hidden'
                            && el.getAttribute('aria-hidden') !== 'true';
                    });
                    return visibles ? 'VISIBLE' : 'OCULTO';
                }"""
            )
            hay_spinner = estado_dom == "VISIBLE"
        except Exception:
            pass
        for sel in selectores_spinner:
            if hay_spinner:
                break
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    hay_spinner = True
                    break
            except Exception:
                pass
        if not hay_spinner:
            for pat in patrones_texto:
                try:
                    loc = page.locator("body *").filter(has_text=pat)
                    for idx in range(min(loc.count(), 5)):
                        try:
                            item = loc.nth(idx)
                            if item.is_visible() and pat.search(item.inner_text(timeout=500) or ""):
                                hay_spinner = True
                                break
                        except Exception:
                            continue
                    if hay_spinner:
                        break
                except Exception:
                    pass
        if not hay_spinner:
            return True
        page.wait_for_timeout(500)

    log.warning("[V09] Spinner persiste despues de %.0fs -- continuando igual", limite)
    return False


# ------------------------------------------------------------
#  V-02: Watchdog de carga -- nunca bloquear por networkidle infinito
# ------------------------------------------------------------
def _esperar_carga_segura(page, timeout_networkidle: int = 15_000) -> None:
    """
    V-02: Espera networkidle hasta timeout_networkidle ms.
    Si no llega, continua con domcontentloaded (sin bloquear para siempre).
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_networkidle)
    except PWTimeout:
        log.debug("[V02] networkidle no llego en %dms -- usando domcontentloaded", timeout_networkidle)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:
            pass
    _esperar_sin_spinner(page, timeout_ms=10_000)


# ------------------------------------------------------------
#  V-03: Deteccion y recuperacion de sesion expirada
# ------------------------------------------------------------
def _verificar_sesion(page) -> bool:
    """
    V-03: Detecta si la sesion expiro (redirect al login).
    Si es asi, intenta re-login automatico y re-navegacion al tablero.
    Retorna True si la sesion esta activa (o fue recuperada), False si fallo.
    """
    try:
        url_actual = page.url.lower()
        # Indicadores de sesion expirada
        en_login = (
            "login" in url_actual
            or "verifylogin" in url_actual
            or page.locator("input[type='password']").count() > 0
        )
        if not en_login:
            return True  # Sesion activa

        log.warning("[V03] Sesion expirada detectada -- intentando re-login automatico")
        ok_login = login(page)
        if not ok_login:
            log.error("[V03] Re-login fallido")
            return False
            
        try:
            with _sesion_lock:
                page.context.storage_state(path=SESION_FILE)
            log.info("[V03] Nueva sesión guardada tras re-login automático")
        except Exception:
            pass

        ok_nav = navegar_a_tablero(page)
        if not ok_nav:
            log.error("[V03] Re-navegacion al tablero fallida")
            return False

        log.info("[V03] Sesion recuperada correctamente")
        return True
    except Exception as e:
        log.error("[V03] Error verificando sesion: %s", e)
        return False

# ------------------------------------------------------------
#  PASO 1 -- LOGIN
# ------------------------------------------------------------
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=3, min=3, max=48),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True
)
def login(page) -> bool:
    log.info("[LOGIN] Iniciando sesion...")
    try:
        if not USUARIO or not PASSWORD:
            log.error(
                "[LOGIN] Credenciales SATyS incompletas en config/configuracion_local.json."
            )
            return False

        log.info("[NET] Cargando pagina login...")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT_NAV)

        log.info("[LOGIN] Llenando credenciales...")
        page.fill("input[name='usuario'], input[name='username']", USUARIO)
        page.fill("input[type='password']", PASSWORD)
        
        # Click login
        with page.expect_navigation(timeout=TIMEOUT_NAV):
            page.click("button[type='submit'], input[type='submit'], a:has-text('Ingresar'), button:has-text('Entrar')")

        # Verificar una señal autenticada. Algunas cuentas aterrizan en el
        # menú principal sin renderizar todavía el texto "Tablero de Control".
        autenticado = False
        limite_confirmacion = time.monotonic() + 10
        while time.monotonic() < limite_confirmacion:
            texto_body = page.locator("body").inner_text(timeout=5_000)
            texto_normalizado = "".join(
                char for char in unicodedata.normalize("NFD", texto_body)
                if unicodedata.category(char) != "Mn"
            )
            texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).lower()
            autenticado = (
                "tablero de control" in texto_normalizado
                or "administracion solicitudes +tys/sigedo/internos ift" in texto_normalizado
            )
            if autenticado:
                break
            page.wait_for_timeout(500)

        if autenticado:
            log.info("[OK] Sesion iniciada correctamente")
            return True
        if "login" in page.url.lower():
            log.error("[ERROR] Credenciales invalidas o error en el portal")
            screenshot(page, "error_login")
            return False
        log.warning("[WARN] Tablero no encontrado tras login, asumiendo exito...")
        return True

    except Exception as e:
        log.error("[ERROR] Fallo critico en login: %s", e)
        screenshot(page, "exception_login")
        raise



# ------------------------------------------------------------
#  PASO 2 -- NAVEGAR A ENLACE OFICIALIA DE PARTES
# ------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_result(_retry_if_false))
def navegar_a_tablero(page) -> bool:
    """
    Hace clic en 'Enlace/SIGEDO' y luego en 'Enlace Oficialia de Partes'.
    IMPORTANTE: La pagina es una SPA -- la URL nunca cambia (siempre Sarccontroller).
    Debemos esperar que aparezca el texto 'Tablero de Control' en el contenido.
    """
    global URL_TABLERO
    log.info("[NAV] Navegando a Enlace Oficialia de Partes...")

    try:
        # V-09: Esperar que no haya spinner antes de interactuar con el menu
        _esperar_sin_spinner(page, timeout_ms=15_000)

        # Esperar menu lateral
        sidebar = page.locator("nav, .sidebar, aside").first
        sidebar.wait_for(state="visible", timeout=TIMEOUT_NAV)
        page.wait_for_timeout(300)  # Reducido de 1000ms: mínima estabilización

        # 2.1 Click en "Enlace/SIGEDO" para expandir el acordeon del menu
        if not _click_menu_text(sidebar, re.compile(r"Enlace\s*/\s*SIGEDO", re.I), TIMEOUT_NAV):
            _click_menu_text(page, re.compile(r"Enlace\s*/\s*SIGEDO", re.I), TIMEOUT_NAV)
        # Esperar que el submenú sea visible en lugar de tiempo fijo
        try:
            page.wait_for_selector(
                "a:has-text('Oficialía'), a:has-text('Officialia'), a:has-text('Ofic')",
                timeout=8_000, state="visible"
            )
        except Exception:
            page.wait_for_timeout(800)

        # 2.2 Click en "Enlace Oficialia de Partes" (subitem del acordeon)
        # El elemento tiene onclick="callAdminSolicitudesPages('muestraGestionSIGEDO');"
        # No tiene href real -- es JavaScript puro
        if not _click_onclick(page, "muestraGestionSIGEDO"):
            if not _click_menu_text(
                sidebar,
                re.compile(r"Oficial[ií]a\s+de\s+Partes|Officialia\s+de\s+Partes", re.I),
                TIMEOUT_NAV,
            ):
                _click_menu_text(
                    page,
                    re.compile(r"Oficial[ií]a\s+de\s+Partes|Officialia\s+de\s+Partes", re.I),
                    TIMEOUT_NAV,
                )

        # 2.3 Esperar que cargue el tablero (SPA -- sin cambio de URL)
        # Texto confirmado en la pagina: "Tablero de Control / Enlace Oficialia de Partes"
        try:
            page.wait_for_selector("text=Tablero de Control", timeout=TIMEOUT_NAV)
            log.info("[OK] Tablero de Control cargado")
        except PWTimeout:
            try:
                page.wait_for_selector("text=Documentos en Proceso", timeout=5_000)
                log.info("[OK] Tablero detectado por 'Documentos en Proceso'")
            except PWTimeout:
                log.warning("[WARN] Tablero no detectado -- continuando de todas formas")
                screenshot(page, "tablero_dudoso")

        URL_TABLERO = page.url   # Siempre sera Sarccontroller en esta SPA
        return True

    except Exception as e:
        log.error("[ERROR] Navegando: %s", e)
        screenshot(page, "nav_error")
        return False


# ------------------------------------------------------------
#  PASO 3 -- BUSCAR FOLIO Y ABRIR DETALLE
# ------------------------------------------------------------
def buscar_y_abrir_folio(page, folio: str) -> bool:
    log.info("[SEARCH] Buscando folio: %s", folio)
    try:
        # V-03: Verificar que la sesion este activa antes de buscar
        if not _verificar_sesion(page):
            log.error("[V03] No se pudo recuperar sesion para folio %s", folio)
            return False

        # 3.1 Asegurar pestana "Documentos en Proceso"
        try:
            tab = page.locator("a, button").filter(
                has_text=re.compile(r"Documentos en Proceso", re.I)
            ).first
            tab.wait_for(state="visible", timeout=3_000)
            tab.click()
            # V-09: Esperar que termine de cargar tras el click de pestana
            _esperar_sin_spinner(page, timeout_ms=8_000)
        except Exception:
            pass

        # 3.2 Campo de busqueda de DataTables
        search = page.locator(
            "input[type='search'], "
            ".dataTables_filter input, "
            "input.form-control[placeholder*='uscar']"
        ).first
        search.wait_for(state="visible", timeout=TIMEOUT_CORTO)
        try:
            search.click(click_count=3)
        except Exception:
            search.click()
            page.keyboard.press("Control+A")
        search.fill(folio)
        # Esperar que DataTables termine de filtrar (más rápido que espera fija)
        try:
            page.wait_for_function(
                "() => { const p = document.querySelector('.dataTables_processing'); "
                "return !p || p.style.display === 'none' || p.style.display === ''; }",
                timeout=8_000
            )
        except Exception:
            page.wait_for_timeout(800)

        # 3.3 Verificar resultados
        tbody = page.locator("table tbody").first
        filas = tbody.locator("tr")
        filas_count = filas.count()

        if filas_count == 0:
            log.warning("[WARN] Tabla vacia para folio %s", folio)
            screenshot(page, f"tabla_vacia_{folio}")
            return False

        primera = filas.first.inner_text().lower()
        if "no hay" in primera or "no data" in primera or "sin resultados" in primera:
            log.warning("[WARN] Sin resultados para folio %s", folio)
            return False

        # 3.4 Boton "Ver detalle" (icono de ojo -- boton azul)
        boton_ver = None

        # Buscar el indice de la columna "Memo / Folio OPC"
        try:
            headers = page.locator("table thead th")
            headers_count = headers.count()
            col_idx = -1
            for j in range(headers_count):
                header_text = headers.nth(j).inner_text().lower()
                if "folio opc" in header_text or "memo" in header_text:
                    col_idx = j
                    break
            if col_idx == -1:
                col_idx = 2  # Fallback al indice de la imagen (3ra columna)
        except Exception:
            col_idx = 2

        # Loop de paginacion
        max_paginas = 20
        for num_pagina in range(max_paginas):
            # Recargar filas para la pagina actual
            tbody = page.locator("table tbody").first
            filas = tbody.locator("tr")
            filas_count = filas.count()

            for i in range(filas_count):
                fila = filas.nth(i)
                
                celdas = fila.locator("td")
                if celdas.count() > col_idx:
                    texto_celda = celdas.nth(col_idx).inner_text().strip()
                else:
                    texto_celda = fila.inner_text() # fallback

                if _fila_contiene_folio(texto_celda, folio):
                    # Selector CSS combinado: más rápido que iterar 14 selectores uno a uno
                    try:
                        boton_ver = fila.locator(
                            "a[title*='Ver'], button[title*='Ver'], "
                            "a:has(i.fa-eye), button:has(i.fa-eye), "
                            "a:has(i.icon-eye), button:has(i.icon-eye), "
                            "a:has(i.glyphicon-eye-open), button:has(i.glyphicon-eye-open), "
                            "a[data-action='ver'], a.js-gestor-sigedo-open-tramite, "
                            "a.btn-info, button.btn-info, a.btn-primary, button.btn-primary, "
                            "a, button"
                        ).first
                        boton_ver.wait_for(state="visible", timeout=3_000)
                    except Exception:
                        boton_ver = None
                    if boton_ver is not None:
                        break
                        
            if boton_ver is not None:
                break # Encontrado
                
            # Intentar avanzar a la siguiente pagina
            try:
                # Selector comun de DataTables para el boton siguiente
                btn_siguiente = page.locator(".paginate_button.next, li.next a, a:has-text('Siguiente')").last
                if btn_siguiente.count() == 0:
                    break
                    
                clases = btn_siguiente.get_attribute("class") or ""
                padre_clases = btn_siguiente.locator("xpath=..").get_attribute("class") or ""
                
                if "disabled" in clases or "disabled" in padre_clases or btn_siguiente.is_disabled():
                    break # Ultima pagina alcanzada
                    
                btn_siguiente.click()
                try:
                    page.wait_for_function(
                        "() => { const p = document.querySelector('.dataTables_processing'); "
                        "return !p || p.style.display === 'none' || p.style.display === ''; }",
                        timeout=5_000
                    )
                except Exception:
                    page.wait_for_timeout(600)
            except Exception as e:
                log.debug("No se pudo avanzar a la siguiente pagina: %s", e)
                break

        if boton_ver is None:
            # V-04: Log claro con tag especifico para FOLIO_NO_ENCONTRADO
            log.warning("[V04-NO_ENCONTRADO] Folio %s no encontrado en Documentos en Proceso "
                        "(buscado en %d pagina(s))", folio, num_pagina + 1)
            screenshot(page, f"folio_no_encontrado_{folio}")
            return False

        log.info("[VIEW] Abriendo detalle del folio %s...", folio)
        boton_ver.scroll_into_view_if_needed()
        boton_ver.click()

        # 3.5 Esperar pagina de detalle
        # En SPA: esperar que aparezca "Visualizacion del documento" o "DATOS DEL SISTEMA"
        try:
            page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_DETALLE)
        except PWTimeout:
            pass

        if not esperar_detalle(page, folio):
            en_tablero = (
                page.locator("text=Documentos en Proceso").count() > 0
                and page.locator("input[type='search']").count() > 0
            )
            if en_tablero:
                log.error("[ERROR] No se entro a detalle para folio %s", folio)
                return False
            log.warning("[WARN] Detalle no confirmado, continuando...")

        log.info("[OK] Detalle del folio %s cargado", folio)
        return True

    except Exception as e:
        log.error("[ERROR] Buscando folio %s: %s", folio, e)
        screenshot(page, f"error_busqueda_{folio}")
        return False


# ------------------------------------------------------------
#  PASO 3.5 -- EXTRAER METADATOS WEB
# ------------------------------------------------------------
def _campos_criticos_completos(res: dict) -> bool:
    """Verifica que los campos criticos del metadata estén presentes y no vacios.

    NOTA IMPORTANTE: 'id_solicitante' e 'id_representante_legal' NO se exigen aqui.
    Los tramites "migrados" desde el sistema predecesor muestran el nombre del
    representante/solicitante como texto plano en los paneles "Representantes
    Legales de Migracion (Origen)" / "Remitentes Solicitantes de Migracion (Origen)"
    -- SIN ningun ID asociado. Ese ID no existe en el DOM bajo ninguna circunstancia
    para esos tramites, asi que exigirlo aqui no detecta una pagina lenta: provoca
    un bucle infinito real que nunca puede resolverse solo. Los nombres
    (representante_legal / nombre_operador) y el registro si son siempre
    recuperables (en la tabla normal o en el panel migrado), por eso son los
    unicos que bloquean el reintento.
    """
    campos = ("nombre_operador", "representante_legal", "registro")
    faltantes = [c for c in campos if not res.get(c)]
    if faltantes:
        log.warning("[META-RETRY] Campos faltantes: %s", faltantes)
        return False
    return True

def extraer_metadatos_satys(page, folio: str, carpeta: Path, registro_esperado: str = None) -> dict:
    """Extrae metadatos del folio con bucle de espera hasta capturar los campos criticos
    (representante_legal, nombre_operador, registro). 'id_representante_legal' e
    'id_solicitante' se guardan si aparecen, pero NO bloquean el reintento: en tramites
    migrados desde el sistema predecesor esos IDs no existen en el DOM bajo ninguna
    circunstancia (ver paneles '...Migracion (Origen)'), solo el nombre en texto plano.

    Tiene un techo de seguridad (MAX_ESPERA_TOTAL_SEG): si tras ese tiempo los campos
    criticos siguen sin aparecer, se asume que la pagina nunca los va a mostrar (no que
    esta cargando lento) y se continua con los datos parciales que se hayan logrado
    capturar, en vez de colgar el worker para siempre.
    """
    ESPERA_ENTRE_INTENTOS = 3        # segundos entre reintentos
    MAX_ESPERA_TOTAL_SEG = 60       # techo de seguridad: 1 minutos. (Antes era 180, pero yo propuse 60)
    intento = 0
    inicio = time.time()

    while True:
        intento += 1
        log.info("[WEB] Extrayendo metadatos web (intento %d) para folio %s", intento, folio)
        metadatos = _extraer_metadatos_satys_una_vez(page, folio, carpeta, registro_esperado)

        if _campos_criticos_completos(metadatos):
            log.info("[META-RETRY] Todos los campos criticos capturados en intento %d para folio %s",
                     intento, folio)
            return metadatos

        transcurrido = time.time() - inicio
        if transcurrido >= MAX_ESPERA_TOTAL_SEG:
            log.error(
                "[META-RETRY] LIMITE alcanzado (%ds) para folio %s tras %d intentos. "
                "La pagina no muestra los campos criticos (no es lentitud de JS) -- "
                "se continua con los datos parciales disponibles.",
                MAX_ESPERA_TOTAL_SEG, folio, intento,
            )
            return metadatos

        log.warning("[META-RETRY] Intento %d fallido para folio %s. Reintentando en %ds...",
                    intento, folio, ESPERA_ENTRE_INTENTOS)
        try:
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        try:
            page.wait_for_timeout(ESPERA_ENTRE_INTENTOS * 1000)
        except Exception as e:
            log.error("[META-RETRY] Navegador/pagina cerrado durante la espera para folio %s "
                      "(%s) -- se devuelven los datos parciales disponibles.", folio, e)
            return metadatos




def _extraer_metadatos_satys_una_vez(page, folio: str, carpeta: Path, registro_esperado: str = None) -> dict:
    log.info("[WEB] Extrayendo metadatos web (una vez) de SATyS para folio %s", folio)
    metadatos = {
        "representante_legal": None,
        "id_representante_legal": None,
        "nombre_operador": None,
        "id_solicitante": None,
        "asunto": None,
        "tipo_tramite": None,
        "registro": None,
    }
    try:
        page.evaluate("window.scrollTo(0, 0)")
        # ── Expandir TODAS las secciones colapsadas de un solo golpe ──────────────
        # Ahorra ~4 s de esperas fijas vs clics individuales con wait por sección.
        # Filtra aria-expanded='false' para no colapsar lo que ya está abierto.
        try:
            page.evaluate("""
                document.querySelectorAll("a[data-toggle='collapse'][aria-expanded='false']").forEach(function(el) {
                    try { el.click(); } catch(e) {}
                });
            """)
            # Bug A fix: apuntar a la tabla de REMITENTE(S)/Solicitante específicamente.
            # Una condición sobre *cualquier* celda resolvía en falso-positivo al instante
            # porque otras tablas de la vista ya tenían texto en el primer render.
            page.wait_for_function(
                """
                () => {
                    const tablas = Array.from(document.querySelectorAll('table'));
                    const tablaOk = tablas.some(function(t) {
                        const head = (t.querySelector('thead') ? t.querySelector('thead').innerText : '').toLowerCase();
                        if (!head.includes('solicitante') && !head.includes('representante legal')) return false;
                        const celda = t.querySelector('tbody tr td');
                        return celda && celda.innerText.trim().length > 0 && !celda.classList.contains('dataTables_empty');
                    });
                    if (tablaOk) return true;

                    // Formato MIGRADO: paneles "...Migracion (Origen)" con <p> plano, sin tabla ni ID.
                    const repMig = document.querySelector('#rep_legal_migrados_texto');
                    const solMig = document.querySelector('#solicitantes_migrados_texto');
                    const repMigOk = repMig && repMig.innerText.trim().length > 0;
                    const solMigOk = solMig && solMig.innerText.trim().length > 0;
                    return repMigOk || solMigOk;
                }
                """,
                timeout=15_000  # 15s para paginas lentas
            )
        except Exception:
            page.wait_for_timeout(3_000)  # Aumentado a 3s para paginas lentas

        # 0. Extraer campo "Registro" de DATOS DEL SISTEMA (ej. CRT26-025230)
        try:
            registro_val = page.evaluate('''() => {
                // Buscar label "Registro" dentro de DATOS DEL SISTEMA
                let labels = Array.from(document.querySelectorAll("label"));
                let lbl = labels.find(el => el.textContent && el.textContent.trim() === "Registro");
                if (lbl) {
                    // Puede estar en un input/textarea siguiente o en el mismo form-group
                    let parent = lbl.parentElement;
                    if (parent) {
                        let inp = parent.querySelector("input, textarea");
                        if (inp) return inp.value.trim();
                        let sib = lbl.nextElementSibling;
                        if (sib) return sib.innerText.trim();
                    }
                }
                // Fallback: buscar input cuyo id contenga "registro"
                let inp2 = document.querySelector("input[id*='registro' i], input[name*='registro' i]");
                if (inp2) return inp2.value.trim();
                return "";
            }''')
            if not registro_val and registro_esperado:
                registro_val = registro_esperado

            if registro_val:
                metadatos["registro"] = registro_val
                log.info("[WEB] Registro encontrado: %s", registro_val)
        except Exception as e:
            log.warning("[WEB] No se pudo extraer campo Registro: %s", e)

        # 1. DATOS DEL DOCUMENTO — captura completa de todos los campos
        try:
            datos_doc = None
            try:
                datos_doc = page.evaluate(r"""
                () => {
                    try {
                        function leerCampo(textoLabel) {
                            var labels = Array.from(document.querySelectorAll('label'));
                            var lbl = labels.find(function(el) {
                                var t = (el.textContent || '').trim().replace(/[*\s]+$/, '').trim();
                                return t === textoLabel || t.startsWith(textoLabel);
                            });
                            if (!lbl) return '';

                            // Estrategia 1: for/htmlFor
                            if (lbl.htmlFor) {
                                var el = document.getElementById(lbl.htmlFor);
                                if (el) {
                                    if (el.tagName === 'SELECT') {
                                        var opt = el.options[el.selectedIndex];
                                        return opt ? opt.text.trim() : '';
                                    }
                                    return (el.value || el.innerText || '').trim();
                                }
                            }

                            // Estrategia 2: col Bootstrap hermana
                            var parentCol = lbl.closest('[class*="col-"]');
                            if (parentCol) {
                                var nextCol = parentCol.nextElementSibling;
                                if (nextCol) {
                                    var sel2 = nextCol.querySelector('select');
                                    if (sel2) {
                                        var opt2 = sel2.options[sel2.selectedIndex];
                                        return opt2 ? opt2.text.trim() : '';
                                    }
                                    var ctrl = nextCol.querySelector('input:not([type="hidden"]), textarea');
                                    if (ctrl) return (ctrl.value || ctrl.innerText || '').trim();
                                    var txt = nextCol.innerText.trim();
                                    return txt;
                                }
                            }

                            // Estrategia 3: sibling directo
                            var sib = lbl.nextElementSibling;
                            while (sib) {
                                if (sib.tagName === 'SELECT') {
                                    var optS = sib.options[sib.selectedIndex];
                                    return optS ? optS.text.trim() : '';
                                }
                                var ctrlS = sib.querySelector
                                    ? sib.querySelector('input:not([type="hidden"]), textarea, select')
                                    : null;
                                if (ctrlS) {
                                    if (ctrlS.tagName === 'SELECT') {
                                        var optCS = ctrlS.options[ctrlS.selectedIndex];
                                        return optCS ? optCS.text.trim() : '';
                                    }
                                    return (ctrlS.value || ctrlS.innerText || '').trim();
                                }
                                if (sib.tagName === 'INPUT' || sib.tagName === 'TEXTAREA') {
                                    return (sib.value || sib.innerText || '').trim();
                                }
                                sib = sib.nextElementSibling;
                            }
                            return '';
                        }

                        return {
                            folio_opc:          leerCampo('Folio OPC'),
                            fecha_folio_opc:    leerCampo('Fecha de folio OPC'),
                            mensajeria:         leerCampo('Mensajería'),
                            no_guia:            leerCampo('No. de guía'),
                            no_documento:       leerCampo('No. del documento'),
                            fecha_documento:    leerCampo('Fecha del documento'),
                            numero_anexos:      leerCampo('Número de anexos'),
                            usb:                leerCampo('USB'),
                            cd_dvd:             leerCampo('CD/DVD'),
                            imagenes:           leerCampo('Imágenes'),
                            otros_dispositivos: leerCampo('Otros dispositivos'),
                            observaciones:      leerCampo('Observaciones'),
                        };
                    } catch(e) {
                        return { _js_error: e.toString() };
                    }
                }
                """)
            except Exception as e_js:
                log.warning("[WEB] page.evaluate DATOS DEL DOCUMENTO lanzó excepción: %s", e_js)

            if datos_doc is None:
                log.warning("[WEB] page.evaluate retornó None en DATOS DEL DOCUMENTO -- "
                            "intentando fallback con locators de Playwright")
                # ── Fallback: leer campo por campo con locators ────────────────
                try:
                    def _leer_campo_playwright(label_texto: str) -> str:
                        """Encuentra el valor del campo buscando el label por texto."""
                        try:
                            # Estrategia A: label con htmlFor → input por id
                            lbl = page.locator(f"label:has-text('{label_texto}')").first
                            if lbl.count() == 0:
                                return ""
                            for_attr = lbl.get_attribute("for") or ""
                            if for_attr:
                                ctrl = page.locator(f"#{for_attr}").first
                                if ctrl.count() > 0:
                                    tag = ctrl.evaluate("el => el.tagName")
                                    if tag == "SELECT":
                                        return ctrl.evaluate(
                                            "el => el.options[el.selectedIndex] ? "
                                            "el.options[el.selectedIndex].text : ''"
                                        ).strip()
                                    return (ctrl.input_value() or "").strip()
                            # Estrategia B: columna Bootstrap hermana
                            parent_row = lbl.locator("xpath=../..").first
                            if parent_row.count() > 0:
                                # Buscar input/textarea/select en la misma fila pero distinta col
                                ctrl2 = parent_row.locator(
                                    "input:not([type='hidden']), textarea, select"
                                ).last
                                if ctrl2.count() > 0:
                                    tag2 = ctrl2.evaluate("el => el.tagName")
                                    if tag2 == "SELECT":
                                        return ctrl2.evaluate(
                                            "el => el.options[el.selectedIndex] ? "
                                            "el.options[el.selectedIndex].text : ''"
                                        ).strip()
                                    return (ctrl2.input_value() or "").strip()
                        except Exception:
                            pass
                        return ""

                    campos_fallback = {
                        "folio_opc":          _leer_campo_playwright("Folio OPC"),
                        "fecha_folio_opc":    _leer_campo_playwright("Fecha de folio OPC"),
                        "mensajeria":         _leer_campo_playwright("Mensajería"),
                        "no_guia":            _leer_campo_playwright("No. de guía"),
                        "no_documento":       _leer_campo_playwright("No. del documento"),
                        "fecha_documento":    _leer_campo_playwright("Fecha del documento"),
                        "numero_anexos":      _leer_campo_playwright("Número de anexos"),
                        "usb":                _leer_campo_playwright("USB"),
                        "cd_dvd":             _leer_campo_playwright("CD/DVD"),
                        "imagenes":           _leer_campo_playwright("Imágenes"),
                        "otros_dispositivos": _leer_campo_playwright("Otros dispositivos"),
                        "observaciones":      _leer_campo_playwright("Observaciones"),
                    }
                    datos_doc = campos_fallback
                    log.info("[WEB] DATOS DEL DOCUMENTO (fallback locator): "
                             "folio_opc='%s' | no_doc='%s'",
                             datos_doc.get("folio_opc"), datos_doc.get("no_documento"))
                except Exception as e_fb:
                    log.warning("[WEB] Fallback locator también falló: %s", e_fb)
                    datos_doc = {}

            if datos_doc and not datos_doc.get("_js_error"):
                # Poblar metadatos con los valores capturados
                def _asignar(campo_meta, valor):
                    v = (valor or "").strip()
                    # Ignorar placeholders del HTML
                    if v and v not in ("Seleccione mensajeria", "Registro de No. de guía",
                                       "Seleccione mensajería"):
                        metadatos[campo_meta] = v

                _asignar("folio_opc",          datos_doc.get("folio_opc"))
                _asignar("mensajeria",         datos_doc.get("mensajeria"))
                _asignar("no_guia",            datos_doc.get("no_guia"))
                _asignar("no_documento",       datos_doc.get("no_documento"))
                _asignar("fecha_documento",    datos_doc.get("fecha_documento"))
                _asignar("numero_anexos",      datos_doc.get("numero_anexos"))
                _asignar("usb",                datos_doc.get("usb"))
                _asignar("cd_dvd",             datos_doc.get("cd_dvd"))
                _asignar("imagenes",           datos_doc.get("imagenes"))
                _asignar("otros_dispositivos", datos_doc.get("otros_dispositivos"))
                _asignar("observaciones",      datos_doc.get("observaciones"))

                log.info(
                    "[WEB] DATOS DEL DOCUMENTO: folio_opc='%s' | no_doc='%s'",
                    metadatos.get("folio_opc"), metadatos.get("no_documento"),
                )
            elif datos_doc and datos_doc.get("_js_error"):
                log.warning("[WEB] Error JS en DATOS DEL DOCUMENTO: %s", datos_doc["_js_error"])

            # Fecha de folio OPC (independiente de si el bloque anterior falló)
            fecha_opc = (
                (datos_doc.get("fecha_folio_opc") if isinstance(datos_doc, dict) else None) or ""
            ).strip()
            if fecha_opc:
                metadatos["fecha_folio_opc"] = fecha_opc
                metadatos["fecha_ejecucion"] = fecha_opc
                metadatos["fecha_registro"]  = fecha_opc
                log.info("[WEB] Fecha de folio OPC: %s", fecha_opc)
            else:
                metadatos["fecha_ejecucion"] = datetime.now().isoformat()

        except Exception as e:
            log.warning("[WEB] No se pudo extraer DATOS DEL DOCUMENTO: %s", e)
            metadatos["fecha_ejecucion"] = datetime.now().isoformat()

            
        # 2. REMITENTE(S) (ya expandido por JS al inicio de la función)
        try:
            # 2.1 Extraer Representante legal e ID (de Tabla de representantes legales)
            rep_nombre = ""
            rep_id = ""
            try:
                tablas = page.locator("table")
                for i in range(tablas.count()):
                    tabla = tablas.nth(i)
                    headers = tabla.locator("thead th")
                    if headers.count() > 0:
                        idx_nombre = -1
                        idx_id = -1
                        for j in range(headers.count()):
                            texto = headers.nth(j).inner_text().strip().lower()
                            if 'representante legal' in texto:
                                idx_nombre = j
                            elif 'id' == texto or 'id' in texto.split():
                                idx_id = j
                        
                        if idx_nombre != -1:
                            filas = tabla.locator("tbody tr")
                            if filas.count() > 0:
                                primera_fila = filas.first
                                tds = primera_fila.locator("td")
                                if tds.count() > 1 or (tds.count() == 1 and "dataTables_empty" not in (tds.first.get_attribute("class") or "")):
                                    if tds.count() > idx_nombre:
                                        rep_nombre = tds.nth(idx_nombre).inner_text().strip()
                                    if idx_id != -1 and tds.count() > idx_id:
                                        rep_id = tds.nth(idx_id).inner_text().strip()
                            break
            except Exception as e:
                log.warning("Error extrayendo representante legal: %s", e)

            # Fallback FORMATO MIGRADO: si la tabla normal vino vacia, el dato puede
            # estar en el panel "Representantes Legales de Migracion (Origen)",
            # que es un <p> de texto plano SIN ningun ID asociado.
            if not rep_nombre:
                try:
                    rep_mig_loc = page.locator("#rep_legal_migrados_texto")
                    if rep_mig_loc.count() > 0:
                        texto_mig = rep_mig_loc.first.inner_text().strip()
                        if texto_mig:
                            rep_nombre = texto_mig
                            log.info("[WEB] Representante legal obtenido de panel MIGRADO "
                                     "(sin ID, folio %s): %s", folio, rep_nombre)
                except Exception as e:
                    log.warning("Error extrayendo representante legal migrado: %s", e)

            if rep_nombre:
                metadatos["representante_legal"] = rep_nombre
            if rep_id:
                metadatos["id_representante_legal"] = rep_id
                
            # 2.2 Extraer Nombre o razon social del Operador (Solicitante) e ID
            op_nombre = ""
            op_id = ""
            try:
                tablas = page.locator("table")
                for i in range(tablas.count()):
                    tabla = tablas.nth(i)
                    headers = tabla.locator("thead th")
                    if headers.count() > 0:
                        idx_nombre = -1
                        idx_id = -1
                        for j in range(headers.count()):
                            texto = headers.nth(j).inner_text().strip().lower()
                            if 'solicitante' in texto:
                                idx_nombre = j
                            elif 'id' == texto or 'id' in texto.split():
                                idx_id = j
                        
                        if idx_nombre != -1:
                            filas = tabla.locator("tbody tr")
                            if filas.count() > 0:
                                primera_fila = filas.first
                                tds = primera_fila.locator("td")
                                if tds.count() > 1 or (tds.count() == 1 and "dataTables_empty" not in (tds.first.get_attribute("class") or "")):
                                    if tds.count() > idx_nombre:
                                        op_nombre = tds.nth(idx_nombre).inner_text().strip()
                                    if idx_id != -1 and tds.count() > idx_id:
                                        op_id = tds.nth(idx_id).inner_text().strip()
                            break
            except Exception as e:
                log.warning("Error extrayendo solicitante: %s", e)

            # Fallback FORMATO MIGRADO: si la tabla normal vino vacia, el dato puede
            # estar en el panel "Remitentes Solicitantes de Migracion (Origen)",
            # que es un <p> de texto plano SIN ningun ID asociado.
            if not op_nombre:
                try:
                    op_mig_loc = page.locator("#solicitantes_migrados_texto")
                    if op_mig_loc.count() > 0:
                        texto_mig = op_mig_loc.first.inner_text().strip()
                        if texto_mig:
                            op_nombre = texto_mig
                            log.info("[WEB] Operador/solicitante obtenido de panel MIGRADO "
                                     "(sin ID, folio %s): %s", folio, op_nombre)
                except Exception as e:
                    log.warning("Error extrayendo solicitante migrado: %s", e)

            if op_nombre:
                metadatos["nombre_operador"] = op_nombre
            if op_id:
                metadatos["id_solicitante"] = op_id
        except Exception as e:
            log.warning("[WEB] No se pudo extraer REMITENTE(S): %s", e)
            
        # 3. DESCRIPCIÓN DEL DOCUMENTO (ya expandido por JS al inicio de la función)
        try:
            # Extraer 'Tipo Trámite'
            tipo_tramite = page.evaluate(r'''() => {
                // Las etiquetas posibles para el campo en distintos formatos de SATyS
                const etiquetas = ['Tipo Trámite', 'Tipo de Trámite', 'Tipo tramite',
                                   'Tipo de tramite', 'Trámite', 'Tramite'];
                function leerLabel(texto) {
                    let labels = Array.from(document.querySelectorAll('label'));
                    let lbl = labels.find(el => {
                        let t = (el.textContent || '').trim().replace(/[:\s]+$/, '');
                        return t === texto || t.startsWith(texto);
                    });
                    if (!lbl) return '';
                    // htmlFor → input/select
                    if (lbl.htmlFor) {
                        let el = document.getElementById(lbl.htmlFor);
                        if (el) {
                            if (el.tagName === 'SELECT') {
                                let opt = el.options[el.selectedIndex];
                                return opt ? opt.text.trim() : '';
                            }
                            return (el.value || el.innerText || '').trim();
                        }
                    }
                    // Columna Bootstrap hermana
                    let parentCol = lbl.closest('[class*="col-"]');
                    if (parentCol) {
                        let nextCol = parentCol.nextElementSibling;
                        if (nextCol) {
                            let sel2 = nextCol.querySelector('select');
                            if (sel2) {
                                let opt2 = sel2.options[sel2.selectedIndex];
                                return opt2 ? opt2.text.trim() : '';
                            }
                            let ctrl = nextCol.querySelector('input:not([type="hidden"]), textarea');
                            if (ctrl) return (ctrl.value || ctrl.innerText || '').trim();
                            return nextCol.innerText.trim();
                        }
                    }
                    // Sibling directo
                    let sib = lbl.nextElementSibling;
                    while (sib) {
                        if (sib.tagName === 'SELECT') {
                            let opt = sib.options[sib.selectedIndex];
                            return opt ? opt.text.trim() : '';
                        }
                        let ctrl = sib.querySelector
                            ? sib.querySelector('input:not([type="hidden"]), textarea, select')
                            : null;
                        if (ctrl) {
                            if (ctrl.tagName === 'SELECT') {
                                let opt = ctrl.options[ctrl.selectedIndex];
                                return opt ? opt.text.trim() : '';
                            }
                            return (ctrl.value || ctrl.innerText || '').trim();
                        }
                        if (sib.tagName === 'INPUT' || sib.tagName === 'TEXTAREA')
                            return (sib.value || sib.innerText || '').trim();
                        sib = sib.nextElementSibling;
                    }
                    return '';
                }
                for (const etq of etiquetas) {
                    let v = leerLabel(etq);
                    if (v) return v;
                }
                return '';
            }''')
            if tipo_tramite:
                metadatos["tipo_tramite"] = tipo_tramite
                log.info("[WEB] Tipo Trámite capturado: %s", tipo_tramite)
        except Exception as e:
            log.warning("[WEB] No se pudo extraer Tipo Trámite: %s", e)

        try:
            # Extraer 'Asunto'
            asunto = page.evaluate('''() => {
                // Buscamos un label que diga "Asunto"
                let labels = Array.from(document.querySelectorAll('label'));
                let label = labels.find(el => el.textContent && el.textContent.includes('Asunto'));
                if(label) {
                    if (label.htmlFor) {
                        let el = document.getElementById(label.htmlFor);
                        if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) {
                            return el.value.trim();
                        }
                    }
                    let parent = label.parentElement;
                    if(parent) {
                        let ta = parent.querySelector('textarea, input[type="text"]');
                        if (ta) return ta.value.trim();
                        
                        let nextDiv = parent.nextElementSibling;
                        if(nextDiv) {
                            let ta2 = nextDiv.querySelector('textarea, input[type="text"]');
                            if (ta2) return ta2.value.trim();
                            return nextDiv.innerText.trim();
                        }
                        
                        let text = parent.innerText.replace(label.innerText, '').trim();
                        if(text) return text;
                    }
                }
                let ta_fallback = document.querySelector('textarea[name*="asunto" i], textarea[id*="asunto" i]');
                if (ta_fallback) return ta_fallback.value.trim();
                return "";
            }''')
            if asunto:
                metadatos["asunto"] = asunto
        except Exception as e:
            log.warning("[WEB] No se pudo extraer DESCRIPCIÓN DEL DOCUMENTO: %s", e)
            
        # Limpiar asunto (quitar ':\n' o similar que pueda quedar)
        if metadatos["asunto"] and metadatos["asunto"].startswith(":"):
            metadatos["asunto"] = metadatos["asunto"][1:].strip()

        # Derivar "folio" desde "folio_opc" si aún no está asignado
        # Ejemplo: folio_opc="VE-184698" → folio="184698"
        #          folio_opc="CORREO-2014" → folio="2014"
        folio_opc_val = metadatos.get("folio_opc", "") or ""
        if folio_opc_val and not metadatos.get("folio"):
            numeros = re.sub(r"[^0-9]", "", folio_opc_val)
            if numeros:
                metadatos["folio"] = numeros
                log.info("[WEB] folio derivado de folio_opc='%s' → folio='%s'",
                         folio_opc_val, numeros)

        # V-11: Convertir strings vacios a None y loguear campos faltantes
        for campo in ("representante_legal", "nombre_operador", "asunto"):
            if not metadatos.get(campo):
                metadatos[campo] = None
                log.warning("[V11-META-FALTANTE] folio=%s campo=%s es nulo/vacio",
                            folio, campo)
        if not metadatos.get("registro"):
            metadatos["registro"] = None
            log.warning("[V11-META-FALTANTE] folio=%s campo=registro es nulo/vacio", folio)


        # Guardar en archivo
        out_path = carpeta / "metadata_satys.json"
        carpeta.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(metadatos, f, ensure_ascii=False, indent=2)

        log.info("[WEB] Metadatos guardados OK: Rep='%s', Ope='%s', Asunto='%s', Tipo='%s', Registro='%s'",
                 metadatos["representante_legal"], metadatos["nombre_operador"],
                 metadatos["asunto"], metadatos.get("tipo_tramite"), metadatos["registro"])
        return metadatos
    except Exception as e:
        log.error("[WEB] Error extrayendo metadatos web: %s", e)
        return metadatos


# ------------------------------------------------------------
#  PASO 4 -- EXPANDIR "ARCHIVOS ASOCIADOS" Y DESCARGAR
# ------------------------------------------------------------
def descargar_archivos(context, page, folio: str, carpeta: Path) -> tuple:
    log.info("[FILES] Descargando archivos del folio %s...", folio)
    resultados = []

    try:
        # 4.1 Scroll al fondo
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)  # Reducido de 1500ms: solo estabilización mínima

        # 4.2 Encontrar y expandir "ARCHIVOS ASOCIADOS"
        seccion = None
        for sel in [
            "text=ARCHIVOS ASOCIADOS",
            "h4:has-text('ARCHIVOS ASOCIADOS')",
            "h5:has-text('ARCHIVOS ASOCIADOS')",
            ".panel-title:has-text('ARCHIVOS')",
            "legend:has-text('ARCHIVOS ASOCIADOS')",
            "a[data-toggle='collapse']:has-text('ARCHIVOS')",
        ]:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=2_000)
                seccion = loc
                break
            except Exception:
                continue

        if seccion is None:
            log.info("[INFO] Seccion 'ARCHIVOS ASOCIADOS' no existe en folio %s -- activar fallback", folio)
            screenshot(page, f"no_seccion_{folio}")
            return resultados, False  # (resultados, seccion_encontrada)

        seccion.scroll_into_view_if_needed()

        # Intentar expandir si esta colapsado; esperar tabla en lugar de tiempo fijo
        try:
            seccion.click()
            try:
                page.wait_for_selector(
                    "table:has(th:has-text('Nombre')), table:has(th:has-text('Acci'))",
                    timeout=5_000, state="visible"
                )
            except Exception:
                page.wait_for_timeout(600)
        except Exception:
            pass

        tabla = _encontrar_tabla_archivos(page)
        if tabla is None:
            try:
                seccion.click()
                try:
                    page.wait_for_selector(
                        "table:has(th:has-text('Nombre')), table:has(th:has-text('Acci'))",
                        timeout=4_000, state="visible"
                    )
                except Exception:
                    page.wait_for_timeout(600)
            except Exception:
                pass
            tabla = _encontrar_tabla_archivos(page)

        if tabla is None:
            log.warning(
                "[WARN] No se encontró tabla en 'ARCHIVOS ASOCIADOS' para folio %s -- "
                "no se puede confirmar si está vacía o aún no cargó bajo carga. "
                "Se intentará también DOCUMENTOS ANEXOS como respaldo.", folio
            )
            screenshot(page, f"no_tabla_{folio}")
            return resultados, False  # antes: True -- declaraba "sin archivos" sin probar el fallback

        # 4.3 Cambiar paginacion a 100 para descargar todos los archivos de una vez.
        # Buscamos el select DENTRO del contenedor de la seccion ARCHIVOS ASOCIADOS
        # (no el selector del tablero principal) para evitar cambiar la pagina equivocada.
        try:
            # Intentar encontrar el select dentro del contenedor de ARCHIVOS ASOCIADOS
            # El contenedor es el ancestro más cercano que engloba la sección
            select_mostrar = None
            # Buscar el select que está visible y cercano a la tabla de archivos
            selects_pagina = page.locator("select[name*='_length'], .dataTables_length select")
            total_selects = selects_pagina.count()
            if total_selects > 0:
                # Preferir el ÚLTIMO select visible (en la seccion ARCHIVOS ASOCIADOS
                # el select aparece al final de la pagina, por eso usamos el último)
                select_mostrar = selects_pagina.last

            if select_mostrar and select_mostrar.count() > 0:
                # Seleccionar 100 (preferido) o el valor máximo disponible
                opciones = select_mostrar.locator("option")
                mejor_val = None
                for oi in range(opciones.count()):
                    val = opciones.nth(oi).get_attribute("value") or ""
                    if val == "100":
                        mejor_val = val
                        break
                    elif val in ("-1", "50") and mejor_val is None:
                        mejor_val = val
                if mejor_val:
                    log.info("[INFO] Cambiando paginacion ARCHIVOS ASOCIADOS a '%s' elementos", mejor_val)
                    select_mostrar.select_option(value=mejor_val)
                    # Esperar que DataTables actualice la lista
                    try:
                        page.wait_for_function(
                            "() => { const p = document.querySelector('.dataTables_processing'); "
                            "return !p || p.style.display === 'none' || p.style.display === ''; }",
                            timeout=6_000
                        )
                    except Exception:
                        page.wait_for_timeout(800)
                    tabla = _encontrar_tabla_archivos(page)
        except Exception:
            pass

        # 4.4 Descargar archivos con paginacion
        pagina_arch = 0
        max_paginas_arch = 50  # Limite de seguridad
        contador_global = 0

        while pagina_arch < max_paginas_arch:
            pagina_arch += 1

            # Re-buscar tabla y botones en cada pagina (evitar locators stale)
            if pagina_arch > 1:
                tabla = _encontrar_tabla_archivos(page)
                if tabla is None:
                    break

            botones = _encontrar_botones_descarga(tabla)

            if not botones:
                if pagina_arch == 1:
                    log.warning("[WARN] No se encontraron botones de descarga para folio %s", folio)
                    screenshot(page, f"no_botones_{folio}")
                break

            log.info("[INFO] Pagina %d: %d archivo(s) encontrado(s)", pagina_arch, len(botones))

            # Descargar cada archivo de esta pagina
            for i, btn_info in enumerate(botones, 1):
                contador_global += 1
                nombre_previo = btn_info["nombre"]
                log.info("  [%d] Descargando: %s", contador_global, nombre_previo)

                try:
                    btn = btn_info["locator"]
                    btn.scroll_into_view_if_needed()

                    href = btn.get_attribute("href")
                    data_url = btn.get_attribute("data-url") or btn.get_attribute("data-href")
                    onclick = btn.get_attribute("onclick")
                    url = ""
                    if href and not href.lower().startswith("javascript"):
                        url = _absolutizar_url(page, href)
                    elif data_url:
                        url = _absolutizar_url(page, data_url)
                    elif onclick:
                        url = _absolutizar_url(page, _extraer_url_from_onclick(onclick))

                    if API_DISCOVERY and onclick:
                        _write_jsonl(API_LOG_PATH, {
                            "type": "onclick",
                            "ts": datetime.now().isoformat(),
                            "raw": onclick,
                        })

                    fname = nombre_previo or f"archivo_{folio}_{contador_global}"
                    if url and not Path(fname).suffix:
                        url_name = Path(urlparse(url).path).name
                        if url_name:
                            fname = url_name

                    dest = carpeta / fname

                    # ── REINTENTOS POR ARCHIVO: hasta 3 intentos antes de marcar ERROR_SERVIDOR ──
                    # Si el archivo falla 3 veces seguidas se considera que no está disponible en
                    # el servidor (error externo) y se continúa con el siguiente archivo.
                    MAX_INTENTOS_ARCHIVO = 3
                    descargado_ok = False
                    archivo_ok_final = False

                    for _intento_archivo in range(1, MAX_INTENTOS_ARCHIVO + 1):
                        descargado_ok = False

                        # ── Intento 1: descarga directa rápida (sin clic) ──
                        # V-07: Verificar tamano y reintentar si el archivo es 0 KB
                        if url:
                            for _reintentoD in range(3):  # hasta 3 sub-intentos internos
                                intento_ok = _descargar_directo(context, page, url, dest)
                                if intento_ok:
                                    size_kb = dest.stat().st_size / 1024
                                    if size_kb > 0:
                                        descargado_ok = True
                                        break
                                    else:
                                        log.warning("[V07] Archivo 0 KB en descarga directa: %s (intento %d)",
                                                    fname, _reintentoD + 1)
                                        dest.unlink(missing_ok=True)
                                        page.wait_for_timeout(3_000)
                                else:
                                    break

                        if descargado_ok:
                            extraidos = extraer_zip_si_aplica(dest, carpeta)
                            if extraidos:
                                for ex in extraidos:
                                    size_ex = ex.stat().st_size / 1024 if ex.exists() else 0
                                    resultados.append({
                                        "folio": folio, "archivo": ex.name,
                                        "tipo": ex.suffix.upper().lstrip("."),
                                        "ruta": str(ex), "tamano_kb": round(size_ex, 1),
                                        "ok": True, "url": url,
                                    })
                                log.info("     [OK] Descarga directa (ZIP extraído): %s (%d archivos)", fname, len(extraidos))
                            else:
                                log.info("     [OK] Descarga directa: %s  (%.1f KB)", fname, size_kb)
                                resultados.append({
                                    "folio": folio, "archivo": fname,
                                    "tipo": Path(fname).suffix.upper().lstrip("."),
                                    "ruta": str(dest), "tamano_kb": round(size_kb, 1),
                                    "ok": True, "url": url,
                                })
                            archivo_ok_final = True
                            break  # Archivo descargado OK, salir del bucle de reintentos

                        # ── Intento 2: descarga por clic en el botón (respaldo) ──
                        try:
                            dl_obj = None
                            np_obj = None

                            def on_dl(d): nonlocal dl_obj; dl_obj = d
                            def on_pg(p): nonlocal np_obj; np_obj = p

                            page.on("download", on_dl)
                            context.on("page", on_pg)

                            log.info("     [DL-CLICK] Click inicio: %s", nombre_previo)
                            btn.click()
                            log.info("     [DL-CLICK] Click enviado: %s", nombre_previo)

                            import time
                            start_t = time.time()
                            timeout_sec = TIMEOUT_DL / 1000.0
                            while time.time() - start_t < timeout_sec:
                                if dl_obj or np_obj:
                                    break
                                page.wait_for_timeout(200)

                            log.info("     [DL-WAIT] Resultado eventos: download=%s popup=%s archivo=%s", bool(dl_obj), bool(np_obj), nombre_previo)

                            if dl_obj:
                                fname = dl_obj.suggested_filename or fname
                                dest = carpeta / fname
                                log.info("     [DL-SAVE] save_as inicio: %s -> %s", nombre_previo, dest)
                                dl_obj.save_as(str(dest))
                                log.info("     [DL-SAVE] save_as fin: %s", dest.name)
                                extraidos = extraer_zip_si_aplica(dest, carpeta)

                                if extraidos:
                                    for ex in extraidos:
                                        size_ex = ex.stat().st_size / 1024 if ex.exists() else 0
                                        resultados.append({
                                            "folio": folio, "archivo": ex.name,
                                            "tipo": ex.suffix.upper().lstrip("."),
                                            "ruta": str(ex), "tamano_kb": round(size_ex, 1), "ok": True,
                                            "url": dl_obj.url if hasattr(dl_obj, "url") and dl_obj.url else url,
                                        })
                                    log.info("     [OK] Guardado (ZIP extraído): %s (%d archivos)", fname, len(extraidos))
                                else:
                                    size_kb = dest.stat().st_size / 1024 if dest.exists() else 0
                                    log.info("     [OK] Guardado: %s  (%.1f KB)", fname, size_kb)
                                    resultados.append({
                                        "folio": folio, "archivo": fname,
                                        "tipo": Path(fname).suffix.upper().lstrip("."),
                                        "ruta": str(dest), "tamano_kb": round(size_kb, 1), "ok": True,
                                        "url": dl_obj.url if hasattr(dl_obj, "url") and dl_obj.url else url,
                                    })
                                archivo_ok_final = True
                                break  # Archivo descargado OK, salir del bucle de reintentos

                            elif np_obj:
                                log.info("     [POPUP] Nueva pestana capturada (PDF) en ARCHIVOS ASOCIADOS")
                                url_popup = ""
                                try:
                                    np_obj.wait_for_load_state("domcontentloaded", timeout=10_000)
                                    url_popup = np_obj.url
                                    np_obj.close()
                                except Exception as ep:
                                    log.info("     [POPUP] No se pudo leer URL: %s", ep)

                                if url_popup and url_popup != page.url:
                                    from urllib.parse import urlparse
                                    url_fname = Path(urlparse(url_popup).path).name
                                    if url_fname:
                                        dest_popup = carpeta / url_fname
                                    else:
                                        dest_popup = dest

                                    ok_popup = _descargar_directo(context, page, url_popup, dest_popup)
                                    if ok_popup and dest_popup.stat().st_size > 0:
                                        extraidos = extraer_zip_si_aplica(dest_popup, carpeta)
                                        if extraidos:
                                            for ex in extraidos:
                                                size_ex = ex.stat().st_size / 1024 if ex.exists() else 0
                                                resultados.append({
                                                    "folio": folio, "archivo": ex.name,
                                                    "tipo": ex.suffix.upper().lstrip("."),
                                                    "ruta": str(ex), "tamano_kb": round(size_ex, 1), "ok": True,
                                                    "url": url_popup,
                                                })
                                            log.info("     [OK-POPUP] Guardado (ZIP extraído): %s (%d archivos)", dest_popup.name, len(extraidos))
                                        else:
                                            size_kb = dest_popup.stat().st_size / 1024 if dest_popup.exists() else 0
                                            log.info("     [OK-POPUP] Guardado: %s (%.1f KB)", dest_popup.name, size_kb)
                                            resultados.append({
                                                "folio": folio, "archivo": dest_popup.name,
                                                "tipo": dest_popup.suffix.upper().lstrip("."),
                                                "ruta": str(dest_popup), "tamano_kb": round(size_kb, 1), "ok": True,
                                                "url": url_popup,
                                            })
                                        archivo_ok_final = True
                                        break  # Archivo descargado OK, salir del bucle de reintentos
                                    else:
                                        log.warning("     [REINTENTO %d/%d] Fallo descarga popup: %s",
                                                    _intento_archivo, MAX_INTENTOS_ARCHIVO, nombre_previo)
                                else:
                                    log.warning("     [REINTENTO %d/%d] Popup sin URL valida: %s",
                                                _intento_archivo, MAX_INTENTOS_ARCHIVO, nombre_previo)
                            else:
                                log.warning("     [REINTENTO %d/%d] Timeout sin descarga ni popup: %s",
                                            _intento_archivo, MAX_INTENTOS_ARCHIVO, nombre_previo)

                        except Exception as _e_click:
                            log.warning("     [REINTENTO %d/%d] Error en clic: %s — %s",
                                        _intento_archivo, MAX_INTENTOS_ARCHIVO, nombre_previo, _e_click)

                        finally:
                            try:
                                page.remove_listener("download", on_dl)
                            except Exception:
                                pass
                            try:
                                context.remove_listener("page", on_pg)
                            except Exception:
                                pass
                            _cerrar_paginas_emergentes(
                                context,
                                page,
                                motivo=f"descarga de {nombre_previo}",
                            )

                        if _intento_archivo < MAX_INTENTOS_ARCHIVO:
                            page.wait_for_timeout(3_000)  # Esperar antes del siguiente reintento

                    # Fin del bucle de reintentos por archivo
                    if not archivo_ok_final:
                        log.error("     [ERROR_SERVIDOR] %s — no disponible tras %d intentos (problema externo)",
                                  nombre_previo, MAX_INTENTOS_ARCHIVO)
                        resultados.append({
                            "folio": folio, "archivo": nombre_previo,
                            "tipo": "ERROR_SERVIDOR", "ruta": "", "tamano_kb": 0, "ok": False
                        })

                except PWTimeout:
                    log.error("     [ERROR] Timeout descargando %s", nombre_previo)
                    resultados.append({
                        "folio": folio, "archivo": nombre_previo,
                        "tipo": "TIMEOUT", "ruta": "", "tamano_kb": 0, "ok": False
                    })
                except Exception as e:
                    log.error("     [ERROR] %s", e)
                    resultados.append({
                        "folio": folio, "archivo": nombre_previo,
                        "tipo": "ERROR", "ruta": "", "tamano_kb": 0, "ok": False
                    })

            # 4.5 Verificar paginacion de ARCHIVOS ASOCIADOS
            mostrado_hasta, total = _parsear_paginacion(page)
            if total > 0:
                log.info("[PAGE] Archivos: mostrando %d de %d total", mostrado_hasta, total)

            if total == 0 or mostrado_hasta >= total:
                break  # Todos los archivos descargados o no hay paginacion

            log.info("[PAGE] Hay mas paginas de archivos, avanzando...")
            # V-05: Usar version mejorada que verifica doble condicion
            if not _avanzar_pagina_datatables_v2(page, mostrado_hasta, total):
                log.info("[PAGE] No se pudo avanzar, fin de paginas de archivos")
                break

        ok_c = sum(1 for r in resultados if r["ok"])
        total_r = len(resultados)
        log.info("[INFO] Folio %s: %d/%d descargados en %d pagina(s)",
                 folio, ok_c, total_r, pagina_arch)

        # V-10: Verificacion de totales en flujo ARCHIVOS ASOCIADOS
        mostrado_hasta_fin, total_fin = _parsear_paginacion(page)
        if total_fin > 0 and ok_c < total_fin:
            log.warning("[V10-VERIFICACION] Folio %s: esperados=%d  descargados_ok=%d  errores=%d",
                        folio, total_fin, ok_c, total_r - ok_c)
        elif total_fin > 0:
            log.info("[V10-VERIFICACION-OK] Folio %s: %d/%d archivos OK",
                     folio, ok_c, total_fin)

        return resultados, True  # (resultados, seccion_encontrada)

    except Exception as e:
        log.error("[ERROR] General en archivos de folio %s: %s", folio, e)
        screenshot(page, f"error_archivos_{folio}")
        return resultados, True  # Seccion existia, fallo durante descarga


def _encontrar_tabla_archivos(page):
    candidatos = page.locator("table").filter(
        has_text=re.compile(r"Nombre archivo|Acci[oó]n|Tipo", re.I)
    )
    for i in range(candidatos.count()):
        tabla = candidatos.nth(i)
        try:
            tabla.wait_for(state="visible", timeout=2_000)
            return tabla
        except Exception:
            continue
    return None


def _encontrar_botones_descarga(root) -> list:
    """Busca botones de descarga en la tabla de ARCHIVOS ASOCIADOS."""
    botones = []

    try:
        filas = root.locator("tbody tr")
        filas_count = filas.count()
        for idx in range(filas_count):
            fila = filas.nth(idx)
            
            # Obtener nombre del archivo de la primera columna
            celdas = fila.locator("td")
            if celdas.count() == 0:
                continue
                
            nombre = celdas.nth(0).inner_text().strip()
            if not nombre:
                nombre = f"archivo_{idx + 1}"
                
            # Estrategias para encontrar el boton en esta fila
            boton_encontrado = None
            
            selectores = [
                # Estrategia 1: links
                "a[href*='download']", "a[href*='Download']", 
                "a[href*='descargar']", "a[href*='archivo']",
                "button[onclick*='descarg']",
                # Estrategia 2: iconos
                "a:has(i[class*='download'])", "button:has(i[class*='download'])",
                "a:has(i[class*='cloud'])", "button:has(i[class*='cloud'])",
                "a:has(.fa-download)", "button:has(.fa-download)",
                "a:has(.glyphicon-download)", "button:has(.glyphicon-download)",
                "a:has(.icon-download)", "button:has(.icon-download)",
                "a:has(i[class*='cloud-download'])", "button:has(i[class*='cloud-download'])"
            ]
            
            for sel in selectores:
                try:
                    loc = fila.locator(sel).first
                    if loc.count() > 0:
                        boton_encontrado = loc
                        break
                except Exception:
                    continue
                    
            # Estrategia 3: ultima columna (fallback)
            if boton_encontrado is None and celdas.count() >= 2:
                try:
                    ultima = celdas.nth(celdas.count() - 1)
                    links = ultima.locator("a, button").first
                    if links.count() > 0:
                        boton_encontrado = links
                except Exception:
                    pass
                    
            if boton_encontrado is not None:
                botones.append({
                    "locator": boton_encontrado,
                    "nombre": nombre
                })
                
    except Exception as e:
        pass

    return botones


# ------------------------------------------------------------
#  PASO 5 -- REGRESAR AL TABLERO
# ------------------------------------------------------------
def volver_al_tablero(page):
    """
    Regresa al tablero de Documentos en Proceso.
    En una SPA, en vez de navegar a otra URL, hacemos click
    en el menu lateral de nuevo.
    """
    log.info("[BACK] Regresando al tablero...")
    try:
        # Estrategia 1: Click directo en "Enlace Oficialia de Partes" en el menu
        # Si el menu sigue visible, esto es suficiente
        oficialia = page.locator("a").filter(
            has_text=re.compile(r"Oficial[ií]a de Partes", re.I)
        ).first
        try:
            oficialia.wait_for(state="visible", timeout=3_000)
            oficialia.click()
            try:
                page.wait_for_selector("text=Tablero de Control", timeout=10_000)
                log.info("[OK] De vuelta en el tablero")
                return
            except PWTimeout:
                pass
        except Exception:
            pass

        # Estrategia 2: Re-navegar completamente
        log.info("[BACK] Re-navegando al tablero...")
        navegar_a_tablero(page)

    except Exception as e:
        log.warning("[WARN] Error al regresar (%s), re-navegando...", e)
        try:
            navegar_a_tablero(page)
        except Exception:
            pass


# ------------------------------------------------------------
#  MODO REGISTRO -- CONFIGURAR TABLERO Y BUSCAR POR REGISTRO
# ------------------------------------------------------------

# Variable global que indica si el tablero ya fue configurado en modo registro
# (Todos los años + 100 trámites). Se resetea a False cada vez que se navega
# al tablero desde cero para que el próximo worker también configure.
_TABLERO_REGISTRO_CONFIGURADO = False
_TABLERO_REGISTRO_LOCK = threading.Lock()


def extraer_anio_de_registro(registro: str) -> str | None:
    """
    Extrae el año completo de 4 dígitos desde un número de registro SATyS.

    Ejemplos:
      'CRT26-028792' -> '2026'
      'CRT25-005481' -> '2025'
      'CRT24-001234' -> '2024'

    Retorna None si no se puede extraer el año.
    """
    m = re.search(r"[A-Z]+(\d{2})-", registro.strip().upper())
    if m:
        sufijo = m.group(1)  # ej: '26', '25'
        # Convertir a año completo: '26' -> 2026, '25' -> 2025
        # Suponemos siglo 21 (20xx) para rangos 00-99
        anio_int = 2000 + int(sufijo)
        return str(anio_int)  # ej: '2026'
    return None


def configurar_tablero_para_busqueda_registro(page, anio_objetivo: str = None) -> bool:
    """
    Configura el tablero de 'Documentos en Proceso' para buscar por Registro:
      1. Selecciona el año del registro en el selector (ej. '2026' para CRT26-).
         Si no existe esa opción, hace fallback a 'Todos los años'.
      2. Cambia el selector 'Mostrar 10 trámites' a '100'.

    Debe llamarse UNA VEZ por worker, justo después de navegar_a_tablero().
    Retorna True si se configuró correctamente, False si falló (el tablero
    puede seguir funcionando con la configuración por defecto).
    """
    log.info("[REG] Configurando tablero para búsqueda por Registro (Año=%s + 100)...",
             anio_objetivo or "Todos los años")
    try:
        # ── 1. Selector de Año ────────────────────────────────────────────────
        # El <select> de Año está a la derecha de "Documentos en Proceso".
        # Buscar por el texto visible "Año:" cercano o por los valores conocidos.
        anio_ok = False
        try:
            # Intentar encontrar el select que contiene la opción del año actual
            # Selectores posibles: select con options como "2026", "2025", "Todos los años"
            selects_anio = page.locator("select").filter(
                has_text=re.compile(r"20\d\d|Todos", re.I)
            )
            count_anio = selects_anio.count()
            if count_anio > 0:
                sel_anio = selects_anio.first
                opciones_año = sel_anio.locator("option")

                # ── Estrategia de selección ────────────────────────────────────
                # 1º: Si tenemos un año objetivo (ej '2026'), intentar seleccionarlo
                # 2º: Si no existe esa opción, hacer fallback a 'Todos los años'
                mejor_val = None
                mejor_texto = None
                fallback_val = None
                fallback_texto = None

                for oi in range(opciones_año.count()):
                    opt = opciones_año.nth(oi)
                    val   = opt.get_attribute("value") or ""
                    texto = opt.inner_text().strip()
                    texto_l = texto.lower()

                    # Guardar opción de 'Todos los años' como fallback
                    if "todos" in texto_l or val in ("", "0", "all"):
                        fallback_val  = val
                        fallback_texto = texto

                    # Buscar el año objetivo exacto (ej '2026' en texto o en value)
                    if anio_objetivo and (texto == anio_objetivo or val == anio_objetivo):
                        mejor_val  = val
                        mejor_texto = texto
                        break  # Encontrado exacto, no seguir buscando

                # Si no encontramos el año objetivo, usar el fallback
                if mejor_val is None:
                    if anio_objetivo:
                        log.warning(
                            "[REG] Año '%s' no encontrado en el selector. "
                            "Haciendo fallback a '%s'",
                            anio_objetivo, fallback_texto or "Todos los años"
                        )
                    mejor_val  = fallback_val
                    mejor_texto = fallback_texto

                if mejor_val is not None:
                    log.info("[REG] Cambiando Año a '%s' (value='%s')...", mejor_texto, mejor_val)
                    sel_anio.select_option(value=mejor_val)
                    # Esperar spinner de carga que aparece al cambiar de año
                    _esperar_sin_spinner(page, timeout_ms=20_000)
                    # Esperar también que el campo de búsqueda sea visible de nuevo
                    try:
                        page.wait_for_selector(
                            "input[type='search'], .dataTables_filter input",
                            timeout=10_000, state="visible"
                        )
                    except Exception:
                        page.wait_for_timeout(1_500)
                    anio_ok = True
                    log.info("[REG] ✅ Año cambiado a '%s'", mejor_texto)
                else:
                    log.warning("[REG] No se encontró opción válida en el selector de Año")
            else:
                log.warning("[REG] No se encontró selector de Año en el tablero")
        except Exception as e:
            log.warning("[REG] Error cambiando selector de Año: %s", e)

        # ── 2. Selector de cantidad de trámites (Mostrar X) ─────────────────
        # El <select> de DataTables para el número de filas por página.
        # En el tablero principal hay un solo select de este tipo.
        mostrar_ok = False
        try:
            selects_pagina = page.locator("select[name*='_length'], .dataTables_length select")
            count_pag = selects_pagina.count()
            if count_pag > 0:
                sel_mostrar = selects_pagina.first
                opciones_mostrar = sel_mostrar.locator("option")
                mejor_val_m = None
                for oi in range(opciones_mostrar.count()):
                    val = opciones_mostrar.nth(oi).get_attribute("value") or ""
                    if val == "100":
                        mejor_val_m = val
                        break
                    elif val in ("-1", "50") and mejor_val_m is None:
                        mejor_val_m = val  # Fallback al máximo disponible
                if mejor_val_m:
                    log.info("[REG] Cambiando 'Mostrar' a %s trámites...", mejor_val_m)
                    sel_mostrar.select_option(value=mejor_val_m)
                    try:
                        page.wait_for_function(
                            "() => { const p = document.querySelector('.dataTables_processing'); "
                            "return !p || p.style.display === 'none' || p.style.display === ''; }",
                            timeout=8_000
                        )
                    except Exception:
                        page.wait_for_timeout(800)
                    mostrar_ok = True
                    log.info("[REG] ✅ Mostrar cambiado a %s trámites", mejor_val_m)
                else:
                    log.warning("[REG] No se encontró opción '100' en el selector de trámites")
            else:
                log.warning("[REG] No se encontró selector 'Mostrar X trámites'")
        except Exception as e:
            log.warning("[REG] Error cambiando selector de cantidad: %s", e)

        return anio_ok or mostrar_ok  # Éxito parcial cuenta

    except Exception as e:
        log.error("[REG] Error crítico configurando tablero: %s", e)
        return False


def buscar_registro_en_tabla(page, registro: str) -> bool:
    """
    Busca el número de registro en el campo de búsqueda del DataTable y
    verifica que la tabla tenga al menos una fila donde la columna 'Registro'
    coincida exactamente con el valor buscado.

    Retorna True SOLO si se encontró una fila con el registro exacto.
    Retorna False si no hay resultados o el filtro no produjo el registro buscado.
    """
    try:
        # Asegurar pestaña "Documentos en Proceso" --- SOLO clicar si NO está activa ya.
        # Si hacemos clic cuando ya estamos en esa pestaña, el portal puede recargar
        # la tabla y resetear el año que acabamos de configurar (ej. 2025 → 2026).
        try:
            tab = page.locator("a, button, li").filter(
                has_text=re.compile(r"Documentos en Proceso", re.I)
            ).first
            tab.wait_for(state="visible", timeout=3_000)
            # Solo clicar si la pestaña NO está marcada como activa
            clases_tab  = (tab.get_attribute("class") or "").lower()
            # Buscar también en el elemento padre (algunos frameworks ponen 'active' en <li>)
            try:
                padre_clase = (tab.locator("xpath=..").get_attribute("class") or "").lower()
            except Exception:
                padre_clase = ""
            ya_activa = any(
                c in clases_tab or c in padre_clase
                for c in ("active", "selected", "current", "is-active")
            )
            if not ya_activa:
                log.debug("[REG-BUSCAR] Activando pestaña 'Documentos en Proceso'")
                tab.click()
                try:
                    page.wait_for_selector(
                        "input[type='search'], .dataTables_filter input",
                        timeout=5_000, state="visible"
                    )
                except Exception:
                    page.wait_for_timeout(600)
            else:
                log.debug("[REG-BUSCAR] Pestaña ya activa, no se hace clic para preservar año configurado")
        except Exception:
            pass

        # Campo de búsqueda de DataTables
        search = page.locator(
            "input[type='search'], "
            ".dataTables_filter input, "
            "input.form-control[placeholder*='uscar']"
        ).first
        search.wait_for(state="visible", timeout=TIMEOUT_CORTO)
        try:
            search.click(click_count=3)
        except Exception:
            search.click()
            page.keyboard.press("Control+A")
        search.fill(registro)

        # Esperar que DataTables active el spinner de procesamiento
        try:
            page.wait_for_function(
                "() => { const p = document.querySelector('.dataTables_processing'); "
                "return p && (p.style.display === 'block' || p.style.display === ''); }",
                timeout=2_000
            )
        except Exception:
            pass  # El spinner puede no aparecer si el filtro es muy rápido

        # Esperar que DataTables termine de filtrar (spinner desaparece)
        try:
            page.wait_for_function(
                "() => { const p = document.querySelector('.dataTables_processing'); "
                "return !p || p.style.display === 'none' || p.style.display === ''; }",
                timeout=10_000
            )
        except Exception:
            page.wait_for_timeout(1_000)

        # Espera adicional para asegurar que las filas del DOM se actualizaron
        # (el spinner puede desaparecer antes de que el filtro textual termine de renderizar)
        page.wait_for_timeout(400)

        # Verificar que hay filas y que alguna tiene el registro correcto
        tbody = page.locator("table tbody").first
        filas = tbody.locator("tr")
        n_filas = filas.count()

        if n_filas == 0:
            log.debug("[REG-BUSCAR] Tabla vacía después de filtrar por '%s'", registro)
            return False
        primera = filas.first.inner_text().lower()
        if "no hay" in primera or "no data" in primera or "sin resultados" in primera:
            log.debug("[REG-BUSCAR] Tabla sin resultados para '%s'", registro)
            return False

        # Verificar que alguna fila tiene exactamente nuestro registro
        # (NO usar fallback: si el DataTable tiene 14 filas sin filtrar,
        #  el fallback producía falsos positivos y REGISTRO_NO_ENCONTRADO posterior)
        col_idx = 1  # Columna "Registro" normalmente en índice 1
        try:
            headers = page.locator("table thead th")
            for j in range(min(headers.count(), 10)):
                header_text = headers.nth(j).inner_text().lower()
                if "registro" in header_text and "fecha" not in header_text:
                    col_idx = j
                    break
        except Exception:
            pass

        for i in range(min(n_filas, 10)):
            fila = filas.nth(i)
            celdas = fila.locator("td")
            if celdas.count() > col_idx:
                reg_celda = celdas.nth(col_idx).inner_text().strip()
                if reg_celda.lower() == registro.lower():
                    log.debug("[REG-BUSCAR] Coincidencia exacta en fila %d: '%s'", i, reg_celda)
                    return True

        # Si no encontramos la coincidencia exacta, el filtro no funcionó aún
        # o el DataTable mostró filas que no corresponden al registro
        log.debug(
            "[REG-BUSCAR] '%s' no encontrado exacto en %d fila(s) visibles — "
            "posible tabla sin filtrar o race condition", registro, n_filas
        )
        return False

    except Exception as e:
        log.debug("[REG-BUSCAR] Excepción en búsqueda de '%s': %s", registro, e)
        return False


def buscar_registro_en_tabla_con_reintentos(page, registro: str,
                                             max_espera_seg: int = 60,
                                             espera_entre_intentos: int = 3) -> bool:
    """
    Igual que buscar_registro_en_tabla(), pero con un bucle de reintentos
    (mismo patron que extraer_metadatos_satys(): hasta max_espera_seg segundos
    -- 1 minuto por defecto -- probando cada espera_entre_intentos segundos).

    Motivo: buscar_registro_en_tabla() por si sola solo intenta UNA vez. Si el
    DataTable de 'Documentos en Proceso' todavia no termino de filtrar/renderizar
    (algo frecuente cuando varios workers de Playwright golpean el tablero al
    mismo tiempo), esa unica pasada puede leer la tabla vacia y el registro se
    marca como REGISTRO_NO_ENCONTRADO de forma erronea -- sin haber creado
    siquiera un JSON, porque el codigo nunca llega a abrir el detalle del
    tramite. Reintentar aqui, igual que se hace para los metadatos, evita esos
    falsos negativos.
    """
    intento = 0
    inicio = time.time()

    while True:
        intento += 1
        log.info("[REG-BUSCAR] Buscando registro %s en tabla (intento %d)...", registro, intento)
        if buscar_registro_en_tabla(page, registro):
            if intento > 1:
                log.info("[REG-BUSCAR] Registro %s encontrado en intento %d", registro, intento)
            return True

        transcurrido = time.time() - inicio
        if transcurrido >= max_espera_seg:
            log.warning(
                "[REG-BUSCAR] LIMITE alcanzado (%ds) buscando registro %s tras %d intentos -- "
                "se marca como no encontrado.", max_espera_seg, registro, intento,
            )
            return False

        log.warning("[REG-BUSCAR] Intento %d fallido buscando registro %s. Reintentando en %ds...",
                    intento, registro, espera_entre_intentos)
        try:
            page.wait_for_timeout(espera_entre_intentos * 1000)
        except Exception as e:
            log.error("[REG-BUSCAR] Navegador/pagina cerrado durante la espera para registro %s "
                      "(%s) -- se marca como no encontrado.", registro, e)
            return False


def procesar_registro_completo(context, page, registro: str, carpeta: Path, folio_raw: str = "") -> list:
    """
    Procesa TODOS los trámites que coinciden con un número de Registro.
    Funciona igual que procesar_folio_completo() pero la coincidencia de fila
    se hace comparando la columna 'Registro' (índice 1) con el registro buscado,
    en lugar de la columna 'Memo / Folio OPC'.

    El folio real se extrae de los metadatos de la fila (columna 'Memo / Folio OPC')
    para guardarlo en metadata_satys.json.
    """
    todos_resultados = []
    registros_procesados = set()
    indice_registro = 0
    max_iteraciones = 50

    for iteracion in range(max_iteraciones):
        # V-03: Verificar sesión
        if not _verificar_sesion(page):
            log.error("[V03] Sesión no recuperable -- abortando registro %s", registro)
            break

        # Buscar el registro en la tabla.
        # En la primera iteracion se usa la version CON reintentos (hasta 1 min)
        # para no confundir "el DataTable todavia no cargo" con "el registro
        # no existe". En iteraciones siguientes (buscando mas filas del mismo
        # registro) una sola pasada es suficiente: ya sabemos que el tablero
        # esta listo porque acabamos de volver de procesar la fila anterior.
        if iteracion == 0:
            encontrado_en_tabla = buscar_registro_en_tabla_con_reintentos(page, registro)
        else:
            encontrado_en_tabla = buscar_registro_en_tabla(page, registro)

        if not encontrado_en_tabla:
            if iteracion == 0:
                log.warning("[REG-NO_ENCONTRADO] Registro %s no encontrado en Documentos en Proceso "
                            "(tras reintentos durante 60s)", registro)
                screenshot(page, f"registro_no_encontrado_{registro}")
                # Guardar un JSON minimo de diagnostico aunque no se haya
                # encontrado nada -- así Partes 3/4 y quien revise la carpeta
                # despues pueden ver POR QUE no hay archivos, en vez de
                # encontrar la carpeta del registro completamente vacia.
                try:
                    carpeta.mkdir(parents=True, exist_ok=True)
                    with open(carpeta / "metadata_satys.json", "w", encoding="utf-8") as f:
                        json.dump({
                            "registro": registro,
                            "folio": None,
                            "nombre_operador": None,
                            "representante_legal": None,
                            "error": "REGISTRO_NO_ENCONTRADO",
                            "detalle": "No se encontro el registro en el tablero 'Documentos en "
                                       "Proceso' tras reintentar durante 60s.",
                            "fecha_proceso": datetime.now().isoformat(),
                        }, f, ensure_ascii=False, indent=2)
                except Exception as e_json:
                    log.warning("[REG] No se pudo guardar JSON de diagnostico para %s: %s",
                                registro, e_json)
                todos_resultados.append({
                    "folio": registro,
                    "archivo": "", "nombre_original": "",
                    "tipo": "", "fecha": "", "descripcion": "",
                    "ruta": "", "tamano_kb": 0, "ok": False,
                    "error": "REGISTRO_NO_ENCONTRADO",
                    "fuente": "NINGUNA",
                })
            break

        # Obtener info de paginación
        mostrado_hasta, total_resultados = _parsear_paginacion(page)
        if total_resultados > 0:
            log.info("[REG-PAGE] Resultados búsqueda: mostrando %d de %d trámites",
                     mostrado_hasta, total_resultados)

        # Obtener índice de la columna "Registro" (normalmente índice 1)
        col_registro_idx = 1
        try:
            headers = page.locator("table thead th")
            for j in range(headers.count()):
                header_text = headers.nth(j).inner_text().lower()
                if "registro" in header_text and "fecha" not in header_text:
                    col_registro_idx = j
                    break
        except Exception:
            pass

        # Obtener índice de la columna "Memo / Folio OPC" para leer el folio
        col_folio_idx = _obtener_col_idx_folio(page)

        # Buscar la fila con nuestro registro en todas las páginas
        fila_encontrada = False
        max_paginas = 20

        for num_pagina in range(max_paginas):
            tbody = page.locator("table tbody").first
            filas = tbody.locator("tr")
            filas_count = filas.count()

            for i in range(filas_count):
                fila = filas.nth(i)
                celdas = fila.locator("td")

                # Leer columna Registro
                if celdas.count() > col_registro_idx:
                    reg_celda = celdas.nth(col_registro_idx).inner_text().strip()
                else:
                    reg_celda = fila.inner_text()

                # Coincidir exactamente con el registro buscado
                if reg_celda.lower() != registro.lower():
                    continue

                # Evitar reprocesar el mismo registro
                if reg_celda in registros_procesados:
                    continue

                # Leer el Memo/Folio OPC de esta fila para usarlo como folio real
                folio_real = ""
                if celdas.count() > col_folio_idx:
                    folio_real = celdas.nth(col_folio_idx).inner_text().strip()

                # Encontrar botón Ver detalle
                boton_ver = _encontrar_boton_ver(fila)
                if boton_ver is None:
                    log.warning("[REG-WARN] No se encontró botón Ver para registro %s", reg_celda)
                    if reg_celda:
                        registros_procesados.add(reg_celda)
                    continue

                # Marcar como procesado y abrir detalle
                registros_procesados.add(reg_celda)
                log.info("[REG-VIEW] Abriendo detalle: registro=%s, folio=%s", reg_celda, folio_real)

                # Carpeta para este registro
                carpeta_actual = carpeta_para_registro(carpeta, indice_registro, reg_celda)
                if indice_registro > 0:
                    log.warning(
                        "[REG-MULTI] Registro %s tiene más de un trámite -- "
                        "usando carpeta separada: %s",
                        registro, carpeta_actual,
                    )
                indice_registro += 1

                boton_ver.scroll_into_view_if_needed()
                boton_ver.click()

                # Esperar página de detalle
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_DETALLE)
                except PWTimeout:
                    pass
                _esperar_sin_spinner(page, timeout_ms=15_000)

                if not esperar_detalle(page, registro):
                    en_tablero = (
                        page.locator("text=Documentos en Proceso").count() > 0
                        and page.locator("input[type='search']").count() > 0
                    )
                    if en_tablero:
                        log.error("[REG-ERROR] No se entró a detalle para registro %s", registro)
                        fila_encontrada = True
                        break
                    log.warning("[REG-WARN] Detalle no confirmado, continuando...")

                log.info("[REG-OK] Detalle cargado: registro=%s", registro)

                # Extraer metadatos web. En modo de reparación conservamos una
                # copia para no perder campos válidos si SATyS devuelve datos parciales.
                metadata_path_actual = carpeta_actual / "metadata_satys.json"
                metadata_anterior = {}
                if metadata_path_actual.exists():
                    try:
                        contenido_anterior = json.loads(metadata_path_actual.read_text(encoding="utf-8"))
                        if isinstance(contenido_anterior, dict):
                            metadata_anterior = contenido_anterior
                    except Exception as e_meta_anterior:
                        log.warning("[REG-META-ID] No se pudo leer metadata anterior de %s: %s", registro, e_meta_anterior)

                try:
                    from tenacity import RetryError as _RetryError
                    meta_satys = extraer_metadatos_satys(page, registro, carpeta_actual, registro_esperado=reg_celda)
                except Exception as _re:
                    log.warning(
                        "[REG-META] Metadatos incompletos para registro %s: %s",
                        registro, _re
                    )
                    meta_satys = {
                        "representante_legal": None,
                        "nombre_operador": None,
                        "asunto": None,
                        "registro": reg_celda,
                    }

                # Guardar el folio real (Memo/Folio OPC) en los metadatos.
                if folio_real and not meta_satys.get("folio"):
                    meta_satys["folio"] = folio_real

                if SOLO_METADATOS_ID:
                    # Mezcla conservadora: los valores nuevos no vacíos sustituyen a
                    # los anteriores; un None o string vacío no borra información buena.
                    metadata_fusionada = dict(metadata_anterior)
                    for clave, valor in (meta_satys or {}).items():
                        if valor is not None and (not isinstance(valor, str) or valor.strip()):
                            metadata_fusionada[clave] = valor
                        elif clave not in metadata_fusionada:
                            metadata_fusionada[clave] = valor
                    metadata_fusionada["registro"] = metadata_fusionada.get("registro") or reg_celda
                    if folio_real:
                        metadata_fusionada["folio"] = metadata_fusionada.get("folio") or folio_real
                    metadata_path_actual.parent.mkdir(parents=True, exist_ok=True)
                    metadata_path_actual.write_text(
                        json.dumps(metadata_fusionada, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    id_actual = metadata_fusionada.get("id_solicitante")
                    id_ok = id_actual is not None and str(id_actual).strip() not in {"", "null", "None"}
                    log.info(
                        "[REG-META-ID] Registro %s actualizado SIN descargar archivos. id_solicitante=%r",
                        registro,
                        id_actual,
                    )
                    todos_resultados.append({
                        "folio": folio_real or registro,
                        "archivo": "metadata_satys.json",
                        "nombre_original": "metadata_satys.json",
                        "tipo": "METADATA_ID_SOLICITANTE",
                        "fecha": datetime.now().isoformat(),
                        "descripcion": "Reconsulta manual de id_solicitante",
                        "ruta": str(metadata_path_actual),
                        "tamano_kb": round(metadata_path_actual.stat().st_size / 1024, 2),
                        "ok": id_ok,
                        "error": "" if id_ok else "ID_SOLICITANTE_VACIO",
                        "fuente": "SATYS_METADATA_ONLY",
                    })
                    fila_encontrada = True
                    break

                meta_tramite = {}

                # Intentar ARCHIVOS ASOCIADOS primero
                fuente = "ARCHIVOS_ASOCIADOS"
                res, seccion_aa_ok = descargar_archivos(context, page, registro, carpeta_actual)
                for r in res:
                    r.setdefault("fuente", "ARCHIVOS_ASOCIADOS")

                if not seccion_aa_ok:
                    fuente = "DOCUMENTOS_ANEXOS"
                    log.info(
                        "[REG-FUENTE] Sin ARCHIVOS ASOCIADOS -- activando flujo DOCUMENTOS ANEXOS "
                        "para registro %s (folio=%s)", registro, folio_real
                    )
                    res, meta_tramite = descargar_via_documentos_anexos_con_fallback(
                        context, page, folio_real or registro, folio_real or folio_raw or registro, carpeta_actual
                    )

                    if meta_tramite and isinstance(meta_tramite, dict):
                        registro_original = meta_satys.get("registro")
                        meta_satys.update(meta_tramite)
                        if not meta_satys.get("registro") and registro_original:
                            meta_satys["registro"] = registro_original
                        out_path_satys = carpeta_actual / "metadata_satys.json"
                        with open(out_path_satys, "w", encoding="utf-8") as f:
                            json.dump(meta_satys, f, ensure_ascii=False, indent=2)
                        log.info("[REG-META] metadata_satys.json actualizado para registro %s", registro)
                else:
                    log.info("[REG-FUENTE] ARCHIVOS ASOCIADOS encontrado para registro %s", registro)

                # Guardar metadata_completo.json
                meta_completo = {
                    "folio": folio_real or registro,
                    "registro": reg_celda,
                    "estado": "OK" if any(r.get("ok") for r in res) else "SIN_ARCHIVOS",
                    "total_archivos": len(res),
                    "total_archivos_ok": sum(1 for r in res if r.get("ok")),
                    "fuente": fuente,
                    "archivos": res,
                    "meta_satys": meta_satys,
                    "fecha_proceso": datetime.now().isoformat(),
                }
                try:
                    with open(carpeta_actual / "metadata_completo.json", "w", encoding="utf-8") as f:
                        json.dump(meta_completo, f, ensure_ascii=False, indent=2)
                except Exception as e_mc:
                    log.warning("[REG] No se pudo guardar metadata_completo.json: %s", e_mc)

                todos_resultados.extend(res)
                fila_encontrada = True
                break  # Encontrada la fila, salir del loop de filas

            if fila_encontrada:
                break

            # Intentar avanzar a la siguiente página
            try:
                btn_siguiente = page.locator(".paginate_button.next, li.next a, a:has-text('Siguiente')").last
                if btn_siguiente.count() == 0:
                    break
                clases = btn_siguiente.get_attribute("class") or ""
                padre_clases = btn_siguiente.locator("xpath=..").get_attribute("class") or ""
                if "disabled" in clases or "disabled" in padre_clases or btn_siguiente.is_disabled():
                    break
                btn_siguiente.click()
                try:
                    page.wait_for_function(
                        "() => { const p = document.querySelector('.dataTables_processing'); "
                        "return !p || p.style.display === 'none' || p.style.display === ''; }",
                        timeout=5_000
                    )
                except Exception:
                    page.wait_for_timeout(600)
            except Exception as e:
                log.debug("[REG] No se pudo avanzar de página: %s", e)
                break

        if not fila_encontrada:
            # No se encontró ninguna fila nueva sin procesar → terminar
            break

        # Regresar al tablero para buscar el siguiente registro (si hay más con el mismo número)
        volver_al_tablero(page)

    return todos_resultados


# ------------------------------------------------------------
#  PASO 6 -- PROCESAR TODOS LOS TRAMITES DE UN FOLIO
# ------------------------------------------------------------
def _buscar_folio_en_tabla(page, folio: str):
    """
    Busca el folio en el campo de busqueda del DataTable.
    Retorna True si se encontraron resultados, False si no.
    """
    try:
        # Asegurar pestana "Documentos en Proceso"
        try:
            tab = page.locator("a, button").filter(
                has_text=re.compile(r"Documentos en Proceso", re.I)
            ).first
            tab.wait_for(state="visible", timeout=3_000)
            tab.click()
            # Esperar que el campo de búsqueda esté listo en lugar de tiempo fijo
            try:
                page.wait_for_selector(
                    "input[type='search'], .dataTables_filter input",
                    timeout=5_000, state="visible"
                )
            except Exception:
                page.wait_for_timeout(600)
        except Exception:
            pass

        # Campo de busqueda de DataTables
        search = page.locator(
            "input[type='search'], "
            ".dataTables_filter input, "
            "input.form-control[placeholder*='uscar']"
        ).first
        search.wait_for(state="visible", timeout=TIMEOUT_CORTO)
        try:
            search.click(click_count=3)
        except Exception:
            search.click()
            page.keyboard.press("Control+A")
        search.fill(folio)
        # Esperar que DataTables termine de filtrar (más rápido que espera fija)
        try:
            page.wait_for_function(
                "() => { const p = document.querySelector('.dataTables_processing'); "
                "return !p || p.style.display === 'none' || p.style.display === ''; }",
                timeout=8_000
            )
        except Exception:
            page.wait_for_timeout(800)

        # Verificar resultados
        tbody = page.locator("table tbody").first
        filas = tbody.locator("tr")
        if filas.count() == 0:
            return False
        primera = filas.first.inner_text().lower()
        if "no hay" in primera or "no data" in primera or "sin resultados" in primera:
            return False
        return True
    except Exception:
        return False


def _obtener_col_idx_folio(page) -> int:
    """Obtiene el indice de la columna 'Memo / Folio OPC'."""
    try:
        headers = page.locator("table thead th")
        for j in range(headers.count()):
            header_text = headers.nth(j).inner_text().lower()
            if "folio opc" in header_text or "memo" in header_text:
                return j
    except Exception:
        pass
    return 2  # Fallback


def _obtener_registro_fila(fila, reg_col_idx: int = 1) -> str:
    """Obtiene el valor de la columna Registro de una fila."""
    try:
        celdas = fila.locator("td")
        if celdas.count() > reg_col_idx:
            return celdas.nth(reg_col_idx).inner_text().strip()
    except Exception:
        pass
    return ""


def _encontrar_boton_ver(fila):
    """Busca el boton 'Ver detalle' en una fila de la tabla."""
    # Selector CSS combinado: mucho más rápido que iterar 14 selectores uno por uno
    try:
        combinado = fila.locator(
            "a[title*='Ver'], button[title*='Ver'], "
            "a:has(i.fa-eye), button:has(i.fa-eye), "
            "a:has(i.icon-eye), button:has(i.icon-eye), "
            "a:has(i.glyphicon-eye-open), button:has(i.glyphicon-eye-open), "
            "a[data-action='ver'], a.js-gestor-sigedo-open-tramite, "
            "a.btn-info, button.btn-info, a.btn-primary, button.btn-primary"
        ).first
        combinado.wait_for(state="visible", timeout=3_000)
        return combinado
    except Exception:
        pass
    # Fallback: cualquier enlace o botón visible en la fila
    try:
        fallback = fila.locator("a, button").first
        fallback.wait_for(state="visible", timeout=1_000)
        return fallback
    except Exception:
        pass
    return None


def _buscar_folio_en_tabla_con_reintentos(page, folio: str,
                                           max_espera_seg: int = 60,
                                           espera_entre_intentos: int = 3) -> bool:
    """
    Igual que _buscar_folio_en_tabla(), pero con reintentos durante un maximo
    de max_espera_seg segundos (1 minuto por defecto). Mismo motivo que
    buscar_registro_en_tabla_con_reintentos(): una sola pasada puede leer el
    DataTable antes de que termine de filtrar/renderizar y producir un
    FOLIO_NO_ENCONTRADO erroneo.
    """
    intento = 0
    inicio = time.time()

    while True:
        intento += 1
        log.info("[FOLIO-BUSCAR] Buscando folio %s en tabla (intento %d)...", folio, intento)
        if _buscar_folio_en_tabla(page, folio):
            if intento > 1:
                log.info("[FOLIO-BUSCAR] Folio %s encontrado en intento %d", folio, intento)
            return True

        transcurrido = time.time() - inicio
        if transcurrido >= max_espera_seg:
            log.warning(
                "[FOLIO-BUSCAR] LIMITE alcanzado (%ds) buscando folio %s tras %d intentos -- "
                "se marca como no encontrado.", max_espera_seg, folio, intento,
            )
            return False

        log.warning("[FOLIO-BUSCAR] Intento %d fallido buscando folio %s. Reintentando en %ds...",
                    intento, folio, espera_entre_intentos)
        try:
            page.wait_for_timeout(espera_entre_intentos * 1000)
        except Exception as e:
            log.error("[FOLIO-BUSCAR] Navegador/pagina cerrado durante la espera para folio %s "
                      "(%s) -- se marca como no encontrado.", folio, e)
            return False


def procesar_folio_completo(context, page, folio: str, carpeta: Path, folio_raw: str = "") -> list:
    """
    Procesa TODOS los tramites que coinciden con un folio:
    1. Busca el folio en la tabla
    2. Verifica paginacion ('Mostrando X a Y de Z tramites')
    3. Para cada fila coincidente en todas las paginas:
       a. Abre Ver detalle
       b. Descarga todos los archivos (con paginacion de ARCHIVOS ASOCIADOS)
       c. Regresa al tablero
       d. Re-busca el folio
    """
    todos_resultados = []
    registros_procesados = set()
    indice_registro = 0   # 0 = carpeta base; 1, 2, ... = registros adicionales del mismo folio
    max_iteraciones = 50  # Limite de seguridad

    for iteracion in range(max_iteraciones):
        # V-03: Verificar sesion al inicio de cada iteracion
        if not _verificar_sesion(page):
            log.error("[V03] Sesion no recuperable -- abortando folio %s", folio)
            break

        # Buscar folio en la tabla (con reintentos hasta 1 min en la primera
        # iteracion, igual que para registro -- ver _buscar_folio_en_tabla_con_reintentos)
        if iteracion == 0:
            encontrado_en_tabla = _buscar_folio_en_tabla_con_reintentos(page, folio)
        else:
            encontrado_en_tabla = _buscar_folio_en_tabla(page, folio)

        if not encontrado_en_tabla:
            if iteracion == 0:
                # V-04: Registrar explicitamente como FOLIO_NO_ENCONTRADO
                log.warning("[V04-NO_ENCONTRADO] Folio %s no encontrado en Documentos en Proceso "
                            "(tras reintentos durante 60s)", folio)
                screenshot(page, f"folio_no_encontrado_{folio}")
                try:
                    carpeta.mkdir(parents=True, exist_ok=True)
                    with open(carpeta / "metadata_satys.json", "w", encoding="utf-8") as f:
                        json.dump({
                            "folio": folio,
                            "nombre_operador": None,
                            "representante_legal": None,
                            "error": "FOLIO_NO_ENCONTRADO",
                            "detalle": "No se encontro el folio en el tablero 'Documentos en "
                                       "Proceso' tras reintentar durante 60s.",
                            "fecha_proceso": datetime.now().isoformat(),
                        }, f, ensure_ascii=False, indent=2)
                except Exception as e_json:
                    log.warning("[FOLIO] No se pudo guardar JSON de diagnostico para %s: %s",
                                folio, e_json)
                todos_resultados.append({
                    "folio": folio,
                    "archivo": "", "nombre_original": "",
                    "tipo": "", "fecha": "", "descripcion": "",
                    "ruta": "", "tamano_kb": 0, "ok": False,
                    "error": "FOLIO_NO_ENCONTRADO",
                    "fuente": "NINGUNA",
                })
            break

        # Verificar paginacion de resultados de busqueda
        mostrado_hasta, total_resultados = _parsear_paginacion(page)
        if total_resultados > 0:
            log.info("[PAGE] Resultados busqueda: mostrando %d de %d tramites",
                     mostrado_hasta, total_resultados)

        col_idx = _obtener_col_idx_folio(page)

        # Buscar siguiente fila no procesada en todas las paginas
        fila_encontrada = False
        max_paginas = 20

        for num_pagina in range(max_paginas):
            tbody = page.locator("table tbody").first
            filas = tbody.locator("tr")
            filas_count = filas.count()

            for i in range(filas_count):
                fila = filas.nth(i)
                celdas = fila.locator("td")

                if celdas.count() > col_idx:
                    texto_celda = celdas.nth(col_idx).inner_text().strip()
                else:
                    texto_celda = fila.inner_text()

                if not _fila_contiene_folio(texto_celda, folio):
                    continue

                # Obtener identificador de registro para evitar duplicados
                registro = _obtener_registro_fila(fila)
                if registro and registro in registros_procesados:
                    continue  # Ya procesado

                # Encontrar boton Ver detalle
                boton_ver = _encontrar_boton_ver(fila)
                if boton_ver is None:
                    log.warning("[WARN] No se encontro boton Ver para registro %s", registro)
                    if registro:
                        registros_procesados.add(registro)
                    continue

                # Abrir detalle
                if registro:
                    registros_procesados.add(registro)
                log.info("[VIEW] Abriendo detalle: registro=%s, folio=%s", registro, folio)

                # Carpeta efectiva para ESTE registro: la base si es el primero
                # encontrado para este folio, o una carpeta separada si ya hay
                # otro(s) registro(s) previos (evita que se mezclen/sobreescriban).
                carpeta_actual = carpeta_para_registro(carpeta, indice_registro, registro)
                if indice_registro > 0:
                    log.warning(
                        "[MULTI-REGISTRO] Folio %s tiene mas de un tramite -- "
                        "registro %s usara una carpeta separada: %s",
                        folio, registro, carpeta_actual,
                    )
                indice_registro += 1

                boton_ver.scroll_into_view_if_needed()
                boton_ver.click()

                # Esperar pagina de detalle
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_DETALLE)
                except PWTimeout:
                    pass
                # V-09: Esperar que no haya spinner en la vista de detalle
                _esperar_sin_spinner(page, timeout_ms=15_000)

                if not esperar_detalle(page, folio):
                    en_tablero = (
                        page.locator("text=Documentos en Proceso").count() > 0
                        and page.locator("input[type='search']").count() > 0
                    )
                    if en_tablero:
                        log.error("[ERROR] No se entro a detalle para registro %s", registro)
                        fila_encontrada = True
                        break
                    log.warning("[WARN] Detalle no confirmado, continuando...")

                log.info("[OK] Detalle cargado: registro=%s", registro)

                # Extraer metadatos web (Representante, Solicitante, Asunto)
                # Bug B fix: capturar RetryError para que un fallo en metadatos
                # nunca cancele la descarga de archivos (dato que realmente importa).
                # En el peor caso la fila del Excel queda con esos campos vacios.
                try:
                    from tenacity import RetryError as _RetryError
                    meta_satys = extraer_metadatos_satys(page, folio, carpeta_actual, registro_esperado=registro)
                except _RetryError as _re:
                    log.warning(
                        "[WEB] Metadatos incompletos tras reintentos para folio %s (%s) "
                        "-- se continua con la descarga de archivos.",
                        folio, _re
                    )
                    meta_satys = {
                        "representante_legal": None,
                        "nombre_operador": None,
                        "asunto": None,
                        "registro": registro,
                    }
                meta_tramite = {}

                # --- BIFURCACION: intentar ARCHIVOS ASOCIADOS primero ---
                # descargar_archivos retorna (resultados, seccion_encontrada)
                # Si seccion_encontrada=False -> no existe la seccion -> usar DOCUMENTOS ANEXOS
                fuente = "ARCHIVOS_ASOCIADOS"
                res, seccion_aa_ok = descargar_archivos(context, page, folio, carpeta_actual)
                for r in res:
                    r.setdefault("fuente", "ARCHIVOS_ASOCIADOS")

                if not seccion_aa_ok:
                    # La seccion ARCHIVOS ASOCIADOS no existe en este folio
                    # -> flujo alternativo via Tramites Nuevos + DOCUMENTOS ANEXOS
                    fuente = "DOCUMENTOS_ANEXOS"
                    log.info(
                        "[FUENTE] Sin ARCHIVOS ASOCIADOS -- activando flujo DOCUMENTOS ANEXOS "
                        "para folio %s", folio
                    )
                    res, meta_tramite = descargar_via_documentos_anexos_con_fallback(
                        context, page, folio, folio_raw or folio, carpeta_actual
                    )
                    
                    if meta_tramite and isinstance(meta_tramite, dict):
                        # Preservar el registro extraído de DATOS DEL SISTEMA si tramite_nuevo no lo tiene
                        registro_original = meta_satys.get("registro")
                        meta_satys.update(meta_tramite)
                        if not meta_satys.get("registro") and registro_original:
                            meta_satys["registro"] = registro_original
                        out_path_satys = carpeta_actual / "metadata_satys.json"
                        with open(out_path_satys, "w", encoding="utf-8") as f:
                            json.dump(meta_satys, f, ensure_ascii=False, indent=2)
                        log.info("[META] metadata_satys.json actualizado con datos de tramite nuevo para folio %s", folio)
                else:
                    log.info("[FUENTE] ARCHIVOS ASOCIADOS encontrado para folio %s", folio)

                # Guardar metadata_completo.json con todo consolidado
                guardar_metadata_completo(
                    folio, folio_raw or folio, carpeta_actual,
                    meta_satys, meta_tramite, res, fuente
                )

                # Descomprimir ZIPs de ESTE registro inmediatamente (cada registro
                # tiene su propia carpeta, asi que cada uno se descomprime aparte).
                descomprimir_todos_zips_en_carpeta(carpeta_actual)

                if res:
                    for r in res:
                        r.setdefault("registro", registro)
                        r.setdefault("carpeta", str(carpeta_actual))
                    todos_resultados.extend(res)
                else:
                    todos_resultados.append({
                        "folio": folio, "archivo": f"SIN_ARCHIVOS_{registro}",
                        "tipo": "N/A", "ruta": "", "tamano_kb": 0, "ok": False,
                        "fuente": fuente, "registro": registro, "carpeta": str(carpeta_actual),
                    })

                # Regresar al tablero para buscar el siguiente tramite
                volver_al_tablero(page)
                page.wait_for_timeout(300)  # Reducido de 1 s: mínima estabilización post-tablero
                fila_encontrada = True
                break  # Reiniciar busqueda desde el inicio

            if fila_encontrada:
                break

            # Verificar paginacion: si no encontramos match en esta pagina, avanzar
            mostrado_hasta_pag, total_pag = _parsear_paginacion(page)
            if total_pag > 0 and mostrado_hasta_pag < total_pag:
                log.info("[PAGE] Avanzando pagina de busqueda (%d/%d)...",
                         mostrado_hasta_pag, total_pag)
                if not _avanzar_pagina_datatables(page):
                    break
            else:
                break  # Ultima pagina

        if not fila_encontrada:
            # No quedan mas filas por procesar
            if iteracion == 0:
                log.warning("[WARN] Folio %s no encontrado en columna 'Memo / Folio OPC'", folio)
            break

    if registros_procesados:
        log.info("[OK] Folio %s: %d tramite(s) procesado(s), registros: %s",
                 folio, len(registros_procesados), ", ".join(registros_procesados))

    # Paso final: descomprimir TODOS los ZIPs que queden en la carpeta del folio.
    # Cubre el caso de multiples ZIPs (ej. folio 1660) y ZIPs dentro de ZIPs.
    descomprimir_todos_zips_en_carpeta(carpeta)

    return todos_resultados


# ============================================================
#  FLUJO ALTERNATIVO: DOCUMENTOS ANEXOS
#  Se activa cuando ARCHIVOS ASOCIADOS no existe en el folio
# ============================================================

def _tiene_seccion_archivos_asociados(page) -> bool:
    """
    Comprueba si la seccion ARCHIVOS ASOCIADOS existe y es visible en la pagina actual.
    Se llama despues de cargar el detalle del folio.
    """
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1_000)
    selectores = [
        "text=ARCHIVOS ASOCIADOS",
        "h4:has-text('ARCHIVOS ASOCIADOS')",
        "h5:has-text('ARCHIVOS ASOCIADOS')",
        ".panel-title:has-text('ARCHIVOS')",
        "legend:has-text('ARCHIVOS ASOCIADOS')",
        "a[data-toggle='collapse']:has-text('ARCHIVOS')",
    ]
    for sel in selectores:
        try:
            if page.locator(sel).count() > 0:
                log.debug("[CHECK] ARCHIVOS ASOCIADOS encontrado con: %s", sel)
                return True
        except Exception:
            pass
    return False


def _esperar_fin_spinner(page, timeout_s: int = 30) -> bool:
    """
    Espera que la SPA termine de cargar usando networkidle.
    Retorna True cuando la red esta inactiva, False si se agoto el tiempo.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_s * 1_000)
        return True
    except PWTimeout:
        log.debug("[WAIT] networkidle timeout (%ds) -- continuando", timeout_s)
        return False
    except Exception:
        return False


def navegar_a_tramites_nuevos(page) -> bool:
    """
    Navega a: Administracion por Asignacion +TyS/SIGEDO/Internos IFT -> Tramites Nuevos.
    Funciona desde cualquier punto de la SPA.
    """
    log.info("[ALT-NAV] Navegando a Tramites Nuevos...")
    try:
        # Expandir menu 'Administracion por Asignacion +TyS/SIGEDO/Internos IFT'
        admin_btn = None
        patrones_admin = [
            re.compile(r"Administraci[oó]n\s+por\s+Asignaci[oó]n", re.I),
            re.compile(r"Asignaci[oó]n\s+\+TyS", re.I),
        ]
        for pat in patrones_admin:
            try:
                loc = page.locator("a, button, span").filter(has_text=pat).first
                if loc.count() > 0:
                    loc.wait_for(state="visible", timeout=TIMEOUT_CORTO)
                    admin_btn = loc
                    break
            except Exception:
                continue

        if admin_btn:
            try:
                admin_btn.scroll_into_view_if_needed()
                admin_btn.click()
                # Esperar que el submenú Trámites Nuevos sea visible en lugar de tiempo fijo
                try:
                    page.wait_for_selector(
                        "a:has-text('Trámites Nuevos'), a:has-text('Tramites Nuevos')",
                        timeout=6_000, state="visible"
                    )
                except Exception:
                    page.wait_for_timeout(600)
            except Exception as e:
                log.debug("[ALT-NAV] Click menu admin: %s", e)
        else:
            log.warning("[ALT-NAV] No se encontro boton Administracion por Asignacion")

        # Click en 'Tramites Nuevos'
        tramites_btn = None
        for pat in [
            re.compile(r"Tr[aá]mites\s+Nuevos", re.I),
        ]:
            try:
                loc = page.locator("a, button, span").filter(has_text=pat).first
                if loc.count() > 0:
                    loc.wait_for(state="visible", timeout=TIMEOUT_CORTO)
                    tramites_btn = loc
                    break
            except Exception:
                continue

        if tramites_btn is None:
            log.error("[ALT-NAV] No se encontro enlace 'Tramites Nuevos'")
            screenshot(page, "tramites_nuevos_no_encontrado")
            return False

        tramites_btn.scroll_into_view_if_needed()
        tramites_btn.click()

        # Esperar spinner y confirmar llegada (_esperar_fin_spinner ya maneja el tiempo)
        _esperar_fin_spinner(page, timeout_s=30)

        for sel in [
            "text=TABLERO DE CONTROL - NUEVOS",
            "text=Trámites ingresados",
            "text=Tramites ingresados",
            "text=Buscar:",
        ]:
            try:
                page.wait_for_selector(sel, timeout=15_000)
                log.info("[ALT-NAV] Tramites Nuevos cargado OK")
                return True
            except PWTimeout:
                continue

        log.warning("[ALT-NAV] Tramites Nuevos no confirmado -- continuando")
        screenshot(page, "tramites_nuevos_dudoso")
        return True

    except Exception as e:
        log.error("[ALT-NAV] Error: %s", e)
        screenshot(page, "tramites_nuevos_error")
        return False


def _normalizar_folio_para_busqueda(folio: str) -> str:
    """
    Extrae SOLO el numero final de un Folio OPC para usarlo en el cuadro
    de busqueda de Tramites Nuevos, donde el sistema solo reconoce numeros.

    Ejemplos:
        'VE-166095'    -> '166095'
        'CORREO-2444'  -> '2444'
        '17217'        -> '17217'
        'ABC-XYZ-099'  -> '099'

    Logica: busca el ultimo grupo de digitos al final del string.
    Si no hay grupo numerico, devuelve el folio original (fallback).
    """
    import re as _re
    m = _re.search(r'(\d+)\s*$', folio.strip())
    if m:
        return m.group(1)
    return folio.strip()


def buscar_folio_en_tramites_nuevos(page, folio: str) -> bool:
    """
    Escribe el folio en el campo 'Buscar:' de Tramites Nuevos
    y verifica que aparece al menos un resultado.
    Normaliza el folio para usar solo el numero final (ej. VE-166095 -> 166095).
    """
    folio_busqueda = _normalizar_folio_para_busqueda(folio)
    if folio_busqueda != folio:
        log.info("[ALT-SEARCH] Folio normalizado para busqueda: '%s' -> '%s'", folio, folio_busqueda)
    log.info("[ALT-SEARCH] Buscando folio %s en Tramites Nuevos...", folio_busqueda)
    try:
        # Campo de busqueda (puede ser diferente al DataTables del tablero)
        search = None
        for sel in [
            "input[placeholder*='uscar']",
            "input[type='search']",
            ".dataTables_filter input",
            "input[type='text']",
        ]:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=5_000)
                search = loc
                break
            except PWTimeout:
                continue

        if search is None:
            log.error("[ALT-SEARCH] No se encontro campo de busqueda")
            screenshot(page, "alt_search_sin_campo")
            return False

        try:
            search.click(click_count=3)
        except Exception:
            search.click()
            page.keyboard.press("Control+A")
        search.fill(folio_busqueda)
        page.wait_for_timeout(2_000)

        # Verificar que la tabla tiene resultados
        tbody = page.locator("table tbody").first
        filas = tbody.locator("tr")
        if filas.count() == 0:
            log.warning("[ALT-SEARCH] Sin resultados para folio %s", folio)
            return False

        primera = filas.first.inner_text().lower()
        if "no hay" in primera or "no data" in primera or "sin resultados" in primera:
            log.warning("[ALT-SEARCH] Tabla vacia para folio %s", folio)
            return False

        log.info("[ALT-SEARCH] Folio %s encontrado en Tramites Nuevos", folio)
        return True

    except Exception as e:
        log.error("[ALT-SEARCH] Error buscando folio %s: %s", folio, e)
        return False


def abrir_revisar_tramite(page, folio: str) -> bool:
    """
    Hace click en el boton verde 'Revisar' para el folio en Tramites Nuevos
    y espera que cargue el DETALLE DE LA SOLICITUD.
    El folio que se muestra en la tabla es el numero normalizado (ej. 166095),
    por lo que se usa _normalizar_folio_para_busqueda antes de buscar la fila.
    """
    log.info("[ALT-REVISAR] Abriendo detalle del tramite %s...", folio)
    # Usar el numero normalizado para coincidir con la celda de la tabla
    folio_num = _normalizar_folio_para_busqueda(folio)
    try:
        tbody = page.locator("table tbody").first
        filas = tbody.locator("tr")
        boton_revisar = None

        for i in range(filas.count()):
            fila = filas.nth(i)
            try:
                texto_fila = fila.inner_text()
            except Exception:
                continue

            # Coincidir por el numero normalizado (que es lo que muestra la tabla)
            if not re.search(rf"\b0*{re.escape(folio_num)}\b", texto_fila):
                continue

            # Buscar el boton Revisar en esta fila
            for sel in [
                "a:has-text('Revisar')",
                "button:has-text('Revisar')",
                "a.btn-success",
                "button.btn-success",
                "a[class*='success']",
                "a, button",
            ]:
                try:
                    loc = fila.locator(sel).first
                    if loc.count() > 0:
                        boton_revisar = loc
                        break
                except Exception:
                    continue
            if boton_revisar:
                break

        if boton_revisar is None:
            log.error("[ALT-REVISAR] No se encontro boton Revisar para folio %s (num=%s)",
                      folio, folio_num)
            screenshot(page, f"revisar_no_encontrado_{folio_num}")
            return False

        boton_revisar.scroll_into_view_if_needed()
        try:
            boton_revisar.click()
        except Exception:
            boton_revisar.click(force=True)

        # Esperar spinner y confirmar carga del detalle
        page.wait_for_timeout(1_500)
        _esperar_fin_spinner(page, timeout_s=30)

        for sel in [
            "text=DETALLE DE LA SOLICITUD",
            "text=DOCUMENTOS ANEXOS",
            "text=DATOS DEL TRÁMITE",
            "text=DATOS DEL TRAMITE",
            "text=HISTORIAL DE TURNADOS",
        ]:
            try:
                page.wait_for_selector(sel, timeout=20_000)
                log.info("[ALT-REVISAR] Detalle del tramite cargado OK")
                return True
            except PWTimeout:
                continue

        log.warning("[ALT-REVISAR] Detalle no confirmado -- continuando")
        screenshot(page, f"revisar_dudoso_{folio}")
        return True

    except Exception as e:
        log.error("[ALT-REVISAR] Error: %s", e)
        screenshot(page, f"revisar_error_{folio}")
        return False


def extraer_metadatos_tramite_nuevo(page, folio: str, carpeta: Path) -> dict:
    """
    Extrae metadatos de la seccion 'DATOS DEL TRAMITE' en la vista de Tramites Nuevos.
    Guarda metadata_tramite_nuevo.json en la carpeta del folio.
    """
    log.info("[ALT-META] Extrayendo metadatos de DATOS DEL TRAMITE para folio %s", folio)
    meta = {
        "tipo_tramite": "",
        "folio": "",
        "fecha_registro": "",
        "plazo_atencion": "",
        "origen": "",
        "solicitante": "",
        "concesionario": "",
        "promovente": "",
        "nombre_operador": "",
        "representante_legal": "",
        "info_adicional": "",
        "asunto": "",
        "descripcion": "",
        "registro": "",
    }
    try:
        page.evaluate("window.scrollTo(0, 0)")

        # ── Esperar a que el campo 'Folio:' tenga valor (máx. 15 s) ─────────────
        # Usamos wait_for_selector nativo de Playwright primero (dispara en cuanto
        # aparece el elemento, sin desperdiciar tiempo con sleep fijo de 1 s).
        # Si en 15 s no llega, continuamos de todas formas porque la página puede
        # haber cargado los datos en un formato distinto al esperado.
        _MAX_ESPERA_FOLIO_S = 15
        _folio_detectado = False
        log.info("[ALT-META] Esperando campo 'Folio:' (máx. %ds)...", _MAX_ESPERA_FOLIO_S)

        # JS mejorado: busca el input/select adyacente o hermano al label 'Folio'
        # según la estructura real de la página (label + input hermano).
        _JS_FOLIO = """
        () => {
            // 1) label con texto exacto 'Folio' o 'Folio:' → buscar input/select hermano o siguiente
            const labels = Array.from(document.querySelectorAll('label, td, th, span'));
            for (const lbl of labels) {
                const txt = (lbl.textContent || '').trim().replace(/:$/, '');
                if (txt === 'Folio' || txt === 'Folio ') {
                    // Hermano siguiente directo
                    let sib = lbl.nextElementSibling;
                    while (sib) {
                        if (sib.tagName === 'INPUT' || sib.tagName === 'SELECT' || sib.tagName === 'TEXTAREA') {
                            const v = (sib.value || sib.textContent || '').trim();
                            if (v) return v;
                        }
                        // Input dentro del hermano
                        const inner = sib.querySelector('input:not([type="hidden"]), select, textarea');
                        if (inner) {
                            const v = (inner.value || inner.textContent || '').trim();
                            if (v) return v;
                        }
                        sib = sib.nextElementSibling;
                    }
                    // Padre → buscar input en el mismo contenedor de fila
                    const row = lbl.closest('tr, .row, .form-group, li');
                    if (row) {
                        const inp = row.querySelector('input:not([type="hidden"]), select, textarea');
                        if (inp) {
                            const v = (inp.value || inp.textContent || '').trim();
                            if (v) return v;
                        }
                    }
                }
            }
            // 2) Fallback: cualquier input cuyo contenedor inmediato mencione 'Folio'
            const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]), textarea'));
            for (const inp of inputs) {
                const row = inp.closest('tr, .row, .form-group, li, .col-md-12') || inp.parentElement;
                if (row && (row.textContent || '').includes('Folio')) {
                    const v = (inp.value || '').trim();
                    if (v) return v;
                }
            }
            // 3) Fallback numérico: span/div de ≥4 dígitos dentro de contenedor con 'Folio'
            const elems = Array.from(document.querySelectorAll('span, td, div'));
            for (const el of elems) {
                const txt = (el.textContent || '').trim();
                if (/^\\d{4,}$/.test(txt)) {
                    const parent = el.parentElement;
                    if (parent && (parent.textContent || '').includes('Folio')) return txt;
                }
            }
            return '';
        }
        """

        # Intento rápido: si la página ya está cargada detectamos al instante
        try:
            _val_rapido = page.evaluate(_JS_FOLIO)
            if _val_rapido:
                log.info("[ALT-META] Campo 'Folio:' detectado al instante: '%s'", _val_rapido)
                _folio_detectado = True
        except Exception:
            pass

        if not _folio_detectado:
            # Espera reactiva: polling cada 500 ms hasta _MAX_ESPERA_FOLIO_S segundos
            _iteraciones = _MAX_ESPERA_FOLIO_S * 2  # 2 intentos por segundo
            for _intento in range(_iteraciones):
                try:
                    _val_folio = page.evaluate(_JS_FOLIO)
                    if _val_folio:
                        log.info("[ALT-META] Campo 'Folio:' detectado con valor '%s' (intento %d)",
                                 _val_folio, _intento + 1)
                        _folio_detectado = True
                        break
                except Exception as _e_poll:
                    log.debug("[ALT-META] Error en polling Folio (intento %d): %s", _intento + 1, _e_poll)
                page.wait_for_timeout(500)  # 500 ms en vez de 1000 ms → el doble de resolución

        if not _folio_detectado:
            log.warning(
                "[ALT-META] Timeout: 'Folio:' no apareció en %ds. "
                "Se intentará extraer de todos modos.",
                _MAX_ESPERA_FOLIO_S
            )
        # ── Fin del ciclo de espera ────────────────────────────────────────────

        # Intentar expandir DATOS DEL TRAMITE si esta colapsado
        for sel in [
            "h3:has-text('DATOS DEL TR')",
            "h4:has-text('DATOS DEL TR')",
            "a:has-text('DATOS DEL TR')",
            "div.panel-heading:has-text('DATOS DEL TR')",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed()
                    loc.click()
                    page.wait_for_timeout(1_000)
                    break
            except Exception:
                continue

        try:
            mapeo = {
                "Tipo": "tipo_tramite",
                "Trámite": "tipo_tramite",
                "Folio": "folio",
                "Solicitante": "nombre_operador",
                "Promovente": "promovente",
                "Representante": "representante_legal",
                "Concesionario": "nombre_operador",
                "Asunto": "asunto",
                "Info. adicional": "asunto",
                "Info adicional": "info_adicional",
                "Descripci": "descripcion",
                "Fecha de recepción": "fecha_registro",
                "Fecha de folio OPC": "fecha_ejecucion",
                "Plazo de atención": "plazo_atencion",   # Plazo de atención al trámite
                "Origen": "origen",
            }
            
            # 1) Buscar valores dentro del mismo texto del label (ej. <label>Concesionario: <span>PANAMSAT...</span></label>)
            labels = page.locator("label, td, th").all()
            for lbl in labels:
                try:
                    text = lbl.inner_text().strip()
                    if ":" in text:
                        partes = text.split(":", 1)
                        if len(partes) == 2:
                            clave_html = partes[0].strip()
                            valor_html = partes[1].strip()
                            if valor_html and len(valor_html) < 500:
                                for clave, campo in mapeo.items():
                                    if clave in clave_html and not meta.get(campo):
                                        meta[campo] = valor_html
                except Exception:
                    pass
                    
            # 2) Buscar valores en inputs/textareas basados en el texto de su contenedor
            inputs = page.locator("input:not([type='hidden']), textarea, select").all()
            for inp in inputs:
                try:
                    val = inp.input_value().strip()
                    if not val:
                        continue
                    
                    # Tratar de obtener el texto del contenedor padre mas cercano
                    # Usamos javascript para obtener el texto del ancestro sin el valor del input
                    row_text = inp.evaluate("""(node) => {
                        let p = node.closest('tr, .row, .form-group, li, .col-md-12');
                        if (!p) p = node.parentElement;
                        return p ? (p.innerText || p.textContent || '') : '';
                    }""")
                    
                    if row_text:
                        for clave, campo in mapeo.items():
                            if clave in row_text and not meta.get(campo):
                                meta[campo] = val
                except Exception:
                    pass
                    
        except Exception as e:
            log.error("[ALT-META] Error extra locators: %s", e)

        # Fallback: tipo_tramite desde la tabla si no se extrajo
        if not meta["tipo_tramite"]:
            try:
                tipo = page.evaluate(r'''
                () => {
                    const celdas = Array.from(document.querySelectorAll("table tbody tr td"));
                    for (const td of celdas) {
                        const t = (td.textContent || "").trim();
                        if (t.match(/^CGPE-|^CRT-|^IFT-/)) return t;
                    }
                    return "";
                }
                ''')
                if tipo:
                    meta["tipo_tramite"] = tipo
            except Exception:
                pass

        # Extraer número de Registro desde el encabezado de la página (ej. CRT26-025230)
        if not meta.get("registro"):
            try:
                registro_hdr = page.evaluate(r'''() => {
                    // El encabezado muestra "CRT26-025230 -> VE-182510" en un select/span
                    let sel = document.querySelector("select option:checked, .breadcrumb-item.active");
                    if (sel) {
                        let m = sel.textContent.match(/(CRT\d+-\d+)/);
                        if (m) return m[1];
                    }
                    // Buscar en cualquier elemento visible del encabezado
                    let all = Array.from(document.querySelectorAll("select, h1, h2, h3, .page-title, .header-title, nav"));
                    for (let el of all) {
                        let t = el.textContent || "";
                        let m = t.match(/(CRT\d{2}-\d+)/);
                        if (m) return m[1];
                    }
                    return "";
                }''')
                if registro_hdr:
                    meta["registro"] = registro_hdr
                    log.info("[ALT-META] Registro extraído del encabezado: %s", registro_hdr)
            except Exception as e:
                log.warning("[ALT-META] No se pudo extraer Registro del encabezado: %s", e)

        if meta.get("nombre_operador") and not meta.get("concesionario"):
            meta["concesionario"] = meta["nombre_operador"]
        if meta.get("promovente") and not meta.get("representante_legal"):
            meta["representante_legal"] = meta["promovente"]
        if meta.get("info_adicional") and not meta.get("asunto"):
            meta["asunto"] = meta["info_adicional"]

        # Guardar archivo
        out_path = carpeta / "metadata_tramite_nuevo.json"
        carpeta.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        log.info("[ALT-META] Metadatos guardados: %s", meta)
        return meta

    except Exception as e:
        log.error("[ALT-META] Error extrayendo metadatos: %s", e)
        return meta


def _encontrar_tabla_documentos_anexos(page):
    """
    Localiza la tabla de DOCUMENTOS ANEXOS usando JavaScript.
    Retorna el locator Playwright de la tabla correcta, o None.
    El JS busca la tabla que contenga botones 'VER DOCUMENTO' en su interior.
    """
    try:
        # Primero intentar por cabecera de la seccion (lo mas fiable)
        idx = page.evaluate(r'''
        () => {
            const tablas = Array.from(document.querySelectorAll("table"));
            for (let i = 0; i < tablas.length; i++) {
                const t = tablas[i];
                // Tiene botones VER DOCUMENTO en el cuerpo
                const btns = t.querySelectorAll(
                    "button, a"
                );
                for (const b of btns) {
                    const txt = (b.textContent || "").trim().toUpperCase();
                    if (txt.includes("VER DOCUMENTO") || txt.includes("VER DOC")) {
                        return i;
                    }
                }
                // O tiene la cabecera "Nombre del Documento"
                const ths = t.querySelectorAll("th");
                for (const th of ths) {
                    if ((th.textContent || "").includes("Nombre del Documento")) {
                        return i;
                    }
                }
            }
            return -1;
        }
        ''')
        if idx is not None and idx >= 0:
            tabla = page.locator("table").nth(idx)
            try:
                tabla.wait_for(state="visible", timeout=3_000)
                return tabla
            except Exception:
                return tabla  # retornar de todas formas
    except Exception as e:
        log.debug("[ALT-TABLE] Error buscando tabla: %s", e)
    return None


def _parsear_paginacion_documentos(page, scope=None) -> tuple:
    """
    Parsea el texto 'Mostrando X a Y de Z Documentos' del paginador personalizado
    de DOCUMENTOS ANEXOS.
    Retorna (desde, hasta, total) o (0, 0, 0) si no se encuentra.
    """
    root = scope if scope else page
    try:
        # Intentar primero en el scope limitado
        texto = root.inner_text() if scope else page.evaluate("document.body.innerText")
        m = re.search(
            r'Mostrando\s+(\d+)\s+a\s+(\d+)\s+de\s+(\d+)\s+Documentos?',
            texto, re.I
        )
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        pass
    return (0, 0, 0)


def _avanzar_pagina_documentos_anexos(page, scope=None) -> bool:
    """
    Avanza a la siguiente pagina en DOCUMENTOS ANEXOS.
    El paginador usa flechas ← → en lugar del DataTables estandar.
    Retorna True si avanzo, False si ya no hay mas paginas.
    """
    root = scope if scope else page
    try:
        # Selectores para la flecha siguiente en el paginador personalizado
        btn_sig = root.locator(
            "a.paginate_button.next, "
            "li.next > a, "
            "a[aria-label*='Next'], "
            "a[aria-label*='Siguiente'], "
            "button[aria-label*='Next']"
        ).last

        if btn_sig.count() == 0:
            return False

        clases = btn_sig.get_attribute("class") or ""
        try:
            padre_clases = btn_sig.locator("xpath=..").get_attribute("class") or ""
        except Exception:
            padre_clases = ""

        if "disabled" in clases or "disabled" in padre_clases:
            return False
        try:
            if btn_sig.is_disabled():
                return False
        except Exception:
            pass

        btn_sig.click()
        page.wait_for_timeout(2_000)
        return True

    except Exception:
        return False


def descargar_via_documentos_anexos(context, page, folio: str, carpeta: Path) -> list:
    """
    Descarga todos los archivos de la seccion 'DOCUMENTOS ANEXOS'.
    Cada fila tiene un boton gris 'VER DOCUMENTO' en la columna Accion.
    Valida que total_descargados_ok == total_documentos_esperados.
    Guarda URLs y metadatos de cada archivo en el resultado.
    """
    log.info("[ALT-DL] Descargando DOCUMENTOS ANEXOS para folio %s", folio)
    resultados = []

    try:
        # Scroll al final para poder ver DOCUMENTOS ANEXOS
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1_500)

        # Verificar si la seccion DOCUMENTOS ANEXOS esta en la pagina
        seccion_encontrada = False
        for sel in [
            "text=DOCUMENTOS ANEXOS",
            "h3:has-text('DOCUMENTOS ANEXOS')",
            "h4:has-text('DOCUMENTOS ANEXOS')",
            "div.panel-heading:has-text('DOCUMENTOS ANEXOS')",
            ".panel-title:has-text('DOCUMENTOS ANEXOS')",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed()
                    seccion_encontrada = True
                    log.info("[ALT-DL] Seccion DOCUMENTOS ANEXOS localizada: %s", sel)
                    break
            except Exception:
                continue

        if not seccion_encontrada:
            log.warning("[ALT-DL] Seccion DOCUMENTOS ANEXOS no encontrada en pagina")
            screenshot(page, f"no_documentos_anexos_{folio}")
            return resultados

        # Verificar si los botones VER DOCUMENTO ya estan visibles (seccion expandida)
        # Si NO estan visibles, intentar expandir haciendo click en el encabezado
        page.wait_for_timeout(1_000)
        ver_doc_visible = False
        try:
            ver_doc_visible = page.locator("button:has-text('VER DOCUMENTO'), a:has-text('VER DOCUMENTO')").count() > 0
        except Exception:
            pass

        if not ver_doc_visible:
            log.info("[ALT-DL] Seccion colapsada -- intentando expandir...")
            for sel in [
                "text=DOCUMENTOS ANEXOS",
                "div.panel-heading:has-text('DOCUMENTOS ANEXOS')",
                ".panel-title:has-text('DOCUMENTOS ANEXOS')",
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        loc.click()
                        page.wait_for_timeout(2_000)
                        ver_doc_visible = page.locator(
                            "button:has-text('VER DOCUMENTO'), a:has-text('VER DOCUMENTO')"
                        ).count() > 0
                        if ver_doc_visible:
                            break
                except Exception:
                    continue

        if not ver_doc_visible:
            log.warning("[ALT-DL] Botones VER DOCUMENTO no encontrados -- capturando screenshot")
            screenshot(page, f"no_ver_documento_{folio}")
            # Continuar de todas formas; el JS intentara extraer lo que haya

        # Leer total esperado (antes de cambiar paginacion)
        desde_ini, hasta_ini, total_esperado = _parsear_paginacion_documentos(page)
        if total_esperado > 0:
            log.info(
                "[ALT-DL] DOCUMENTOS ANEXOS: Mostrando %d a %d de %d",
                desde_ini, hasta_ini, total_esperado
            )
        else:
            log.warning("[ALT-DL] No se pudo determinar total de documentos")

        # Cambiar el selector de paginacion de 10 a 100 para ver todos los documentos.
        # En DOCUMENTOS ANEXOS el selector dice 'Mostrar [10 v] Documentos'.
        # Esta tabla NO usa DataTables estandar (name*='_length') sino un selector
        # propio de la SPA. Usamos JS para encontrar el <select> cercano al texto "Documentos".
        try:
            cambiado = page.evaluate(r"""
            () => {
                // Buscar todos los <select> de la pagina
                const selects = Array.from(document.querySelectorAll('select'));
                for (const sel of selects) {
                    // Verificar si el select o su contenedor cercano menciona "Documentos"
                    let parent = sel.parentElement;
                    for (let i = 0; i < 4; i++) {
                        if (!parent) break;
                        const txt = parent.textContent || '';
                        if (txt.includes('Documentos') && !txt.includes('trámites')) {
                            // Cambiar a 100 o al valor maximo disponible
                            const opciones = Array.from(sel.options);
                            let valorElegido = null;
                            for (const op of opciones) {
                                if (op.value === '100') { valorElegido = '100'; break; }
                                if (op.value === '-1') valorElegido = '-1';
                                else if (op.value === '50' && valorElegido === null) valorElegido = '50';
                            }
                            if (valorElegido) {
                                sel.value = valorElegido;
                                sel.dispatchEvent(new Event('change', { bubbles: true }));
                                return valorElegido;
                            }
                        }
                        parent = parent.parentElement;
                    }
                }
                // Fallback: si no encontramos por "Documentos", probar el ultimo select visible
                for (let i = selects.length - 1; i >= 0; i--) {
                    const sel = selects[i];
                    if (sel.offsetParent !== null) {  // visible
                        const opciones = Array.from(sel.options);
                        let valorElegido = null;
                        for (const op of opciones) {
                            if (op.value === '100') { valorElegido = '100'; break; }
                            if (op.value === '-1') valorElegido = '-1';
                        }
                        if (valorElegido) {
                            sel.value = valorElegido;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            return valorElegido + '_fallback';
                        }
                    }
                }
                return null;
            }
            """)
            if cambiado:
                log.info("[ALT-DL] Selector DOCUMENTOS ANEXOS cambiado a: %s", cambiado)
                # Esperar que la tabla actualice despues del cambio
                try:
                    page.wait_for_function(
                        "() => { const p = document.querySelector('.dataTables_processing'); "
                        "return !p || p.style.display === 'none' || p.style.display === ''; }",
                        timeout=6_000
                    )
                except Exception:
                    page.wait_for_timeout(1_000)
                # Re-leer el total ahora que se muestran mas documentos
                desde_ini, hasta_ini, total_esperado = _parsear_paginacion_documentos(page)
                if total_esperado > 0:
                    log.info(
                        "[ALT-DL] DOCUMENTOS ANEXOS (post-100): Mostrando %d a %d de %d",
                        desde_ini, hasta_ini, total_esperado
                    )
            else:
                log.warning("[ALT-DL] No se encontro selector de paginacion en DOCUMENTOS ANEXOS")
        except Exception as e:
            log.warning("[ALT-DL] No se pudo cambiar paginacion a 100: %s", e)

        # Loop de paginacion
        # ESTRATEGIA: localizar directamente los botones VER DOCUMENTO con Playwright
        # (la pagina usa divs, no <table>/<tr>/<td>, por eso querySelectorAll("table")=0)
        pagina_doc = 0
        max_paginas_doc = 50
        contador_global = 0

        while pagina_doc < max_paginas_doc:
            pagina_doc += 1

            # Encontrar todos los botones VER DOCUMENTO visibles en esta pagina
            sel_btn = (
                "button:has-text('VER DOCUMENTO'), a:has-text('VER DOCUMENTO'), "
                "button:has-text('Ver documento'), a:has-text('Ver documento'), "
                "input[value='VER DOCUMENTO'], input[value='Ver documento']"
            )
            try:
                page.wait_for_selector(sel_btn, timeout=5_000)
            except PWTimeout:
                pass

            btns_loc = page.locator(sel_btn)
            filas_count = btns_loc.count()

            if filas_count == 0:
                if pagina_doc == 1:
                    log.warning("[ALT-DL] Sin botones VER DOCUMENTO en pagina %d (folio %s)",
                                pagina_doc, folio)
                    screenshot(page, f"no_ver_documento_{folio}")
                break

            # Limitar al numero de documentos esperados para no capturar botones
            # de otras secciones (ej: HISTORIAL DE TURNADOS) que aparecen mas abajo
            if total_esperado > 0 and filas_count > total_esperado:
                log.warning(
                    "[ALT-DL] %d botones > %d esperados -- usando solo los primeros %d",
                    filas_count, total_esperado, total_esperado
                )
                filas_count = total_esperado

            log.info("[ALT-DL] Pagina %d: %d botones VER DOCUMENTO", pagina_doc, filas_count)

            for i in range(filas_count):
                boton_ver_doc = btns_loc.nth(i)
                contador_global += 1

                # Extraer metadatos: subir al ancestor fila para leer columnas de texto
                # --- Extraer atributos del boton directamente con Playwright ---
                # (evaluate() retorna None por serializacion; get_attribute() es mas confiable)
                nombre_doc = tipo_doc = fecha_doc = descripcion_doc = ""
                btn_href = btn_onclick = btn_data_url = ""
                try:
                    btn_href     = boton_ver_doc.get_attribute("href") or ""
                    btn_onclick  = boton_ver_doc.get_attribute("onclick") or ""
                    btn_data_url = (boton_ver_doc.get_attribute("data-url")
                                    or boton_ver_doc.get_attribute("data-href") or "")
                    # Metadatos de texto (nombre/tipo/fecha): intentar via JS desde la pagina
                    # usando el indice del boton para localizar la fila correspondiente
                    try:
                        meta_js = page.evaluate(
                            r'''(idx) => {
                                const btns = Array.from(document.querySelectorAll(
                                    "button, a, input[type=button]"
                                )).filter(function(b) {
                                    return (b.textContent || b.value || "").trim()
                                           .toUpperCase().startsWith("VER DOC");
                                });
                                const btn = btns[idx];
                                if (!btn) return {nombre:"",tipo:"",fecha:"",descripcion:""};
                                // Buscar ancestor con >= 3 hijos
                                let fila = btn.closest("tr");
                                if (!fila) {
                                    let el = btn;
                                    for (let n = 0; n < 10 && el && el.tagName !== "BODY"; n++) {
                                        el = el.parentElement;
                                        if (!el) break;
                                        const kids = Array.from(el.children).filter(function(k) {
                                            return (k.textContent || "").trim();
                                        });
                                        if (kids.length >= 3) { fila = el; break; }
                                    }
                                }
                                if (!fila) return {nombre:"",tipo:"",fecha:"",descripcion:""};
                                const tds = Array.from(fila.querySelectorAll(":scope > td"));
                                const cols = tds.length >= 3 ? tds : Array.from(fila.children);
                                const celdas = cols.filter(function(c) {
                                    const bs = c.querySelectorAll("button,input[type=button]");
                                    for (const b of bs) {
                                        if ((b.textContent||"").trim().toUpperCase().startsWith("VER DOC"))
                                            return false;
                                    }
                                    return true;
                                });
                                function t(el) { return el ? (el.textContent||"").trim() : ""; }
                                return {
                                    nombre: t(celdas[0]),
                                    tipo:   t(celdas[1]),
                                    fecha:  t(celdas[2]),
                                    descripcion: t(celdas[3])
                                };
                            }''',
                            i  # indice del boton entre todos los VER DOCUMENTO de la pagina
                        ) or {}
                        nombre_doc      = meta_js.get("nombre", "") or ""
                        tipo_doc        = meta_js.get("tipo", "") or ""
                        fecha_doc       = meta_js.get("fecha", "") or ""
                        descripcion_doc = meta_js.get("descripcion", "") or ""
                    except Exception as em:
                        log.debug("  [META] Error JS metadatos fila %d: %s", i, em)
                except Exception as e:
                    log.info("  [META-ERR] get_attribute fila %d: %s", i, e)

                if not nombre_doc:
                    nombre_doc = descripcion_doc or f"documento_{folio}_{contador_global}"

                log.info("  [%d] nombre=%s  tipo=%s  fecha=%s",
                         contador_global, nombre_doc, tipo_doc, fecha_doc)

                # Extraer URL del documento
                url_doc = ""
                try:
                    if btn_href and not btn_href.lower().startswith("javascript"):
                        url_doc = _absolutizar_url(page, btn_href)
                    elif btn_data_url:
                        url_doc = _absolutizar_url(page, btn_data_url)
                    elif btn_onclick:
                        url_doc = _absolutizar_url(page, _extraer_url_from_onclick(btn_onclick))
                except Exception:
                    pass

                # Nombre de archivo destino
                fname = nombre_doc
                if url_doc and not Path(fname).suffix:
                    url_name = Path(urlparse(url_doc).path).name
                    if url_name:
                        fname = url_name
                if not fname or (fname == nombre_doc and not Path(fname).suffix):
                    fname = f"documento_{folio}_{contador_global}"

                # Evitar sobrescritura
                dest = carpeta / fname
                if dest.exists():
                    for n in range(2, 99):
                        candidate = carpeta / f"{dest.stem}_{n}{dest.suffix}"
                        if not candidate.exists():
                            dest = candidate
                            fname = dest.name
                            break

                # Descarga directa via URL (si disponible)
                descargado = False
                if url_doc:
                    ok_directo = _descargar_directo(context, page, url_doc, dest)
                    if ok_directo:
                        size_kb = dest.stat().st_size / 1024
                        if size_kb > 0:
                            descargado = True
                        else:
                            log.warning("  [WARN] Archivo 0 KB (descarga directa): %s", fname)
                            dest.unlink(missing_ok=True)

                # Click en VER DOCUMENTO con expect_download
                # Fallback: si el boton abre nueva pestana (PDF en viewer), capturar URL
                if not descargado:
                    boton_ver_doc.scroll_into_view_if_needed()
                    url_popup = ""
                    try:
                        dl_obj, np_obj = _click_y_esperar_descarga(page, context, boton_ver_doc)

                        if dl_obj:
                            fname_sugerido = dl_obj.suggested_filename or fname
                            dest_nuevo = carpeta / fname_sugerido
                            if dest_nuevo.exists() and dest_nuevo != dest:
                                for n in range(2, 99):
                                    c = carpeta / f"{dest_nuevo.stem}_{n}{dest_nuevo.suffix}"
                                    if not c.exists():
                                        dest_nuevo = c
                                        break
                            dl_obj.save_as(str(dest_nuevo))
                            dest = dest_nuevo
                            fname = dest.name
                            size_kb = dest.stat().st_size / 1024
                            if size_kb == 0:
                                log.warning("  [WARN] Archivo 0 KB: %s", fname)
                            descargado = True
                            if hasattr(dl_obj, "url") and dl_obj.url:
                                url_doc = dl_obj.url

                        elif np_obj:
                            log.info("  [POPUP] Nueva pestana capturada al instante (PDF)")
                            try:
                                np_obj.wait_for_load_state("domcontentloaded", timeout=10_000)
                                url_popup = np_obj.url
                                log.info("  [POPUP] Nueva pestana URL: %s", url_popup)
                                np_obj.close()
                            except Exception as ep:
                                log.info("  [POPUP] No se pudo leer URL de la pestana: %s", ep)

                            if url_popup and url_popup != page.url:
                                # Usar el nombre de archivo de la URL del popup
                                url_fname = Path(urlparse(url_popup).path).name
                                if url_fname:
                                    dest_popup = carpeta / url_fname
                                    if dest_popup.exists():
                                        for n in range(2, 99):
                                            c = carpeta / f"{dest_popup.stem}_{n}{dest_popup.suffix}"
                                            if not c.exists():
                                                dest_popup = c
                                                break
                                else:
                                    dest_popup = dest  # fallback al nombre generico

                                # Descargar desde la URL de la nueva pestana
                                ok_popup = _descargar_directo(context, page, url_popup, dest_popup)
                                if ok_popup:
                                    size_kb = dest_popup.stat().st_size / 1024
                                    if size_kb > 0:
                                        dest = dest_popup
                                        fname = dest.name
                                        descargado = True
                                        url_doc = url_popup
                                        log.info("  [OK-POPUP] %s (%.1f KB)", fname, size_kb)
                                    else:
                                        dest_popup.unlink(missing_ok=True)

                        if not descargado:
                            log.error("  [ERROR] Timeout descargando %s (url_popup=%s)",
                                      nombre_doc, url_popup if 'url_popup' in locals() else "ninguna")
                            resultados.append({
                                "folio": folio,
                                "archivo": nombre_doc, "nombre_original": nombre_doc,
                                "indice_documento_portal": contador_global,
                                "total_documentos_portal": total_esperado,
                                "tipo": tipo_doc, "fecha": fecha_doc,
                                "descripcion": descripcion_doc,
                                "ruta": "", "tamano_kb": 0, "ok": False,
                                "error": "timeout", "url": (url_popup if 'url_popup' in locals() else "") or url_doc,
                                "fuente": "DOCUMENTOS_ANEXOS",
                            })
                            continue

                    except Exception as ex:
                        log.error("  [ERROR] %s", ex)
                        resultados.append({
                            "folio": folio,
                            "archivo": nombre_doc, "nombre_original": nombre_doc,
                            "indice_documento_portal": contador_global,
                            "total_documentos_portal": total_esperado,
                            "tipo": tipo_doc, "fecha": fecha_doc,
                            "descripcion": descripcion_doc,
                            "ruta": "", "tamano_kb": 0, "ok": False,
                            "error": str(ex)[:120], "url": url_doc,
                            "fuente": "DOCUMENTOS_ANEXOS",
                        })
                        continue
                    finally:
                        _cerrar_modal_documento(page)
                        _cerrar_paginas_emergentes(
                            context,
                            page,
                            motivo=f"documento anexo {nombre_doc}",
                        )

                if descargado:
                    extraidos = extraer_zip_si_aplica(dest, carpeta)
                    if extraidos:
                        for ex in extraidos:
                            size_ex = ex.stat().st_size / 1024 if ex.exists() else 0
                            log.info("  [OK] %s (%.1f KB) [extraído de %s]", ex.name, size_ex, fname)
                            resultados.append({
                                "folio": folio,
                                "archivo": ex.name,
                                "nombre_original": nombre_doc,
                                "indice_documento_portal": contador_global,
                                "total_documentos_portal": total_esperado,
                                "tipo": ex.suffix.upper().lstrip("."),
                                "fecha": fecha_doc,
                                "descripcion": descripcion_doc,
                                "ruta": str(ex),
                                "tamano_kb": round(size_ex, 1),
                                "ok": True,
                                "url": url_doc,
                                "fuente": "DOCUMENTOS_ANEXOS",
                            })
                    else:
                        try:
                            size_kb = dest.stat().st_size / 1024
                        except Exception:
                            size_kb = 0
                        es_ok = size_kb > 0
                        log.info("  [%s] %s  (%.1f KB)", "OK" if es_ok else "WARN", fname, size_kb)
                        resultados.append({
                            "folio": folio,
                            "archivo": fname,
                            "nombre_original": nombre_doc,
                            "indice_documento_portal": contador_global,
                            "total_documentos_portal": total_esperado,
                            "tipo": tipo_doc,
                            "fecha": fecha_doc,
                            "descripcion": descripcion_doc,
                            "ruta": str(dest),
                            "tamano_kb": round(size_kb, 1),
                            "ok": es_ok,
                            "url": url_doc,
                            "fuente": "DOCUMENTOS_ANEXOS",
                        })

            # Verificar paginacion de DOCUMENTOS ANEXOS
            desde_act, hasta_act, total_act = _parsear_paginacion_documentos(page)
            if total_act > 0:
                log.info("[ALT-DL] Paginacion: mostrando %d a %d de %d",
                         desde_act, hasta_act, total_act)
                if hasta_act >= total_act:
                    break  # Todos vistos
            else:
                break  # Sin info de paginacion

            # Verificar que la flecha derecha no este deshabilitada
            if not _avanzar_pagina_documentos_anexos(page):
                log.info("[ALT-DL] No hay mas paginas en DOCUMENTOS ANEXOS")
                break

        # --- VALIDACION FINAL ---
        ok_count = sum(1 for r in resultados if r.get("ok"))
        err_count = len(resultados) - ok_count
        indices_vistos = {
            int(r.get("indice_documento_portal") or 0)
            for r in resultados
            if int(r.get("indice_documento_portal") or 0) > 0
        }
        indices_ok = {
            indice for indice in indices_vistos
            if any(
                int(r.get("indice_documento_portal") or 0) == indice and r.get("ok")
                for r in resultados
            )
            and not any(
                int(r.get("indice_documento_portal") or 0) == indice and not r.get("ok")
                for r in resultados
            )
        }

        if total_esperado > 0:
            if len(indices_ok) == total_esperado and len(indices_vistos) == total_esperado:
                log.info(
                    "[ALT-DL] VALIDACION OK: %d/%d documentos descargados",
                    len(indices_ok), total_esperado
                )
            else:
                log.warning(
                    "[ALT-DL] VALIDACION: esperados=%d  documentos_ok=%d  "
                    "documentos_vistos=%d  archivos_error=%d",
                    total_esperado, len(indices_ok), len(indices_vistos), err_count
                )
                screenshot(page, f"validacion_fallo_{folio}")
                if len(indices_vistos) < total_esperado:
                    resultados.append({
                        "folio": folio,
                        "archivo": f"ERROR_CONTEO_DOCUMENTOS_{folio}",
                        "nombre_original": "",
                        "indice_documento_portal": 0,
                        "total_documentos_portal": total_esperado,
                        "tipo": "ERROR_CONTEO",
                        "fecha": "",
                        "descripcion": "",
                        "ruta": "",
                        "tamano_kb": 0,
                        "ok": False,
                        "error": (
                            f"SATyS reportó {total_esperado} documento(s), "
                            f"pero sólo se recorrieron {len(indices_vistos)}."
                        ),
                        "fuente": "DOCUMENTOS_ANEXOS",
                    })
        else:
            log.info(
                "[ALT-DL] Descargados %d documento(s) (%d errores)",
                ok_count, err_count
            )

        return resultados

    except Exception as e:
        log.error("[ALT-DL] Error general DOCUMENTOS ANEXOS folio %s: %s", folio, e)
        screenshot(page, f"error_documentos_anexos_{folio}")
        return resultados


def descargar_via_documentos_anexos_con_fallback(
    context, page, folio: str, folio_raw: str, carpeta: Path
) -> tuple:
    """
    Flujo completo de fallback cuando ARCHIVOS ASOCIADOS no existe:
      1. Navegar a Tramites Nuevos
      2. Buscar el folio
      3. Click en Revisar
      4. Extraer metadatos de DATOS DEL TRAMITE
      5. Descargar DOCUMENTOS ANEXOS

    Retorna (resultados: list, meta_tramite: dict).
    El regreso al tablero principal lo realiza volver_al_tablero() en el llamador.
    """
    log.info("[ALT] Iniciando flujo DOCUMENTOS ANEXOS para folio %s (raw=%s)",
             folio, folio_raw)
    meta_tramite = {}
    resultados = []

    try:
        # 1. Navegar al tablero de Tramites Nuevos
        if not navegar_a_tramites_nuevos(page):
            log.error("[ALT] No se pudo navegar a Tramites Nuevos para folio %s", folio)
            return resultados, meta_tramite

        # 2. Buscar folio
        if not buscar_folio_en_tramites_nuevos(page, folio):
            log.warning("[ALT] Folio %s no encontrado en Tramites Nuevos", folio)
            return resultados, meta_tramite

        # 3. Abrir detalle via boton Revisar
        if not abrir_revisar_tramite(page, folio):
            log.error("[ALT] No se pudo abrir detalle Revisar para folio %s", folio)
            return resultados, meta_tramite

        # 4. Extraer metadatos de DATOS DEL TRAMITE
        meta_tramite = extraer_metadatos_tramite_nuevo(page, folio, carpeta)

        # 5. Descargar DOCUMENTOS ANEXOS
        resultados = descargar_via_documentos_anexos(context, page, folio, carpeta)

    except Exception as e:
        log.error("[ALT] Error en flujo DOCUMENTOS ANEXOS folio %s: %s", folio, e)
        screenshot(page, f"alt_error_{folio}")

    return resultados, meta_tramite


# ============================================================
#  FLUJO NUEVO: ADMINISTRACION SOLICITUDES +TYS/SIGEDO/INTERNOS IFT
# ============================================================

REGISTRO_INTERNO_RE = re.compile(r"\b[A-Z]{2,6}\d{2}-\d{3,}\b", re.I)


def _unique_preserve_order(items: list[str]) -> list[str]:
    vistos = set()
    resultado = []
    for item in items:
        texto = str(item or "").strip()
        key = texto.lower()
        if not texto or key in vistos:
            continue
        vistos.add(key)
        resultado.append(texto)
    return resultado


def _cargar_objetivos_internos(path: Path) -> list[dict]:
    """Load [{bandeja, folio}] targets produced by the daily inventory."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    raw_items = data.get("objetivos", []) if isinstance(data, dict) else data
    if not isinstance(raw_items, list):
        raise ValueError("El JSON de objetivos Internos no contiene una lista valida.")

    objetivos = []
    vistos = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        bandeja = str(item.get("bandeja") or "").strip()
        folio = str(item.get("folio") or "").strip()
        key = (bandeja.lower(), folio)
        if bandeja and re.fullmatch(r"\d{1,15}", folio) and key not in vistos:
            vistos.add(key)
            objetivos.append({"bandeja": bandeja, "folio": folio})
    return objetivos


def _slug_internos(texto: str) -> str:
    return slug_bandeja_internos(texto)


def _firma_texto(texto: str) -> str:
    return hashlib.sha1((texto or "").encode("utf-8", errors="replace")).hexdigest()


def _normalizar_registro_interno(valor: str) -> str:
    m = REGISTRO_INTERNO_RE.search(str(valor or "").upper())
    return m.group(0).upper() if m else str(valor or "").strip()


def _click_texto_visible(page, patron: re.Pattern, descripcion: str, timeout_ms: int = TIMEOUT_NAV) -> bool:
    """Click robusto por texto en enlaces/botones visibles."""
    raices = []
    try:
        raices.append(page.locator("nav, .sidebar, aside").first)
    except Exception:
        pass
    raices.append(page)

    for root in raices:
        for selector in ("a, button", "li a, li button", "span"):
            try:
                locs = root.locator(selector).filter(has_text=patron)
                count = locs.count()
            except Exception:
                continue
            for idx in range(count):
                loc = locs.nth(idx)
                try:
                    loc.wait_for(state="visible", timeout=min(timeout_ms, 5_000))
                    loc.scroll_into_view_if_needed()
                    loc.click()
                    log.info("[INT-NAV] Click en %s", descripcion)
                    return True
                except Exception:
                    continue
    # Fallback DOM para las vistas que cargan el Map heredado de SATyS. El
    # resultado es texto plano para evitar la serializacion de objetos rota.
    try:
        pattern_js = json.dumps(patron.pattern)
        flags_js = json.dumps("i" if patron.flags & re.I else "")
        selector_js = json.dumps(
            "nav a, nav button, .sidebar a, .sidebar button, "
            "aside a, aside button, a, button"
        )
        texto = page.evaluate(
            f"""() => {{
                const rx = new RegExp({pattern_js}, {flags_js});
                const visible = el => {{
                    const s = getComputedStyle(el);
                    return s.display !== 'none' && s.visibility !== 'hidden'
                        && el.offsetParent !== null;
                }};
                const candidatos = Array.from(document.querySelectorAll({selector_js}))
                    .filter(visible)
                    .filter(el => rx.test((el.innerText || el.textContent || '').trim()))
                    .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                if (!candidatos.length) return '';
                const el = candidatos[0];
                const texto = (el.innerText || el.textContent || '').trim();
                el.scrollIntoView({{block: 'center'}});
                el.click();
                return texto || 'OK';
            }}"""
        )
        if texto:
            log.info("[INT-NAV] Click en %s", descripcion)
            return True
    except Exception:
        pass
    return False


def _normalizar_nombre_internos(valor: str) -> str:
    texto = unicodedata.normalize("NFD", str(valor or ""))
    texto = "".join(ch for ch in texto if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", texto).strip().lower()


def _esperar_estado_bandeja_internos(
    page,
    tab_id: str | None = None,
    timeout_ms: int = 60_000,
) -> bool:
    """Espera el tablero Internos sin usar el motor de selectores de Playwright."""
    inicio = time.time()
    limite = timeout_ms / 1000
    ultimo_estado = ""
    while (time.time() - inicio) < limite:
        try:
            tab_expr = json.dumps(str(tab_id)) if tab_id else "null"
            ultimo_estado = page.evaluate(
                f"""() => {{
                    const ids = ['1', '2', '3', '4', '5', '6'];
                    if (!ids.every(id => document.getElementById(id))) return 'SIN_TABS';
                    const contador = id => {{
                        const span = document.getElementById(id).querySelector('span');
                        const texto = (span?.textContent || '').replace(/[,\s]/g, '');
                        return /^\d+$/.test(texto) ? Number(texto) : null;
                    }};
                    if (!ids.every(id => contador(id) !== null)) return 'SIN_CONTADORES';
                    const overlay = document.querySelector('#pantalla-carga');
                    if (overlay) {{
                        const s = getComputedStyle(overlay);
                        if (s.display !== 'none' && s.visibility !== 'hidden') return 'CARGANDO';
                    }}
                    const processing = document.querySelector('.dataTables_processing');
                    if (processing) {{
                        const s = getComputedStyle(processing);
                        if (s.display !== 'none' && s.visibility !== 'hidden') return 'CARGANDO';
                    }}
                    const wanted = {tab_expr};
                    if (wanted) {{
                        const tab = document.getElementById(wanted);
                        if (!tab || !tab.disabled) return 'TAB_NO_ACTIVA';
                        const esperado = contador(wanted);
                        const textoPagina = document.body.innerText || '';
                        const coincidencias = Array.from(textoPagina.matchAll(
                            /Mostrando\s+\d+\s+a\s+\d+\s+de\s+([\d,.]+)\s+tr[aá]mites/gi
                        ));
                        const totalTabla = coincidencias.length
                            ? Number(coincidencias[coincidencias.length - 1][1].replace(/[,\.]/g, ''))
                            : null;
                        if (totalTabla !== esperado) return 'ESPERANDO_TOTAL';
                        if (esperado > 0) {{
                            const filas = Array.from(document.querySelectorAll('table tbody tr'));
                            const hayDatos = filas.some(tr => {{
                                const texto = (tr.innerText || tr.textContent || '').trim().toLowerCase();
                                return texto && !texto.includes('sin datos');
                            }});
                            if (!hayDatos) return 'ESPERANDO_FILAS';
                        }}
                    }}
                    return 'LISTO';
                }}"""
            ) or ""
            if ultimo_estado == "LISTO":
                return True
        except Exception:
            ultimo_estado = "CONTEXTO_CAMBIANDO"
        page.wait_for_timeout(400)

    log.warning(
        "[INT-WAIT] El tablero no termino de cargar en %.0fs (estado=%s).",
        limite,
        ultimo_estado or "desconocido",
    )
    return False


def _click_submenu_internos_dom(page) -> bool:
    """Pulsa el sub-menu exacto; evita el clic ambiguo del acordeon padre."""
    try:
        resultado = page.evaluate(
            """() => {
                const norm = texto => (texto || '').normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/\s+/g, ' ').trim().toLowerCase();
                const esperado = '+tys/sigedo/internos ift';
                const candidato = Array.from(document.querySelectorAll('a, button'))
                    .find(el => norm(el.innerText || el.textContent) === esperado
                        && el.offsetParent !== null);
                if (!candidato) return 'NO_VISIBLE';
                candidato.scrollIntoView({block: 'center'});
                candidato.click();
                return 'CLICK';
            }"""
        )
        if resultado == "CLICK":
            log.info("[INT-NAV] Click DOM en sub-menu +TyS/SIGEDO/Internos IFT")
            return True
    except Exception:
        pass
    return False


def navegar_a_internos_ift(page) -> bool:
    """
    Abre el tablero:
      Administracion solicitudes +TyS/SIGEDO/Internos IFT
      -> +TyS/SIGEDO/Internos IFT
    """
    log.info("[INT-NAV] Navegando a Administracion solicitudes +TyS/SIGEDO/Internos IFT...")
    try:
        _esperar_sin_spinner(page, timeout_ms=20_000)
        sidebar = page.locator("nav, .sidebar, aside").first
        sidebar.wait_for(state="visible", timeout=TIMEOUT_NAV)

        submenu_directo = _click_submenu_internos_dom(page)

        admin_patterns = [
            re.compile(r"Administraci.n\s+solicitudes.*Internos\s+IFT", re.I),
            re.compile(r"Administraci.n\s+solicitudes.*\+TyS", re.I),
            re.compile(r"solicitudes\s+\+TyS\s*/\s*SIGEDO\s*/\s*Internos", re.I),
        ]
        admin_ok = submenu_directo
        if not admin_ok:
            for pat in admin_patterns:
                if _click_texto_visible(page, pat, "menu Administracion solicitudes", TIMEOUT_NAV):
                    admin_ok = True
                    break
        if not admin_ok:
            log.warning("[INT-NAV] No se pudo confirmar click en menu principal; se intentara sub-menu directo.")

        try:
            page.wait_for_timeout(600)
            page.wait_for_selector("a:has-text('Internos IFT'), button:has-text('Internos IFT')", timeout=8_000)
        except Exception:
            pass

        submenu_patterns = [
            re.compile(r"^\s*\+?\s*TyS\s*/\s*SIGEDO\s*/\s*Internos\s+IFT\s*$", re.I),
            re.compile(r"\+TyS\s*/\s*SIGEDO\s*/\s*Internos\s+IFT", re.I),
        ]
        submenu_ok = submenu_directo
        if not submenu_ok:
            submenu_ok = _click_submenu_internos_dom(page)
        for pat in submenu_patterns:
            if submenu_ok:
                break
            try:
                locs = page.locator("a, button").filter(has_text=pat)
                count = locs.count()
            except Exception:
                count = 0
            for idx in range(count):
                loc = locs.nth(idx)
                try:
                    texto = loc.inner_text(timeout=1_000)
                except Exception:
                    texto = ""
                # Evita volver a pulsar el acordeon principal cuando el subitem existe.
                if count > 1 and re.search(r"Administraci.n\s+solicitudes", texto, re.I):
                    continue
                try:
                    loc.wait_for(state="visible", timeout=5_000)
                    loc.scroll_into_view_if_needed()
                    loc.click()
                    submenu_ok = True
                    log.info("[INT-NAV] Click en sub-menu +TyS/SIGEDO/Internos IFT")
                    break
                except Exception:
                    continue
            if submenu_ok:
                break

        if not submenu_ok:
            for pat in submenu_patterns:
                if _click_texto_visible(
                    page,
                    pat,
                    "sub-menu +TyS/SIGEDO/Internos IFT",
                    TIMEOUT_NAV,
                ):
                    submenu_ok = True
                    break

        if not submenu_ok:
            log.error("[INT-NAV] No se encontro sub-menu '+TyS/SIGEDO/Internos IFT'.")
            screenshot(page, "internos_submenu_no_encontrado")
            return False

        try:
            page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_NAV)
        except PWTimeout:
            pass
        if _esperar_estado_bandeja_internos(page, timeout_ms=60_000):
            log.info("[INT-NAV] Tablero Internos IFT cargado.")
            return True
        log.warning("[INT-NAV] El tablero Internos no termino de cargar.")
        screenshot(page, "internos_tablero_timeout")
        return False
        _esperar_sin_spinner(page, timeout_ms=30_000)

        for sel in (
            "text=Recibidos",
            "text=En proceso",
            "text=En Proceso",
            "text=Tramites",
            "text=TrÃ¡mites",
        ):
            try:
                page.wait_for_selector(sel, timeout=15_000)
                log.info("[INT-NAV] Tablero Internos IFT cargado.")
                return True
            except PWTimeout:
                continue

        log.warning("[INT-NAV] Tablero Internos no confirmado, se continuara con cautela.")
        screenshot(page, "internos_tablero_dudoso")
        return True
    except Exception as exc:
        log.error("[INT-NAV] Error navegando a Internos IFT: %s", exc)
        screenshot(page, "internos_nav_error")
        return False


def seleccionar_bandeja_internos(page, bandeja: str) -> bool:
    """Activa una bandeja por el ID fijo que publica el tablero Internos."""
    log.info("[INT-TAB] Activando bandeja '%s'...", bandeja)
    tab_id = INTERNOS_BANDEJA_IDS.get(_normalizar_nombre_internos(bandeja))
    if not tab_id:
        log.warning("[INT-TAB] Bandeja no reconocida: '%s'.", bandeja)
        return False
    try:
        resultado = page.evaluate(
            """(id) => {
                const target = document.getElementById(id);
                if (!target) return 'NO_EXISTE';
                if (target.disabled) return 'ACTIVA';
                target.scrollIntoView({block: 'center', inline: 'center'});
                target.click();
                return 'CLICK';
            }""",
            tab_id,
        ) or ""
        if resultado == "NO_EXISTE" or not resultado:
            log.warning("[INT-TAB] No se encontro la bandeja '%s'.", bandeja)
            screenshot(page, f"internos_bandeja_no_encontrada_{_slug_internos(bandeja)}")
            return False
        if not _esperar_estado_bandeja_internos(page, tab_id=tab_id, timeout_ms=90_000):
            screenshot(page, f"internos_bandeja_timeout_{_slug_internos(bandeja)}")
            return False
        log.info("[INT-TAB] Bandeja '%s' lista.", bandeja)
        return True
    except Exception as exc:
        log.error("[INT-TAB] Error seleccionando bandeja '%s': %s", bandeja, exc)
        return False


def _seleccionar_bandeja_internos_legacy(page, bandeja: str) -> bool:
    log.info("[INT-TAB] Activando bandeja '%s'...", bandeja)
    try:
        resultado = page.evaluate(
            r"""
            (wantedRaw) => {
              const visible = el => {
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
              };
              const norm = txt => (txt || '').normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
              const wanted = norm(wantedRaw);
              const candidates = Array.from(document.querySelectorAll('a, button, [role="tab"]'))
                .filter(visible)
                .map(el => {
                  const raw = norm(el.innerText || el.textContent || '');
                  const label = raw.replace(/\s+[\d,.]+\s*$/, '').trim();
                  const inTabs = !!el.closest('.nav-tabs, .nav-pills, [role="tablist"]');
                  return {el, label, score: (inTabs ? 1000 : 0) - raw.length};
                })
                .filter(item => item.label === wanted)
                .sort((a, b) => b.score - a.score);
              if (!candidates.length) return {ok: false};
              const target = candidates[0].el;
              const parent = target.closest('li, [role="presentation"]');
              const alreadyActive = target.disabled || target.getAttribute('aria-selected') === 'true'
                || target.classList.contains('active') || !!parent?.classList.contains('active');
              target.scrollIntoView({block: 'center', inline: 'center'});
              if (!alreadyActive) target.click();
              return {ok: true, alreadyActive};
            }
            """,
            bandeja,
        ) or {}
        if not resultado.get("ok"):
            log.warning("[INT-TAB] No se encontro la bandeja '%s'.", bandeja)
            screenshot(page, f"internos_bandeja_no_encontrada_{_slug_internos(bandeja)}")
            return False

        if not resultado.get("alreadyActive"):
            page.wait_for_function(
                r"""
                (wantedRaw) => {
                  const visible = el => {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
                  };
                  const norm = txt => (txt || '').normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
                  const wanted = norm(wantedRaw);
                  return Array.from(document.querySelectorAll('a, button, [role="tab"]'))
                    .filter(visible)
                    .some(el => {
                      const label = norm(el.innerText || el.textContent || '')
                        .replace(/\s+[\d,.]+\s*$/, '').trim();
                      const parent = el.closest('li, [role="presentation"]');
                      return label === wanted && (
                        el.disabled || el.getAttribute('aria-selected') === 'true'
                        || el.classList.contains('active') || !!parent?.classList.contains('active')
                      );
                    });
                }
                """,
                arg=bandeja,
                timeout=TIMEOUT_NAV,
            )
            page.wait_for_timeout(300)
            _esperar_sin_spinner(page, timeout_ms=30_000)
        try:
            page.wait_for_selector("table, input[type='search'], .dataTables_wrapper", timeout=15_000)
        except Exception:
            pass
        log.info("[INT-TAB] Bandeja '%s' lista.", bandeja)
        return True
    except Exception as exc:
        log.error("[INT-TAB] Error seleccionando bandeja '%s': %s", bandeja, exc)
        return False


def configurar_mostrar_100_internos(page, etiqueta: str = "tramites") -> bool:
    """Cambia el selector visible 'Mostrar 10 tramites/documentos' a 100."""
    try:
        elegido = page.evaluate(
            r"""
            (etiqueta) => {
                const visible = el => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && !el.hidden;
                };
                const norm = txt => (txt || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
                const selects = Array.from(document.querySelectorAll('select')).filter(visible);
                const candidatos = [];
                for (const sel of selects) {
                    const options = Array.from(sel.options || []);
                    const opt100 = options.find(op => (op.value || op.textContent || '').trim() === '100');
                    if (!opt100) continue;
                    const hasYears = options.some(op => /\b20\d{2}\b/.test(`${op.value || ''} ${op.textContent || ''}`));
                    if (hasYears) continue;
                    let ctx = '';
                    let p = sel;
                    for (let i = 0; i < 5 && p; i++, p = p.parentElement) ctx += ' ' + (p.textContent || '');
                    const ctxNorm = norm(ctx);
                    const score = (ctxNorm.includes('mostrar') ? 100 : 0)
                        + (ctxNorm.includes(norm(etiqueta)) ? 80 : 0)
                        + (ctxNorm.includes('tramite') ? 40 : 0)
                        + (ctxNorm.includes('documento') ? 40 : 0);
                    candidatos.push({sel, opt100, score});
                }
                candidatos.sort((a, b) => b.score - a.score);
                if (!candidatos.length) return null;
                const {sel, opt100} = candidatos[0];
                if (sel.value !== opt100.value) {
                    sel.value = opt100.value;
                    opt100.selected = true;
                    sel.dispatchEvent(new Event('input', {bubbles: true}));
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                }
                return opt100.value || opt100.textContent || '100';
            }
            """,
            etiqueta,
        )
        if elegido:
            log.info("[INT-CFG] Selector Mostrar cambiado/verificado a %s (%s).", elegido, etiqueta)
            _esperar_sin_spinner(page, timeout_ms=20_000)
            try:
                page.wait_for_function(
                    "() => { const p = document.querySelector('.dataTables_processing'); "
                    "return !p || p.style.display === 'none' || p.style.display === ''; }",
                    timeout=8_000,
                )
            except Exception:
                page.wait_for_timeout(800)
            return True
    except Exception as exc:
        log.warning("[INT-CFG] No se pudo cambiar Mostrar 100 (%s): %s", etiqueta, exc)
    return False


def _fila_sin_datos(texto: str) -> bool:
    t = (texto or "").strip().lower()
    return not t or "no hay" in t or "no data" in t or "sin datos" in t or "sin resultados" in t


def _extraer_datos_fila_internos(fila) -> dict:
    try:
        data_raw = fila.evaluate(
            r"""
            (row) => {
                const clean = txt => (txt || '').replace(/\s+/g, ' ').trim();
                const table = row.closest('table');
                const headers = table
                    ? Array.from(table.querySelectorAll('thead th')).map(th => clean(th.textContent))
                    : [];
                const cells = Array.from(row.querySelectorAll('td')).map(td => clean(td.textContent));
                const columnas = {};
                cells.forEach((value, idx) => {
                    const header = headers[idx] || `col_${idx + 1}`;
                    columnas[header] = value;
                });
                return JSON.stringify({headers, cells, columnas});
            }
            """
        ) or "{}"
        data = json.loads(data_raw)
    except Exception:
        data = {}

    columnas = data.get("columnas") or {}
    meta = {"folio": "", "tipo_tramite": "", "texto_fila": ""}
    try:
        meta["texto_fila"] = fila.inner_text()
    except Exception:
        pass

    for header, value in columnas.items():
        h = str(header or "").lower()
        v = str(value or "").strip()
        if not v:
            continue
        if "tipo" in h:
            meta["tipo_tramite"] = v
            continue
        if "folio" in h and not meta["folio"]:
            meta["folio"] = v
        elif ("tipo" in h and "tram" in h) or h.strip() in {"tramite", "trÃ¡mite"}:
            meta["tipo_tramite"] = v
    return meta


def _encontrar_boton_revisar(fila):
    try:
        controles = fila.locator("a, button, input[type='button'], input[type='submit']")
        for idx in range(controles.count()):
            loc = controles.nth(idx)
            try:
                texto = (loc.inner_text(timeout=500) or "").strip()
            except Exception:
                try:
                    texto = (loc.input_value(timeout=500) or "").strip()
                except Exception:
                    texto = ""
            try:
                titulo = loc.get_attribute("title") or ""
                clases = loc.get_attribute("class") or ""
                html = loc.inner_html(timeout=500) or ""
            except Exception:
                titulo = clases = html = ""
            marca = f"{texto} {titulo} {clases} {html}".lower()
            if "revisar" in marca or "fa-eye" in marca or "glyphicon-eye-open" in marca:
                loc.wait_for(state="visible", timeout=2_000)
                return loc
    except Exception:
        pass

    selectores = [
        "input[value*='Revisar']",
        "a[title*='Revisar']",
        "button[title*='Revisar']",
        "a.btn-success, button.btn-success",
        "a[class*='success'], button[class*='success']",
    ]
    for sel in selectores:
        try:
            loc = fila.locator(sel).first
            if loc.count() > 0:
                loc.wait_for(state="visible", timeout=2_000)
                return loc
        except Exception:
            continue
    return None


def _carpeta_internos_para(bandeja: str, identificador: str, firma: str, usadas: set[str]) -> Path:
    base = DESCARGA_BASE / "internos" / _slug_internos(bandeja)
    safe = _sanitizar_nombre_carpeta(identificador or f"tramite_{firma[:8]}")
    carpeta = base / safe
    key = str(carpeta).lower()
    if key in usadas:
        carpeta = base / f"{safe}_{firma[:8]}"
        key = str(carpeta).lower()
    if carpeta.exists():
        meta_path = carpeta / "metadata_satys.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except Exception:
            meta = {}
        if meta.get("satys_internos_signature") and meta.get("satys_internos_signature") != firma:
            carpeta = base / f"{safe}_{firma[:8]}"
            key = str(carpeta).lower()
    usadas.add(key)
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _actualizar_metadata_internos(
    carpeta: Path,
    bandeja: str,
    firma: str,
    row_meta: dict,
    meta: dict,
    resultados: list,
) -> dict:
    meta_final = dict(meta or {})
    for key, value in (row_meta or {}).items():
        if value and not meta_final.get(key):
            meta_final[key] = value
    if meta_final.get("nombre_operador") and not meta_final.get("concesionario"):
        meta_final["concesionario"] = meta_final["nombre_operador"]
    if meta_final.get("concesionario") and not meta_final.get("nombre_operador"):
        meta_final["nombre_operador"] = meta_final["concesionario"]
    if meta_final.get("promovente") and not meta_final.get("representante_legal"):
        meta_final["representante_legal"] = meta_final["promovente"]

    folio = str(meta_final.get("folio") or row_meta.get("folio") or "").strip()
    folio_tabla_internos = str(row_meta.get("folio") or "").strip()
    registro = _normalizar_registro_interno(meta_final.get("registro") or folio)
    if registro:
        meta_final["registro"] = registro
    if folio:
        meta_final["folio"] = folio
    if folio_tabla_internos:
        meta_final["folio_tabla_internos"] = folio_tabla_internos

    meta_final.update({
        "satys_flujo": "internos",
        "bandeja_internos": bandeja,
        "satys_internos_signature": firma,
        "fuente_descarga": "DOCUMENTOS_ANEXOS",
        "fecha_proceso": datetime.now().isoformat(),
        "total_archivos_encontrados": len(resultados),
        "total_archivos_ok": sum(1 for r in resultados if r.get("ok")),
    })

    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre in ("metadata_tramite_nuevo.json", "metadata_satys.json"):
        with open(carpeta / nombre, "w", encoding="utf-8") as f:
            json.dump(meta_final, f, ensure_ascii=False, indent=2)
    return meta_final


def _volver_a_bandeja_internos(page, bandeja: str) -> bool:
    try:
        if not navegar_a_internos_ift(page):
            return False
        if not seleccionar_bandeja_internos(page, bandeja):
            return False
        configurar_mostrar_100_internos(page, "tramites")
        return True
    except Exception as exc:
        log.warning("[INT-BACK] No se pudo volver a bandeja '%s': %s", bandeja, exc)
        return False


def procesar_bandeja_internos(
    context,
    page,
    bandeja: str,
    max_pasadas: int = 10_000,
    folios_objetivo: list[str] | None = None,
) -> list:
    """Procesa todas las filas visibles/paginadas de una bandeja de Internos IFT."""
    resultados_bandeja = []
    procesadas: set[str] = set()
    carpetas_usadas: set[str] = set()
    objetivos = {
        str(folio or "").strip()
        for folio in (folios_objetivo or [])
        if re.fullmatch(r"\d{1,15}", str(folio or "").strip())
    }
    objetivos_encontrados: set[str] = set()

    if not seleccionar_bandeja_internos(page, bandeja):
        return resultados_bandeja
    configurar_mostrar_100_internos(page, "tramites")

    pasadas = 0
    while pasadas < max_pasadas:
        if objetivos and objetivos.issubset(objetivos_encontrados):
            break
        pasadas += 1
        _esperar_sin_spinner(page, timeout_ms=30_000)

        try:
            page.wait_for_selector("table tbody tr", timeout=15_000)
        except PWTimeout:
            log.warning("[INT-LOOP] Sin filas detectables en '%s'.", bandeja)
            break

        filas = page.locator("table tbody tr")
        total_filas = filas.count()
        abrio_fila = False

        for idx in range(total_filas):
            fila = filas.nth(idx)
            try:
                texto_fila = fila.inner_text().strip()
            except Exception:
                continue
            if _fila_sin_datos(texto_fila):
                continue

            row_meta = _extraer_datos_fila_internos(fila)
            folio_fila = str(row_meta.get("folio") or "").strip()
            if objetivos and folio_fila not in objetivos:
                continue

            firma = _firma_texto(f"{bandeja}|{texto_fila}")
            if firma in procesadas:
                continue

            boton = _encontrar_boton_revisar(fila)
            if boton is None:
                log.warning("[INT-LOOP] Fila sin boton Revisar en '%s': %s", bandeja, texto_fila[:120])
                procesadas.add(firma)
                continue

            identificador = row_meta.get("folio") or f"tramite_{firma[:8]}"
            carpeta = _carpeta_internos_para(bandeja, identificador, firma, carpetas_usadas)

            log.info(
                "[INT-REVISAR] Bandeja=%s folio=%s carpeta=%s",
                bandeja,
                identificador,
                carpeta,
            )
            try:
                boton.scroll_into_view_if_needed()
                try:
                    boton.click()
                except Exception:
                    boton.click(force=True)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_DETALLE)
                except PWTimeout:
                    pass
                _esperar_sin_spinner(page, timeout_ms=30_000)

                for sel in (
                    "text=DETALLE DE LA SOLICITUD",
                    "text=DATOS DEL TRAMITE",
                    "text=DATOS DEL TRÃMITE",
                    "text=DOCUMENTOS ANEXOS",
                ):
                    try:
                        page.wait_for_selector(sel, timeout=8_000)
                        break
                    except PWTimeout:
                        continue

                meta = extraer_metadatos_tramite_nuevo(page, identificador, carpeta)
                meta = _actualizar_metadata_internos(carpeta, bandeja, firma, row_meta, meta, [])

                folio_descarga = str(meta.get("folio") or identificador or firma[:8])
                res = descargar_via_documentos_anexos(context, page, folio_descarga, carpeta)
                for item in res:
                    item.setdefault("fuente", "DOCUMENTOS_ANEXOS")
                    item["bandeja_internos"] = bandeja
                    item.setdefault("registro", meta.get("registro") or folio_descarga)
                    item.setdefault("carpeta", str(carpeta))

                descomprimir_todos_zips_en_carpeta(carpeta)
                zips_residuales = sorted(carpeta.rglob("*.zip"))
                for zip_residual in zips_residuales:
                    res.append({
                        "folio": folio_descarga,
                        "archivo": zip_residual.name,
                        "nombre_original": zip_residual.name,
                        "tipo": "ERROR_ZIP_RESIDUAL",
                        "ruta": str(zip_residual),
                        "tamano_kb": round(zip_residual.stat().st_size / 1024, 1),
                        "ok": False,
                        "error": "El ZIP no pudo descomprimirse por completo.",
                        "fuente": "DOCUMENTOS_ANEXOS",
                        "bandeja_internos": bandeja,
                        "registro": meta.get("registro") or folio_descarga,
                        "carpeta": str(carpeta),
                    })

                meta = _actualizar_metadata_internos(carpeta, bandeja, firma, row_meta, meta, res)
                guardar_metadata_completo(
                    folio_descarga,
                    folio_descarga,
                    carpeta,
                    meta,
                    meta,
                    res,
                    "INTERNOS_DOCUMENTOS_ANEXOS",
                )

                if res:
                    resultados_bandeja.extend(res)
                else:
                    resultados_bandeja.append({
                        "folio": folio_descarga,
                        "archivo": f"SIN_ARCHIVOS_{folio_descarga}",
                        "tipo": "N/A",
                        "ruta": "",
                        "tamano_kb": 0,
                        "ok": False,
                        "fuente": "INTERNOS_DOCUMENTOS_ANEXOS",
                        "bandeja_internos": bandeja,
                        "registro": meta.get("registro") or folio_descarga,
                        "carpeta": str(carpeta),
                    })

                procesadas.add(firma)
                if folio_fila:
                    objetivos_encontrados.add(folio_fila)
                abrio_fila = True
            except Exception as exc:
                log.error("[INT-ERROR] Error procesando fila de '%s': %s", bandeja, exc)
                screenshot(page, f"internos_error_{_slug_internos(bandeja)}_{firma[:8]}")
                procesadas.add(firma)
                if folio_fila:
                    objetivos_encontrados.add(folio_fila)
                resultados_bandeja.append({
                    "folio": folio_fila or identificador,
                    "archivo": f"ERROR_INTERNO_{folio_fila or identificador}",
                    "tipo": "N/A",
                    "ruta": "",
                    "tamano_kb": 0,
                    "ok": False,
                    "fuente": "INTERNOS_DOCUMENTOS_ANEXOS",
                    "bandeja_internos": bandeja,
                    "registro": "",
                    "carpeta": str(carpeta),
                    "error": str(exc),
                })
            finally:
                _cerrar_paginas_emergentes(
                    context,
                    page,
                    motivo=f"folio Internos {identificador}",
                )
                if not _volver_a_bandeja_internos(page, bandeja):
                    log.warning("[INT-BACK] Reintento de navegacion a Internos tras detalle.")
                    navegar_a_internos_ift(page)
                    seleccionar_bandeja_internos(page, bandeja)
                    configurar_mostrar_100_internos(page, "tramites")

            if abrio_fila:
                break

        if abrio_fila:
            continue

        mostrado_hasta, total = _parsear_paginacion(page)
        if total > 0:
            log.info("[INT-PAGE] Bandeja '%s': mostrando %d de %d tramites.", bandeja, mostrado_hasta, total)
        if total > 0 and mostrado_hasta < total and _avanzar_pagina_datatables(page):
            continue
        if _avanzar_pagina_datatables(page):
            continue
        break

    for folio_faltante in sorted(objetivos - objetivos_encontrados):
        resultados_bandeja.append({
            "folio": folio_faltante,
            "archivo": f"FOLIO_INTERNO_NO_ENCONTRADO_{folio_faltante}",
            "tipo": "N/A",
            "ruta": "",
            "tamano_kb": 0,
            "ok": False,
            "fuente": "INTERNOS_INVENTARIO",
            "bandeja_internos": bandeja,
            "registro": "",
            "carpeta": "",
        })

    log.info(
        "[INT-OK] Bandeja '%s' finalizada: %d fila(s) revisada(s), %d resultado(s).",
        bandeja,
        len(procesadas),
        len(resultados_bandeja),
    )
    return resultados_bandeja


def _validar_sesion_internos() -> bool:
    """Valida o renueva una sola vez la sesion que clonaran los workers."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS, slow_mo=0 if HEADLESS else 50)
        try:
            context_args = {
                "accept_downloads": True,
                "viewport": {"width": 1400, "height": 900},
                "locale": "es-MX",
            }
            if SESION_FILE.exists():
                context_args["storage_state"] = str(SESION_FILE)
            context = browser.new_context(**context_args)
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_NAV)

            login_requerido = True
            if SESION_FILE.exists():
                try:
                    page.goto(
                        urljoin(BASE_URL, "Sarccontroller"),
                        wait_until="domcontentloaded",
                        timeout=TIMEOUT_NAV,
                    )
                    url_actual = page.url.lower()
                    login_requerido = (
                        "login" in url_actual
                        or "verifylogin" in url_actual
                        or page.locator("input[type='password']").count() > 0
                    )
                    if not login_requerido:
                        log.info("[INT-SESION] Sesion activa lista para los workers.")
                except Exception as exc:
                    log.warning("[INT-SESION] Error validando sesion guardada: %s", exc)

            if login_requerido:
                if not login(page):
                    return False
                with _sesion_lock:
                    context.storage_state(path=SESION_FILE)
                log.info("[INT-SESION] Sesion renovada para los workers.")
            return True
        finally:
            browser.close()


def _resultado_error_bandeja_internos(bandeja: str, codigo: str, detalle: str) -> list[dict]:
    return [{
        "folio": "",
        "archivo": codigo,
        "tipo": "ERROR",
        "ruta": detalle[:240],
        "tamano_kb": 0,
        "ok": False,
        "fuente": "INTERNOS_WORKER",
        "bandeja_internos": bandeja,
        "registro": "",
        "carpeta": "",
        "error": detalle,
    }]


def _worker_bandeja_internos(
    bandeja: str,
    folios_objetivo: list[str] | None = None,
) -> tuple[str, list]:
    """Procesa una bandeja en un Playwright y Chromium independientes."""
    tname = threading.current_thread().name
    log.info("[INT-W:%s] Iniciando bandeja '%s'.", tname, bandeja)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                slow_mo=0 if HEADLESS else 50,
            )
            try:
                context_args = {
                    "accept_downloads": True,
                    "viewport": {"width": 1400, "height": 900},
                    "locale": "es-MX",
                }
                if SESION_FILE.exists():
                    context_args["storage_state"] = str(SESION_FILE)
                context = browser.new_context(**context_args)
                habilitar_api_discovery(context)
                page = context.new_page()
                page.set_default_timeout(TIMEOUT_NAV)
                page.goto(
                    urljoin(BASE_URL, "Sarccontroller"),
                    wait_until="domcontentloaded",
                    timeout=TIMEOUT_NAV,
                )
                url_actual = page.url.lower()
                en_login = (
                    "login" in url_actual
                    or "verifylogin" in url_actual
                    or page.locator("input[type='password']").count() > 0
                )
                if en_login:
                    detalle = "La sesion clonada expiro antes de iniciar la bandeja."
                    log.error("[INT-W:%s] %s Bandeja=%s", tname, detalle, bandeja)
                    return bandeja, _resultado_error_bandeja_internos(
                        bandeja,
                        "ERROR_SESION_WORKER",
                        detalle,
                    )
                if not navegar_a_internos_ift(page):
                    detalle = "No se pudo abrir el tablero Internos IFT."
                    return bandeja, _resultado_error_bandeja_internos(
                        bandeja,
                        "ERROR_TABLERO_WORKER",
                        detalle,
                    )

                resultados = procesar_bandeja_internos(
                    context,
                    page,
                    bandeja,
                    folios_objetivo=folios_objetivo,
                )
                ok = sum(1 for item in resultados if item.get("ok"))
                log.info(
                    "[INT-W:%s] Bandeja '%s' terminada: %d OK / %d resultado(s).",
                    tname,
                    bandeja,
                    ok,
                    len(resultados),
                )
                return bandeja, resultados
            finally:
                browser.close()
    except Exception as exc:
        log.exception("[INT-W:%s] Error fatal en bandeja '%s'.", tname, bandeja)
        return bandeja, _resultado_error_bandeja_internos(
            bandeja,
            "ERROR_WORKER_BANDEJA",
            str(exc),
        )


_ERRORES_REINTENTABLES_WORKER_INTERNOS = {
    "ERROR_SESION_WORKER",
    "ERROR_TABLERO_WORKER",
    "ERROR_WORKER_BANDEJA",
    "EXCEPCION_WORKER_BANDEJA",
}


def _worker_bandeja_internos_con_reintentos(
    bandeja: str,
    folios_objetivo: list[str] | None = None,
) -> tuple[str, list]:
    """Reintenta fallos de infraestructura o un segmento vacío inesperado."""
    ultimo_resultado: list = []
    intentos_totales = INTERNOS_WORKER_REINTENTOS + 1
    for intento in range(1, intentos_totales + 1):
        nombre_bandeja, ultimo_resultado = _worker_bandeja_internos(
            bandeja,
            folios_objetivo,
        )
        codigos = {
            str(item.get("archivo") or "")
            for item in ultimo_resultado
            if isinstance(item, dict)
        }
        segmento_vacio = bool(folios_objetivo) and not ultimo_resultado
        fallo_infraestructura = bool(codigos & _ERRORES_REINTENTABLES_WORKER_INTERNOS)
        if not segmento_vacio and not fallo_infraestructura:
            return nombre_bandeja, ultimo_resultado
        if intento >= intentos_totales:
            break
        motivo = "segmento vacio" if segmento_vacio else ", ".join(sorted(codigos))
        log.warning(
            "[INT-RETRY] Bandeja '%s' intento %d/%d no valido (%s); reintentando segmento.",
            bandeja,
            intento,
            intentos_totales,
            motivo,
        )
        if INTERNOS_WORKER_ESPERA:
            time.sleep(INTERNOS_WORKER_ESPERA)

    if folios_objetivo and not ultimo_resultado:
        detalle = (
            f"El segmento termino vacio tras {intentos_totales} intento(s), "
            f"aunque tenia {len(folios_objetivo)} folio(s) asignado(s)."
        )
        log.error("[INT-RETRY] %s Bandeja=%s", detalle, bandeja)
        ultimo_resultado = _resultado_error_bandeja_internos(
            bandeja,
            "ERROR_SEGMENTO_INTERNO_VACIO",
            detalle,
        )
    return bandeja, ultimo_resultado


def descargar_internos_ift(
    bandejas: list[str] | None = None,
    objetivos: list[dict] | None = None,
    workers: int | None = None,
) -> list:
    """Descarga Internos en navegadores independientes y paralelos.

    Cuando hay objetivos explícitos, reparte los workers entre las bandejas de
    acuerdo con su carga y divide sus folios en segmentos disjuntos. Así una
    bandeja grande puede usar varios navegadores sin descargar el mismo folio
    dos veces.
    """
    objetivos = objetivos or []
    objetivos_por_bandeja: dict[str, list[str]] = {}
    folios_vistos_por_bandeja: dict[str, set[str]] = {}
    nombres_bandeja: dict[str, str] = {}
    for item in objetivos:
        bandeja = str(item.get("bandeja") or "").strip()
        folio = str(item.get("folio") or "").strip()
        if not bandeja or not re.fullmatch(r"\d{1,15}", folio):
            continue
        key = bandeja.lower()
        nombres_bandeja.setdefault(key, bandeja)
        vistos = folios_vistos_por_bandeja.setdefault(key, set())
        if folio not in vistos:
            vistos.add(folio)
            objetivos_por_bandeja.setdefault(key, []).append(folio)

    if objetivos_por_bandeja:
        bandejas = [nombres_bandeja[key] for key in objetivos_por_bandeja]
    else:
        bandejas = _unique_preserve_order(bandejas or BANDEJAS_INTERNOS_DEFAULT)
    if not bandejas:
        return []

    workers_configurados = INTERNOS_WORKERS_DEFAULT if workers is None else int(workers)
    if workers_configurados < 0:
        raise ValueError("internos_workers no puede ser negativo")

    # Cada tarea usa un Chromium. Sin objetivos se conserva el comportamiento
    # histórico de una tarea por bandeja. Con objetivos, los slots adicionales
    # se asignan a la bandeja con mayor carga por worker y sus folios se reparten
    # round-robin para que todos los segmentos avancen por páginas similares.
    tareas: list[tuple[str, str, list[str] | None]] = []
    if objetivos_por_bandeja:
        claves = [bandeja.lower() for bandeja in bandejas]
        limite_workers = len(claves) if workers_configurados == 0 else max(1, workers_configurados)
        minimo_por_bandeja = 2 if limite_workers >= (2 * len(claves)) else 1
        segmentos_por_bandeja = {
            key: min(minimo_por_bandeja, len(objetivos_por_bandeja[key]))
            for key in claves
        }
        slots_extra = max(0, limite_workers - sum(segmentos_por_bandeja.values()))
        while slots_extra > 0:
            candidatas = [
                key for key in claves
                if segmentos_por_bandeja[key] < len(objetivos_por_bandeja[key])
            ]
            if not candidatas:
                break
            key = max(
                candidatas,
                key=lambda item: (
                    len(objetivos_por_bandeja[item]) / segmentos_por_bandeja[item],
                    -claves.index(item),
                ),
            )
            segmentos_por_bandeja[key] += 1
            slots_extra -= 1

        for bandeja in bandejas:
            key = bandeja.lower()
            folios = objetivos_por_bandeja[key]
            total_segmentos = segmentos_por_bandeja[key]
            for indice in range(total_segmentos):
                segmento = folios[indice::total_segmentos]
                if segmento:
                    tarea_id = f"{key}#{indice + 1}/{total_segmentos}"
                    tareas.append((tarea_id, bandeja, segmento))
    else:
        tareas = [
            (f"{bandeja.lower()}#1/1", bandeja, None)
            for bandeja in bandejas
        ]

    if workers_configurados == 0:
        workers_activos = len(tareas)
    else:
        workers_activos = min(max(1, workers_configurados), len(tareas))

    try:
        sesion_lista = _validar_sesion_internos()
    except Exception as exc:
        log.exception("[INT-MAIN] Fallo preparando la sesion para los workers.")
        return _resultado_error_bandeja_internos(
            "Sesion",
            "ERROR_PREPARACION_SESION_INTERNOS",
            str(exc),
        )

    if not sesion_lista:
        log.error("[INT-MAIN] No se pudo preparar la sesion para los workers.")
        return _resultado_error_bandeja_internos(
            "Sesion",
            "ERROR_SESION_INTERNOS",
            "No se pudo iniciar o validar la sesion SATyS.",
        )

    log.info(
        "[INT-MAIN] Paralelismo Internos: %d navegador(es), %d segmento(s), %d bandeja(s).",
        workers_activos,
        len(tareas),
        len(bandejas),
    )
    for tarea_id, bandeja, folios_segmento in tareas:
        if folios_segmento is not None:
            log.info(
                "[INT-MAIN] Segmento %s: bandeja='%s', %d folio(s).",
                tarea_id,
                bandeja,
                len(folios_segmento),
            )

    resultados_por_tarea: dict[str, list] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers_activos,
        thread_name_prefix="SATyS-Internos",
    ) as executor:
        futures = {
            executor.submit(
                _worker_bandeja_internos_con_reintentos,
                bandeja,
                folios_segmento,
            ): (tarea_id, bandeja)
            for tarea_id, bandeja, folios_segmento in tareas
        }
        completadas = 0
        for future in concurrent.futures.as_completed(futures):
            tarea_id, bandeja = futures[future]
            completadas += 1
            try:
                _bandeja_resultado, resultados = future.result()
            except Exception as exc:
                resultados = _resultado_error_bandeja_internos(
                    bandeja,
                    "EXCEPCION_WORKER_BANDEJA",
                    str(exc),
                )
            resultados_por_tarea[tarea_id] = resultados
            log.info(
                "[INT-CONC] [%d/%d] Segmento %s de '%s' completado.",
                completadas,
                len(tareas),
                tarea_id,
                bandeja,
            )

    todos_resultados = []
    for tarea_id, _bandeja, _folios_segmento in tareas:
        todos_resultados.extend(resultados_por_tarea.get(tarea_id, []))

    carpeta_resumen = DESCARGA_BASE / "internos"
    try:
        guardar_resumen_global(todos_resultados, carpeta_resumen)
    except Exception as exc:
        log.warning("[INT] No se pudo guardar resumen global de Internos: %s", exc)
    return todos_resultados


def guardar_metadata_completo(
    folio: str,
    folio_raw: str,
    carpeta: Path,
    meta_satys: dict,
    meta_tramite: dict,
    resultados: list,
    fuente: str,
) -> dict:
    """
    Consolida todos los metadatos y resultados de un folio en un unico JSON:
      descargas/<folio>/metadata_completo.json
    Incluye: fuente de descarga, metadatos SATyS, metadatos del tramite,
             lista de archivos con URLs, y validacion de conteo.
    """
    ok_count = sum(1 for r in resultados if r.get("ok"))
    total = len(resultados)

    def _entero_positivo(valor) -> int:
        try:
            return max(0, int(valor or 0))
        except (TypeError, ValueError):
            return 0

    totales_portal = {
        _entero_positivo(r.get("total_documentos_portal"))
        for r in resultados
        if isinstance(r, dict) and _entero_positivo(r.get("total_documentos_portal")) > 0
    }
    total_documentos_portal = max(totales_portal) if totales_portal else 0
    indices_portal = {
        _entero_positivo(r.get("indice_documento_portal"))
        for r in resultados
        if isinstance(r, dict) and _entero_positivo(r.get("indice_documento_portal")) > 0
    }
    indices_portal_ok = {
        indice for indice in indices_portal
        if any(
            _entero_positivo(r.get("indice_documento_portal")) == indice and r.get("ok")
            for r in resultados if isinstance(r, dict)
        )
        and not any(
            _entero_positivo(r.get("indice_documento_portal")) == indice and not r.get("ok")
            for r in resultados if isinstance(r, dict)
        )
    }
    documentos_portal_completos = (
        total_documentos_portal <= 0
        or (
            len(indices_portal) == total_documentos_portal
            and len(indices_portal_ok) == total_documentos_portal
        )
    )
    metadata_ok = ok_count > 0 and ok_count == total and documentos_portal_completos

    data = {
        "folio": folio,
        "folio_raw": folio_raw,
        "fecha_proceso": datetime.now().isoformat(),
        "fuente_descarga": fuente,
        "estado": "OK" if metadata_ok else (
            "PARCIAL" if ok_count > 0 else "SIN_ARCHIVOS"
        ),
        "metadatos_satys": meta_satys if meta_satys else {},
        "metadatos_tramite": meta_tramite if meta_tramite else {},
        "total_archivos_encontrados": total,
        "total_archivos_ok": ok_count,
        "total_archivos_error": total - ok_count,
        "total_documentos_portal": total_documentos_portal,
        "total_documentos_portal_recorridos": len(indices_portal),
        "total_documentos_portal_ok": len(indices_portal_ok),
        "documentos_portal_completos": documentos_portal_completos,
        "coincide": metadata_ok,
        "archivos": resultados,
    }

    out_path = carpeta / "metadata_completo.json"
    carpeta.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log.info(
        "[META] metadata_completo.json: fuente=%s  ok=%d/%d  estado=%s",
        fuente, ok_count, total, data["estado"]
    )
    return data


def guardar_resumen_global(todos_resultados: list, carpeta_base: Path) -> dict:
    por_folio: dict = {}
    for r in todos_resultados:
        f = r["folio"]
        por_folio.setdefault(f, {"ok": 0, "err": 0, "archivos": [], "no_encontrado": False})
        por_folio[f]["archivos"].append(r)
        if r.get("archivo") == "FOLIO_NO_ENCONTRADO":
            por_folio[f]["no_encontrado"] = True
            por_folio[f]["err"] += 1
        elif r.get("ok"):
            por_folio[f]["ok"] += 1
        else:
            por_folio[f]["err"] += 1

    folios_exitosos = 0
    folios_incompletos = 0
    folios_no_encontrados = 0

    for f, d in por_folio.items():
        if d["no_encontrado"]:
            folios_no_encontrados += 1
        elif d["err"] > 0:
            folios_incompletos += 1
        else:
            folios_exitosos += 1

    resumen = {
        "fecha": datetime.now().isoformat(),
        "total_folios": len(por_folio),
        "folios_exitosos": folios_exitosos,
        "folios_incompletos": folios_incompletos,
        "folios_no_encontrados": folios_no_encontrados,
        "total_archivos_descargados": sum(1 for r in todos_resultados if r.get("ok")),
        "detalle_folios": [
            {
                "folio": folio,
                "archivos_ok": d["ok"],
                "archivos_error": d["err"],
                "estado": (
                    "NO_ENCONTRADO" if d["no_encontrado"]
                    else "OK" if d["err"] == 0
                    else "INCOMPLETO"
                ),
                "carpeta": str(carpeta_base / folio),
            }
            for folio, d in por_folio.items()
        ],
    }

    carpeta_base.mkdir(parents=True, exist_ok=True)
    out_path = carpeta_base / "resumen_global.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(resumen, f, ensure_ascii=False, indent=2)

    log.info(
        "[RESUMEN] resumen_global.json: %d folios | %d Exitosos | %d Incompletos | %d No encontrados",
        resumen["total_folios"],
        resumen["folios_exitosos"],
        resumen["folios_incompletos"],
        resumen["folios_no_encontrados"],
    )
    return resumen


# ------------------------------------------------------------
#  REPORTE FINAL
# ------------------------------------------------------------
def generar_reporte(todos: list):
    print("\n" + "=" * 70)
    print("  REPORTE FINAL - PARTE 1: DESCARGA DE ARCHIVOS")
    print("  Fecha:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 70)

    por_folio: dict = {}
    for r in todos:
        f = r["folio"]
        por_folio.setdefault(f, {"ok": 0, "err": 0, "archivos": [], "no_encontrado": False})
        por_folio[f]["archivos"].append(r)
        if r.get("archivo") == "FOLIO_NO_ENCONTRADO":
            por_folio[f]["no_encontrado"] = True
            por_folio[f]["err"] += 1
        elif r.get("ok"):
            por_folio[f]["ok"] += 1
        else:
            por_folio[f]["err"] += 1

    folios_exitosos = {f: d for f, d in por_folio.items() if not d["no_encontrado"] and d["err"] == 0}
    folios_incompletos = {f: d for f, d in por_folio.items() if not d["no_encontrado"] and d["err"] > 0}
    folios_no_encontrados = {f: d for f, d in por_folio.items() if d["no_encontrado"]}

    def imprimir_grupo(titulo, grupo, icono):
        if not grupo: return
        print(f"\n  ▶ {titulo} ({len(grupo)} folios)")
        for folio, d in grupo.items():
            print(f"\n  {icono}  Folio: {folio}  ->  {DESCARGA_BASE / folio}")
            print(f"      Descargados: {d['ok']}   Errores: {d['err']}")
            print(f"      {'-'*50}")
            for a in d["archivos"]:
                if a.get("archivo") == "FOLIO_NO_ENCONTRADO":
                    print(f"      [--] El folio no existe en el Tablero")
                else:
                    est = "OK" if a["ok"] else "XX"
                    print(f"      [{est}] {a['archivo']:<45s}  {a['tipo']:<6s}  {a['tamano_kb']} KB")

    imprimir_grupo("FOLIOS EXITOSOS", folios_exitosos, "[OK]")
    imprimir_grupo("FOLIOS INCOMPLETOS / CON ERRORES", folios_incompletos, "[WARN]")
    imprimir_grupo("FOLIOS NO ENCONTRADOS", folios_no_encontrados, "[ERR]")

    total_archivos_ok = sum(1 for r in todos if r.get("ok"))
    
    print(f"\n  {'-'*66}")
    print(f"  TOTAL FOLIOS: {len(por_folio)} | Exitosos: {len(folios_exitosos)} | Incompletos: {len(folios_incompletos)} | No Encontrados: {len(folios_no_encontrados)}")
    print(f"  TOTAL ARCHIVOS DESCARGADOS: {total_archivos_ok}")
    print("=" * 70 + "\n")


def guardar_log_json(todos: list):
    DESCARGA_BASE.mkdir(parents=True, exist_ok=True)
    log_path = DESCARGA_BASE / "descarga_log.json"
    data = {
        "fecha": datetime.now().isoformat(),
        "total": len(todos),
        "ok": sum(1 for r in todos if r["ok"]),
        "errores": sum(1 for r in todos if not r["ok"]),
        "resultados": todos,
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("[OK] Log guardado: %s", log_path.resolve())


# ============================================================
#  WORKER -- FASE 3: Procesamiento concurrente por folio
# ============================================================
def _worker_folio(folio: str, folio_raw: str) -> tuple:
    """
    Procesa un único folio en un contexto Playwright COMPLETAMENTE independiente.

    Cada worker lanza su propio sync_playwright() + chromium browser.
    Esto es obligatorio para el API síncrono de Playwright (no es thread-safe
    compartir un Browser entre hilos). El estado de sesión se carga desde
    sesion_guardada.json, que el hilo principal validó/actualizó antes de
    lanzar los workers.

    Retorna: (folio: str, resultados: list)
    """
    tname = threading.current_thread().name
    log.info("[W:%s] ── Iniciando folio %s ──", tname, folio)
    resultados = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                slow_mo=0 if HEADLESS else 50,  # 0 en headless (producción); 50 en visible para depuración
            )
            context_args = {
                "accept_downloads": True,
                "viewport": {"width": 1400, "height": 900},
                "locale": "es-MX",
            }
            if SESION_FILE.exists():
                context_args["storage_state"] = str(SESION_FILE)

            context = browser.new_context(**context_args)
            habilitar_api_discovery(context)
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_NAV)

            # ── Validar sesión en este contexto ──────────────────────
            sesion_ok = False
            try:
                test_url = urljoin(BASE_URL, "Sarccontroller")
                page.goto(test_url, wait_until="domcontentloaded", timeout=TIMEOUT_NAV)
                url_actual = page.url.lower()
                en_login = (
                    "login" in url_actual
                    or "verifylogin" in url_actual
                    or page.locator("input[type='password']").count() > 0
                )
                if not en_login:
                    sesion_ok = True
                    log.info("[W:%s] Sesión activa (folio %s)", tname, folio)
                else:
                    # Sesión expiró entre el chequeo principal y el arranque del worker
                    log.warning("[W:%s] Sesión expirada — re-login (folio %s)", tname, folio)
                    if login(page):
                        with _sesion_lock:
                            try:
                                context.storage_state(path=SESION_FILE)
                            except Exception:
                                pass
                        sesion_ok = True
                    else:
                        log.error("[W:%s] Re-login fallido (folio %s)", tname, folio)
            except Exception as e:
                log.warning("[W:%s] Error validando sesión: %s", tname, e)

            if not sesion_ok:
                browser.close()
                return folio, [{
                    "folio": folio, "archivo": "ERROR_SESION",
                    "tipo": "ERROR", "ruta": "Sesión inválida o re-login fallido",
                    "tamano_kb": 0, "ok": False,
                }]

            # ── Navegar al tablero ────────────────────────────────────
            if not navegar_a_tablero(page):
                log.error("[W:%s] No se pudo navegar al tablero (folio %s)", tname, folio)
                browser.close()
                return folio, [{
                    "folio": folio, "archivo": "ERROR_TABLERO",
                    "tipo": "ERROR", "ruta": "No se pudo navegar al tablero",
                    "tamano_kb": 0, "ok": False,
                }]

            # Procesamiento único del folio. Los reintentos por archivo se manejan
            # dentro de descargar_archivos_folio; no existe un ciclo externo infinito.
            carpeta = crear_carpeta(folio)
            try:
                res = procesar_folio_completo(
                    context, page, folio, carpeta, folio_raw=folio_raw
                )
                resultados = res if res else [{
                    "folio": folio, "archivo": "FOLIO_NO_ENCONTRADO",
                    "tipo": "N/A", "ruta": "", "tamano_kb": 0, "ok": False,
                }]
            except Exception as e:
                log.error("[W:%s] Error procesando folio %s: %s", tname, folio, e)
                screenshot(page, f"fatal_{folio}")
                resultados = [{
                    "folio": folio, "archivo": "ERROR_FATAL",
                    "tipo": "ERROR", "ruta": str(e)[:120], "tamano_kb": 0, "ok": False,
                }]

            ok_c = sum(1 for r in resultados if r.get("ok"))
            errores_servidor = sum(1 for r in resultados if r.get("tipo") == "ERROR_SERVIDOR")
            errores_otros    = sum(1 for r in resultados if not r.get("ok") and r.get("tipo") not in
                                   ("ERROR_SERVIDOR", "N/A") and r.get("archivo") not in
                                   ("FOLIO_NO_ENCONTRADO", "ERROR_FATAL", "ERROR_SESION", "ERROR_TABLERO"))
            log.info("[W:%s] ── Folio %s: %d/%d OK | %d error(es) servidor | %d error(es) red ──",
                     tname, folio, ok_c, len(resultados), errores_servidor, errores_otros)

            browser.close()

    except Exception as e:
        log.error("[W:%s] Error crítico en worker para folio %s: %s", tname, folio, e)
        resultados = resultados or [{
            "folio": folio, "archivo": "ERROR_WORKER_CRITICO",
            "tipo": "ERROR", "ruta": str(e)[:120], "tamano_kb": 0, "ok": False,
        }]

    return folio, resultados



# ============================================================
#  WATCHDOG REGISTRO -- Procesamiento aislado y matable
# ============================================================
def _sanitize_for_filename(text: str) -> str:
    """Convierte un registro/folio en un nombre seguro de archivo."""
    text = (text or "SIN_REGISTRO").strip()
    text = re.sub(r'[^A-Za-z0-9_.-]+', '_', text)
    return text[:80] or "SIN_REGISTRO"


def _resultado_controlado_registro(registro: str, archivo: str, tipo: str, detalle: str, **extra) -> list:
    """Resultado uniforme para errores controlados de un Registro."""
    item = {
        "folio": registro,
        "registro": registro,
        "archivo": archivo,
        "tipo": tipo,
        "ruta": detalle,
        "tamano_kb": 0,
        "ok": False,
        "controlado": True,
        "fecha": datetime.now().isoformat(),
    }
    item.update(extra)
    return [item]


def _kill_process_tree(proc: subprocess.Popen, registro: str, motivo: str = "timeout") -> None:
    """Mata el proceso hijo y sus Chromium/descendientes sin bloquear el lote."""
    if proc.poll() is not None:
        return
    log.error("[WATCHDOG] Matando proceso del registro %s (pid=%s) por %s", registro, proc.pid, motivo)
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
    except Exception as exc:
        log.warning("[WATCHDOG] No se pudo matar árbol de proceso para %s: %s", registro, exc)
        try:
            proc.kill()
        except Exception:
            pass


def _relay_worker_log(info: dict, registro: str, final: bool = False) -> None:
    """
    Copia al log principal las líneas nuevas del log del proceso hijo.

    La versión con watchdog ejecuta cada Registro en un proceso hijo para poder
    matarlo si se congela. Eso resolvió el bloqueo, pero escondía los mensajes
    descriptivos dentro de logs/workers_registro/*.worker.log. Esta función hace
    tail incremental de ese archivo y reemite las líneas útiles al monitor
    principal, sin perder el aislamiento del proceso.
    """
    if not RELAY_WORKER_LOGS:
        return

    worker_log = info.get("worker_log")
    if not worker_log:
        return
    worker_log = Path(worker_log)
    if not worker_log.exists():
        return

    pos = int(info.get("log_pos") or 0)
    try:
        with worker_log.open("r", encoding="utf-8", errors="replace") as rf:
            rf.seek(pos)
            chunk = rf.read()
            info["log_pos"] = rf.tell()
    except Exception:
        return

    if not chunk:
        return

    for raw_line in chunk.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # No ensuciar el monitor con comandos internos o marcadores técnicos.
        if line.startswith("CMD:") or line.startswith("[WORKER] return_code="):
            continue

        # El worker ya escribe líneas tipo: "11:15:00  INFO      [WEB] ...".
        # Quitamos hora/nivel para evitar doble timestamp en el monitor principal,
        # pero conservamos el nivel original.
        level_name = "INFO"
        msg = line
        m = re.match(r"^\d{2}:\d{2}:\d{2}\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)$", line)
        if m:
            level_name = m.group(1)
            msg = m.group(2).strip()

        if not msg:
            continue
        if len(msg) > MAX_RELAY_LINE_CHARS:
            msg = msg[:MAX_RELAY_LINE_CHARS] + " ...[truncado]"

        level = getattr(logging, level_name, logging.INFO)
        log.log(level, "[DETALLE:%s] %s", registro, msg)


def _leer_resultado_worker_json(path: Path, registro: str, rc: int, worker_log: Path) -> list:
    """Lee el JSON producido por un worker; si no existe, devuelve error controlado."""
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            resultados = payload.get("resultados") or []
            if isinstance(resultados, list) and resultados:
                return resultados
        except Exception as exc:
            return _resultado_controlado_registro(
                registro,
                "ERROR_RESULTADO_WORKER",
                "ERROR_WORKER_JSON",
                f"No se pudo leer JSON del worker: {exc}. Log worker: {worker_log}",
                return_code=rc,
                worker_log=str(worker_log),
            )
    return _resultado_controlado_registro(
        registro,
        "ERROR_WORKER_SIN_RESULTADO",
        "ERROR_WORKER_PROCESO",
        f"El worker terminó sin JSON de resultado. return_code={rc}. Log worker: {worker_log}",
        return_code=rc,
        worker_log=str(worker_log),
    )


def _ejecutar_worker_registro_interno(registro: str, registro_raw: str, resultado_json: Path) -> int:
    """Procesa un Registro aislado y devuelve éxito solo si su carpeta tiene descarga real."""
    started = datetime.now().isoformat()
    try:
        reg_ret, resultados = _worker_registro(registro, registro_raw)
        completo = registro_esta_completo(DESCARGA_BASE, registro)
        payload = {
            "ok": completo,
            "registro": reg_ret,
            "started_at": started,
            "finished_at": datetime.now().isoformat(),
            "resultados": resultados,
            "criterio_completo": "carpeta_con_archivo_real",
        }
        resultado_json.parent.mkdir(parents=True, exist_ok=True)
        resultado_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return 0 if completo else 2
    except Exception as exc:
        log.exception("[WORKER-INTERNO] Error fatal procesando registro %s", registro)
        resultados = _resultado_controlado_registro(
            registro,
            "ERROR_FATAL_WORKER_INTERNO",
            "ERROR",
            str(exc)[:500],
        )
        payload = {
            "ok": False,
            "registro": registro,
            "started_at": started,
            "finished_at": datetime.now().isoformat(),
            "error": str(exc),
            "resultados": resultados,
        }
        try:
            resultado_json.parent.mkdir(parents=True, exist_ok=True)
            resultado_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass
        return 1

# ============================================================
#  WORKER REGISTRO -- Procesamiento concurrente por Registro
# ============================================================
def _worker_registro(registro: str, registro_raw: str) -> tuple:
    """
    Procesa un único número de Registro en un contexto Playwright independiente.
    Equivalente a _worker_folio pero para búsqueda por columna 'Registro'.

    Antes de buscar, configura el tablero:
      - Año → Todos los años
      - Mostrar → 100 trámites

    Retorna: (registro: str, resultados: list)
    """
    tname = threading.current_thread().name
    log.info("[WR:%s] ── Iniciando registro %s ──", tname, registro)
    resultados = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=HEADLESS,
                slow_mo=0 if HEADLESS else 50,
            )
            context_args = {
                "accept_downloads": True,
                "viewport": {"width": 1400, "height": 900},
                "locale": "es-MX",
            }
            if SESION_FILE.exists():
                context_args["storage_state"] = str(SESION_FILE)

            context = browser.new_context(**context_args)
            habilitar_api_discovery(context)
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_NAV)

            # ── Validar sesión ───────────────────────────────────────────────
            sesion_ok = False
            try:
                test_url = urljoin(BASE_URL, "Sarccontroller")
                page.goto(test_url, wait_until="domcontentloaded", timeout=TIMEOUT_NAV)
                url_actual = page.url.lower()
                en_login = (
                    "login" in url_actual
                    or "verifylogin" in url_actual
                    or page.locator("input[type='password']").count() > 0
                )
                if not en_login:
                    sesion_ok = True
                    log.info("[WR:%s] Sesión activa (registro %s)", tname, registro)
                else:
                    log.warning("[WR:%s] Sesión expirada — re-login (registro %s)", tname, registro)
                    if login(page):
                        with _sesion_lock:
                            try:
                                context.storage_state(path=SESION_FILE)
                            except Exception:
                                pass
                        sesion_ok = True
                    else:
                        log.error("[WR:%s] Re-login fallido (registro %s)", tname, registro)
            except Exception as e:
                log.warning("[WR:%s] Error validando sesión: %s", tname, e)

            if not sesion_ok:
                browser.close()
                return registro, [{
                    "folio": registro, "archivo": "ERROR_SESION",
                    "tipo": "ERROR", "ruta": "Sesión inválida o re-login fallido",
                    "tamano_kb": 0, "ok": False,
                }]

            # ── Navegar al tablero ───────────────────────────────────────────
            if not navegar_a_tablero(page):
                log.error("[WR:%s] No se pudo navegar al tablero (registro %s)", tname, registro)
                browser.close()
                return registro, [{
                    "folio": registro, "archivo": "ERROR_TABLERO",
                    "tipo": "ERROR", "ruta": "No se pudo navegar al tablero",
                    "tamano_kb": 0, "ok": False,
                }]

            # ── Configurar tablero para búsqueda por Registro ────────────────
            # Extraer el año del registro para seleccionarlo en el tablero:
            # CRT26-... → '2026', CRT25-... → '2025', etc.
            anio_reg = extraer_anio_de_registro(registro)
            if anio_reg:
                log.info("[WR:%s] Año extraído del registro %s: %s", tname, registro, anio_reg)
            else:
                log.warning("[WR:%s] No se pudo extraer año de registro %s -- se usará 'Todos los años'",
                            tname, registro)

            cfg_ok = configurar_tablero_para_busqueda_registro(page, anio_objetivo=anio_reg)
            if not cfg_ok:
                log.warning(
                    "[WR:%s] Configuración de tablero parcial/fallida para registro %s "
                    "-- se continuará con la configuración por defecto",
                    tname, registro
                )

            # ── Procesar el registro ─────────────────────────────────────────
            carpeta = crear_carpeta(registro)
            try:
                res = procesar_registro_completo(
                    context, page, registro, carpeta, folio_raw=registro_raw
                )
                resultados = res if res else [{
                    "folio": registro, "archivo": "REGISTRO_NO_ENCONTRADO",
                    "tipo": "N/A", "ruta": "", "tamano_kb": 0, "ok": False,
                }]
            except Exception as e:
                log.error("[WR:%s] Error procesando registro %s: %s", tname, registro, e)
                screenshot(page, f"fatal_registro_{registro}")
                resultados = [{
                    "folio": registro, "archivo": "ERROR_FATAL",
                    "tipo": "ERROR", "ruta": str(e)[:120], "tamano_kb": 0, "ok": False,
                }]

            ok_c = sum(1 for r in resultados if r.get("ok"))
            errores_servidor = sum(1 for r in resultados if r.get("tipo") == "ERROR_SERVIDOR")
            errores_otros    = sum(1 for r in resultados if not r.get("ok") and r.get("tipo") not in
                                   ("ERROR_SERVIDOR", "N/A") and r.get("archivo") not in
                                   ("REGISTRO_NO_ENCONTRADO", "ERROR_FATAL", "ERROR_SESION", "ERROR_TABLERO"))
            log.info("[WR:%s] ── Registro %s: %d/%d OK | %d error(es) servidor | %d error(es) red ──",
                     tname, registro, ok_c, len(resultados), errores_servidor, errores_otros)

            browser.close()

    except Exception as e:
        log.error("[WR:%s] Error crítico en worker para registro %s: %s", tname, registro, e)
        resultados = resultados or [{
            "folio": registro, "archivo": "ERROR_WORKER_CRITICO",
            "tipo": "ERROR", "ruta": str(e)[:120], "tamano_kb": 0, "ok": False,
        }]

    return registro, resultados


# ============================================================
#  FUNCION PRINCIPAL
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="SATyS - Descarga Automática de Archivos (Parte 1)")
    parser.add_argument("--folios",   nargs="+",     help="Lista de folios a procesar")
    parser.add_argument("--archivo",  type=str,       help="Archivo de texto con folios (uno por línea)")
    parser.add_argument("--headless", action="store_true", help="Ejecutar en modo sin ventana")
    parser.add_argument("--visible",  action="store_true", help="Forzar modo visible (desactiva headless)")
    parser.add_argument("--limite",   type=int, default=0,  help="Límite máximo de folios a procesar")
    parser.add_argument(
        "--workers", type=int, default=0,
        help="Máximo de workers en paralelo (default: 0 = todos los folios a la vez). "
             "Cada worker abre su propio navegador Chromium. "
             "Usa --workers 3 si quieres limitar a 3 simultáneos.",
    )
    # ── Modo Registro ──────────────────────────────────────────────────
    parser.add_argument("--modo-registro", action="store_true",
                        help="Buscar por número de Registro en vez de Folio OPC")
    parser.add_argument("--registros", nargs="+",
                        help="Lista de números de Registro a procesar (ej. CRT26-002483)")
    parser.add_argument("--timeout-registro", type=int, default=TIMEOUT_REGISTRO,
                        help="Timeout duro por Registro en segundos. Si se excede, se mata el proceso hijo y se continúa.")
    parser.add_argument("--reintentos-registro", type=int, default=REINTENTOS_REGISTRO,
                        help="Reintentos automáticos SOLO para registros que sigan incompletos. 2 = 3 intentos totales.")
    parser.add_argument("--workers-reintento", type=int, default=WORKERS_REINTENTO_REGISTRO,
                        help="Workers usados en reintentos de registros fallidos/incompletos.")
    parser.add_argument("--forzar-registros", action="store_true",
                        help="Procesa los registros indicados aunque ya tengan archivos descargados.")
    parser.add_argument("--solo-metadatos-id", action="store_true",
                        help="Reconsulta metadata_satys.json sin volver a descargar documentos asociados.")
    parser.add_argument("--no-relay-worker-logs", action="store_true",
                        help="No retransmitir al log principal los logs detallados de cada registro. Útil solo si quieres logs más cortos.")
    parser.add_argument("--internos", action="store_true",
                        help="Procesa Administracion solicitudes +TyS/SIGEDO/Internos IFT por bandejas completas.")
    parser.add_argument("--internos-bandejas", nargs="+", default=None,
                        help="Bandejas de Internos IFT a procesar. Default: las seis bandejas del tablero.")
    parser.add_argument("--internos-workers", type=int, default=INTERNOS_WORKERS_DEFAULT,
                        help="Navegadores paralelos para Internos. Default configurable; 0 usa uno por bandeja.")
    parser.add_argument("--internos-objetivos", type=Path, default=None,
                        help="JSON con pares bandeja/folio; limita la descarga a los Folios nuevos indicados.")
    parser.add_argument("--_registro-worker", type=str, default="", help=argparse.SUPPRESS)
    parser.add_argument("--_registro-raw", type=str, default="", help=argparse.SUPPRESS)
    parser.add_argument("--_resultado-json", type=str, default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    global HEADLESS, RELAY_WORKER_LOGS, SOLO_METADATOS_ID
    SOLO_METADATOS_ID = bool(getattr(args, "solo_metadatos_id", False))
    if getattr(args, "no_relay_worker_logs", False):
        RELAY_WORKER_LOGS = False
    if args.headless:
        HEADLESS = True
    if args.visible:
        HEADLESS = False

    num_workers = args.workers  # 0 = sin límite (todos a la vez)

    # Modo interno usado por el watchdog: procesa un único Registro en un
    # proceso hijo matable. No imprime banner para mantener limpios los logs.
    if args._registro_worker:
        resultado_path = Path(args._resultado_json) if args._resultado_json else (Path(tempfile.gettempdir()) / f"satys_result_{uuid.uuid4().hex}.json")
        return _ejecutar_worker_registro_interno(
            args._registro_worker.strip(),
            (args._registro_raw or args._registro_worker).strip(),
            resultado_path,
        )

    if args.internos:
        print("\n+" + "-" * 68 + "+")
        print("|" + "  SATyS - DESCARGA INTERNOS IFT  ".center(68) + "|")
        print("+" + "-" * 68 + "+")
        print(f"|  Modo: {'HEADLESS' if HEADLESS else 'VISIBLE (GUI)'}".ljust(69) + "|")
        print("+" + "-" * 68 + "+\n")
        objetivos_int = []
        if args.internos_objetivos:
            objetivos_int = _cargar_objetivos_internos(args.internos_objetivos)
            if not objetivos_int:
                log.info("[INT-MAIN] El archivo de objetivos Internos esta vacio; no hay descargas pendientes.")
                return 0
        bandejas = _unique_preserve_order(args.internos_bandejas or BANDEJAS_INTERNOS_DEFAULT)
        if args.internos_workers < 0:
            parser.error("--internos-workers debe ser 0 o un entero positivo")
        log.info("[INT-MAIN] Bandejas Internos a procesar: %s", ", ".join(bandejas))
        todos_int = descargar_internos_ift(
            bandejas,
            objetivos=objetivos_int,
            workers=args.internos_workers,
        )
        generar_reporte(todos_int)
        ok_int = sum(1 for r in todos_int if r.get("ok"))
        err_int = len(todos_int) - ok_int
        log.info("[INT-MAIN] Internos finalizado: %d OK | %d error(es).", ok_int, err_int)
        return 0 if err_int == 0 else 2

    print("\n+" + "-" * 68 + "+")
    print("|" + "  SATyS - DESCARGA AUTOMATICA DE ARCHIVOS (PARTE 1)  ".center(68) + "|")
    print("+" + "-" * 68 + "+")
    print(f"|  Modo: {'HEADLESS' if HEADLESS else 'VISIBLE (GUI)'}   Workers: {'TODOS' if num_workers == 0 else num_workers}".ljust(69) + "|")
    print("+" + "-" * 68 + "+\n")

    if not HEADLESS and num_workers != 1:
        log.warning(
            "[WARN] --visible activo: se abrirán múltiples ventanas de Chrome simultáneamente. "
            "Usa --workers 1 para una sola ventana."
        )

    # ──────────────────────────────────────────────────────────────
    # MODO REGISTRO: procesar por número de Registro
    # ──────────────────────────────────────────────────────────────
    modo_registro = getattr(args, 'modo_registro', False)
    registros_arg = getattr(args, 'registros', None) or []

    if modo_registro or registros_arg:
        # ── Validar / renovar sesión (igual que modo folio) ──────
        log.info("[MAIN] ── MODO REGISTRO: FASE 1: Validación de sesión ──")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=HEADLESS, slow_mo=100)
            context_args_m = {
                "accept_downloads": True,
                "viewport": {"width": 1400, "height": 900},
                "locale": "es-MX",
            }
            if SESION_FILE.exists():
                context_args_m["storage_state"] = SESION_FILE
            ctx_m   = browser.new_context(**context_args_m)
            page_m  = ctx_m.new_page()
            page_m.set_default_timeout(TIMEOUT_NAV)
            login_req_m = True
            if SESION_FILE.exists():
                try:
                    test_url = urljoin(BASE_URL, "Sarccontroller")
                    page_m.goto(test_url, wait_until="domcontentloaded", timeout=TIMEOUT_NAV)
                    url_m = page_m.url.lower()
                    if not ("login" in url_m or "verifylogin" in url_m
                            or page_m.locator("input[type='password']").count() > 0):
                        log.info("[OK] Sesion activa recuperada con exito.")
                        login_req_m = False
                    else:
                        log.info("[SESION] Sesion expirada, requiere nuevo login.")
                except Exception as e:
                    log.warning("[SESION] Error al probar sesion: %s", e)
            if login_req_m:
                if not login(page_m):
                    log.critical("[ABORT] No se pudo iniciar sesion.")
                    browser.close()
                    return
                try:
                    ctx_m.storage_state(path=SESION_FILE)
                    log.info("[SESION] Sesion guardada correctamente.")
                except Exception as e:
                    log.warning("[SESION] No se pudo guardar la sesion: %s", e)
            if not navegar_a_tablero(page_m):
                log.critical("[ABORT] No se pudo llegar al tablero.")
                browser.close()
                return
            browser.close()
            log.info("[MAIN] Sesión validada para modo registro.")

        # ── Construir lista de registros ──────────────────────────
        registros = list(dict.fromkeys(r.strip() for r in registros_arg if r.strip()))
        limite_folios_r = args.limite
        if limite_folios_r > 0:
            registros = registros[:limite_folios_r]
        if not registros:
            log.error("[ABORT] No se proporcionaron registros para modo --modo-registro")
            return
        log.info("[LIST] Registros a procesar (%d): %s", len(registros), ", ".join(registros))
        num_workers_r = num_workers  # Usar el mismo número de workers

        # ── Separar registros ya completados (SKIP) ───────────────
        # Única regla: descargas/<REGISTRO>/ contiene al menos un archivo real.
        # Los tres JSON de metadata, archivos vacíos y temporales no cuentan.
        def _registro_ya_completo(reg: str) -> bool:
            return registro_esta_completo(DESCARGA_BASE, reg)

        todos_resultados_r = []
        registros_pendientes = []
        for reg in registros:
            if getattr(args, "forzar_registros", False):
                registros_pendientes.append(reg)
                log.info("[FORCE] Registro %s será reprocesado aunque tenga archivos existentes.", reg)
            elif _registro_ya_completo(reg):
                log.info("[SKIP] Registro %s ya descargado y completo. Saltando...", reg)
            else:
                registros_pendientes.append(reg)

        log.info(
            "[MAIN] %d registro(s) pendientes | %d ya completados (SKIP)",
            len(registros_pendientes), len(registros) - len(registros_pendientes),
        )

        # ── FASE 2: Procesamiento concurrente aislado por proceso ───
        def _guardar_txt_fallidos(regs: list[str], etiqueta: str) -> Path | None:
            regs = list(dict.fromkeys(r for r in regs if r))
            if not regs:
                return None
            REGISTROS_FALLIDOS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = REGISTROS_FALLIDOS_DIR / f"registros_fallidos_{ts}_{etiqueta}.txt"
            contenido = "\n".join(regs) + "\n"
            out.write_text(contenido, encoding="utf-8")
            latest = REGISTROS_FALLIDOS_DIR / "registros_fallidos_latest.txt"
            latest.write_text(contenido, encoding="utf-8")
            log.warning("[FALLIDOS] Lista de registros pendientes/fallidos guardada: %s", out.resolve())
            log.warning("[FALLIDOS] Última lista actualizada: %s", latest.resolve())
            return out

        def _procesar_lote_registros_subprocesos(regs_lote: list[str], workers_lote: int, intento_num: int) -> list:
            """Lanza workers como procesos hijos matables y devuelve resultados controlados."""
            if not regs_lote:
                return []

            workers_lote = len(regs_lote) if workers_lote == 0 else min(max(1, workers_lote), len(regs_lote))
            timeout_reg = max(60, int(args.timeout_registro or TIMEOUT_REGISTRO))
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
            worker_dir = log_dir / "workers_registro" / run_id
            worker_dir.mkdir(parents=True, exist_ok=True)

            log.info(
                "[PROC-REG] Intento %d: %d registro(s), workers=%d, timeout=%ds, logs=%s",
                intento_num, len(regs_lote), workers_lote, timeout_reg, worker_dir,
            )

            pendientes = list(regs_lote)
            running: dict[str, dict] = {}
            resultados_lote: list = []
            completados = 0
            total = len(regs_lote)

            def _lanzar(reg: str):
                safe = _sanitize_for_filename(reg)
                result_json = worker_dir / f"{safe}.resultado.json"
                worker_log = worker_dir / f"{safe}.worker.log"
                cmd = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--_registro-worker", reg,
                    "--_registro-raw", reg,
                    "--_resultado-json", str(result_json),
                ]
                cmd.append("--headless" if HEADLESS else "--visible")
                if SOLO_METADATOS_ID:
                    cmd.append("--solo-metadatos-id")
                # Los procesos hijo no reciben --workers para evitar recursión/concurrencia interna.
                log.info("[PROC-REG] Intento %d iniciando registro %s", intento_num, reg)
                fh = worker_log.open("w", encoding="utf-8", errors="replace")
                fh.write("CMD: " + " ".join(str(x) for x in cmd) + "\n")
                fh.flush()
                popen_kwargs = {
                    "cwd": str(Path(__file__).resolve().parent),
                    "stdout": fh,
                    "stderr": subprocess.STDOUT,
                    "text": True,
                    "encoding": "utf-8",
                    "errors": "replace",
                }
                if os.name == "nt":
                    popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                else:
                    popen_kwargs["preexec_fn"] = os.setsid
                proc = subprocess.Popen(cmd, **popen_kwargs)
                running[reg] = {
                    "proc": proc,
                    "fh": fh,
                    "start": time.time(),
                    "result_json": result_json,
                    "worker_log": worker_log,
                    "log_pos": 0,
                    "intento": intento_num,
                }

            try:
                while pendientes or running:
                    while pendientes and len(running) < workers_lote:
                        _lanzar(pendientes.pop(0))

                    now = time.time()
                    for reg, info in list(running.items()):
                        # Retransmitir en vivo al monitor principal los logs descriptivos
                        # generados por el proceso hijo de este registro.
                        _relay_worker_log(info, reg)
                        proc = info["proc"]
                        rc = proc.poll()
                        elapsed = now - info["start"]

                        if rc is None and elapsed > timeout_reg:
                            _relay_worker_log(info, reg, final=True)
                            _kill_process_tree(proc, reg, motivo=f"TIMEOUT_REGISTRO>{timeout_reg}s")
                            try:
                                proc.wait(timeout=10)
                            except Exception:
                                pass
                            try:
                                info["fh"].write(f"\n[TIMEOUT_REGISTRO] Superó {timeout_reg}s; proceso terminado por watchdog.\n")
                                info["fh"].flush()
                                info["fh"].close()
                            except Exception:
                                pass
                            completados += 1
                            resultados_lote.extend(_resultado_controlado_registro(
                                reg,
                                "TIMEOUT_REGISTRO",
                                "TIMEOUT_REGISTRO",
                                f"Superó {timeout_reg}s en intento {intento_num}. Worker log: {info['worker_log']}",
                                timeout_segundos=timeout_reg,
                                intento=intento_num,
                                worker_log=str(info["worker_log"]),
                            ))
                            log.error(
                                "[CONC-REG] [%d/%d] ✗ Registro %s TIMEOUT_REGISTRO tras %ds (intento %d). Log: %s",
                                completados, total, reg, timeout_reg, intento_num, info["worker_log"],
                            )
                            del running[reg]
                            continue

                        if rc is not None:
                            _relay_worker_log(info, reg, final=True)
                            try:
                                info["fh"].write(f"\n[WORKER] return_code={rc}\n")
                                info["fh"].flush()
                                info["fh"].close()
                            except Exception:
                                pass
                            res = _leer_resultado_worker_json(info["result_json"], reg, rc, info["worker_log"])
                            resultados_lote.extend(res)
                            ok_c = sum(1 for r in res if r.get("ok"))
                            completados += 1
                            completo_fs = registro_esta_completo(DESCARGA_BASE, reg)
                            if completo_fs and ok_c == len(res):
                                log.info(
                                    "[CONC-REG] [%d/%d] ✓ Registro %s completo: %d OK / %d total (intento %d)",
                                    completados, total, reg, ok_c, len(res), intento_num,
                                )
                            elif completo_fs:
                                log.warning(
                                    "[CONC-REG] [%d/%d] ⚠ Registro %s completo por evidencia en carpeta, "
                                    "pero el worker reportó %d OK / %d total (intento %d).",
                                    completados, total, reg, ok_c, len(res), intento_num,
                                )
                            else:
                                log.error(
                                    "[CONC-REG] [%d/%d] ✗ Registro %s incompleto: código %s, %d OK / %d total. Log: %s",
                                    completados, total, reg, rc, ok_c, len(res), info["worker_log"],
                                )
                            del running[reg]

                    if pendientes or running:
                        time.sleep(1)
            except KeyboardInterrupt:
                log.error("[PROC-REG] Interrupción manual. Matando workers activos...")
                for reg, info in list(running.items()):
                    _kill_process_tree(info["proc"], reg, motivo="KeyboardInterrupt")
                    try:
                        info["fh"].close()
                    except Exception:
                        pass
                raise
            finally:
                for reg, info in list(running.items()):
                    try:
                        info["fh"].close()
                    except Exception:
                        pass

            return resultados_lote

        fallidos_finales: list[str] = []
        if registros_pendientes:
            todos_resultados_r = []
            registros_por_intentar = list(registros_pendientes)
            max_reintentos = max(0, int(args.reintentos_registro or 0))
            total_intentos = 1 + max_reintentos

            for intento_num in range(1, total_intentos + 1):
                if not registros_por_intentar:
                    break

                if intento_num == 1:
                    workers_lote = (len(registros_por_intentar) if num_workers_r == 0
                                    else min(num_workers_r, len(registros_por_intentar)))
                else:
                    workers_lote = min(
                        max(1, int(args.workers_reintento or WORKERS_REINTENTO_REGISTRO)),
                        len(registros_por_intentar),
                    )

                log.info(
                    "[MAIN] ── FASE 2 REGISTRO: intento %d/%d con %d registro(s) ──",
                    intento_num, total_intentos, len(registros_por_intentar),
                )
                resultados_intento = _procesar_lote_registros_subprocesos(
                    registros_por_intentar,
                    workers_lote,
                    intento_num,
                )
                todos_resultados_r.extend(resultados_intento)

                # Recalcular fallidos con evidencia real en descargas/, no solo por return code.
                fallidos_post = [reg for reg in registros_por_intentar if not _registro_ya_completo(reg)]
                recuperados = len(registros_por_intentar) - len(fallidos_post)
                log.info(
                    "[REINTENTOS] Intento %d/%d terminado: recuperados=%d | siguen pendientes=%d",
                    intento_num, total_intentos, recuperados, len(fallidos_post),
                )

                if fallidos_post and intento_num < total_intentos:
                    _guardar_txt_fallidos(fallidos_post, f"tras_intento_{intento_num}")
                    registros_por_intentar = fallidos_post
                    log.warning(
                        "[REINTENTOS] Reintentando SOLO %d registro(s) incompletos con workers=%s",
                        len(registros_por_intentar), args.workers_reintento,
                    )
                else:
                    registros_por_intentar = fallidos_post
                    break

            fallidos_finales = [reg for reg in registros_pendientes if not _registro_ya_completo(reg)]
            if fallidos_finales:
                out_fallidos = _guardar_txt_fallidos(fallidos_finales, "final")
                log.error(
                    "[FALLIDOS] %d registro(s) no pudieron completarse tras %d intento(s). Archivo: %s",
                    len(fallidos_finales), total_intentos, out_fallidos,
                )
            else:
                try:
                    REGISTROS_FALLIDOS_DIR.mkdir(parents=True, exist_ok=True)
                    (REGISTROS_FALLIDOS_DIR / "registros_fallidos_latest.txt").write_text("", encoding="utf-8")
                except Exception:
                    pass
                log.info("[FALLIDOS] Todos los registros pendientes quedaron completos tras los intentos controlados.")

        # ── Reporte final modo registro ───────────────────────────
        if todos_resultados_r:
            generar_reporte(todos_resultados_r)
            guardar_log_json(todos_resultados_r)
            guardar_resumen_global(todos_resultados_r, DESCARGA_BASE)
        else:
            log.warning("[WARN] No se procesó ningún registro")
        return 1 if fallidos_finales else 0  # Terminar aquí, no procesar folios

    # ──────────────────────────────────────────────────────────────
    # Construcción de lista de folios (modo normal)
    # ──────────────────────────────────────────────────────────────
    folios_raw = []
    if args.folios:
        folios_raw.extend(args.folios)
    if args.archivo:
        try:
            with open(args.archivo, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip() and not line.startswith("#"):
                        folios_raw.append(line.strip())
        except Exception as e:
            log.error("[ERROR] No se pudo leer el archivo de folios: %s", e)
            return

    if not folios_raw:
        folios_raw = FOLIOS_DEFAULT

    folio_raw_map = {normalizar_folio(f): f for f in folios_raw}
    folios = list(dict.fromkeys(normalizar_folio(f) for f in folios_raw))

    limite_folios = args.limite
    if limite_folios > 0:
        folios = folios[:limite_folios]
        log.info("[LIST] Límite aplicado: primeros %d folios", limite_folios)

    log.info("[LIST] Folios a procesar (%d): %s", len(folios), ", ".join(folios))

    # ──────────────────────────────────────────────────────────────
    # FASE 1: Validación / renovación de sesión en hilo principal
    # (un único navegador temporal que se cierra antes de lanzar workers)
    # ──────────────────────────────────────────────────────────────
    log.info("[MAIN] ── FASE 1: Validación de sesión ──")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS, slow_mo=100)
        context_args = {
            "accept_downloads": True,
            "viewport": {"width": 1400, "height": 900},
            "locale": "es-MX",
        }
        if SESION_FILE.exists():
            context_args["storage_state"] = SESION_FILE

        context = browser.new_context(**context_args)
        page    = context.new_page()
        page.set_default_timeout(TIMEOUT_NAV)

        login_requerido = True
        if SESION_FILE.exists():
            log.info("[SESION] Probando sesion guardada...")
            try:
                test_url = urljoin(BASE_URL, "Sarccontroller")
                page.goto(test_url, wait_until="domcontentloaded", timeout=TIMEOUT_NAV)
                url_actual = page.url.lower()
                en_login = (
                    "login" in url_actual
                    or "verifylogin" in url_actual
                    or page.locator("input[type='password']").count() > 0
                )
                if not en_login:
                    log.info("[OK] Sesion activa recuperada con exito.")
                    login_requerido = False
                else:
                    log.info("[SESION] Sesion expirada, requiere nuevo login.")
            except Exception as e:
                log.warning("[SESION] Error al probar sesion: %s", e)

        if login_requerido:
            if not login(page):
                log.critical("[ABORT] No se pudo iniciar sesion.")
                browser.close()
                return
            try:
                context.storage_state(path=SESION_FILE)
                log.info("[SESION] Sesion guardada correctamente.")
            except Exception as e:
                log.warning("[SESION] No se pudo guardar la sesion: %s", e)

        # Navegar brevemente al tablero para confirmar sesión operativa
        if not navegar_a_tablero(page):
            log.critical("[ABORT] No se pudo llegar al tablero tras el login.")
            browser.close()
            return

        browser.close()
        log.info("[MAIN] Sesión validada. Navegador principal cerrado.")

    # ──────────────────────────────────────────────────────────────
    # Separar folios ya completados (SKIP) de los pendientes
    # ──────────────────────────────────────────────────────────────
    todos_resultados = []
    folios_pendientes = []

    for folio in folios:
        carpeta      = crear_carpeta(folio)
        metadata_file = carpeta / "metadata_completo.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    meta_existente = json.load(f)
                if (meta_existente.get("estado") == "OK"
                        and meta_existente.get("total_archivos_ok", 0) > 0):
                    log.info("[SKIP] Folio %s ya descargado. Saltando...", folio)
                    todos_resultados.extend(meta_existente.get("archivos", []))
                    continue
            except Exception as e:
                log.warning("[SKIP] No se pudo leer metadata de %s: %s", folio, e)
        folios_pendientes.append(folio)

    log.info(
        "[MAIN] %d folio(s) pendientes | %d ya completados (SKIP)",
        len(folios_pendientes), len(folios) - len(folios_pendientes),
    )

    # ──────────────────────────────────────────────────────────────
    # FASE 2: Procesamiento concurrente con ThreadPoolExecutor
    # ──────────────────────────────────────────────────────────────
    if folios_pendientes:
        workers_activos = len(folios_pendientes) if num_workers == 0 else min(num_workers, len(folios_pendientes))
        log.info(
            "[MAIN] ── FASE 2: Lanzando %d worker(s) para %d folio(s) ──",
            workers_activos, len(folios_pendientes),
        )
        resultados_lock = threading.Lock()

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers_activos,
            thread_name_prefix="SATyS",
        ) as executor:
            futures_map = {
                executor.submit(
                    _worker_folio,
                    folio,
                    folio_raw_map.get(folio, folio),
                ): folio
                for folio in folios_pendientes
            }

            completados = 0
            for future in concurrent.futures.as_completed(futures_map):
                folio_key = futures_map[future]
                completados += 1
                try:
                    folio_ret, res = future.result()
                    ok_c = sum(1 for r in res if r.get("ok"))
                    with resultados_lock:
                        todos_resultados.extend(res)
                    log.info(
                        "[CONC] [%d/%d] ✓ Folio %s completado: %d OK / %d total",
                        completados, len(folios_pendientes),
                        folio_ret, ok_c, len(res),
                    )
                except Exception as e:
                    log.error(
                        "[CONC] [%d/%d] ✗ Excepción en worker folio %s: %s",
                        completados, len(folios_pendientes), folio_key, e,
                    )
                    with resultados_lock:
                        todos_resultados.append({
                            "folio": folio_key, "archivo": "EXCEPCION_WORKER",
                            "tipo": "ERROR", "ruta": str(e)[:120],
                            "tamano_kb": 0, "ok": False,
                        })

    # ──────────────────────────────────────────────────────────────
    # Reporte final (igual que antes)
    # ──────────────────────────────────────────────────────────────
    if todos_resultados:
        generar_reporte(todos_resultados)
        guardar_log_json(todos_resultados)
        guardar_resumen_global(todos_resultados, DESCARGA_BASE)
    else:
        log.warning("[WARN] No se proceso ningun archivo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
