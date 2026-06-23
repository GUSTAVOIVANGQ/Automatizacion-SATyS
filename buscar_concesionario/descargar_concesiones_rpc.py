#!/usr/bin/env python3
"""
descargar_concesiones_rpc.py
────────────────────────────
Descarga automática de la base de datos más reciente de concesiones
del Registro Público de Concesiones (RPC-IFT).

Fuente: https://rpc.ift.org.mx/vrpc/visor/downloads
Archivo: 03_concesiones_permisos_autorizaciones_DDMMYY.xlsx

Estrategias (en orden):
  1. Scraping de la página de descargas (BS4 + regex).
  2. Búsqueda por fecha con HEAD → fallback a GET Range:bytes=0-0.
     Prueba desde hoy hacia atrás MAX_DAYS_BACK días.

Uso:
    python descargar_concesiones_rpc.py                  # guarda en carpeta actual
    python descargar_concesiones_rpc.py C:/Descargas     # ruta personalizada

Dependencias:
    pip install requests beautifulsoup4
"""

import os
import re
import sys
import glob
import requests
from datetime import datetime, timedelta

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# ── Configuración ────────────────────────────────────────────────────────────

BASE_URL        = "https://rpc.ift.org.mx"
DOWNLOADS_PAGE  = f"{BASE_URL}/vrpc/visor/downloads"
ASSET_BASE      = f"{BASE_URL}/vrpc/assets/publish/uploads/concesiones/"
FILENAME_PREFIX = "03_concesiones_permisos_autorizaciones_"
FILENAME_SUFFIX = ".xlsx"

# Días hacia atrás que busca si el scraping falla
MAX_DAYS_BACK   = 90

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Referer": BASE_URL,
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def build_url(fecha_ddmmyy: str) -> str:
    return f"{ASSET_BASE}{FILENAME_PREFIX}{fecha_ddmmyy}{FILENAME_SUFFIX}"


def make_session() -> requests.Session:
    """Crea sesión con headers de navegador real."""
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def warm_up_session(session: requests.Session) -> None:
    """
    Carga la página principal para obtener cookies de sesión.
    El portal puede bloquear peticiones sin cookie válida.
    """
    try:
        for url in [BASE_URL, DOWNLOADS_PAGE]:
            r = session.get(url, timeout=15)
            if r.ok:
                print(f"      ✓ Sesión inicializada ({url.replace(BASE_URL, '')})")
                return
    except requests.RequestException as e:
        print(f"      ⚠  Warm-up falló: {e} (se continúa de todos modos)")


def file_exists_at_url(session: requests.Session, url: str) -> bool:
    """
    Verifica existencia del archivo sin descargarlo.
    Intenta HEAD primero; si falla, usa GET con Range: bytes=0-0.
    """
    # ── Intento 1: HEAD ──────────────────────────────────────────────────────
    try:
        r = session.head(url, timeout=10, allow_redirects=True,
                         headers={"Referer": DOWNLOADS_PAGE})
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            return False
        # 405 Method Not Allowed → el servidor no soporta HEAD
    except requests.RequestException:
        pass

    # ── Intento 2: GET Range ─────────────────────────────────────────────────
    try:
        r = session.get(
            url, timeout=10, stream=True, allow_redirects=True,
            headers={"Range": "bytes=0-0", "Referer": DOWNLOADS_PAGE},
        )
        r.close()
        return r.status_code in (200, 206)
    except requests.RequestException:
        return False


def _print_bar(done: int, total: int, width: int = 45) -> None:
    filled  = int(width * done / total) if total else 0
    bar     = "█" * filled + "░" * (width - filled)
    pct     = done / total * 100 if total else 0
    done_kb = done  / 1_024
    tot_kb  = total / 1_024
    print(f"\r   [{bar}] {pct:5.1f}%  {done_kb:>8,.1f} / {tot_kb:>8,.1f} KB",
          end="", flush=True)


# ── Estrategia 1: scraping de la página de descargas ─────────────────────────

def find_url_from_page(session: requests.Session) -> str | None:
    print(f"\n{'─'*60}")
    print("[1/2] Consultando página de descargas (scraping)…")
    print(f"      {DOWNLOADS_PAGE}")
    print(f"{'─'*60}")

    try:
        r = session.get(DOWNLOADS_PAGE, timeout=30,
                        headers={"Referer": BASE_URL})
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"      ⚠  No se pudo acceder: {e}")
        return None

    html = r.text

    # ── a) BeautifulSoup: <a href="...concesiones*.xlsx"> ────────────────────
    if BS4_AVAILABLE:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if re.search(rf"{re.escape(FILENAME_PREFIX)}\d{{6}}{re.escape(FILENAME_SUFFIX)}$",
                         href, re.I):
                url = href if href.startswith("http") else BASE_URL + href
                print(f"      ✓ Enlace encontrado (BeautifulSoup): {url}")
                return url

    # ── b) Regex sobre HTML raw ──────────────────────────────────────────────
    m = re.search(
        rf'["\']({re.escape(ASSET_BASE)}{re.escape(FILENAME_PREFIX)}\d{{6}}{re.escape(FILENAME_SUFFIX)})["\']',
        html, re.I,
    )
    if m:
        print(f"      ✓ Enlace encontrado (regex): {m.group(1)}")
        return m.group(1)

    print("      ℹ  No se encontró enlace directo (página puede estar renderizada con JS).")
    return None


