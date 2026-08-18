"""
Extrae todos los numeros de Registro visibles en:
SATyS -> Enlace/SIGEDO -> Enlace Oficialia de Partes -> Documentos en Proceso.

Salida por defecto:
  registros_documentos_en_proceso.txt

Credenciales:
  config/configuracion_local.json
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from configuracion_local import credenciales_satys


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
SESION_FILE = Path(os.getenv("SATYS_SESION_FILE", str(Path(__file__).resolve().parent / "sesion_guardada.json")))
OUTPUT_DEFAULT = Path(os.getenv("SATYS_REGISTROS_OUT", "registros_documentos_en_proceso.txt"))
HEADLESS_DEFAULT = os.getenv("SATYS_HEADLESS", "False").lower() in ("true", "1", "yes")
TIMEOUT_NAV = int(os.getenv("SATYS_TIMEOUT_NAV", "60000"))
TIMEOUT_CORTO = int(os.getenv("SATYS_TIMEOUT_CORTO", "10000"))
TIMEOUT_TABLA_REGISTROS = int(os.getenv("SATYS_TIMEOUT_TABLA_REGISTROS", "120000"))
INTENTOS_ANIO_DEFAULT = max(1, int(os.getenv("SATYS_INTENTOS_ANIO", "3")))
INTENTOS_PAGINA_DEFAULT = max(1, int(os.getenv("SATYS_INTENTOS_PAGINA", "3")))
VACIO_ESTABLE_SEGUNDOS_DEFAULT = max(3, int(os.getenv("SATYS_VACIO_ESTABLE_SEGUNDOS", "8")))
BANDEJAS_INTERNOS = (
    "Recibidos",
    "En proceso",
    "Copias Marcadas",
    "Atendidos",
    "Ultimos Movimientos",
    "Fuera de tiempo",
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SATyS-Registros")


def cargar_credenciales_satys() -> tuple[str, str]:
    """Lee las credenciales exclusivamente del archivo local del proyecto."""
    return credenciales_satys()


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
            try:
                hay_spinner = bool(
                    page.evaluate(
                        r"""
                        () => Array.from(document.querySelectorAll('body *')).some(el => {
                          if (el.children.length) return false;
                          const style = window.getComputedStyle(el);
                          if (style.display === 'none' || style.visibility === 'hidden' || el.offsetParent === null) {
                            return false;
                          }
                          const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
                          return /^Cargando(?:\.{3}|\u2026)?$/i.test(text);
                        })
                        """
                    )
                )
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
            "[LOGIN] Faltan credenciales en config/configuracion_local.json.",
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
    """Detecta las opciones del selector Año con una firma estable del control."""
    raw_opciones = page.evaluate(
        r"""
        JSON.stringify((() => {
          const visible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && !el.hidden;
          };
          const norm = txt => (txt || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
          const allSelects = Array.from(document.querySelectorAll('select'));
          const selects = allSelects.filter(visible);
          const candidatos = [];
          for (const select of selects) {
            const options = Array.from(select.options || []);
            const years = options
              .map((opt, idx) => {
                const text = norm(opt.textContent || opt.value || '');
                const value = opt.value || text;
                const match = `${text} ${value}`.match(/\b(20\d{2})\b/);
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
            const score = (/\bAno\b|\bAnio\b/i.test(ctx) ? 1000 : 0)
              + (/Documentos en Proceso/i.test(ctx) ? 200 : 0)
              + years.length;
            const selector = {
              selectorId: select.id || '',
              selectorName: select.name || '',
              selectorDomIndex: allSelects.indexOf(select),
              selectorScore: score
            };
            candidatos.push({score, years: years.map(y => ({...y, ...selector}))});
          }
          candidatos.sort((a, b) => b.score - a.score);
          return candidatos.length ? candidatos[0].years : [];
        })())
        """
    )
    opciones = json.loads(raw_opciones or "[]")

    unicos: dict[int, dict] = {}
    for opcion in opciones:
        year = opcion.get("year")
        if isinstance(year, int) and year not in unicos:
            unicos[year] = opcion
    return [unicos[year] for year in sorted(unicos.keys(), reverse=True)]


def obtener_anio_seleccionado(page) -> int | None:
    """Obtiene el año del mejor selector candidato, aunque esté deshabilitado durante AJAX."""
    try:
        valor = page.evaluate(
            r"""
            (() => {
              const visible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && !el.hidden;
              };
              const norm = txt => (txt || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
              const candidatos = [];
              for (const select of Array.from(document.querySelectorAll('select')).filter(visible)) {
                const options = Array.from(select.options || []);
                const years = options.filter(opt => /\b20\d{2}\b/.test(`${opt.textContent || ''} ${opt.value || ''}`));
                if (!years.length) continue;
                const ctx = norm([
                  select.labels ? Array.from(select.labels).map(l => l.textContent).join(' ') : '',
                  select.closest('.form-group, .row, div')?.textContent || '',
                  select.parentElement?.textContent || ''
                ].join(' '));
                const score = (/\bAno\b|\bAnio\b/i.test(ctx) ? 1000 : 0)
                  + (/Documentos en Proceso/i.test(ctx) ? 200 : 0)
                  + years.length;
                candidatos.push({select, options, score});
              }
              candidatos.sort((a, b) => b.score - a.score);
              if (!candidatos.length) return null;
              const {select, options} = candidatos[0];
              const selected = options.find(opt => opt.value === select.value) || options[select.selectedIndex];
              const match = `${selected?.textContent || ''} ${selected?.value || select.value || ''}`.match(/\b(20\d{2})\b/);
              return match ? Number(match[1]) : null;
            })()
            """
        )
        return int(valor) if valor is not None else None
    except Exception:
        return None


def preparar_observador_tabla(page) -> int:
    """Instala un contador de mutaciones antes de cambiar filtros de DataTables."""
    try:
        return int(page.evaluate(
            """
            (() => {
              window.__satysTableMutationCounter = Number(window.__satysTableMutationCounter || 0);
              try { window.__satysTableObserver?.disconnect(); } catch (_) {}
              const visible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && !el.hidden;
              };
              const compact = txt => (txt || '')
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^A-Za-z0-9]/g, '').toLowerCase();
              let root = null;
              for (const table of Array.from(document.querySelectorAll('table')).filter(visible)) {
                const headerRow = table.querySelector('thead tr') || table.querySelector('tr');
                const headers = headerRow ? Array.from(headerRow.querySelectorAll('th, td')) : [];
                if (headers.some(th => compact(th.innerText || th.textContent || '') === 'registro')) {
                  root = table.closest('.dataTables_wrapper') || table.parentElement || table;
                  break;
                }
              }
              root = root || document.body;
              window.__satysTableObserver = new MutationObserver(() => {
                window.__satysTableMutationCounter += 1;
              });
              window.__satysTableObserver.observe(root, {
                subtree: true, childList: true, characterData: true
              });
              return window.__satysTableMutationCounter;
            })()
            """
        ))
    except Exception:
        return 0


def seleccionar_anio(page, opcion: dict) -> dict:
    """Selecciona el año exacto sin disparar un AJAX innecesario si ya estaba activo."""
    year = int(opcion["year"])
    payload = json.dumps({
        "year": year,
        "value": str(opcion.get("value") or year),
        "selectorId": str(opcion.get("selectorId") or ""),
        "selectorName": str(opcion.get("selectorName") or ""),
        "selectorDomIndex": opcion.get("selectorDomIndex"),
    })
    mutation_counter_antes = preparar_observador_tabla(page)
    raw_resultado = page.evaluate(
        f"""
        JSON.stringify((() => {{
            const target = {payload};
            const visible = el => {{
              if (!el) return false;
              const style = window.getComputedStyle(el);
              return style.display !== 'none' && style.visibility !== 'hidden' && !el.hidden;
            }};
            const norm = txt => (txt || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').trim();
            const allSelects = Array.from(document.querySelectorAll('select'));
            const candidatos = [];
            for (const select of allSelects.filter(visible)) {{
              const options = Array.from(select.options || []);
              const hasYears = options.some(opt => /\\b20\\d{{2}}\\b/.test(`${{opt.textContent || ''}} ${{opt.value || ''}}`));
              if (!hasYears) continue;
              const option = options.find(opt => opt.value === target.value)
                || options.find(opt => new RegExp(`\\\\b${{target.year}}\\\\b`).test(`${{opt.textContent || ''}} ${{opt.value || ''}}`));
              if (!option) continue;
              const ctx = norm([
                select.labels ? Array.from(select.labels).map(l => l.textContent).join(' ') : '',
                select.closest('.form-group, .row, div')?.textContent || '',
                select.parentElement?.textContent || ''
              ].join(' '));
              let score = (/\\bAno\\b|\\bAnio\\b/i.test(ctx) ? 1000 : 0)
                + (/Documentos en Proceso/i.test(ctx) ? 200 : 0)
                + options.length;
              if (target.selectorId && select.id === target.selectorId) score += 10000;
              if (target.selectorName && select.name === target.selectorName) score += 5000;
              if (Number.isInteger(target.selectorDomIndex) && allSelects.indexOf(select) === target.selectorDomIndex) score += 2500;
              candidatos.push({{select, option, score}});
            }}
            candidatos.sort((a, b) => b.score - a.score);
            if (!candidatos.length) return {{ok: false, reason: 'selector/opción no encontrados'}};
            const {{select, option}} = candidatos[0];
            const changed = select.value !== option.value;
            let drawAntes = null;
            try {{
              if (window.jQuery && window.jQuery.fn?.dataTable) {{
                const tables = Array.from(document.querySelectorAll('table')).filter(visible);
                for (const table of tables) {{
                  const headers = Array.from((table.querySelector('thead tr') || table.querySelector('tr'))?.querySelectorAll('th, td') || []);
                  const hasRegistro = headers.some(th => norm(th.innerText || th.textContent || '').replace(/[^A-Za-z0-9]/g, '').toLowerCase() === 'registro');
                  if (!hasRegistro || !window.jQuery.fn.dataTable.isDataTable(table)) continue;
                  drawAntes = Number(window.jQuery(table).DataTable().settings()[0]?.iDraw || 0);
                  break;
                }}
              }}
            }} catch (_) {{}}
            if (changed && select.disabled) return {{ok: false, reason: 'selector deshabilitado durante carga AJAX'}};
            if (changed) {{
              select.value = option.value;
              option.selected = true;
              select.dispatchEvent(new Event('input', {{bubbles: true}}));
              select.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}
            const selectedText = `${{select.options[select.selectedIndex]?.textContent || ''}} ${{select.value || ''}}`;
            const selectedMatch = selectedText.match(/\\b(20\\d{{2}})\\b/);
            return {{
              ok: true,
              changed,
              text: (option.textContent || option.value || '').trim(),
              selectedYear: selectedMatch ? Number(selectedMatch[1]) : null,
              drawAntes
            }};
        }})())
        """
    )
    resultado = json.loads(raw_resultado or "{}")
    if not resultado.get("ok"):
        raise RuntimeError(
            f"No pude seleccionar el Año {year} en SATyS: {resultado.get('reason') or 'causa desconocida'}."
        )
    seleccionado = resultado.get("selectedYear")
    if seleccionado != year:
        raise RuntimeError(f"SATyS no confirmó el Año {year}; el selector quedó en {seleccionado!r}.")
    if resultado.get("changed"):
        log.info("[CFG] Año seleccionado y verificado: %s (cambio solicitado).", resultado.get("text") or year)
        page.wait_for_timeout(300)
    else:
        log.info("[CFG] Año seleccionado y verificado: %s (ya estaba activo).", resultado.get("text") or year)
    resultado["mutationCounterAntes"] = mutation_counter_antes
    return resultado


def cambiar_mostrar_a_100(page, timeout_ms: int = 30_000) -> bool:
    """Configura 100 filas; espera si DataTables deshabilita temporalmente el control."""
    inicio = time.monotonic()
    ultimo_resultado: dict = {}
    while (time.monotonic() - inicio) * 1000 < max(1_000, timeout_ms):
        try:
            raw_resultado = page.evaluate(
                r"""
                JSON.stringify((() => {
                  const visible = el => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && !el.hidden;
                  };
                  const norm = txt => (txt || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
                  const selects = Array.from(document.querySelectorAll('select')).filter(visible);
                  const candidatos = [];
                  for (const select of selects) {
                    const options = Array.from(select.options || []);
                    const values = options.map(opt => norm(opt.value || opt.textContent || ''));
                    const hasLengthOptions = ['10', '25', '50', '100'].every(v => values.includes(v));
                    const hasYears = options.some(opt => /\b20\d{2}\b/.test(`${opt.textContent || ''} ${opt.value || ''}`));
                    const opt100 = options.find(opt => norm(opt.value || opt.textContent || '') === '100');
                    if (!opt100 || hasYears) continue;
                    const ctx = norm([
                      select.closest('.dataTables_length')?.textContent || '',
                      select.closest('.row, .form-group, div')?.textContent || '',
                      select.parentElement?.textContent || ''
                    ].join(' '));
                    const score = (hasLengthOptions ? 100 : 0)
                      + (/Mostrar/i.test(ctx) ? 50 : 0)
                      + (/tramites/i.test(ctx) ? 25 : 0);
                    candidatos.push({select, opt100, score});
                  }
                  candidatos.sort((a, b) => b.score - a.score);
                  if (!candidatos.length) return {ok: false, retryable: true, reason: 'selector no disponible'};
                  const {select, opt100} = candidatos[0];
                  const changed = select.value !== opt100.value;
                  if (changed && select.disabled) {
                    return {ok: false, retryable: true, reason: 'selector deshabilitado durante AJAX'};
                  }
                  if (changed) {
                    select.value = opt100.value;
                    opt100.selected = true;
                    select.dispatchEvent(new Event('input', {bubbles: true}));
                    select.dispatchEvent(new Event('change', {bubbles: true}));
                  }
                  return {ok: true, changed, value: select.value};
                })())
                """
            )
            resultado = json.loads(raw_resultado or "{}")
            ultimo_resultado = resultado
            if resultado.get("ok"):
                if resultado.get("changed"):
                    log.info("[CFG] Selector 'Mostrar' cambiado a 100 tramites.")
                    esperar_datatables(page, timeout_ms=20_000)
                else:
                    log.info("[CFG] Selector 'Mostrar' ya estaba en 100 tramites.")
                return True
        except Exception as exc:
            ultimo_resultado = {"reason": str(exc), "retryable": True}
        page.wait_for_timeout(500)

    log.warning(
        "[CFG] No fue posible configurar 'Mostrar 100 tramites' en %ds: %s",
        max(1_000, timeout_ms) // 1000,
        ultimo_resultado.get("reason") or ultimo_resultado,
    )
    return False


def leer_estado_tabla(page) -> dict:
    """Lee el estado auditable de la tabla activa de Documentos en Proceso."""
    estado_default = {
        "registros": [],
        "info": "",
        "hasNext": False,
        "found": False,
        "pageKey": "",
        "activePage": None,
        "selectedYear": None,
        "mutationCounter": 0,
        "ready": False,
        "error": "",
        "recordsDisplay": None,
        "recordsTotal": None,
        "pageLength": None,
        "pageStart": None,
        "pageEnd": None,
        "pages": None,
        "draw": None,
        "serverSide": None,
        "dataTableReady": False,
        "realRowCount": 0,
        "invalidRegistroCount": 0,
        "emptyRowVisible": False,
        "zeroUi": False,
        "emptyConfirmed": False,
    }
    try:
        raw_estado = page.evaluate(
            r"""
            JSON.stringify((() => {
              const visible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && !el.hidden;
              };
              const norm = txt => (txt || '')
                .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
                .replace(/\s+/g, ' ').trim();
              const compact = txt => norm(txt).replace(/[^A-Za-z0-9]/g, '').toLowerCase();
              const tableCount = Array.from(document.querySelectorAll('table')).filter(visible).length;

              let selectedYear = null;
              const yearCandidates = [];
              for (const select of Array.from(document.querySelectorAll('select')).filter(visible)) {
                const options = Array.from(select.options || []);
                const years = options.filter(opt => /\b20\d{2}\b/.test(`${opt.textContent || ''} ${opt.value || ''}`));
                if (!years.length) continue;
                const ctx = norm([
                  select.labels ? Array.from(select.labels).map(l => l.textContent).join(' ') : '',
                  select.closest('.form-group, .row, div')?.textContent || '',
                  select.parentElement?.textContent || ''
                ].join(' '));
                const score = (/\bAno\b|\bAnio\b/i.test(ctx) ? 1000 : 0)
                  + (/Documentos en Proceso/i.test(ctx) ? 200 : 0) + years.length;
                yearCandidates.push({select, options, score});
              }
              yearCandidates.sort((a, b) => b.score - a.score);
              if (yearCandidates.length) {
                const {select, options} = yearCandidates[0];
                const selected = options.find(opt => opt.value === select.value) || options[select.selectedIndex];
                const match = `${selected?.textContent || ''} ${selected?.value || select.value || ''}`.match(/\b(20\d{2})\b/);
                if (match) selectedYear = Number(match[1]);
              }

              const candidatos = [];
              for (const table of Array.from(document.querySelectorAll('table')).filter(visible)) {
                const headerRow = table.querySelector('thead tr') || table.querySelector('tr');
                const headers = headerRow ? Array.from(headerRow.querySelectorAll('th, td')) : [];
                const registroIndex = headers.findIndex(th => compact(th.innerText || th.textContent || '') === 'registro');
                if (registroIndex < 0) continue;

                const wrapper = table.closest('.dataTables_wrapper') || table.parentElement || document.body;
                const infoEl = wrapper.querySelector('.dataTables_info, [id$="_info"]');
                const info = infoEl ? norm(infoEl.innerText || infoEl.textContent || '') : '';
                const processingVisible = Array.from(wrapper.querySelectorAll('.dataTables_processing')).some(visible);
                const rows = Array.from(table.querySelectorAll('tbody tr')).filter(visible);
                const registros = [];
                let pageKey = '';
                let realRowCount = 0;
                let invalidRegistroCount = 0;
                let emptyRowVisible = false;

                for (const row of rows) {
                  const cells = Array.from(row.querySelectorAll('td'));
                  const rowText = norm(row.innerText || row.textContent || '');
                  if (row.querySelector('td.dataTables_empty') || /no hay|sin resultados|no data|ningun dato|ningún dato/i.test(rowText)) {
                    emptyRowVisible = true;
                    continue;
                  }
                  if (!cells.length) continue;
                  realRowCount += 1;
                  if (cells.length <= registroIndex) {
                    invalidRegistroCount += 1;
                    continue;
                  }
                  const raw = norm(cells[registroIndex].innerText || cells[registroIndex].textContent || '');
                  const joined = raw.replace(/\s+/g, '');
                  const match = joined.match(/[A-Z]{2,8}\d{2}-\d{3,}/i);
                  if (match) registros.push(match[0].toUpperCase());
                  else invalidRegistroCount += 1;
                  if (!pageKey) pageKey = rowText.slice(0, 180);
                }

                let recordsDisplay = null;
                let recordsTotal = null;
                let pageLength = null;
                let pageStart = null;
                let pageEnd = null;
                let pages = null;
                let draw = null;
                let serverSide = null;
                let dataTableReady = false;
                let activePage = null;
                try {
                  if (window.jQuery && window.jQuery.fn?.dataTable && window.jQuery.fn.dataTable.isDataTable(table)) {
                    const api = window.jQuery(table).DataTable();
                    const p = api.page.info();
                    const settings = api.settings()[0];
                    recordsDisplay = Number(p.recordsDisplay);
                    recordsTotal = Number(p.recordsTotal);
                    pageLength = Number(p.length);
                    pageStart = Number(p.start);
                    pageEnd = Number(p.end);
                    pages = Number(p.pages);
                    activePage = Number(p.page) + 1;
                    draw = Number(settings?.iDraw || 0);
                    serverSide = !!settings?.oFeatures?.bServerSide;
                    dataTableReady = true;
                  }
                } catch (_) {}

                const nextCandidates = Array.from(wrapper.querySelectorAll('.paginate_button.next, li.next, a.next, button.next'));
                const next = nextCandidates.find(el => /siguiente|next/i.test(el.innerText || el.textContent || '')) || nextCandidates[0] || null;
                const nextClass = next ? `${next.className || ''} ${next.parentElement?.className || ''}` : '';
                let hasNext = !!next && !/disabled/i.test(nextClass);
                if (pages !== null && activePage !== null) hasNext = activePage < pages;
                if (activePage === null) {
                  const active = wrapper.querySelector('.paginate_button.current, li.active, .pagination .active');
                  const activeText = active ? norm(active.innerText || active.textContent || '') : '';
                  const activeMatch = activeText.match(/\d+/);
                  activePage = activeMatch ? Number(activeMatch[0]) : null;
                }
                const zeroUi = recordsDisplay === 0
                  || /(?:Mostrando|Showing)\s+0\s+(?:a|to)\s+0\s+(?:de|of)\s+0/i.test(info)
                  || (emptyRowVisible && realRowCount === 0);
                const wrapperText = norm(wrapper.innerText || wrapper.textContent || '');
                const score = (/Documentos en Proceso/i.test(wrapperText) ? 1000 : 0)
                  + (/tramites/i.test(info) ? 100 : 0) + registros.length;

                candidatos.push({
                  score, registros, info, hasNext, pageKey, activePage, selectedYear,
                  mutationCounter: Number(window.__satysTableMutationCounter || 0),
                  ready: !processingVisible && (dataTableReady || !!table),
                  recordsDisplay, recordsTotal, pageLength, pageStart, pageEnd, pages,
                  draw, serverSide, dataTableReady, realRowCount, invalidRegistroCount,
                  emptyRowVisible, zeroUi
                });
              }

              candidatos.sort((a, b) => b.score - a.score);
              if (!candidatos.length) return {
                tableCount, found: false, selectedYear,
                mutationCounter: Number(window.__satysTableMutationCounter || 0)
              };
              return {tableCount, found: true, ...candidatos[0]};
            })())
            """
        )
        estado = json.loads(raw_estado or "{}")
        estado_default.update(estado)
        return estado_default
    except Exception as exc:
        estado_default["error"] = str(exc)
        return estado_default


def firma_estado_tabla(estado: dict) -> tuple:
    """Firma estable para detectar cambios reales de filtro o página."""
    paginacion = parsear_info_paginacion(estado.get("info"))
    registros = tuple(estado.get("registros") or [])
    return (
        estado.get("selectedYear"),
        estado.get("draw"),
        estado.get("pageStart"),
        estado.get("pageEnd"),
        estado.get("recordsDisplay"),
        paginacion.get("desde"),
        paginacion.get("hasta"),
        paginacion.get("total"),
        estado.get("activePage"),
        registros[:2],
        registros[-2:],
    )


def esperar_tabla_registros_lista(
    page,
    timeout_ms: int = TIMEOUT_TABLA_REGISTROS,
    *,
    anio_esperado: int | None = None,
    firma_anterior: tuple | None = None,
    desde_minimo: int | None = None,
    mutation_minima: int | None = None,
    draw_minimo: int | None = None,
    permitir_vacio_confirmado: bool = False,
    vacio_estable_segundos: int = VACIO_ESTABLE_SEGUNDOS_DEFAULT,
    contexto: str = "tabla de Registros",
) -> dict:
    """Espera un estado positivo o un cero confirmado; nunca confunde timeout con vacío."""
    inicio = time.monotonic()
    limite = max(timeout_ms, 1_000) / 1000
    ultimo_estado: dict = {}
    siguiente_aviso = 30
    firma_vacio = None
    inicio_vacio = None
    lecturas_vacio = 0

    while (time.monotonic() - inicio) < limite:
        estado = leer_estado_tabla(page)
        ultimo_estado = estado or {}
        registros = ultimo_estado.get("registros") or []
        paginacion = parsear_info_paginacion(ultimo_estado.get("info"))
        firma_actual = firma_estado_tabla(ultimo_estado)
        anio_ok = anio_esperado is None or ultimo_estado.get("selectedYear") == anio_esperado
        cambio_ok = firma_anterior is None or firma_actual != firma_anterior
        rango_ok = desde_minimo is None or (
            paginacion.get("desde") is not None and paginacion["desde"] >= desde_minimo
        )
        mutation_actual = int(ultimo_estado.get("mutationCounter") or 0)
        mutation_ok = mutation_minima is not None and mutation_actual >= mutation_minima
        draw_actual = ultimo_estado.get("draw")
        draw_ok = draw_minimo is not None and draw_actual is not None and int(draw_actual) >= draw_minimo
        if mutation_minima is None and draw_minimo is None:
            evidencia_cambio_ok = True
        else:
            # DataTables puede reemplazar el nodo observado o no exponer iDraw.
            # Cualquiera de las dos evidencias confirma el refresco solicitado.
            evidencia_cambio_ok = mutation_ok or draw_ok

        estado_base_ok = (
            ultimo_estado.get("found")
            and ultimo_estado.get("ready")
            and anio_ok
            and cambio_ok
            and rango_ok
            and evidencia_cambio_ok
        )
        if estado_base_ok and registros:
            ultimo_estado["emptyConfirmed"] = False
            return ultimo_estado

        total_estado = ultimo_estado.get("recordsDisplay")
        if total_estado is None:
            total_estado = paginacion.get("total")
        candidato_vacio = (
            permitir_vacio_confirmado
            and estado_base_ok
            and not registros
            and total_estado == 0
            and bool(ultimo_estado.get("zeroUi"))
            and int(ultimo_estado.get("realRowCount") or 0) == 0
            and int(ultimo_estado.get("invalidRegistroCount") or 0) == 0
            and bool(ultimo_estado.get("dataTableReady"))
            and (ultimo_estado.get("draw") is None or int(ultimo_estado.get("draw") or 0) > 0)
        )
        if candidato_vacio:
            if firma_vacio == firma_actual:
                lecturas_vacio += 1
            else:
                firma_vacio = firma_actual
                inicio_vacio = time.monotonic()
                lecturas_vacio = 1
            estable_por = time.monotonic() - (inicio_vacio if inicio_vacio is not None else time.monotonic())
            if lecturas_vacio >= 3 and estable_por >= max(3, vacio_estable_segundos):
                ultimo_estado["emptyConfirmed"] = True
                log.info(
                    "[WAIT] %s confirmó cero Registros durante %.0fs (%d lecturas estables).",
                    contexto, estable_por, lecturas_vacio,
                )
                return ultimo_estado
        else:
            firma_vacio = None
            inicio_vacio = None
            lecturas_vacio = 0

        transcurrido = time.monotonic() - inicio
        if transcurrido >= siguiente_aviso:
            log.info(
                "[WAIT] Esperando %s: %.0fs/%ds | año=%s (esperado=%s), registros=%d, "
                "total=%s, info=%r, ready=%s, draw=%s/%s, mutación=%s/%s",
                contexto, transcurrido, int(limite), ultimo_estado.get("selectedYear"),
                anio_esperado, len(registros), total_estado, ultimo_estado.get("info") or "",
                ultimo_estado.get("ready"), ultimo_estado.get("draw"), draw_minimo,
                ultimo_estado.get("mutationCounter"), mutation_minima,
            )
            siguiente_aviso += 30

        page.wait_for_timeout(1_000)

    screenshot(page, "tabla_registros_timeout")
    segundos = int(limite)
    raise RuntimeError(
        f"No se obtuvo un estado confirmado de {contexto} dentro de {segundos} segundos. "
        f"Ultimo estado: año={ultimo_estado.get('selectedYear')}, "
        f"found={ultimo_estado.get('found')}, ready={ultimo_estado.get('ready')}, "
        f"registros={len(ultimo_estado.get('registros') or [])}, "
        f"recordsDisplay={ultimo_estado.get('recordsDisplay')}, "
        f"info={ultimo_estado.get('info')!r}, error={ultimo_estado.get('error')!r}. "
        "Este resultado es INDETERMINADO, no significa que SATyS tenga cero registros."
    )


def avanzar_siguiente(page) -> bool:
    """Pulsa Siguiente sin asumir que DataTables ya terminó de cambiar."""
    try:
        return bool(
            page.evaluate(
                """
                (() => {
                  const visible = el => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && !el.hidden;
                  };
                  const compact = txt => (txt || '')
                    .normalize('NFD')
                    .replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/[^A-Za-z0-9]/g, '')
                    .toLowerCase();
                  const tables = Array.from(document.querySelectorAll('table')).filter(visible);
                  for (const table of tables) {
                    const headerRow = table.querySelector('thead tr') || table.querySelector('tr');
                    const headers = headerRow ? Array.from(headerRow.querySelectorAll('th, td')) : [];
                    if (!headers.some(th => compact(th.innerText || th.textContent || '') === 'registro')) continue;
                    const wrapper = table.closest('.dataTables_wrapper') || table.parentElement || document.body;
                    const candidates = Array.from(wrapper.querySelectorAll('.paginate_button.next, li.next, a.next, button.next'));
                    const next = candidates.find(el => /siguiente|next/i.test(el.innerText || el.textContent || '')) || candidates[0] || null;
                    if (!next) continue;
                    const cls = `${next.className || ''} ${next.parentElement?.className || ''}`;
                    if (/disabled/i.test(cls)) continue;
                    const clickable = next.matches('a, button') ? next : (next.querySelector('a, button') || next);
                    clickable.scrollIntoView({block: 'center', inline: 'center'});
                    clickable.click();
                    return true;
                  }
                  return false;
                })()
                """
            )
        )
    except Exception:
        return False


def extraer_registros_detallado(
    page,
    max_paginas: int = 100,
    timeout_primera_pagina_ms: int = TIMEOUT_TABLA_REGISTROS,
    anio_label: str = "",
    intentos_pagina: int = INTENTOS_PAGINA_DEFAULT,
    mutation_minima: int | None = None,
    draw_minimo: int | None = None,
    permitir_vacio_confirmado: bool = False,
) -> dict:
    registros: list[str] = []
    vistos: set[str] = set()
    paginas_leidas = 0
    filas_leidas = 0
    duplicados_internos = 0
    filas_invalidas = 0
    total_esperado = None
    primera_info = ""
    ultima_info = ""
    ultima_paginacion: dict = {}
    year = int(anio_label) if anio_label else None
    prefijo = f"[AÑO {anio_label}]" if anio_label else "[TABLA]"

    estado = esperar_tabla_registros_lista(
        page,
        timeout_ms=timeout_primera_pagina_ms,
        anio_esperado=year,
        mutation_minima=mutation_minima,
        draw_minimo=draw_minimo,
        permitir_vacio_confirmado=permitir_vacio_confirmado,
        contexto=f"primera página del Año {anio_label}" if anio_label else "primera página",
    )

    if estado.get("emptyConfirmed"):
        info = estado.get("info") or "Mostrando 0 a 0 de 0 tramites"
        log.info("%s VACIO CONFIRMADO por DataTables: %s", prefijo, info)
        return {
            "estado": "VACIO_CONFIRMADO",
            "registros": [],
            "total_esperado": 0,
            "filas_leidas": 0,
            "registros_unicos": 0,
            "duplicados_internos": 0,
            "filas_invalidas": 0,
            "paginas_leidas": 0,
            "primera_info": info,
            "ultima_info": info,
        }

    pagina = 1
    while pagina <= max_paginas:
        if not estado.get("found") or not estado.get("registros"):
            raise RuntimeError(f"{prefijo} La página {pagina} no contiene Registros válidos.")

        info = estado.get("info") or "sin texto de paginacion"
        paginacion = parsear_info_paginacion(info)
        total_api = estado.get("recordsDisplay")
        if total_api is not None:
            total_esperado = int(total_api)
        elif paginacion.get("total") is not None:
            total_esperado = paginacion["total"]
        if pagina == 1:
            primera_info = info
        ultima_info = info
        ultima_paginacion = paginacion

        validos_pagina = len(estado.get("registros") or [])
        reales_pagina = int(estado.get("realRowCount") or validos_pagina)
        invalidos_pagina = int(estado.get("invalidRegistroCount") or 0)
        if invalidos_pagina or validos_pagina != reales_pagina:
            raise RuntimeError(
                f"{prefijo} La página {pagina} contiene filas sin Registro válido: "
                f"filas={reales_pagina}, válidos={validos_pagina}, inválidos={invalidos_pagina}."
            )

        esperados_pagina = None
        if paginacion.get("desde") is not None and paginacion.get("hasta") is not None:
            esperados_pagina = paginacion["hasta"] - paginacion["desde"] + 1
        elif estado.get("pageStart") is not None and estado.get("pageEnd") is not None:
            esperados_pagina = int(estado["pageEnd"]) - int(estado["pageStart"])
        if esperados_pagina is not None and reales_pagina != esperados_pagina:
            raise RuntimeError(
                f"{prefijo} La página {pagina} está incompleta: SATyS indica {esperados_pagina} filas "
                f"pero se leyeron {reales_pagina}. Info={info!r}."
            )

        nuevos = 0
        duplicados_pagina = 0
        for registro in estado.get("registros", []):
            filas_leidas += 1
            if registro not in vistos:
                vistos.add(registro)
                registros.append(registro)
                nuevos += 1
            else:
                duplicados_pagina += 1
                duplicados_internos += 1
        filas_invalidas += invalidos_pagina
        paginas_leidas = pagina
        dup_msg = f", {duplicados_pagina} duplicados" if duplicados_pagina else ""
        log.info(
            "%s Pagina %d: %d filas, %d nuevos%s, %d únicos acumulados | %s",
            prefijo, pagina, reales_pagina, nuevos, dup_msg, len(registros), info,
        )

        if not estado.get("hasNext"):
            log.info("%s Ultima pagina alcanzada (pagina %d).", prefijo, pagina)
            break

        firma_anterior = firma_estado_tabla(estado)
        desde_minimo = (paginacion.get("hasta") + 1) if paginacion.get("hasta") is not None else None
        ultimo_error = None
        siguiente_estado = None
        for intento_pagina in range(1, max(1, intentos_pagina) + 1):
            if intento_pagina > 1:
                actual = leer_estado_tabla(page)
                pag_actual = parsear_info_paginacion(actual.get("info"))
                if (
                    actual.get("found") and actual.get("ready") and actual.get("registros")
                    and (year is None or actual.get("selectedYear") == year)
                    and firma_estado_tabla(actual) != firma_anterior
                    and (desde_minimo is None or (
                        pag_actual.get("desde") is not None and pag_actual["desde"] >= desde_minimo
                    ))
                ):
                    siguiente_estado = actual
                    break
            if not avanzar_siguiente(page):
                ultimo_error = RuntimeError("no se encontró un botón Siguiente habilitado")
            else:
                try:
                    siguiente_estado = esperar_tabla_registros_lista(
                        page,
                        timeout_ms=timeout_primera_pagina_ms,
                        anio_esperado=year,
                        firma_anterior=firma_anterior,
                        desde_minimo=desde_minimo,
                        contexto=f"página siguiente del Año {anio_label}" if anio_label else "página siguiente",
                    )
                    break
                except Exception as exc:
                    ultimo_error = exc
            if intento_pagina < max(1, intentos_pagina):
                log.warning(
                    "%s La tabla no avanzó; reintento de página %d/%d inmediato.",
                    prefijo, intento_pagina, max(1, intentos_pagina),
                )

        if siguiente_estado is None:
            raise RuntimeError(
                f"{prefijo} No fue posible avanzar después de {max(1, intentos_pagina)} intento(s): {ultimo_error}"
            )
        estado = siguiente_estado
        pagina += 1
    else:
        raise RuntimeError(f"{prefijo} Se alcanzó el máximo de páginas configurado: {max_paginas}")

    if total_esperado is None:
        raise RuntimeError(f"{prefijo} SATyS no proporcionó un total auditable de filas.")
    hasta = ultima_paginacion.get("hasta")
    if hasta is None and estado.get("pageEnd") is not None:
        hasta = int(estado["pageEnd"])
    log.info(
        "%s Validacion final: hasta=%s, total_esperado=%s, filas_leidas=%d, registros_unicos=%d",
        prefijo, hasta, total_esperado, filas_leidas, len(registros),
    )
    if total_esperado <= 0:
        raise RuntimeError(f"{prefijo} Se obtuvo cero sin cumplir la confirmación de vacío.")
    if hasta != total_esperado:
        raise RuntimeError(
            f"{prefijo} La paginacion no llego al final: ultima_info={ultima_info!r}, "
            f"total_esperado={total_esperado}, hasta={hasta}, filas_leidas={filas_leidas}."
        )
    if filas_leidas != total_esperado:
        raise RuntimeError(
            f"{prefijo} EXTRACCIÓN INCOMPLETA: SATyS reporta {total_esperado} filas "
            f"pero se leyeron {filas_leidas}. No se asumirá que la diferencia son duplicados."
        )

    return {
        "estado": "ENCONTRADOS_COMPLETOS",
        "registros": registros,
        "total_esperado": total_esperado,
        "filas_leidas": filas_leidas,
        "registros_unicos": len(registros),
        "duplicados_internos": duplicados_internos,
        "filas_invalidas": filas_invalidas,
        "paginas_leidas": paginas_leidas,
        "primera_info": primera_info,
        "ultima_info": ultima_info,
    }


def extraer_registros(
    page,
    max_paginas: int = 100,
    timeout_primera_pagina_ms: int = TIMEOUT_TABLA_REGISTROS,
) -> list[str]:
    return extraer_registros_detallado(
        page,
        max_paginas=max_paginas,
        timeout_primera_pagina_ms=timeout_primera_pagina_ms,
    )["registros"]


def reabrir_tablero_para_reintento(page) -> None:
    """Regresa al tablero con una navegación limpia, conservando la sesión."""
    log.info("[REINTENTO] Reabriendo el tablero de Documentos en Proceso...")
    if not sesion_activa(page):
        raise RuntimeError("La sesión SATyS dejó de estar activa durante el reintento por año.")
    if not navegar_a_enlace_oficialia(page):
        raise RuntimeError("No fue posible reabrir el tablero de Documentos en Proceso.")


def extraer_un_anio_con_reintentos(
    page,
    year: int,
    *,
    max_paginas: int,
    timeout_primera_pagina_ms: int,
    intentos_anio: int = INTENTOS_ANIO_DEFAULT,
    intentos_pagina: int = INTENTOS_PAGINA_DEFAULT,
) -> tuple[dict, list[dict]]:
    """Extrae un solo año; un fallo no obliga a repetir los años ya completados."""
    historial: list[dict] = []
    total_intentos = max(1, int(intentos_anio))

    for intento in range(1, total_intentos + 1):
        if intento > 1:
            reabrir_tablero_para_reintento(page)
        log.info("[AÑO %s] Intento %d/%d.", year, intento, total_intentos)
        try:
            opciones_actuales = descubrir_anios_disponibles(page)
            opcion = next((op for op in opciones_actuales if int(op.get("year", 0)) == year), None)
            if opcion is None:
                raise RuntimeError(f"El Año {year} no aparece en el selector actual de SATyS.")

            seleccion = seleccionar_anio(page, opcion)
            mutation_minima = None
            draw_minimo = None
            if seleccion.get("changed"):
                mutation_minima = int(seleccion.get("mutationCounterAntes") or 0) + 1
                draw_antes = seleccion.get("drawAntes")
                if draw_antes is not None and int(draw_antes) > 0:
                    draw_minimo = int(draw_antes) + 1
            # No se vuelve a consultar inmediatamente el selector: SATyS puede
            # deshabilitarlo mientras inicia el AJAX. Cuando hubo cambio de año,
            # primero se confirma el draw/mutación del filtro y solo después se
            # cambia el tamaño de página. Así el redraw de "Mostrar 100" no puede
            # hacerse pasar por la respuesta del nuevo año.
            if seleccion.get("changed"):
                esperar_tabla_registros_lista(
                    page,
                    timeout_ms=timeout_primera_pagina_ms,
                    anio_esperado=year,
                    mutation_minima=mutation_minima,
                    draw_minimo=draw_minimo,
                    permitir_vacio_confirmado=True,
                    contexto=f"confirmación del filtro Año {year}",
                )

            if not cambiar_mostrar_a_100(page):
                raise RuntimeError(f"No pude configurar 'Mostrar 100 tramites' para el Año {year}.")

            detalle = extraer_registros_detallado(
                page,
                max_paginas=max_paginas,
                timeout_primera_pagina_ms=timeout_primera_pagina_ms,
                anio_label=str(year),
                intentos_pagina=intentos_pagina,
                permitir_vacio_confirmado=True,
            )
            historial.append({"intento": intento, "ok": True, "error": ""})
            detalle["intentos_anio"] = intento
            return detalle, historial
        except Exception as exc:
            historial.append({"intento": intento, "ok": False, "error": str(exc)})
            log.warning("[AÑO %s] Intento %d/%d falló: %s", year, intento, total_intentos, exc)
            screenshot(page, f"anio_{year}_intento_{intento}_fallido")
            if intento < total_intentos:
                log.info("[AÑO %s] Reintento inmediato; los años ya completados se conservan.", year)

    raise RuntimeError(
        f"No fue posible extraer el Año {year} después de {total_intentos} intento(s). "
        f"Historial: {historial}"
    )


def extraer_registros_por_anio(
    page,
    max_paginas: int = 100,
    timeout_primera_pagina_ms: int = TIMEOUT_TABLA_REGISTROS,
    intentos_anio: int = INTENTOS_ANIO_DEFAULT,
    intentos_pagina: int = INTENTOS_PAGINA_DEFAULT,
) -> dict:
    opciones_anio = descubrir_anios_disponibles(page)
    if not opciones_anio:
        log.warning("[CFG] No encontre selector de Año; extraigo solo el estado actual de la tabla.")
        if not cambiar_mostrar_a_100(page):
            raise RuntimeError("No pude configurar 'Mostrar 100 tramites'.")
        detalle = extraer_registros_detallado(
            page,
            max_paginas,
            timeout_primera_pagina_ms,
            intentos_pagina=intentos_pagina,
            permitir_vacio_confirmado=True,
        )
        resumen_actual = {
            "anio": None,
            "estado": detalle.get("estado"),
            "total_reportado_satys": detalle.get("total_esperado"),
            "filas_leidas": detalle.get("filas_leidas", 0),
            "total_guardados_anio": len(detalle["registros"]),
            "registros_unicos": detalle.get("registros_unicos", len(detalle["registros"])),
            "duplicados_internos": detalle.get("duplicados_internos", 0),
            "filas_invalidas": detalle.get("filas_invalidas", 0),
            "paginas_leidas": detalle.get("paginas_leidas"),
            "primera_info": detalle.get("primera_info"),
            "ultima_info": detalle.get("ultima_info"),
        }
        return {
            "estado": "COMPLETO",
            "vacio_confirmado": detalle.get("estado") == "VACIO_CONFIRMADO",
            "integridad": "VALIDADA",
            "total_filas_satys": detalle.get("filas_leidas", 0),
            "modo": "actual",
            "anios_detectados": [],
            "por_anio": [resumen_actual],
            "registros": detalle["registros"],
            "total_registros": len(detalle["registros"]),
        }

    anios = [int(op["year"]) for op in opciones_anio]
    log.info("[CFG] %d Años detectados: %s", len(anios), ", ".join(map(str, anios)))
    registros_globales: list[str] = []
    vistos_globales: set[str] = set()
    resumen_anios: list[dict] = []

    for idx, year in enumerate(anios, start=1):
        log.info("[CFG] ══════ Procesando Año %s (%d/%d) ══════", year, idx, len(anios))
        detalle, historial = extraer_un_anio_con_reintentos(
            page,
            year,
            max_paginas=max_paginas,
            timeout_primera_pagina_ms=timeout_primera_pagina_ms,
            intentos_anio=intentos_anio,
            intentos_pagina=intentos_pagina,
        )
        registros_anio = detalle["registros"]

        nuevos_globales = 0
        for registro in registros_anio:
            if registro not in vistos_globales:
                vistos_globales.add(registro)
                registros_globales.append(registro)
                nuevos_globales += 1

        duplicados_entre_anios = len(registros_anio) - nuevos_globales
        resumen_anio = {
            "anio": year,
            "estado": detalle.get("estado"),
            "total_reportado_satys": detalle.get("total_esperado"),
            "filas_leidas": detalle.get("filas_leidas", 0),
            "total_guardados_anio": len(registros_anio),
            "registros_unicos": detalle.get("registros_unicos", len(registros_anio)),
            "duplicados_internos": detalle.get("duplicados_internos", 0),
            "filas_invalidas": detalle.get("filas_invalidas", 0),
            "nuevos_globales": nuevos_globales,
            "duplicados_entre_anios": duplicados_entre_anios,
            "paginas_leidas": detalle.get("paginas_leidas"),
            "primera_info": detalle.get("primera_info"),
            "ultima_info": detalle.get("ultima_info"),
            "intentos_anio": detalle.get("intentos_anio"),
            "historial_intentos": historial,
        }
        resumen_anios.append(resumen_anio)
        log.info(
            "[AÑO %s] ✔ %s: %d registros únicos | filas SATyS=%s | páginas=%s | intento=%s/%s | acumulado global=%d",
            year,
            detalle.get("estado"),
            len(registros_anio),
            detalle.get("filas_leidas"),
            detalle.get("paginas_leidas"),
            detalle.get("intentos_anio"),
            max(1, intentos_anio),
            len(registros_globales),
        )
        if duplicados_entre_anios:
            log.info(
                "[AÑO %s] Nota: %d registros de este año ya existían en años anteriores (deduplicados).",
                year, duplicados_entre_anios,
            )

    estados_validos = {"ENCONTRADOS_COMPLETOS", "VACIO_CONFIRMADO"}
    if any(item.get("estado") not in estados_validos for item in resumen_anios):
        raise RuntimeError("La extracción terminó con al menos un Año en estado indeterminado.")
    vacio_confirmado = bool(resumen_anios) and all(
        item.get("estado") == "VACIO_CONFIRMADO" for item in resumen_anios
    )
    total_filas_satys = sum(int(item.get("filas_leidas") or 0) for item in resumen_anios)
    log.info(
        "[CFG] ══════ EXTRACCION COMPLETA: %d registros únicos, %d filas auditadas en %d años ══════",
        len(registros_globales), total_filas_satys, len(anios),
    )
    return {
        "estado": "COMPLETO",
        "vacio_confirmado": vacio_confirmado,
        "integridad": "VALIDADA",
        "total_filas_satys": total_filas_satys,
        "modo": "por_anio",
        "anios_detectados": anios,
        "por_anio": resumen_anios,
        "registros": registros_globales,
        "total_registros": len(registros_globales),
        "timeout_primera_pagina_segundos": timeout_primera_pagina_ms // 1000,
        "intentos_anio": max(1, intentos_anio),
        "intentos_pagina": max(1, intentos_pagina),
    }


# ---------------------------------------------------------------------------
# Internos IFT: inventory of numeric Folio values from all dashboard tabs.
# ---------------------------------------------------------------------------

def navegar_a_internos_ift(page) -> bool:
    """Open Administracion solicitudes +TyS/SIGEDO/Internos IFT."""
    log.info("[INT-NAV] Abriendo Administracion solicitudes +TyS/SIGEDO/Internos IFT...")
    try:
        esperar_sin_spinner(page, timeout_ms=20_000)
        resultado = page.evaluate(
            r"""
            (() => {
              const visible = el => {
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
              };
              const norm = txt => (txt || '').normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
              const candidates = Array.from(document.querySelectorAll('a, button')).filter(visible)
                .map(el => ({el, text: norm(el.innerText || el.textContent || '')}))
                .filter(item => item.text.includes('administracion solicitudes')
                  && item.text.includes('internos ift'))
                .sort((a, b) => a.text.length - b.text.length);
              if (!candidates.length) return false;
              candidates[0].el.click();
              return true;
            })()
            """
        )
        if not resultado:
            raise RuntimeError("No se encontro el menu principal de Administracion solicitudes.")

        page.wait_for_timeout(500)
        submenu = page.evaluate(
            r"""
            (() => {
              const visible = el => {
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
              };
              const norm = txt => (txt || '').normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
              const candidates = Array.from(document.querySelectorAll('a, button')).filter(visible)
                .map(el => ({el, text: norm(el.innerText || el.textContent || '')}))
                .filter(item => item.text.includes('tys/sigedo/internos ift')
                  && !item.text.includes('administracion'))
                .sort((a, b) => a.text.length - b.text.length);
              if (!candidates.length) return false;
              candidates[0].el.click();
              return true;
            })()
            """
        )
        if not submenu:
            raise RuntimeError("No se encontro el submenu +TyS/SIGEDO/Internos IFT.")

        esperar_datatables(page, timeout_ms=20_000)
        page.wait_for_function(
            r"""
            () => {
              const norm = txt => (txt || '').normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
              const text = norm(document.body.innerText || '');
              return text.includes('recibidos') && text.includes('en proceso')
                && text.includes('fuera de tiempo');
            }
            """,
            timeout=TIMEOUT_NAV,
        )
        log.info("[INT-NAV] Tablero de Internos IFT cargado.")
        return True
    except Exception as exc:
        log.error("[INT-NAV] No se pudo abrir Internos IFT: %s", exc)
        screenshot(page, "internos_nav_error")
        return False


def seleccionar_bandeja_internos(page, bandeja: str) -> None:
    """Activate one of the six Internos dashboard tabs by its visible label."""
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
        raise RuntimeError(f"No se encontro la bandeja de Internos: {bandeja}")
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
        esperar_sin_spinner(page, timeout_ms=30_000)
    esperar_datatables(page, timeout_ms=30_000)
    log.info("[INT-TAB] Bandeja activa: %s", bandeja)


def leer_estado_tabla_folios(page) -> dict:
    """Read the active DataTable whose first logical column is Folio."""
    estado_default = {
        "folios": [],
        "info": "",
        "hasNext": False,
        "found": False,
        "ready": False,
        "activePage": None,
        "recordsDisplay": None,
        "recordsTotal": None,
        "pageLength": None,
        "pageStart": None,
        "pageEnd": None,
        "pages": None,
        "draw": None,
        "dataTableReady": False,
        "realRowCount": 0,
        "invalidFolioCount": 0,
        "emptyRowVisible": False,
        "zeroUi": False,
        "error": "",
    }
    try:
        raw = page.evaluate(
            r"""
            JSON.stringify((() => {
              const visible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && !el.hidden
                  && el.offsetParent !== null;
              };
              const norm = txt => (txt || '').normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim();
              const compact = txt => norm(txt).replace(/[^A-Za-z0-9]/g, '').toLowerCase();
              const candidates = [];

              for (const table of Array.from(document.querySelectorAll('table')).filter(visible)) {
                const headerRow = table.querySelector('thead tr') || table.querySelector('tr');
                const headers = headerRow ? Array.from(headerRow.querySelectorAll('th, td')) : [];
                const folioIndex = headers.findIndex(th => compact(th.innerText || th.textContent || '') === 'folio');
                if (folioIndex < 0) continue;

                const wrapper = table.closest('.dataTables_wrapper') || table.parentElement || document.body;
                const infoEl = wrapper.querySelector('.dataTables_info, [id$="_info"]');
                const info = infoEl ? norm(infoEl.innerText || infoEl.textContent || '') : '';
                const processingVisible = Array.from(wrapper.querySelectorAll('.dataTables_processing')).some(visible);
                const rows = Array.from(table.querySelectorAll('tbody tr')).filter(visible);
                const folios = [];
                let realRowCount = 0;
                let invalidFolioCount = 0;
                let emptyRowVisible = false;

                for (const row of rows) {
                  const cells = Array.from(row.querySelectorAll('td'));
                  const rowText = norm(row.innerText || row.textContent || '');
                  if (row.querySelector('td.dataTables_empty')
                    || /no hay|sin datos|sin resultados|no data|ningun dato/i.test(rowText)) {
                    emptyRowVisible = true;
                    continue;
                  }
                  if (!cells.length) continue;
                  realRowCount += 1;
                  if (cells.length <= folioIndex) {
                    invalidFolioCount += 1;
                    continue;
                  }
                  const value = norm(cells[folioIndex].innerText || cells[folioIndex].textContent || '');
                  const match = value.match(/\b\d{1,15}\b/);
                  if (match) folios.push(match[0]);
                  else invalidFolioCount += 1;
                }

                let recordsDisplay = null;
                let recordsTotal = null;
                let pageLength = null;
                let pageStart = null;
                let pageEnd = null;
                let pages = null;
                let draw = null;
                let activePage = null;
                let dataTableReady = false;
                try {
                  if (window.jQuery && window.jQuery.fn?.dataTable
                    && window.jQuery.fn.dataTable.isDataTable(table)) {
                    const api = window.jQuery(table).DataTable();
                    const p = api.page.info();
                    const settings = api.settings()[0];
                    recordsDisplay = Number(p.recordsDisplay);
                    recordsTotal = Number(p.recordsTotal);
                    pageLength = Number(p.length);
                    pageStart = Number(p.start);
                    pageEnd = Number(p.end);
                    pages = Number(p.pages);
                    activePage = Number(p.page) + 1;
                    draw = Number(settings?.iDraw || 0);
                    dataTableReady = true;
                  }
                } catch (_) {}

                const nextCandidates = Array.from(wrapper.querySelectorAll(
                  '.paginate_button.next, li.next, a.next, button.next, [aria-label="Next"], [aria-label="Siguiente"]'
                ));
                const next = nextCandidates.find(el => /siguiente|next|\u2192/i.test(el.innerText || el.textContent || ''))
                  || nextCandidates[0] || null;
                const nextClass = next ? `${next.className || ''} ${next.parentElement?.className || ''}` : '';
                let hasNext = !!next && !/disabled/i.test(nextClass)
                  && next?.getAttribute('aria-disabled') !== 'true';
                if (pages !== null && activePage !== null) hasNext = activePage < pages;
                const zeroUi = recordsDisplay === 0
                  || /(?:Mostrando|Showing)\s+0\s+(?:a|to)\s+0\s+(?:de|of)\s+0/i.test(info)
                  || (emptyRowVisible && realRowCount === 0);
                const wrapperText = norm(wrapper.innerText || wrapper.textContent || '');
                const score = (/tramites/i.test(info) ? 1000 : 0)
                  + (/Tipo Tramite/i.test(wrapperText) ? 100 : 0) + folios.length;
                candidates.push({
                  score, folios, info, hasNext, activePage, ready: !processingVisible,
                  recordsDisplay, recordsTotal, pageLength, pageStart, pageEnd, pages, draw,
                  dataTableReady, realRowCount, invalidFolioCount, emptyRowVisible, zeroUi
                });
              }

              candidates.sort((a, b) => b.score - a.score);
              return candidates.length ? {found: true, ...candidates[0]} : {found: false};
            })())
            """
        )
        estado_default.update(json.loads(raw or "{}"))
    except Exception as exc:
        estado_default["error"] = str(exc)
    return estado_default


def firma_estado_folios(estado: dict) -> tuple:
    paginacion = parsear_info_paginacion(estado.get("info"))
    folios = tuple(estado.get("folios") or [])
    return (
        estado.get("draw"),
        estado.get("pageStart"),
        estado.get("pageEnd"),
        estado.get("recordsDisplay"),
        paginacion.get("desde"),
        paginacion.get("hasta"),
        paginacion.get("total"),
        estado.get("activePage"),
        folios[:2],
        folios[-2:],
    )


def esperar_tabla_folios_lista(
    page,
    timeout_ms: int,
    *,
    firma_anterior: tuple | None = None,
    desde_minimo: int | None = None,
    permitir_vacio_confirmado: bool = False,
    contexto: str = "tabla de Folios",
) -> dict:
    inicio = time.monotonic()
    limite = max(timeout_ms, 1_000) / 1000
    ultimo_estado: dict = {}
    firma_vacio = None
    inicio_vacio = None
    lecturas_vacio = 0

    while time.monotonic() - inicio < limite:
        estado = leer_estado_tabla_folios(page)
        ultimo_estado = estado
        paginacion = parsear_info_paginacion(estado.get("info"))
        firma_actual = firma_estado_folios(estado)
        cambio_ok = firma_anterior is None or firma_actual != firma_anterior
        rango_ok = desde_minimo is None or (
            paginacion.get("desde") is not None and paginacion["desde"] >= desde_minimo
        )
        estado_base_ok = estado.get("found") and estado.get("ready") and cambio_ok and rango_ok
        if estado_base_ok and estado.get("folios"):
            estado["emptyConfirmed"] = False
            return estado

        total = estado.get("recordsDisplay")
        if total is None:
            total = paginacion.get("total")
        candidato_vacio = (
            permitir_vacio_confirmado
            and estado_base_ok
            and not estado.get("folios")
            and total == 0
            and bool(estado.get("zeroUi"))
            and int(estado.get("realRowCount") or 0) == 0
            and int(estado.get("invalidFolioCount") or 0) == 0
            and bool(estado.get("dataTableReady"))
        )
        if candidato_vacio:
            if firma_vacio == firma_actual:
                lecturas_vacio += 1
            else:
                firma_vacio = firma_actual
                inicio_vacio = time.monotonic()
                lecturas_vacio = 1
            estable = time.monotonic() - (inicio_vacio or time.monotonic())
            if lecturas_vacio >= 3 and estable >= VACIO_ESTABLE_SEGUNDOS_DEFAULT:
                estado["emptyConfirmed"] = True
                return estado
        else:
            firma_vacio = None
            inicio_vacio = None
            lecturas_vacio = 0
        page.wait_for_timeout(750)

    screenshot(page, "tabla_folios_timeout")
    raise RuntimeError(
        f"No se obtuvo un estado confirmado de {contexto} en {int(limite)} segundos. "
        f"found={ultimo_estado.get('found')}, ready={ultimo_estado.get('ready')}, "
        f"folios={len(ultimo_estado.get('folios') or [])}, info={ultimo_estado.get('info')!r}, "
        f"error={ultimo_estado.get('error')!r}."
    )


def avanzar_siguiente_folios(page) -> bool:
    try:
        return bool(
            page.evaluate(
                r"""
                (() => {
                  const visible = el => {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
                  };
                  const compact = txt => (txt || '').normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '').replace(/[^A-Za-z0-9]/g, '').toLowerCase();
                  for (const table of Array.from(document.querySelectorAll('table')).filter(visible)) {
                    const headers = Array.from((table.querySelector('thead tr') || table.querySelector('tr'))
                      ?.querySelectorAll('th, td') || []);
                    if (!headers.some(th => compact(th.innerText || th.textContent || '') === 'folio')) continue;
                    const wrapper = table.closest('.dataTables_wrapper') || table.parentElement || document.body;
                    const candidates = Array.from(wrapper.querySelectorAll(
                      '.paginate_button.next, li.next, a.next, button.next, [aria-label="Next"], [aria-label="Siguiente"]'
                    ));
                    const next = candidates.find(el => /siguiente|next|\u2192/i.test(el.innerText || el.textContent || ''))
                      || candidates[0] || null;
                    if (!next) continue;
                    const cls = `${next.className || ''} ${next.parentElement?.className || ''}`;
                    if (/disabled/i.test(cls) || next.getAttribute('aria-disabled') === 'true') continue;
                    const clickable = next.matches('a, button') ? next : (next.querySelector('a, button') || next);
                    clickable.click();
                    return true;
                  }
                  return false;
                })()
                """
            )
        )
    except Exception:
        return False


def extraer_folios_bandeja_internos(
    page,
    bandeja: str,
    *,
    max_paginas: int,
    timeout_ms: int,
) -> dict:
    seleccionar_bandeja_internos(page, bandeja)
    if not cambiar_mostrar_a_100(page):
        raise RuntimeError(f"No pude configurar Mostrar 100 en la bandeja {bandeja}.")

    estado = esperar_tabla_folios_lista(
        page,
        timeout_ms,
        permitir_vacio_confirmado=True,
        contexto=f"primera pagina de Internos/{bandeja}",
    )
    if estado.get("emptyConfirmed"):
        info = estado.get("info") or "Mostrando 0 a 0 de 0 tramites"
        return {
            "bandeja": bandeja,
            "estado": "VACIO_CONFIRMADO",
            "folios": [],
            "total_reportado_satys": 0,
            "filas_leidas": 0,
            "folios_unicos": 0,
            "duplicados_internos": 0,
            "filas_invalidas": 0,
            "paginas_leidas": 0,
            "primera_info": info,
            "ultima_info": info,
        }

    folios: list[str] = []
    vistos: set[str] = set()
    filas_leidas = 0
    duplicados = 0
    primera_info = ""
    ultima_info = ""
    total_esperado = None
    paginas_leidas = 0

    for pagina in range(1, max_paginas + 1):
        valores = estado.get("folios") or []
        info = estado.get("info") or ""
        paginacion = parsear_info_paginacion(info)
        total_api = estado.get("recordsDisplay")
        total_esperado = int(total_api) if total_api is not None else paginacion.get("total")
        if pagina == 1:
            primera_info = info
        ultima_info = info

        reales = int(estado.get("realRowCount") or len(valores))
        invalidos = int(estado.get("invalidFolioCount") or 0)
        if invalidos or len(valores) != reales:
            raise RuntimeError(
                f"Internos/{bandeja} pagina {pagina}: filas={reales}, "
                f"folios={len(valores)}, invalidas={invalidos}."
            )
        esperados_pagina = None
        if paginacion.get("desde") is not None and paginacion.get("hasta") is not None:
            esperados_pagina = paginacion["hasta"] - paginacion["desde"] + 1
        elif estado.get("pageStart") is not None and estado.get("pageEnd") is not None:
            esperados_pagina = int(estado["pageEnd"]) - int(estado["pageStart"])
        if esperados_pagina is not None and reales != esperados_pagina:
            raise RuntimeError(
                f"Internos/{bandeja} pagina {pagina} incompleta: "
                f"esperadas={esperados_pagina}, leidas={reales}, info={info!r}."
            )

        for folio in valores:
            filas_leidas += 1
            if folio in vistos:
                duplicados += 1
            else:
                vistos.add(folio)
                folios.append(folio)
        paginas_leidas = pagina
        log.info(
            "[INT-%s] Pagina %d: %d filas, %d folios unicos acumulados | %s",
            bandeja, pagina, reales, len(folios), info,
        )

        if not estado.get("hasNext"):
            break
        firma_anterior = firma_estado_folios(estado)
        desde_minimo = (paginacion.get("hasta") + 1) if paginacion.get("hasta") is not None else None
        if not avanzar_siguiente_folios(page):
            raise RuntimeError(f"No se pudo avanzar la paginacion de Internos/{bandeja}.")
        estado = esperar_tabla_folios_lista(
            page,
            timeout_ms,
            firma_anterior=firma_anterior,
            desde_minimo=desde_minimo,
            contexto=f"pagina siguiente de Internos/{bandeja}",
        )
    else:
        raise RuntimeError(f"Internos/{bandeja} alcanzo el limite de {max_paginas} paginas.")

    if total_esperado is None or total_esperado <= 0:
        raise RuntimeError(f"Internos/{bandeja} no proporciono un total auditable.")
    ultima_paginacion = parsear_info_paginacion(ultima_info)
    hasta = ultima_paginacion.get("hasta")
    if hasta is None and estado.get("pageEnd") is not None:
        hasta = int(estado["pageEnd"])
    if hasta != total_esperado or filas_leidas != total_esperado:
        raise RuntimeError(
            f"Internos/{bandeja} incompleto: hasta={hasta}, total={total_esperado}, "
            f"filas_leidas={filas_leidas}."
        )

    return {
        "bandeja": bandeja,
        "estado": "ENCONTRADOS_COMPLETOS",
        "folios": folios,
        "total_reportado_satys": total_esperado,
        "filas_leidas": filas_leidas,
        "folios_unicos": len(folios),
        "duplicados_internos": duplicados,
        "filas_invalidas": 0,
        "paginas_leidas": paginas_leidas,
        "primera_info": primera_info,
        "ultima_info": ultima_info,
    }


def extraer_folios_internos(
    page,
    *,
    max_paginas: int = 100,
    timeout_ms: int = TIMEOUT_TABLA_REGISTROS,
) -> dict:
    """Extract and validate every Folio from the six Internos tabs."""
    if not navegar_a_internos_ift(page):
        raise RuntimeError("No fue posible abrir el tablero de Internos IFT.")

    por_bandeja = []
    folios_globales: list[str] = []
    vistos_globales: set[str] = set()
    duplicados_entre_bandejas = 0

    for bandeja in BANDEJAS_INTERNOS:
        detalle = extraer_folios_bandeja_internos(
            page,
            bandeja,
            max_paginas=max_paginas,
            timeout_ms=timeout_ms,
        )
        nuevos = 0
        for folio in detalle["folios"]:
            if folio in vistos_globales:
                duplicados_entre_bandejas += 1
            else:
                vistos_globales.add(folio)
                folios_globales.append(folio)
                nuevos += 1
        detalle["nuevos_globales"] = nuevos
        por_bandeja.append(detalle)

    estados_validos = {"ENCONTRADOS_COMPLETOS", "VACIO_CONFIRMADO"}
    if any(item.get("estado") not in estados_validos for item in por_bandeja):
        raise RuntimeError("La extraccion de Internos termino con una bandeja indeterminada.")
    total_filas = sum(int(item.get("filas_leidas") or 0) for item in por_bandeja)
    vacio_confirmado = all(item.get("estado") == "VACIO_CONFIRMADO" for item in por_bandeja)
    log.info(
        "[INT-OK] Extraccion completa: %d folios unicos, %d filas en %d bandejas.",
        len(folios_globales), total_filas, len(por_bandeja),
    )
    return {
        "estado": "COMPLETO",
        "integridad": "VALIDADA",
        "vacio_confirmado": vacio_confirmado,
        "bandejas": list(BANDEJAS_INTERNOS),
        "por_bandeja": por_bandeja,
        "folios": folios_globales,
        "total_folios": len(folios_globales),
        "total_filas_satys": total_filas,
        "duplicados_entre_bandejas": duplicados_entre_bandejas,
    }

def escribir_texto_atomico(path: Path, contenido: str) -> None:
    """Publica un archivo completo mediante os.replace; nunca deja una salida parcial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with temporal.open("w", encoding="utf-8", newline="") as fh:
            fh.write(contenido)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporal, path)
    finally:
        try:
            temporal.unlink()
        except FileNotFoundError:
            pass


