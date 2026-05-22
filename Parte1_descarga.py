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
from urllib.parse import urljoin, urlparse
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

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
BASE_URL      = "https://satys.ift.org.mx/"
DESCARGA_BASE = Path("descargas")

# False = ver el navegador (recomendado para depurar)
# True  = sin ventana (modo produccion)
HEADLESS = False

TIMEOUT_NAV   = 60_000   # ms -- carga de paginas (red intranet IFT)
TIMEOUT_CORTO = 10_000   # ms -- esperas de elementos
TIMEOUT_DL    = 90_000   # ms -- espera de descarga
TIMEOUT_DETALLE = 120_000  # ms -- espera de carga en Ver detalle

# API discovery y descarga directa (experimental)
API_DISCOVERY = True
DIRECT_DOWNLOAD = True
API_LOG_PATH = Path("debug") / "api_log.jsonl"

# Folios a procesar (se normalizan automaticamente)
FOLIOS_DEFAULT = ["6407", "6801", "6802"]
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SATyS-P1")

# URL del tablero (se asigna durante la navegacion)
URL_TABLERO = ""


# ------------------------------------------------------------
#  AUXILIARES
# ------------------------------------------------------------
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
#  PASO 1 -- LOGIN
# ------------------------------------------------------------
def login(page) -> bool:
    log.info("[LOGIN] Iniciando sesion...")
    try:
        # Reintentar hasta 3 veces si hay timeout de red (intranet IFT)
        for intento in range(1, 4):
            try:
                log.info("[NET] Cargando pagina (intento %d/3)...", intento)
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT_NAV)
                break  # exito
            except PWTimeout:
                if intento == 3:
                    raise
                log.warning("[WARN] Timeout en intento %d, reintentando...", intento)
                page.wait_for_timeout(3_000)

        # Esperar campo de usuario
        user_sel = "input[name='username'], input[id='username'], input[type='email'], input[type='text']"
        page.wait_for_selector(user_sel, timeout=TIMEOUT_NAV)
        page.locator(user_sel).first.fill(USUARIO)

        # Contrasena -- la pagina tiene typo 'passowrd' en el HTML original
        pass_sel = (
            "input[name='passowrd'], input[id='passowrd'], "
            "input[name='password'], input[id='password'], "
            "input[type='password']"
        )
        page.locator(pass_sel).first.fill(PASSWORD)

        # Submit
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_NAV)
        page.wait_for_timeout(3_000)

        url = page.url
        log.info("[URL] Post-login: %s", url)

        if "login" in url.lower() or "verifylogin" in url.lower():
            log.error("[ERROR] Login fallido -- credenciales incorrectas")
            screenshot(page, "login_fallido")
            return False

        log.info("[OK] Login exitoso")
        return True

    except Exception as e:
        log.error("[ERROR] En login: %s", e)
        screenshot(page, "login_error")
        return False


