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
    raw_opciones = page.evaluate(
        """
        JSON.stringify((() => {
          const visible = el => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden'
              && !el.hidden && !el.disabled;
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


def seleccionar_anio(page, opcion: dict) -> None:
    year = int(opcion["year"])
    value = str(opcion.get("value") or year)
    payload = json.dumps({"year": year, "value": value})
    raw_resultado = page.evaluate(
        f"""
        JSON.stringify((() => {{
            const target = {payload};
            const year = target.year;
            const value = target.value;
            const visible = el => {{
              if (!el) return false;
              const style = window.getComputedStyle(el);
              return style.display !== 'none' && style.visibility !== 'hidden'
                && !el.hidden && !el.disabled;
            }};
            const selects = Array.from(document.querySelectorAll('select')).filter(visible);
            for (const select of selects) {{
              const options = Array.from(select.options || []);
              const hasYears = options.some(opt => /\\b20\\d{{2}}\\b/.test(`${{opt.textContent || ''}} ${{opt.value || ''}}`));
              if (!hasYears) continue;
              const option = options.find(opt => opt.value === value)
                || options.find(opt => new RegExp(`\\\\b${{year}}\\\\b`).test(`${{opt.textContent || ''}} ${{opt.value || ''}}`));
              if (!option) continue;
              const changed = select.value !== option.value;
              select.value = option.value;
              select.dispatchEvent(new Event('input', {{ bubbles: true }}));
              select.dispatchEvent(new Event('change', {{ bubbles: true }}));
              return {{ok: true, changed, text: (option.textContent || option.value || '').trim()}};
            }}
            return {{ok: false}};
        }})())
        """
    )
    resultado = json.loads(raw_resultado or "{}")
    if not resultado.get("ok"):
        raise RuntimeError(f"No pude seleccionar el Año {year} en SATyS.")
    log.info("[CFG] Año seleccionado: %s", resultado.get("text") or year)
    esperar_datatables(page, timeout_ms=25_000)


def cambiar_mostrar_a_100(page) -> bool:
    try:
        raw_resultado = page.evaluate(
            """
            JSON.stringify((() => {
              const visible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && !el.hidden && !el.disabled;
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
            })())
            """
        )
        resultado = json.loads(raw_resultado or "{}")
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
        raw_estado = page.evaluate(
            """
            JSON.stringify((() => {
              const visible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && !el.hidden;
              };
              const norm = txt => (txt || '')
                .normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .replace(/\\s+/g, ' ')
                .trim();
              const compact = txt => norm(txt).replace(/[^A-Za-z0-9]/g, '').toLowerCase();
              const tableCount = Array.from(document.querySelectorAll('table')).filter(visible).length;
              const candidatos = [];

              for (const table of Array.from(document.querySelectorAll('table')).filter(visible)) {
                const headerRow = table.querySelector('thead tr') || table.querySelector('tr');
                const headers = headerRow ? Array.from(headerRow.querySelectorAll('th, td')) : [];
                const registroIndex = headers.findIndex(th => compact(th.innerText || th.textContent || '') === 'registro');
                if (registroIndex < 0) continue;

                const wrapper = table.closest('.dataTables_wrapper') || table.parentElement || document.body;
                const infoEl = wrapper.querySelector('.dataTables_info, [id$="_info"]');
                const info = infoEl ? norm(infoEl.innerText || infoEl.textContent || '') : '';
                const processingEls = Array.from(wrapper.querySelectorAll('.dataTables_processing'));
                const processingVisible = processingEls.some(visible);
                const rows = Array.from(table.querySelectorAll('tbody tr')).filter(visible);
                const registros = [];
                let pageKey = '';

                for (const row of rows) {
                  const cells = Array.from(row.querySelectorAll('td'));
                  if (cells.length <= registroIndex) continue;
                  const raw = norm(cells[registroIndex].innerText || cells[registroIndex].textContent || '');
                  if (!raw || /no hay|sin resultados|no data/i.test(raw)) continue;
                  const joined = raw.replace(/\\s+/g, '');
                  const match = joined.match(/[A-Z]{2,8}\\d{2}-\\d{3,}/i);
                  if (match) registros.push(match[0].toUpperCase());
                  if (!pageKey) pageKey = norm(row.innerText || row.textContent || '').slice(0, 180);
                }

                const nextCandidates = Array.from(wrapper.querySelectorAll('.paginate_button.next, li.next, a.next, button.next'));
                const next = nextCandidates.find(el => /siguiente|next/i.test(el.innerText || el.textContent || '')) || nextCandidates[0] || null;
                const nextClass = next ? `${next.className || ''} ${next.parentElement?.className || ''}` : '';
                const hasNext = !!next && !/disabled/i.test(nextClass);
                const wrapperText = norm(wrapper.innerText || wrapper.textContent || '');
                const score = (/Documentos en Proceso/i.test(wrapperText) ? 1000 : 0)
                  + (/tramites|trámites/i.test(info) ? 100 : 0)
                  + registros.length;

                candidatos.push({
                  score,
                  registros,
                  info,
                  hasNext,
                  pageKey,
                  ready: !processingVisible && (registros.length > 0 || /Mostrando|Showing|No hay|Sin resultados|0\\s+a\\s+0/i.test(info)),
                });
              }

              candidatos.sort((a, b) => b.score - a.score);
              if (!candidatos.length) return {tableCount, found: false};
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
        ok = page.evaluate(
            """
            (() => {
              const visible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && !el.hidden;
              };
              const compact = txt => (txt || '')
                .normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^A-Za-z0-9]/g, '')
                .toLowerCase();
              const tables = Array.from(document.querySelectorAll('table')).filter(visible);
              const wrappers = [];
              for (const table of tables) {
                const headerRow = table.querySelector('thead tr') || table.querySelector('tr');
                const headers = headerRow ? Array.from(headerRow.querySelectorAll('th, td')) : [];
                if (!headers.some(th => compact(th.innerText || th.textContent || '') === 'registro')) continue;
                wrappers.push(table.closest('.dataTables_wrapper') || table.parentElement || document.body);
              }
              for (const wrapper of wrappers) {
                const nextCandidates = Array.from(wrapper.querySelectorAll('.paginate_button.next, li.next, a.next, button.next'));
                const next = nextCandidates.find(el => /siguiente|next/i.test(el.innerText || el.textContent || '')) || nextCandidates[0] || null;
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
        if not ok:
            return False
        esperar_datatables(page, timeout_ms=15_000)
        return True
    except Exception:
        return False


def extraer_registros_detallado(
    page,
    max_paginas: int = 100,
    timeout_primera_pagina_ms: int = TIMEOUT_TABLA_REGISTROS,
    anio_label: str = "",
) -> dict:
    registros: list[str] = []
    vistos: set[str] = set()
    paginas_leidas = 0
    total_esperado = None
    primera_info = ""
    ultima_info = ""
    ultima_paginacion: dict = {}
    prefijo = f"[AÑO {anio_label}]" if anio_label else "[TABLA]"

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
        duplicados_pagina = 0
        for registro in estado.get("registros", []):
            if registro:
                if registro not in vistos:
                    vistos.add(registro)
                    registros.append(registro)
                    nuevos += 1
                else:
                    duplicados_pagina += 1

        info = estado.get("info") or "sin texto de paginacion"
        paginacion = parsear_info_paginacion(info)
        if pagina == 1:
            primera_info = info
        ultima_info = info
        ultima_paginacion = paginacion
        if paginacion.get("total") is not None:
            total_esperado = paginacion["total"]
        paginas_leidas = pagina
        dup_msg = f", {duplicados_pagina} duplicados" if duplicados_pagina else ""
        log.info(
            "%s Pagina %d: %d nuevos%s, %d acumulados | %s",
            prefijo, pagina, nuevos, dup_msg, len(registros), info,
        )

        if not estado.get("hasNext"):
            log.info("%s Ultima pagina alcanzada (pagina %d).", prefijo, pagina)
            break

        page_key = estado.get("pageKey", "")
        if not avanzar_siguiente(page):
            log.warning("%s No pude avanzar a la siguiente pagina; detengo en pagina %d.", prefijo, pagina)
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
        log.warning("%s Se alcanzo el maximo de paginas configurado: %d", prefijo, max_paginas)

    # ── Validación final: verificar que llegamos al final de la tabla ────────────
    if total_esperado is not None:
        hasta = ultima_paginacion.get("hasta")
        log.info(
            "%s Validacion final: hasta=%s, total_esperado=%s, registros_guardados=%d",
            prefijo, hasta, total_esperado, len(registros),
        )
        if total_esperado > 0 and hasta != total_esperado:
            raise RuntimeError(
                f"{prefijo} La paginacion no llego al final: "
                f"ultima_info={ultima_info!r}, total_esperado={total_esperado}, hasta={hasta}, "
                f"registros_guardados={len(registros)}. Revisa --max-paginas o el selector Mostrar 100."
            )
        # Permitir diferencia SOLO si hay duplicados (registros repetidos en SATyS)
        if len(registros) > total_esperado:
            raise RuntimeError(
                f"{prefijo} Se guardaron MAS registros que el total reportado por SATyS: "
                f"guardados={len(registros)}, total_pagina={total_esperado}, ultima_info={ultima_info!r}."
            )
        if len(registros) < total_esperado:
            diferencia = total_esperado - len(registros)
            log.warning(
                "%s AVISO: %d registro(s) en SATyS son duplicados (mismo Registro en filas distintas). "
                "Se guardaron %d registros unicos de %d reportados. ultima_info=%r",
                prefijo, diferencia, len(registros), total_esperado, ultima_info,
            )

    return {
        "registros": registros,
        "total_esperado": total_esperado,
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


def extraer_registros_por_anio(
    page,
    max_paginas: int = 100,
    timeout_primera_pagina_ms: int = TIMEOUT_TABLA_REGISTROS,
) -> dict:
    opciones_anio = descubrir_anios_disponibles(page)
    if not opciones_anio:
        log.warning("[CFG] No encontre selector de Año; extraigo solo el estado actual de la tabla.")
        if not cambiar_mostrar_a_100(page):
            raise RuntimeError("No pude configurar 'Mostrar 100 tramites'.")
        detalle = extraer_registros_detallado(page, max_paginas, timeout_primera_pagina_ms)
        detalle["anio"] = None
        return {
            "modo": "actual",
            "anios_detectados": [],
            "por_anio": [detalle],
            "registros": detalle["registros"],
            "total_registros": len(detalle["registros"]),
        }

    log.info(
        "[CFG] %d Años detectados: %s",
        len(opciones_anio),
        ", ".join(str(op["year"]) for op in opciones_anio),
    )
    registros_globales: list[str] = []
    vistos_globales: set[str] = set()
    resumen_anios: list[dict] = []

    for idx, opcion in enumerate(opciones_anio, start=1):
        year = int(opcion["year"])
        log.info(
            "[CFG] ══════ Procesando Año %s (%d/%d) ══════",
            year, idx, len(opciones_anio),
        )

        # Seleccionar año en el dropdown
        seleccionar_anio(page, opcion)

        # Cambiar 'Mostrar' a 100 para minimizar páginas
        if not cambiar_mostrar_a_100(page):
            raise RuntimeError(f"No pude configurar 'Mostrar 100 tramites' para el Año {year}.")

        detalle = extraer_registros_detallado(
            page,
            max_paginas=max_paginas,
            timeout_primera_pagina_ms=timeout_primera_pagina_ms,
            anio_label=str(year),
        )
        registros_anio = detalle["registros"]

        # Acumular registros globales deduplicando por si un registro aparece en varios años
        nuevos_globales = 0
        for registro in registros_anio:
            if registro not in vistos_globales:
                vistos_globales.add(registro)
                registros_globales.append(registro)
                nuevos_globales += 1

        duplicados_entre_anios = len(registros_anio) - nuevos_globales
        resumen_anio = {
            "anio": year,
            "total_reportado_satys": detalle.get("total_esperado"),
            "total_guardados_anio": len(registros_anio),
            "nuevos_globales": nuevos_globales,
            "duplicados_entre_anios": duplicados_entre_anios,
            "paginas_leidas": detalle.get("paginas_leidas"),
            "primera_info": detalle.get("primera_info"),
            "ultima_info": detalle.get("ultima_info"),
        }
        resumen_anios.append(resumen_anio)
        log.info(
            "[AÑO %s] ✔ COMPLETADO: %d registros unicos | total SATyS=%s | paginas=%s | acumulado global=%d",
            year,
            len(registros_anio),
            detalle.get("total_esperado"),
            detalle.get("paginas_leidas"),
            len(registros_globales),
        )
        if duplicados_entre_anios:
            log.info(
                "[AÑO %s] Nota: %d registros de este año ya existian en años anteriores (deduplicados).",
                year, duplicados_entre_anios,
            )

    log.info(
        "[CFG] ══════ EXTRACCION COMPLETA: %d registros unicos en %d años ══════",
        len(registros_globales), len(opciones_anio),
    )
    return {
        "modo": "por_anio",
        "anios_detectados": [int(op["year"]) for op in opciones_anio],
        "por_anio": resumen_anios,
        "registros": registros_globales,
        "total_registros": len(registros_globales),
    }

def guardar_registros(registros: list[str], output: Path, separador: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if separador == "espacio":
        contenido = " ".join(registros)
    else:
        contenido = "\n".join(registros)
    if contenido:
        contenido += "\n"
    output.write_text(contenido, encoding="utf-8")


def guardar_resumen_extraccion(output: Path, resumen: dict) -> Path:
    resumen_path = output.with_suffix(output.suffix + ".json")
    resumen_path.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumen_path


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
        help="Compatibilidad: extrae solo el Año actual visible.",
    )
    parser.add_argument(
        "--modo-anios",
        choices=("todos", "actual"),
        default="todos",
        help="todos=detecta y recorre cada Año disponible; actual=solo el Año visible.",
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
                )
            else:
                if not cambiar_mostrar_a_100(page):
                    raise RuntimeError("No pude configurar 'Mostrar 100 tramites'.")
                detalle = extraer_registros_detallado(
                    page,
                    max_paginas=args.max_paginas,
                    timeout_primera_pagina_ms=args.timeout_tabla * 1000,
                )
                resumen = {
                    "modo": "actual",
                    "anios_detectados": [],
                    "por_anio": [{
                        "anio": None,
                        "total_pagina": detalle.get("total_esperado"),
                        "total_guardados_anio": len(detalle["registros"]),
                        "paginas_leidas": detalle.get("paginas_leidas"),
                        "primera_info": detalle.get("primera_info"),
                        "ultima_info": detalle.get("ultima_info"),
                    }],
                    "registros": detalle["registros"],
                    "total_registros": len(detalle["registros"]),
                }

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