# ── Estrategia 2: búsqueda por fecha ─────────────────────────────────────────

def find_url_by_date(session: requests.Session) -> str | None:
    print(f"\n{'─'*60}")
    print(f"[2/2] Buscando archivo por fecha (últimos {MAX_DAYS_BACK} días)…")
    print( "      Formato en nombre de archivo: DDMMYY  (ej. 250526 = 25 may 2026)")
    print(f"{'─'*60}")

    today = datetime.today()

    for days_ago in range(0, MAX_DAYS_BACK):
        fecha  = today - timedelta(days=days_ago)
        ddmmyy = fecha.strftime("%d%m%y")        # 25/05/2026 → "250526"
        url    = build_url(ddmmyy)

        print(f"\r      Probando {fecha.strftime('%d/%m/%Y')} [{ddmmyy}]…", end="", flush=True)

        if file_exists_at_url(session, url):
            print(f"\r      ✓ Encontrado: {fecha.strftime('%d/%m/%Y')} [{ddmmyy}]")
            print(f"        {url}")
            return url

    print("\r      ✗ No se encontró el archivo en el rango de fechas buscado.            ")
    return None


# ── Descarga ──────────────────────────────────────────────────────────────────

def download_file(session: requests.Session, url: str, output_dir: str) -> str | None:
    filename    = url.split("/")[-1]
    output_path = os.path.join(output_dir, filename)

    print(f"\n{'═'*60}")
    print( "  Descargando archivo…")
    print(f"  URL  : {url}")
    print(f"  Dest : {output_path}")
    print(f"{'═'*60}")

    try:
        r = session.get(url, stream=True, timeout=180,
                        headers={"Referer": DOWNLOADS_PAGE})
        r.raise_for_status()

        total = int(r.headers.get("Content-Length", 0))
        done  = 0

        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65_536):   # 64 KB
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        _print_bar(done, total)

        print()   # nueva línea tras la barra

        final_size = os.path.getsize(output_path)
        print(f"\n✅ Descarga completada.")
        print(f"   Archivo : {output_path}")
        print(f"   Tamaño  : {final_size / 1_024:,.1f} KB  ({final_size:,} bytes)")
        return output_path

    except requests.RequestException as e:
        print(f"\n✗ Error durante la descarga: {e}")
        if os.path.exists(output_path):
            os.remove(output_path)   # eliminar archivo parcial
        return None


def cleanup_old_files(output_dir: str, current_file_path: str) -> None:
    print(f"\n{'═'*60}")
    print("  Limpiando archivos antiguos...")
    pattern = os.path.join(output_dir, f"{FILENAME_PREFIX}*{FILENAME_SUFFIX}")
    for filepath in glob.glob(pattern):
        if os.path.abspath(filepath) != os.path.abspath(current_file_path):
            try:
                os.remove(filepath)
                print(f"   🗑️  Eliminado: {os.path.basename(filepath)}")
            except Exception as e:
                print(f"   ⚠  No se pudo eliminar {os.path.basename(filepath)}: {e}")
    print(f"{'═'*60}")

# ── Punto de entrada ──────────────────────────────────────────────────────────

def descargar_bd(output_dir: str = ".") -> str | None:
    os.makedirs(output_dir, exist_ok=True)

    print("╔══════════════════════════════════════════════════════════╗")
    print("║    Descargador RPC-IFT — Base de datos de Concesiones   ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"  Directorio de salida : {os.path.abspath(output_dir)}")
    print(f"  Fecha de búsqueda    : {datetime.today().strftime('%d/%m/%Y')}")

    if not BS4_AVAILABLE:
        print("\n  ⚠  beautifulsoup4 no instalado — scraping usará solo regex.")
        print("     pip install beautifulsoup4")

    session = make_session()

    # Calentar sesión (cookies)
    print("\n  → Inicializando sesión con el portal…")
    warm_up_session(session)

    # Estrategia 1
    url = find_url_from_page(session)

    # Estrategia 2 (fallback)
    if not url:
        url = find_url_by_date(session)

    if not url:
        print("\n✗ No se pudo determinar la URL del archivo.")
        print("  Verifica tu conexión a Internet o visita manualmente:")
        print(f"  {DOWNLOADS_PAGE}")
        return None

    result = download_file(session, url, output_dir)
    if result:
        cleanup_old_files(output_dir, result)
        
    return result

def main() -> None:
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    result = descargar_bd(output_dir)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