def guardar_registros(registros: list[str], output: Path, separador: str) -> None:
    if separador == "espacio":
        contenido = " ".join(registros)
    else:
        contenido = "\n".join(registros)
    if contenido:
        contenido += "\n"
    escribir_texto_atomico(output, contenido)


def guardar_resumen_extraccion(output: Path, resumen: dict) -> Path:
    resumen_path = output.with_suffix(output.suffix + ".json")
    escribir_texto_atomico(
        resumen_path,
        json.dumps(resumen, ensure_ascii=False, indent=2) + "\n",
    )
    return resumen_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extrae Registros de Oficialia y Folios de Internos IFT desde SATyS."
    )
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
        help="Segundos máximos para obtener un estado confirmado por año/página. Default: 120.",
    )
    parser.add_argument(
        "--intentos-anio",
        type=int,
        default=INTENTOS_ANIO_DEFAULT,
        help="Intentos totales por año antes de fallar. Default: 3.",
    )
    parser.add_argument(
        "--intentos-pagina",
        type=int,
        default=INTENTOS_PAGINA_DEFAULT,
        help="Intentos totales para confirmar el avance de cada página. Default: 3.",
    )
    parser.add_argument(
        "--sin-todos-los-anios",
        action="store_true",
        help="Compatibilidad: extrae solo el Año actual visible.",
    )
    parser.add_argument(
        "--modo-anios",
        choices=("todos", "actual"),
        default="todos",
        help="todos=detecta y recorre cada Año disponible; actual=solo el Año visible.",
    )
    parser.add_argument(
        "--sin-internos",
        action="store_true",
        help="Omite la extraccion adicional de Folios en Internos IFT.",
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

            modo_anios = "actual" if args.sin_todos_los_anios else args.modo_anios
            if modo_anios == "todos":
                resumen = extraer_registros_por_anio(
                    page,
                    max_paginas=args.max_paginas,
                    timeout_primera_pagina_ms=args.timeout_tabla * 1000,
                    intentos_anio=args.intentos_anio,
                    intentos_pagina=args.intentos_pagina,
                )
            else:
                if not cambiar_mostrar_a_100(page):
                    raise RuntimeError("No pude configurar 'Mostrar 100 tramites'.")
                detalle = extraer_registros_detallado(
                    page,
                    max_paginas=args.max_paginas,
                    timeout_primera_pagina_ms=args.timeout_tabla * 1000,
                    intentos_pagina=args.intentos_pagina,
                    permitir_vacio_confirmado=True,
                )
                resumen = {
                    "estado": "COMPLETO",
                    "vacio_confirmado": detalle.get("estado") == "VACIO_CONFIRMADO",
                    "integridad": "VALIDADA",
                    "total_filas_satys": detalle.get("filas_leidas", 0),
                    "modo": "actual",
                    "anios_detectados": [],
                    "por_anio": [{
                        "anio": None,
                        "estado": detalle.get("estado"),
                        "total_reportado_satys": detalle.get("total_esperado"),
                        "filas_leidas": detalle.get("filas_leidas", 0),
                        "total_guardados_anio": len(detalle["registros"]),
                        "registros_unicos": detalle.get("registros_unicos", len(detalle["registros"])),
                        "duplicados_internos": detalle.get("duplicados_internos", 0),
                        "filas_invalidas": detalle.get("filas_invalidas", 0),
                        "paginas_leidas": detalle.get("paginas_leidas"),
                        "primera_info": detalle.get("primera_info"),
                        "ultima_info": detalle.get("ultima_info"),
                    }],
                    "registros": detalle["registros"],
                    "total_registros": len(detalle["registros"]),
                }

            if args.sin_internos:
                resumen["internos"] = {
                    "estado": "OMITIDO",
                    "integridad": "NO_APLICA",
                    "folios": [],
                    "total_folios": 0,
                    "por_bandeja": [],
                }
            else:
                resumen["internos"] = extraer_folios_internos(
                    page,
                    max_paginas=args.max_paginas,
                    timeout_ms=args.timeout_tabla * 1000,
                )
            resumen["folios_internos"] = resumen["internos"].get("folios", [])
            resumen["total_folios_internos"] = resumen["internos"].get("total_folios", 0)

            registros = resumen["registros"]
            guardar_registros(registros, args.output, args.separador)
            resumen_path = guardar_resumen_extraccion(args.output, resumen)
            log.info("[OK] %d registros guardados en %s", len(registros), args.output)
            log.info("[OK] Resumen de extraccion guardado en %s", resumen_path)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
