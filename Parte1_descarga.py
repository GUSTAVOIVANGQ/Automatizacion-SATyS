"""
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
import logging
import argparse
import threading
import concurrent.futures
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_result, before_sleep_log
from logging.handlers import RotatingFileHandler

from urllib.parse import urljoin, urlparse
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

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
USUARIO       = os.getenv("SATYS_USER", "david.palestina@ift.org.mx")
PASSWORD      = os.getenv("SATYS_PASS", "Crt20261234*")
BASE_URL      = os.getenv("SATYS_BASE_URL", "https://satys.ift.org.mx/")
DESCARGA_BASE = Path(os.getenv("SATYS_DIR", "descargas"))
SESION_FILE   = Path("sesion_guardada.json")

# False = ver el navegador (recomendado para depurar)
# True  = sin ventana (modo produccion)
HEADLESS = os.getenv("SATYS_HEADLESS", "False").lower() in ("true", "1", "yes")

TIMEOUT_NAV   = int(os.getenv("SATYS_TIMEOUT_NAV", "60000"))   # ms -- carga de paginas (red intranet IFT)
TIMEOUT_CORTO = int(os.getenv("SATYS_TIMEOUT_CORTO", "10000"))   # ms -- esperas de elementos
TIMEOUT_DL    = int(os.getenv("SATYS_TIMEOUT_DL", "90000"))   # ms -- espera de descarga
TIMEOUT_DETALLE = int(os.getenv("SATYS_TIMEOUT_DETALLE", "120000"))  # ms -- espera de carga en Ver detalle

# API discovery y descarga directa (experimental)
API_DISCOVERY = os.getenv("SATYS_API_DISCOVERY", "True").lower() in ("true", "1", "yes")
DIRECT_DOWNLOAD = os.getenv("SATYS_DIRECT_DOWNLOAD", "True").lower() in ("true", "1", "yes")
API_LOG_PATH = Path("debug") / "api_log.jsonl"

# Folios a procesar (se normalizan automaticamente)
FOLIOS_DEFAULT = ["6407", "6801", "6802"]
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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_result(_retry_if_false))
def _descargar_directo(context, page, url: str, dest: Path) -> bool:
    if not DIRECT_DOWNLOAD or not url:
        return False
    req_ctx = getattr(context, "request", None)
    if req_ctx is None:
        return False
    try:
        resp = req_ctx.get(url, timeout=TIMEOUT_DL)
        if not resp.ok:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.body())
        return True
    except Exception:
        return False

def _retry_if_both_none(res):
    return res[0] is None and res[1] is None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_result(_retry_if_both_none))
def _click_y_esperar_descarga(page, context, boton_ver_doc):
    dl_obj = None
    np_obj = None
    def on_dl(d): nonlocal dl_obj; dl_obj = d
    def on_pg(p): nonlocal np_obj; np_obj = p
    page.on("download", on_dl)
    context.on("page", on_pg)
    try:
        boton_ver_doc.click()
    except Exception:
        boton_ver_doc.click(force=True)
    import time
    start_t = time.time()
    while time.time() - start_t < (TIMEOUT_DL / 1000.0):
        if dl_obj or np_obj:
            break
        page.wait_for_timeout(200)
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
        page.wait_for_timeout(2_000)
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
            page.wait_for_timeout(2_000)
            return True
        except Exception:
            page.wait_for_timeout(1_000)
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
    inicio = time.time()
    limite = timeout_ms / 1000

    while (time.time() - inicio) < limite:
        hay_spinner = False
        for sel in selectores_spinner:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    hay_spinner = True
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
        log.info("[NET] Cargando pagina login...")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT_NAV)

        log.info("[LOGIN] Llenando credenciales...")
        page.fill("input[name='usuario'], input[name='username']", USUARIO)
        page.fill("input[type='password']", PASSWORD)
        
        # Click login
        with page.expect_navigation(timeout=TIMEOUT_NAV):
            page.click("button[type='submit'], input[type='submit'], a:has-text('Ingresar'), button:has-text('Entrar')")

        # Verificar dashboard
        try:
            page.wait_for_selector("text=Tablero de Control", timeout=10_000)
            log.info("[OK] Sesion iniciada correctamente")
            return True
        except PWTimeout:
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
        page.wait_for_timeout(1_000)

        # 2.1 Click en "Enlace/SIGEDO" para expandir el acordeon del menu
        if not _click_menu_text(sidebar, re.compile(r"Enlace\s*/\s*SIGEDO", re.I), TIMEOUT_NAV):
            _click_menu_text(page, re.compile(r"Enlace\s*/\s*SIGEDO", re.I), TIMEOUT_NAV)
        page.wait_for_timeout(1_500)

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
        page.wait_for_timeout(2_000)

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
                    for sel in [
                        "a[title*='Ver']",
                        "button[title*='Ver']",
                        "a:has(i.fa-eye)",
                        "button:has(i.fa-eye)",
                        "a:has(i.icon-eye)",
                        "button:has(i.icon-eye)",
                        "a:has(i.glyphicon-eye-open)",
                        "button:has(i.glyphicon-eye-open)",
                        "a[data-action='ver']",
                        "a.js-gestor-sigedo-open-tramite",
                        "a.btn-info",
                        "button.btn-info",
                        "a.btn-primary",
                        "button.btn-primary",
                        "a, button",
                    ]:
                        try:
                            c = fila.locator(sel).first
                            c.wait_for(state="visible", timeout=1_000)
                            boton_ver = c
                            break
                        except Exception:
                            continue
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
                page.wait_for_timeout(1_500)
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
def _retry_if_faltan_metadatos(res):
    # Reintenta si faltan los metadatos importantes
    return not res.get("representante_legal") or not res.get("asunto")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_result(_retry_if_faltan_metadatos))
def extraer_metadatos_satys(page, folio: str, carpeta: Path) -> dict:
    log.info("[WEB] Extrayendo metadatos web de SATyS para folio %s", folio)
    metadatos = {
        "representante_legal": None,
        "nombre_operador": None,
        "asunto": None
    }
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(1500)
        
        # 1. Expandir DATOS DEL DOCUMENTO
        try:
            datos_doc = page.locator("a[data-toggle='collapse']:has-text('DATOS DEL DOCUMENTO'), h4:has-text('DATOS DEL DOCUMENTO')").first
            datos_doc.scroll_into_view_if_needed()
            datos_doc.click()
            page.wait_for_timeout(1000)
        except Exception:
            pass
            
        # 2. Expandir REMITENTE(S)
        try:
            remitentes = page.locator("a[data-toggle='collapse']:has-text('REMITENTE(S)'), legend:has-text('REMITENTE(S)'), h5:has-text('REMITENTE(S)')").first
            remitentes.scroll_into_view_if_needed()
            remitentes.click()
            page.wait_for_timeout(1500)
            
            # 2.1 Extraer Representante legal (de Tabla de representantes legales)
            rep = page.evaluate('''() => {
                let ths = Array.from(document.querySelectorAll('th'));
                let th = ths.find(el => el.textContent && el.textContent.includes('Representante legal'));
                if(th) {
                    let table = th.closest('table');
                    if(table && table.querySelector('tbody tr td')) {
                        let cols = Array.from(table.querySelectorAll('thead th'));
                        let idx = cols.indexOf(th);
                        let firstRow = table.querySelector('tbody tr');
                        if(firstRow) {
                            let tds = firstRow.querySelectorAll('td');
                            if(tds.length > idx) {
                                return tds[idx].textContent.trim();
                            }
                        }
                    }
                }
                return "";
            }''')
            if rep:
                metadatos["representante_legal"] = rep
                
            # 2.2 Extraer Nombre o razon social del Operador (Solicitante)
            op = page.evaluate('''() => {
                let ths = Array.from(document.querySelectorAll('th'));
                let th = ths.find(el => el.textContent && el.textContent.includes('Solicitante'));
                if(th) {
                    let table = th.closest('table');
                    if(table && table.querySelector('tbody tr td')) {
                        let cols = Array.from(table.querySelectorAll('thead th'));
                        let idx = cols.indexOf(th);
                        let firstRow = table.querySelector('tbody tr');
                        if(firstRow) {
                            let tds = firstRow.querySelectorAll('td');
                            if(tds.length > idx) {
                                return tds[idx].textContent.trim();
                            }
                        }
                    }
                }
                return "";
            }''')
            if op:
                metadatos["nombre_operador"] = op
        except Exception as e:
            log.warning("[WEB] No se pudo extraer REMITENTE(S): %s", e)
            
        # 3. Expandir DESCRIPCIÓN DEL DOCUMENTO
        try:
            page.evaluate("window.scrollBy(0, 300)")
            desc_doc = page.locator("a[data-toggle='collapse']:has-text('DESCRIPCIÓN DEL DOCUMENTO'), h4:has-text('DESCRIPCIÓN DEL DOCUMENTO')").first
            desc_doc.scroll_into_view_if_needed()
            desc_doc.click()
            page.wait_for_timeout(1500)
            
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

        # V-11: Convertir strings vacios a None y loguear campos faltantes
        for campo in ("representante_legal", "nombre_operador", "asunto"):
            if not metadatos.get(campo):
                metadatos[campo] = None
                log.warning("[V11-META-FALTANTE] folio=%s campo=%s es nulo/vacio",
                            folio, campo)

        # Guardar en archivo
        out_path = carpeta / "metadata_satys.json"
        carpeta.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(metadatos, f, ensure_ascii=False, indent=2)

        log.info("[WEB] Metadatos guardados OK: Rep='%s', Ope='%s', Asunto='%s'",
                 metadatos["representante_legal"], metadatos["nombre_operador"], metadatos["asunto"])
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
        page.wait_for_timeout(1_500)

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
        page.wait_for_timeout(500)

        # Intentar expandir si esta colapsado
        try:
            seccion.click()
            page.wait_for_timeout(2_000)
        except Exception:
            pass

        tabla = _encontrar_tabla_archivos(page)
        if tabla is None:
            try:
                seccion.click()
                page.wait_for_timeout(2_000)
            except Exception:
                pass
            tabla = _encontrar_tabla_archivos(page)

        if tabla is None:
            log.error("[ERROR] No se encontro la tabla de archivos asociados")
            screenshot(page, f"no_tabla_{folio}")
            return resultados, True  # Seccion existe pero tabla vacia

        # 4.3 Intentar mostrar TODOS los archivos de una vez (cambiar paginacion)
        try:
            select_mostrar = page.locator(
                "select[name*='_length'], .dataTables_length select"
            ).last
            if select_mostrar.count() > 0:
                opciones = select_mostrar.locator("option")
                for oi in range(opciones.count()):
                    val = opciones.nth(oi).get_attribute("value") or ""
                    if val in ("-1", "100", "50"):
                        select_mostrar.select_option(value=val)
                        page.wait_for_timeout(2_000)
                        tabla = _encontrar_tabla_archivos(page)
                        break
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
                    # V-07: Verificar tamano y reintentar si el archivo es 0 KB
                    descargado_ok = False
                    if url:
                        for _reintentoD in range(3):  # hasta 3 intentos
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
                        log.info("     [OK] Descarga directa: %s  (%.1f KB)", fname, size_kb)
                        resultados.append({
                            "folio": folio, "archivo": fname,
                            "tipo": Path(fname).suffix.upper().lstrip("."),
                            "ruta": str(dest), "tamano_kb": round(size_kb, 1),
                            "ok": True, "url": url,
                        })
                        continue

                    dl_obj = None
                    np_obj = None
                    
                    def on_dl(d): nonlocal dl_obj; dl_obj = d
                    def on_pg(p): nonlocal np_obj; np_obj = p
                    
                    page.on("download", on_dl)
                    context.on("page", on_pg)
                    
                    btn.click()
                    
                    import time
                    start_t = time.time()
                    timeout_sec = TIMEOUT_DL / 1000.0
                    while time.time() - start_t < timeout_sec:
                        if dl_obj or np_obj:
                            break
                        page.wait_for_timeout(200)
                        
                    page.remove_listener("download", on_dl)
                    context.remove_listener("page", on_pg)

                    if dl_obj:
                        fname = dl_obj.suggested_filename or fname
                        dest = carpeta / fname
                        dl_obj.save_as(str(dest))

                        size_kb = dest.stat().st_size / 1024
                        log.info("     [OK] Guardado: %s  (%.1f KB)", fname, size_kb)

                        resultados.append({
                            "folio": folio, "archivo": fname,
                            "tipo": Path(fname).suffix.upper().lstrip("."),
                            "ruta": str(dest), "tamano_kb": round(size_kb, 1), "ok": True,
                            "url": dl_obj.url if hasattr(dl_obj, "url") and dl_obj.url else url,
                        })
                        
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
                                size_kb = dest_popup.stat().st_size / 1024
                                log.info("     [OK-POPUP] Guardado: %s (%.1f KB)", dest_popup.name, size_kb)
                                resultados.append({
                                    "folio": folio, "archivo": dest_popup.name,
                                    "tipo": dest_popup.suffix.upper().lstrip("."),
                                    "ruta": str(dest_popup), "tamano_kb": round(size_kb, 1), "ok": True,
                                    "url": url_popup,
                                })
                            else:
                                raise Exception("Fallo descarga directa desde popup")
                        else:
                            raise Exception("Popup no tiene URL valida")
                            
                    else:
                        raise PWTimeout("Timeout descargando o esperando popup")

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
            page.wait_for_timeout(1_500)
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
        page.wait_for_timeout(2_000)

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
    for sel in [
        "a[title*='Ver']",
        "button[title*='Ver']",
        "a:has(i.fa-eye)",
        "button:has(i.fa-eye)",
        "a:has(i.icon-eye)",
        "button:has(i.icon-eye)",
        "a:has(i.glyphicon-eye-open)",
        "button:has(i.glyphicon-eye-open)",
        "a[data-action='ver']",
        "a.js-gestor-sigedo-open-tramite",
        "a.btn-info",
        "button.btn-info",
        "a.btn-primary",
        "button.btn-primary",
        "a, button",
    ]:
        try:
            c = fila.locator(sel).first
            c.wait_for(state="visible", timeout=1_000)
            return c
        except Exception:
            continue
    return None


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
    max_iteraciones = 50  # Limite de seguridad

    for iteracion in range(max_iteraciones):
        # V-03: Verificar sesion al inicio de cada iteracion
        if not _verificar_sesion(page):
            log.error("[V03] Sesion no recuperable -- abortando folio %s", folio)
            break

        # Buscar folio en la tabla
        if not _buscar_folio_en_tabla(page, folio):
            if iteracion == 0:
                # V-04: Registrar explicitamente como FOLIO_NO_ENCONTRADO
                log.warning("[V04-NO_ENCONTRADO] Folio %s no encontrado en Documentos en Proceso", folio)
                screenshot(page, f"folio_no_encontrado_{folio}")
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
                meta_satys = extraer_metadatos_satys(page, folio, carpeta)
                meta_tramite = {}

                # --- BIFURCACION: intentar ARCHIVOS ASOCIADOS primero ---
                # descargar_archivos retorna (resultados, seccion_encontrada)
                # Si seccion_encontrada=False -> no existe la seccion -> usar DOCUMENTOS ANEXOS
                fuente = "ARCHIVOS_ASOCIADOS"
                res, seccion_aa_ok = descargar_archivos(context, page, folio, carpeta)
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
                        context, page, folio, folio_raw or folio, carpeta
                    )
                else:
                    log.info("[FUENTE] ARCHIVOS ASOCIADOS encontrado para folio %s", folio)

                # Guardar metadata_completo.json con todo consolidado
                guardar_metadata_completo(
                    folio, folio_raw or folio, carpeta,
                    meta_satys, meta_tramite, res, fuente
                )

                if res:
                    todos_resultados.extend(res)
                else:
                    todos_resultados.append({
                        "folio": folio, "archivo": f"SIN_ARCHIVOS_{registro}",
                        "tipo": "N/A", "ruta": "", "tamano_kb": 0, "ok": False,
                        "fuente": fuente,
                    })

                # Regresar al tablero para buscar el siguiente tramite
                volver_al_tablero(page)
                time.sleep(1)
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
                page.wait_for_timeout(1_500)
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

        # Esperar spinner y confirmar llegada
        page.wait_for_timeout(1_500)
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


def buscar_folio_en_tramites_nuevos(page, folio: str) -> bool:
    """
    Escribe el folio en el campo 'Buscar:' de Tramites Nuevos
    y verifica que aparece al menos un resultado.
    """
    log.info("[ALT-SEARCH] Buscando folio %s en Tramites Nuevos...", folio)
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
        search.fill(folio)
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
    """
    log.info("[ALT-REVISAR] Abriendo detalle del tramite %s...", folio)
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

            # Verificar que esta fila contiene el folio
            if not re.search(rf"0*{re.escape(folio)}\b", texto_fila):
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
            log.error("[ALT-REVISAR] No se encontro boton Revisar para folio %s", folio)
            screenshot(page, f"revisar_no_encontrado_{folio}")
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
        "fecha_registro": "",
        "solicitante": "",
        "nombre_operador": "",
        "representante_legal": "",
        "asunto": "",
        "descripcion": "",
    }
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(1_000)

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
                "Solicitante": "solicitante",
                "Promovente": "nombre_operador",
                "Representante": "representante_legal",
                "Concesionario": "representante_legal",
                "Asunto": "asunto",
                "Info. adicional": "asunto",
                "Descripci": "descripcion",
                "Fecha de recepción": "fecha_registro"
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
                            "tipo": tipo_doc, "fecha": fecha_doc,
                            "descripcion": descripcion_doc,
                            "ruta": "", "tamano_kb": 0, "ok": False,
                            "error": str(ex)[:120], "url": url_doc,
                            "fuente": "DOCUMENTOS_ANEXOS",
                        })
                        continue

                if descargado:
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

        if total_esperado > 0:
            if ok_count == total_esperado:
                log.info(
                    "[ALT-DL] VALIDACION OK: %d/%d documentos descargados",
                    ok_count, total_esperado
                )
            else:
                log.warning(
                    "[ALT-DL] VALIDACION: esperados=%d  ok=%d  errores=%d",
                    total_esperado, ok_count, err_count
                )
                screenshot(page, f"validacion_fallo_{folio}")
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

    # Intentar obtener el total esperado desde los resultados mismos
    # (puede haber sido registrado como error si hay discrepancia)
    total_esperado_externo = total  # Fallback conservador

    data = {
        "folio": folio,
        "folio_raw": folio_raw,
        "fecha_proceso": datetime.now().isoformat(),
        "fuente_descarga": fuente,
        "estado": "OK" if ok_count > 0 and ok_count == total else (
            "PARCIAL" if ok_count > 0 else "SIN_ARCHIVOS"
        ),
        "metadatos_satys": meta_satys if meta_satys else {},
        "metadatos_tramite": meta_tramite if meta_tramite else {},
        "total_archivos_encontrados": total,
        "total_archivos_ok": ok_count,
        "total_archivos_error": total - ok_count,
        "coincide": ok_count == total and total > 0,
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


def guardar_resumen_global(todos: list, carpeta_base: Path) -> dict:
    """
    Genera descargas/resumen_global.json con el resumen de todos los folios procesados.
    """
    por_folio: dict = {}
    for r in todos:
        f = r.get("folio", "DESCONOCIDO")
        if f not in por_folio:
            por_folio[f] = {
                "ok": 0, "err": 0,
                "fuente": r.get("fuente", "ARCHIVOS_ASOCIADOS"),
                "archivos": [],
            }
        por_folio[f]["ok" if r.get("ok") else "err"] += 1
        por_folio[f]["archivos"].append(r)
        # Si hay al menos un resultado con fuente DOCUMENTOS_ANEXOS, usar esa
        if r.get("fuente") == "DOCUMENTOS_ANEXOS":
            por_folio[f]["fuente"] = "DOCUMENTOS_ANEXOS"

    resumen = {
        "fecha_generacion": datetime.now().isoformat(),
        "total_folios": len(por_folio),
        "total_archivos": len(todos),
        "total_ok": sum(1 for r in todos if r.get("ok")),
        "total_errores": sum(1 for r in todos if not r.get("ok")),
        "folios": [
            {
                "folio": folio,
                "fuente_descarga": d["fuente"],
                "archivos_ok": d["ok"],
                "archivos_error": d["err"],
                "estado": (
                    "OK" if d["err"] == 0 and d["ok"] > 0
                    else "PARCIAL" if d["ok"] > 0
                    else "ERROR"
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
        "[RESUMEN] resumen_global.json: %d folios | %d OK | %d errores",
        resumen["total_folios"],
        resumen["total_ok"],
        resumen["total_errores"],
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
        por_folio.setdefault(f, {"ok": 0, "err": 0, "archivos": []})
        por_folio[f]["ok" if r["ok"] else "err"] += 1
        por_folio[f]["archivos"].append(r)

    for folio, d in por_folio.items():
        ico = "[OK]" if d["err"] == 0 else ("[WARN]" if d["ok"] > 0 else "[ERR]")
        print(f"\n  {ico}  Folio: {folio}  ->  {DESCARGA_BASE / folio}")
        print(f"      Descargados: {d['ok']}   Errores: {d['err']}")
        print(f"      {'-'*50}")
        for a in d["archivos"]:
            est = "OK" if a["ok"] else "XX"
            print(f"      [{est}] {a['archivo']:<45s}  {a['tipo']:<6s}  {a['tamano_kb']} KB")

    total_ok  = sum(1 for r in todos if r["ok"])
    total_err = len(todos) - total_ok
    print(f"\n  {'-'*50}")
    print(f"  TOTAL: {len(todos)} archivos  OK: {total_ok}  Errores: {total_err}")
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
                slow_mo=50,          # Reducido vs 100 del hilo principal: N browsers en paralelo
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

            # ── Procesar el folio ─────────────────────────────────────
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

            browser.close()
            ok_c = sum(1 for r in resultados if r.get("ok"))
            log.info("[W:%s] ── Folio %s OK (%d/%d archivos) ──",
                     tname, folio, ok_c, len(resultados))

    except Exception as e:
        log.error("[W:%s] Error crítico en worker para folio %s: %s", tname, folio, e)
        resultados = resultados or [{
            "folio": folio, "archivo": "ERROR_WORKER_CRITICO",
            "tipo": "ERROR", "ruta": str(e)[:120], "tamano_kb": 0, "ok": False,
        }]

    return folio, resultados


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
    args = parser.parse_args()

    global HEADLESS
    if args.headless:
        HEADLESS = True
    if args.visible:
        HEADLESS = False

    num_workers = args.workers  # 0 = sin límite (todos a la vez)

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
    # Construcción de lista de folios
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



if __name__ == "__main__":
    main()