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
                const options = Array.from(select.options);
                const option = options.find(opt => /todos/i.test(opt.textContent || ''));
                const looksLikeYear = options.some(opt => /20\\d\\d/.test(opt.textContent || opt.value || ''));
                if (option && looksLikeYear && select.value !== option.value) {
                  select.value = option.value;
                  select.dispatchEvent(new Event('change', { bubbles: true }));
                  return { changed: true, text: option.textContent.trim() };
                }
              }
              return { changed: false };
            }
            """
        )
        if cambio.get("changed"):
            log.info("[CFG] Año cambiado a: %s", cambio.get("text"))
            esperar_datatables(page, timeout_ms=20_000)
    except Exception as exc:
        log.warning("[CFG] No se pudo cambiar el selector de Año: %s", exc)


def cambiar_mostrar_a_100(page) -> bool:
    try:
        resultado = page.evaluate(
            """
            () => {
              const visible = el => {
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
              };
              const selects = Array.from(
                document.querySelectorAll('.dataTables_length select, select[name*="_length"]')
              ).filter(visible);
              for (const select of selects) {
                const opt100 = Array.from(select.options).find(opt => opt.value === '100');
                if (opt100) {
                  if (select.value !== '100') {
                    select.value = '100';
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                  }
                  return true;
                }
              }
              return false;
            }
            """
        )
        if not resultado:
            log.warning("[CFG] No encontre el selector 'Mostrar 100 tramites'.")
            return False
        esperar_datatables(page, timeout_ms=20_000)
        log.info("[CFG] Selector 'Mostrar' configurado en 100 tramites.")
        return True
    except Exception as exc:
        log.error("[CFG] Error cambiando 'Mostrar' a 100: %s", exc)
        return False


def leer_estado_tabla(page) -> dict:
    return page.evaluate(
        """
        () => {
          const visible = el => {
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
          };
          const normalize = text => (text || '').replace(/\\s+/g, ' ').trim();
          const cleanRegistro = text => {
            const compact = (text || '').replace(/\\s+/g, '');
            const match = compact.match(/[A-Z]{2,6}\\d{2}-\\d{3,}/i);
            return match ? match[0].toUpperCase() : compact.toUpperCase();
          };

          const tables = Array.from(document.querySelectorAll('table')).filter(visible);
          let chosen = null;
          let registroIndex = -1;

          for (const table of tables) {
            const headers = Array.from(table.querySelectorAll('thead th')).map(th => normalize(th.innerText));
            const idx = headers.findIndex(header => /^Registro$/i.test(header));
            if (idx >= 0) {
              chosen = table;
              registroIndex = idx;
              break;
            }
          }

          if (!chosen) {
            return { registros: [], info: '', hasNext: false, found: false, pageKey: '' };
          }

          const wrapper = chosen.closest('.dataTables_wrapper') || chosen.parentElement || document;
          const registros = [];
          for (const tr of Array.from(chosen.querySelectorAll('tbody tr'))) {
            if (!visible(tr)) continue;
            const cells = Array.from(tr.querySelectorAll('td'));
            if (cells.length <= registroIndex) continue;
            const raw = normalize(cells[registroIndex].innerText);
            if (!raw || /no hay|sin resultados|no data/i.test(raw)) continue;
            const registro = cleanRegistro(raw);
            if (registro) registros.push(registro);
          }

          const infoEl = wrapper.querySelector('.dataTables_info, [id$="_info"]');
          const info = infoEl ? normalize(infoEl.innerText) : '';
          const nextCandidates = Array.from(
            wrapper.querySelectorAll('.paginate_button.next, li.next, a.next, button.next')
          ).filter(visible);
          const next = nextCandidates.find(el => /siguiente|next/i.test(el.innerText || el.textContent || '')) || nextCandidates[0];
          const nextClass = next ? (next.getAttribute('class') || '') : '';
          const hasNext = Boolean(next && !/disabled/i.test(nextClass));
          const firstRow = chosen.querySelector('tbody tr');
          const pageKey = firstRow ? normalize(firstRow.innerText).slice(0, 180) : '';

          return { registros, info, hasNext, found: true, pageKey };
        }
        """
    )


def avanzar_siguiente(page) -> bool:
    try:
        clicked = page.evaluate(
            """
            () => {
              const visible = el => {
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
              };
              const tables = Array.from(document.querySelectorAll('table')).filter(visible);
              const table = tables.find(t => Array.from(t.querySelectorAll('thead th')).some(th => /^\\s*Registro\\s*$/i.test(th.innerText || '')));
              if (!table) return false;
              const wrapper = table.closest('.dataTables_wrapper') || table.parentElement || document;
              const nextCandidates = Array.from(
                wrapper.querySelectorAll('.paginate_button.next, li.next, a.next, button.next')
              ).filter(visible);
              const next = nextCandidates.find(el => /siguiente|next/i.test(el.innerText || el.textContent || '')) || nextCandidates[0];
              if (!next || /disabled/i.test(next.getAttribute('class') || '')) return false;
              const clickable = next.matches('a, button') ? next : next.querySelector('a, button') || next;
              clickable.click();
              return true;
            }
            """
        )
        if clicked:
            esperar_datatables(page, timeout_ms=15_000)
        return bool(clicked)
    except Exception:
        return False


def extraer_registros(page, max_paginas: int = 100) -> list[str]:
    registros: list[str] = []
    vistos: set[str] = set()

    for pagina in range(1, max_paginas + 1):
        estado = leer_estado_tabla(page)
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

            registros = extraer_registros(page, max_paginas=args.max_paginas)
            guardar_registros(registros, args.output, args.separador)
            log.info("[OK] %d registros guardados en %s", len(registros), args.output)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
