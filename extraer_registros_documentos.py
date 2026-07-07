"""
Extrae todos los numeros de Registro visibles en:
SATyS -> Enlace/SIGEDO -> Enlace Oficialia de Partes -> Documentos en Proceso.

Salida por defecto:
  registros_documentos_en_proceso.txt

Credenciales:
  1) Variables de entorno SATYS_USER / SATYS_PASS
  2) Archivo %USERPROFILE%\\.satys\\credenciales.txt
     linea 1: usuario
     linea 2: contrasena
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright


if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, "encoding", "") != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def cargar_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


cargar_dotenv()

BASE_URL = os.getenv("SATYS_BASE_URL", "https://satys.ift.org.mx/")
CREDENCIALES_FILE = Path(
    os.getenv("SATYS_CREDENTIALS_FILE", str(Path.home() / ".satys" / "credenciales.txt"))
)
SESION_FILE = Path(os.getenv("SATYS_SESSION_FILE", "sesion_guardada.json"))
OUTPUT_DEFAULT = Path(os.getenv("SATYS_REGISTROS_OUT", "registros_documentos_en_proceso.txt"))
HEADLESS_DEFAULT = os.getenv("SATYS_HEADLESS", "False").lower() in ("true", "1", "yes")
TIMEOUT_NAV = int(os.getenv("SATYS_TIMEOUT_NAV", "60000"))
TIMEOUT_CORTO = int(os.getenv("SATYS_TIMEOUT_CORTO", "10000"))
TIMEOUT_TABLA_REGISTROS = int(os.getenv("SATYS_TIMEOUT_TABLA_REGISTROS", "60000"))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SATyS-Registros")


def cargar_credenciales_satys() -> tuple[str, str]:
    usuario = os.getenv("SATYS_USER", "").strip()
    password = os.getenv("SATYS_PASS", "").strip()

    if usuario and password:
        return usuario, password

    try:
        if CREDENCIALES_FILE.exists():
            with CREDENCIALES_FILE.open("r", encoding="utf-8") as f:
                usuario_archivo = f.readline().strip()
                password_archivo = f.readline().strip()
            usuario = usuario or usuario_archivo
            password = password or password_archivo
    except Exception:
        pass

    return usuario, password


def screenshot(page, nombre: str) -> None:
    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    destino = debug_dir / f"{time.strftime('%H%M%S')}_{nombre}.png"
    try:
        page.screenshot(path=str(destino), full_page=True)
        log.info("[DEBUG] Screenshot guardado: %s", destino)
    except Exception:
        pass


def esperar_sin_spinner(page, timeout_ms: int = 30_000) -> bool:
    selectores_spinner = [
        ".loading-overlay",
        ".overlay-loading",
        "[class*='loading'][class*='show']",
        "[class*='spinner'][style*='display: block']",
        "#loadingModal[style*='display: block']",
        ".modal-backdrop",
        "#pantalla-carga[style*='display: block']",
    ]
    inicio = time.time()
    limite = timeout_ms / 1000

    while time.time() - inicio < limite:
        hay_spinner = False
        for selector in selectores_spinner:
            try:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    hay_spinner = True
                    break
            except Exception:
                pass
        if not hay_spinner:
            return True
        page.wait_for_timeout(500)

    log.warning("[WAIT] La pantalla de carga sigue visible; continuo con cautela.")
    return False


def esperar_datatables(page, timeout_ms: int = 12_000) -> None:
    page.wait_for_timeout(800)  # Wait for AJAX to trigger and UI to show processing
    try:
        page.wait_for_function(
            """
            () => {
              const processing = Array.from(document.querySelectorAll('.dataTables_processing'));
              return processing.every(el => {
                const style = window.getComputedStyle(el);
                return style.display === 'none' || style.visibility === 'hidden' || el.offsetParent === null;
              });
            }
            """,
            timeout=timeout_ms,
        )
    except Exception:
        page.wait_for_timeout(900)
    esperar_sin_spinner(page, timeout_ms=8_000)


def login(page, usuario: str, password: str) -> bool:
    if not usuario or not password:
        log.error(
            "[LOGIN] Faltan credenciales. Configura SATYS_USER/SATYS_PASS "
            "o crea %s con usuario y contrasena.",
            CREDENCIALES_FILE,
        )
        return False

    try:
        log.info("[LOGIN] Abriendo SATyS...")
        page.goto(urljoin(BASE_URL, "Login"), wait_until="domcontentloaded", timeout=TIMEOUT_NAV)
        page.fill("input[name='usuario'], input[name='username']", usuario)
        page.fill("input[type='password']", password)

        try:
            with page.expect_navigation(timeout=TIMEOUT_NAV):
                page.click(
                    "button[type='submit'], input[type='submit'], "
                    "button:has-text('Ingresar'), a:has-text('Ingresar')"
                )
        except PWTimeout:
            log.info("[LOGIN] Sin navegacion completa; verificando SPA...")

        esperar_sin_spinner(page, timeout_ms=20_000)
        if page.locator("input[type='password']").count() > 0 and "login" in page.url.lower():
            log.error("[LOGIN] El portal sigue en login. Revisa usuario/contrasena.")
            screenshot(page, "login_fallido")
            return False

        log.info("[LOGIN] Sesion iniciada.")
        return True
    except Exception as exc:
        log.error("[LOGIN] Error iniciando sesion: %s", exc)
        screenshot(page, "login_error")
        return False


def sesion_activa(page) -> bool:
    try:
        page.goto(urljoin(BASE_URL, "Sarccontroller"), wait_until="domcontentloaded", timeout=TIMEOUT_NAV)
        esperar_sin_spinner(page, timeout_ms=15_000)
        return not (
            "login" in page.url.lower()
            or "verifylogin" in page.url.lower()
            or page.locator("input[type='password']").count() > 0
        )
    except Exception:
        return False


def click_menu_text(root, pattern: re.Pattern[str], timeout: int = TIMEOUT_CORTO) -> bool:
    try:
        loc = root.locator("a, button").filter(has_text=pattern).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.scroll_into_view_if_needed()
        loc.click()
        return True
    except Exception:
        return False


def click_onclick(page, snippet: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                (snippet) => {
                  const candidates = Array.from(document.querySelectorAll('[onclick]'));
                  const el = candidates.find(item => (item.getAttribute('onclick') || '').includes(snippet));
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


def navegar_a_enlace_oficialia(page) -> bool:
    log.info("[NAV] Abriendo Enlace/SIGEDO > Enlace Oficialia de Partes...")
    try:
        esperar_sin_spinner(page, timeout_ms=15_000)
        sidebar = page.locator("nav, .sidebar, aside").first
        sidebar.wait_for(state="visible", timeout=TIMEOUT_NAV)

        if not click_menu_text(sidebar, re.compile(r"Enlace\s*/\s*SIGEDO", re.I), TIMEOUT_NAV):
            click_menu_text(page, re.compile(r"Enlace\s*/\s*SIGEDO", re.I), TIMEOUT_NAV)

        try:
            page.wait_for_selector(
                "a:has-text('Oficialía'), a:has-text('Oficialia'), a:has-text('Ofic')",
                timeout=8_000,
                state="visible",
            )
        except Exception:
            page.wait_for_timeout(700)

        if not click_onclick(page, "muestraGestionSIGEDO"):
            if not click_menu_text(sidebar, re.compile(r"Oficial[ií]a\s+de\s+Partes", re.I), TIMEOUT_NAV):
                click_menu_text(page, re.compile(r"Oficial[ií]a\s+de\s+Partes", re.I), TIMEOUT_NAV)

        page.wait_for_selector("text=Documentos en Proceso", timeout=TIMEOUT_NAV)
        esperar_datatables(page, timeout_ms=15_000)
        log.info("[NAV] Tablero cargado.")
        return True
    except Exception as exc:
        log.error("[NAV] No se pudo llegar al tablero: %s", exc)
        screenshot(page, "nav_error")
        return False


def seleccionar_todos_los_anios(page) -> None:
    try:
        cambio = page.evaluate(
            """
            () => {
              const visible = el => {
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
              };
              const selects = Array.from(document.querySelectorAll('select')).filter(visible);
              for (const select of selects) {
                const options = Array.from(select.options || []);
                const option = options.find(opt => /todos/i.test(opt.textContent || ''));
                const looksLikeYear = options.some(opt => /20\\d\\d/.test(opt.textContent || opt.value || ''));
                if (option && looksLikeYear && select.value !== option.value) {
                  select.value = option.value;
                  select.dispatchEvent(new Event('change', { bubbles: true }));
                  return { changed: true, text: (option.textContent || '').trim() };
                }
              }
              return { changed: false };
            }
            """
        ) or {"changed": False}
        if cambio.get("changed"):
            log.info("[CFG] Año cambiado a: %s", cambio.get("text"))
            esperar_datatables(page, timeout_ms=20_000)
    except Exception as exc:
        log.warning("[CFG] No se pudo cambiar el selector de Año: %s", exc)


def parsear_numero(texto: str | None) -> int | None:
    if not texto:
        return None
    limpio = re.sub(r"[^\d]", "", str(texto))
    return int(limpio) if limpio else None


def parsear_info_paginacion(info: str | None) -> dict:
    """Convierte 'Mostrando 1 a 100 de 1,332 tramites' en numeros auditables."""
    texto = (info or "").replace("\xa0", " ").strip()
    resultado = {"desde": None, "hasta": None, "total": None}
    m = re.search(
        r"(?:Mostrando|Showing)\s+([\d,\.]+)\s+(?:a|to)\s+([\d,\.]+)\s+(?:de|of)\s+([\d,\.]+)",
        texto,
        re.I,
    )
    if not m:
        m = re.search(r"([\d,\.]+)\s+a\s+([\d,\.]+)\s+de\s+([\d,\.]+)", texto, re.I)
    if m:
        resultado["desde"] = parsear_numero(m.group(1))
        resultado["hasta"] = parsear_numero(m.group(2))
        resultado["total"] = parsear_numero(m.group(3))
    return resultado


def descubrir_anios_disponibles(page) -> list[dict]:
    """Detecta las opciones reales del selector Año visible en SATyS."""
    opciones = page.evaluate(
        """
        () => {
          const visible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          };
          const norm = txt => (txt || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim();
          const selects = Array.from(document.querySelectorAll('select')).filter(visible);
          const candidatos = [];
          for (const select of selects) {
            const options = Array.from(select.options || []);
            const years = options
              .map((opt, idx) => {
                const text = norm(opt.textContent || opt.value || '');
                const value = opt.value || text;
                const match = `${text} ${value}`.match(/\\b(20\\d{2})\\b/);
                return match ? {
                  year: Number(match[1]),
                  value,
                  text: text || value,
                  selected: select.value === value,
                  index: idx
                } : null;
              })
              .filter(Boolean);
            if (!years.length) continue;
            const ctx = norm([
              select.labels ? Array.from(select.labels).map(l => l.textContent).join(' ') : '',
              select.closest('.form-group, .row, div')?.textContent || '',
              select.parentElement?.textContent || ''
            ].join(' '));
            const score = (/\\bAno\\b|\\bAnio\\b|\\bAño\\b/i.test(ctx) ? 100 : 0) + years.length;
            candidatos.push({score, years});
          }
          candidatos.sort((a, b) => b.score - a.score);
          return candidatos.length ? candidatos[0].years : [];
        }
        """
    ) or []

    unicos: dict[int, dict] = {}
    for opcion in opciones:
        year = opcion.get("year")
        if isinstance(year, int) and year not in unicos:
            unicos[year] = opcion
    return [unicos[year] for year in sorted(unicos.keys(), reverse=True)]


def seleccionar_anio(page, opcion: dict) -> None:
    year = int(opcion["year"])
    value = str(opcion.get("value") or year)
    resultado = page.evaluate(
        """
        ({year, value}) => {
          const visible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          };
          const selects = Array.from(document.querySelectorAll('select')).filter(visible);
          for (const select of selects) {
            const options = Array.from(select.options || []);
            const hasYears = options.some(opt => /\\b20\\d{2}\\b/.test(`${opt.textContent || ''} ${opt.value || ''}`));
            if (!hasYears) continue;
            const option = options.find(opt => opt.value === value)
              || options.find(opt => new RegExp(`\\\\b${year}\\\\b`).test(`${opt.textContent || ''} ${opt.value || ''}`));
            if (!option) continue;
            const changed = select.value !== option.value;
            select.value = option.value;
            select.dispatchEvent(new Event('input', { bubbles: true }));
            select.dispatchEvent(new Event('change', { bubbles: true }));
            return {ok: true, changed, text: (option.textContent || option.value || '').trim()};
          }
          return {ok: false};
        }
        """,
        {"year": year, "value": value},
    ) or {"ok": False}
    if not resultado.get("ok"):
        raise RuntimeError(f"No pude seleccionar el Año {year} en SATyS.")
    log.info("[CFG] Año seleccionado: %s", resultado.get("text") or year)
    esperar_datatables(page, timeout_ms=25_000)


def cambiar_mostrar_a_100(page) -> bool:
    try:
        resultado = page.evaluate(
            """
            () => {
              const visible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              const norm = txt => (txt || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim();
              const selects = Array.from(document.querySelectorAll('select')).filter(visible);
              const candidatos = [];
              for (const select of selects) {
                const options = Array.from(select.options || []);
                const values = options.map(opt => norm(opt.value || opt.textContent || ''));
                const hasLengthOptions = ['10', '25', '50', '100'].every(v => values.includes(v));
                const hasYears = options.some(opt => /\\b20\\d{2}\\b/.test(`${opt.textContent || ''} ${opt.value || ''}`));
                const opt100 = options.find(opt => norm(opt.value || opt.textContent || '') === '100');
                if (!opt100 || hasYears) continue;
                const ctx = norm([
                  select.closest('.dataTables_length')?.textContent || '',
                  select.closest('.row, .form-group, div')?.textContent || '',
                  select.parentElement?.textContent || ''
                ].join(' '));
                const score = (hasLengthOptions ? 100 : 0)
                  + (/Mostrar/i.test(ctx) ? 50 : 0)
                  + (/tramites|trámites/i.test(ctx) ? 25 : 0);
                candidatos.push({select, opt100, score});
              }
              candidatos.sort((a, b) => b.score - a.score);
              if (!candidatos.length) return {ok: false};
              const {select, opt100} = candidatos[0];
              const changed = select.value !== opt100.value;
              select.value = opt100.value;
              select.dispatchEvent(new Event('input', { bubbles: true }));
              select.dispatchEvent(new Event('change', { bubbles: true }));
              return {ok: true, changed, value: select.value};
            }
            """
        )
        if not resultado or not resultado.get("ok"):
            log.warning("[CFG] No encontre el selector 'Mostrar 100 tramites'.")
            return False
        esperar_datatables(page, timeout_ms=20_000)
        log.info("[CFG] Selector 'Mostrar' configurado en 100 tramites.")
        return True
    except Exception as exc:
        log.error("[CFG] Error cambiando 'Mostrar' a 100: %s", exc)
        return False


def leer_estado_tabla(page) -> dict:
    """Lee el estado de la tabla de Documentos en Proceso.

    Importante: en SATyS la vista es una SPA. A veces `page.evaluate()` puede
    regresar None o fallar mientras el JavaScript del portal todavía está
    construyendo la tabla. Por eso esta función siempre regresa un dict seguro.
    """
    estado_default = {
        "registros": [],
        "info": "",
        "hasNext": False,
        "found": False,
        "pageKey": "",
        "ready": False,
        "error": "",
    }
    try:
        estado_default["tableCount"] = page.locator("table:visible").count()
        # Find the table that has a "Registro" column
        tables = page.locator("table:visible")
        chosen_table = None
        registro_index = -1
        
        for i in range(tables.count()):
            table = tables.nth(i)
            headers = table.locator("thead th, tr th")
            for j in range(headers.count()):
                text = headers.nth(j).inner_text().strip()
                if text.lower() == "registro":
                    chosen_table = table
                    registro_index = j
                    break
            if chosen_table:
                break
                
        if not chosen_table:
            return estado_default
            
        estado_default["found"] = True
        
        # Check if processing is visible
        wrapper = chosen_table.locator("xpath=ancestor::*[contains(@class, 'dataTables_wrapper')]").first
        if wrapper.count() == 0:
            wrapper = page.locator("body")
            
        processing = wrapper.locator(".dataTables_processing:visible")
        processing_visible = processing.count() > 0
        
        # Extract records
        registros = []
        rows = chosen_table.locator("tbody tr:visible")
        for i in range(rows.count()):
            row = rows.nth(i)
            cells = row.locator("td")
            if cells.count() > registro_index:
                raw_text = cells.nth(registro_index).inner_text().strip()
                if raw_text and not re.search(r"no hay|sin resultados|no data", raw_text, re.I):
                    compact = re.sub(r"\s+", "", raw_text)
                    match = re.search(r"[A-Z]{2,6}\d{2}-\d{3,}", compact, re.I)
                    registro = match.group(0).upper() if match else compact.upper()
                    if registro:
                        registros.append(registro)
                        
        estado_default["registros"] = registros
        
        # Extract info
        info_el = wrapper.locator(".dataTables_info, [id$='_info']").first
        info = info_el.inner_text().strip() if info_el.count() > 0 else ""
        estado_default["info"] = info
        
        # Check if next button is enabled
        next_btn = wrapper.locator(".paginate_button.next, li.next, a.next, button.next").filter(has_text=re.compile(r"siguiente|next", re.I)).first
        if next_btn.count() == 0:
            next_btn = wrapper.locator(".paginate_button.next, li.next, a.next, button.next").first
            
        has_next = False
        if next_btn.count() > 0:
            # Check classes for 'disabled'
            next_class = next_btn.get_attribute("class") or ""
            parent = next_btn.locator("xpath=..").first
            parent_class = parent.get_attribute("class") if parent.count() > 0 else ""
            if "disabled" not in next_class.lower() and "disabled" not in parent_class.lower():
                has_next = True
                
        estado_default["hasNext"] = has_next
        
        # Page key
        if rows.count() > 0:
            estado_default["pageKey"] = rows.first.inner_text().strip()[:180]
            
        # Ready
        estado_default["ready"] = not processing_visible and (len(registros) > 0 or re.search(r"Mostrando|Showing|No hay|Sin resultados|0\s+a\s+0", info, re.I) is not None)
        
        return estado_default
    except Exception as exc:
        estado_default["error"] = str(exc)
        return estado_default


def esperar_tabla_registros_lista(page, timeout_ms: int = TIMEOUT_TABLA_REGISTROS) -> dict:
    """Espera en ciclo hasta 1 minuto a que la tabla de Registros quede lista."""
    inicio = time.time()
    limite = max(timeout_ms, 1_000) / 1000
    ultimo_estado: dict = {}
    aviso_30s = False

    while (time.time() - inicio) < limite:
        esperar_sin_spinner(page, timeout_ms=3_000)
        estado = leer_estado_tabla(page)
        ultimo_estado = estado or {}

        if ultimo_estado.get("ready") and ultimo_estado.get("found"):
            if len(ultimo_estado.get("registros", [])) > 0 or (time.time() - inicio) > 10:
                return ultimo_estado

        if ultimo_estado.get("found") and ultimo_estado.get("registros"):
            return ultimo_estado

        if not aviso_30s and (time.time() - inicio) >= 30:
            log.info(
                "[WAIT] Aun esperando tabla de Registros... %.0fs/%ds (found=%s, ready=%s, tablas=%s, error=%s)",
                time.time() - inicio,
                int(limite),
                ultimo_estado.get("found"),
                ultimo_estado.get("ready"),
                ultimo_estado.get("tableCount"),
                ultimo_estado.get("error") or "",
            )
            aviso_30s = True

        page.wait_for_timeout(1_000)

    screenshot(page, "tabla_registros_timeout")
    raise RuntimeError(
        "No cargó la tabla visible con columna 'Registro' dentro de 60 segundos. "
        f"Ultimo estado: found={ultimo_estado.get('found')}, "
        f"ready={ultimo_estado.get('ready')}, tablas={ultimo_estado.get('tableCount')}, "
        f"info={ultimo_estado.get('info')!r}, error={ultimo_estado.get('error')!r}"
    )

def avanzar_siguiente(page) -> bool:
    try:
        wrapper = page.locator(".dataTables_wrapper:visible").first
        if wrapper.count() == 0:
            wrapper = page.locator("body")
        
        next_btn = wrapper.locator(".paginate_button.next, li.next, a.next, button.next").filter(has_text=re.compile(r"siguiente|next", re.I)).first
        if next_btn.count() == 0:
            next_btn = wrapper.locator(".paginate_button.next, li.next, a.next, button.next").first
            
        if next_btn.count() == 0:
            return False
            
        next_class = next_btn.get_attribute("class") or ""
        parent = next_btn.locator("xpath=..").first
        parent_class = parent.get_attribute("class") if parent.count() > 0 else ""
        
        if "disabled" in next_class.lower() or "disabled" in parent_class.lower():
            return False
            
        next_btn.click()
        esperar_datatables(page, timeout_ms=15_000)
        return True
    except Exception:
        return False


def extraer_registros(
    page,
    max_paginas: int = 100,
    timeout_primera_pagina_ms: int = TIMEOUT_TABLA_REGISTROS,
) -> list[str]:
    registros: list[str] = []
    vistos: set[str] = set()

    for pagina in range(1, max_paginas + 1):
        if pagina == 1:
            estado = esperar_tabla_registros_lista(page, timeout_ms=timeout_primera_pagina_ms)
        else:
            estado = leer_estado_tabla(page)
            if not isinstance(estado, dict):
                estado = {}
            if not estado.get("found"):
                # En cambios de página DataTables puede tardar unos segundos.
                estado = esperar_tabla_registros_lista(page, timeout_ms=15_000)

        if not estado.get("found"):
            raise RuntimeError("No encontre una tabla visible con columna 'Registro'.")

        nuevos = 0
        for registro in estado.get("registros", []):
            if registro and registro not in vistos:
                vistos.add(registro)
                registros.append(registro)
                nuevos += 1

        info = estado.get("info") or "sin texto de paginacion"
        log.info("[TABLA] Pagina %d: %d nuevos, %d acumulados (%s)", pagina, nuevos, len(registros), info)

        if not estado.get("hasNext"):
            break

        page_key = estado.get("pageKey", "")
        if not avanzar_siguiente(page):
            break

        try:
            page.wait_for_function(
                "(previous) => { const row = document.querySelector('table tbody tr'); "
                "return row && row.innerText.trim().slice(0, 180) !== previous; }",
                arg=page_key,
                timeout=8_000,
            )
        except Exception:
            esperar_datatables(page, timeout_ms=8_000)
    else:
        log.warning("[TABLA] Se alcanzo el maximo de paginas configurado: %d", max_paginas)

    return registros

def guardar_registros(registros: list[str], output: Path, separador: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if separador == "espacio":
        contenido = " ".join(registros)
    else:
        contenido = "\n".join(registros)
    if contenido:
        contenido += "\n"
    output.write_text(contenido, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extrae los numeros de Registro desde SATyS.")
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT, help="Archivo TXT de salida.")
    parser.add_argument("--headless", action="store_true", help="Ejecuta el navegador sin ventana.")
    parser.add_argument("--visible", action="store_true", help="Fuerza navegador visible.")
    parser.add_argument(
        "--separador",
        choices=("linea", "espacio"),
        default="linea",
        help="Formato del TXT: un registro por linea o separados por espacio.",
    )
    parser.add_argument("--max-paginas", type=int, default=100, help="Limite de paginas del DataTable.")
    parser.add_argument(
        "--timeout-tabla",
        type=int,
        default=TIMEOUT_TABLA_REGISTROS // 1000,
        help="Segundos maximos para esperar a que cargue la tabla de Registros. Default: 60.",
    )
    parser.add_argument(
        "--sin-todos-los-anios",
        action="store_true",
        help="No cambia el selector de Año a 'Todos los años'.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    usuario, password = cargar_credenciales_satys()
    headless = HEADLESS_DEFAULT
    if args.headless:
        headless = True
    if args.visible:
        headless = False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=50 if not headless else 0)
        context_args = {
            "viewport": {"width": 1400, "height": 900},
            "locale": "es-MX",
        }
        if SESION_FILE.exists():
            context_args["storage_state"] = str(SESION_FILE)

        context = browser.new_context(**context_args)
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_NAV)

        try:
            if not sesion_activa(page):
                if not login(page, usuario, password):
                    return 1
                try:
                    context.storage_state(path=str(SESION_FILE))
                    log.info("[SESION] Sesion guardada en %s", SESION_FILE)
                except Exception as exc:
                    log.warning("[SESION] No se pudo guardar sesion: %s", exc)

            if not navegar_a_enlace_oficialia(page):
                return 1

            if not args.sin_todos_los_anios:
                seleccionar_todos_los_anios(page)
            cambiar_mostrar_a_100(page)

            registros = extraer_registros(
                page,
                max_paginas=args.max_paginas,
                timeout_primera_pagina_ms=args.timeout_tabla * 1000,
            )
            guardar_registros(registros, args.output, args.separador)
            log.info("[OK] %d registros guardados en %s", len(registros), args.output)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