# ------------------------------------------------------------
#  PASO 2 -- NAVEGAR A ENLACE OFICIALIA DE PARTES
# ------------------------------------------------------------
def navegar_a_tablero(page) -> bool:
    """
    Hace clic en 'Enlace/SIGEDO' y luego en 'Enlace Oficialia de Partes'.
    IMPORTANTE: La pagina es una SPA -- la URL nunca cambia (siempre Sarccontroller).
    Debemos esperar que aparezca el texto 'Tablero de Control' en el contenido.
    """
    global URL_TABLERO
    log.info("[NAV] Navegando a Enlace Oficialia de Partes...")

    try:
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
        # 3.1 Asegurar pestana "Documentos en Proceso"
        try:
            tab = page.locator("a, button").filter(
                has_text=re.compile(r"Documentos en Proceso", re.I)
            ).first
            tab.wait_for(state="visible", timeout=3_000)
            tab.click()
            page.wait_for_timeout(1_500)
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
            log.warning("[WARN] Folio %s no encontrado en la columna 'Memo / Folio OPC' (buscado en %d paginas)", folio, num_pagina + 1)
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
def extraer_metadatos_satys(page, folio: str, carpeta: Path) -> dict:
    log.info("[WEB] Extrayendo metadatos web de SATyS para folio %s", folio)
    metadatos = {
        "representante_legal": "",
        "nombre_operador": "",
        "asunto": ""
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
def descargar_archivos(context, page, folio: str, carpeta: Path) -> list:
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
            log.error("[ERROR] No se encontro la seccion 'ARCHIVOS ASOCIADOS'")
            screenshot(page, f"no_seccion_{folio}")
            return resultados

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
            return resultados

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
                    if url and _descargar_directo(context, page, url, dest):
                        size_kb = dest.stat().st_size / 1024
                        log.info("     [OK] Descarga directa: %s  (%.1f KB)", fname, size_kb)
                        resultados.append({
                            "folio": folio, "archivo": fname,
                            "tipo": Path(fname).suffix.upper().lstrip("."),
                            "ruta": str(dest), "tamano_kb": round(size_kb, 1),
                            "ok": True, "url": url,
                        })
                        continue

                    with page.expect_download(timeout=TIMEOUT_DL) as dl_info:
                        btn.click()

                    dl = dl_info.value
                    fname = dl.suggested_filename or fname
                    dest = carpeta / fname
                    dl.save_as(str(dest))

                    size_kb = dest.stat().st_size / 1024
                    log.info("     [OK] Guardado: %s  (%.1f KB)", fname, size_kb)

                    resultados.append({
                        "folio": folio, "archivo": fname,
                        "tipo": Path(fname).suffix.upper().lstrip("."),
                        "ruta": str(dest), "tamano_kb": round(size_kb, 1), "ok": True,
                        "url": url,
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
            if not _avanzar_pagina_datatables(page):
                log.info("[PAGE] No se pudo avanzar, fin de paginas de archivos")
                break

        ok_c = sum(1 for r in resultados if r["ok"])
        log.info("[INFO] Folio %s: %d/%d descargados en %d pagina(s)",
                 folio, ok_c, len(resultados), pagina_arch)
        return resultados

    except Exception as e:
        log.error("[ERROR] General en archivos de folio %s: %s", folio, e)
        screenshot(page, f"error_archivos_{folio}")
        return resultados


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


def procesar_folio_completo(context, page, folio: str, carpeta: Path) -> list:
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
        # Buscar folio en la tabla
        if not _buscar_folio_en_tabla(page, folio):
            if iteracion == 0:
                log.warning("[WARN] Sin resultados para folio %s", folio)
                screenshot(page, f"tabla_vacia_{folio}")
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
                extraer_metadatos_satys(page, folio, carpeta)

                # Descargar todos los archivos
                res = descargar_archivos(context, page, folio, carpeta)
                if res:
                    todos_resultados.extend(res)
                else:
                    todos_resultados.append({
                        "folio": folio, "archivo": f"SIN_ARCHIVOS_{registro}",
                        "tipo": "N/A", "ruta": "", "tamano_kb": 0, "ok": False
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
#  FUNCION PRINCIPAL
# ============================================================
def main():
    print("\n+" + "-" * 68 + "+")
    print("|" + "  SATyS - DESCARGA AUTOMATICA DE ARCHIVOS (PARTE 1)  ".center(68) + "|")
    print("+" + "-" * 68 + "+\n")

    folios_raw = sys.argv[1:] if len(sys.argv) > 1 else FOLIOS_DEFAULT
    folios = list(dict.fromkeys(normalizar_folio(f) for f in folios_raw))
    log.info("[LIST] Folios a procesar (%d): %s", len(folios), ", ".join(folios))

    todos_resultados = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            slow_mo=100,
        )
        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1400, "height": 900},
            locale="es-MX",
        )
        habilitar_api_discovery(context)
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_NAV)

        # PASO 1: Login
        if not login(page):
            log.critical("[ABORT] No se pudo iniciar sesion.")
            browser.close()
            return

        # PASO 2: Navegar al tablero
        if not navegar_a_tablero(page):
            log.critical("[ABORT] No se pudo llegar al tablero.")
            browser.close()
            return

        # PASOS 3-5: Iterar por folio
        folios_procesados = 0
        limite_folios = int(os.environ.get("SATYS_MAX_FOLIOS", "0"))

        for idx, folio in enumerate(folios, 1):
            if limite_folios > 0 and folios_procesados >= limite_folios:
                log.info("[INFO] Se alcanzó el límite de %d folios existentes. Deteniendo búsqueda.", limite_folios)
                break

            print("\n" + "=" * 70)
            print(f"  PROCESANDO FOLIO [{idx}/{len(folios)}]: {folio}")
            print("=" * 70)

            carpeta = crear_carpeta(folio)
            log.info("[DIR] Carpeta: %s", carpeta.resolve())

            try:
                res = procesar_folio_completo(context, page, folio, carpeta)
                if res:
                    todos_resultados.extend(res)
                    folios_procesados += 1
                else:
                    todos_resultados.append({
                        "folio": folio, "archivo": "FOLIO_NO_ENCONTRADO",
                        "tipo": "N/A", "ruta": "", "tamano_kb": 0, "ok": False
                    })

            except Exception as e:
                log.error("[ERROR] Fatal en folio %s: %s", folio, e)
                screenshot(page, f"fatal_{folio}")
                todos_resultados.append({
                    "folio": folio, "archivo": "ERROR_FATAL",
                    "tipo": "ERROR", "ruta": str(e)[:120], "tamano_kb": 0, "ok": False
                })

            # Asegurar que estamos en el tablero antes del siguiente folio
            if idx < len(folios):
                volver_al_tablero(page)
                time.sleep(1)

        browser.close()
        log.info("[END] Navegador cerrado")

    # Reporte final
    if todos_resultados:
        generar_reporte(todos_resultados)
        guardar_log_json(todos_resultados)
    else:
        log.warning("[WARN] No se proceso ningun archivo")


if __name__ == "__main__":
    main()