#!/usr/bin/env python3
r"""
=============================================================
  PARTE 3 — BÚSQUEDA EN REGISTRO PÚBLICO DE CONCESIONES (RPC)
=============================================================
Busca el operador en el RPC del IFT usando la API REST directa.
NO requiere Playwright ni navegador.

Modos:
  1. API REST directa (searchBP endpoint)
  2. Fuzzy matching contra catálogo local (catalogo_rpc.json)

Uso como módulo:
  from Parte3_rpc import buscar_en_rpc
  resultado = buscar_en_rpc("TELECOMUNICACIÓN Y MERCADOTECNIA DE MONTERREY")

Uso independiente:
  .\python_portable\python.exe Parte3_rpc.py "TELECOMUNICACIÓN Y MERCADOTECNIA DE MONTERREY"
  .\python_portable\python.exe Parte3_rpc.py --rebuild-catalogo
=============================================================
"""

import sys
import io
import os
import re
import json
import time
import string
import logging
import unicodedata
from pathlib import Path
from collections import deque
from difflib import SequenceMatcher
from urllib.parse import quote

# Forzar UTF-8 (solo si no está ya configurado)
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, 'encoding', '') != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import requests
except ImportError:
    print("ERROR: Instala requests con:")
    print("  .\\python_portable\\python.exe -m pip install requests")
    sys.exit(1)

# ════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ════════════════════════════════════════════════════════

# API endpoint del autocompletado RPC (descubierto via DevTools)
AUTOCOMPLETE_URL = "https://rpc.ift.org.mx/vrpc//RpcServicesController/searchBP"
AUTOCOMPLETE_PARAM = "query"

# Catálogo local
CATALOGO_DIR = Path(__file__).parent
CACHE_JSON = CATALOGO_DIR / "catalogo_rpc.json"
CACHE_CSV = CATALOGO_DIR / "catalogo_rpc.csv"

# También buscar en buscar_concesionario/
CACHE_JSON_ALT = CATALOGO_DIR / "buscar_concesionario" / "catalogo_rpc.json"

# Descarga por prefijos
ALPHABET = string.ascii_uppercase + string.digits
BASE_PREFIXES = list(ALPHABET)
MAX_PREFIX_LEN = 5
SLEEP_SECONDS = 0.2

# Umbral mínimo de score para considerar un match válido
SCORE_MINIMO = 0.80

# ════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SATyS-RPC")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "es-MX,es;q=0.9",
    "Referer": "https://rpc.ift.org.mx/vrpc/",
}

LEGAL_SUFFIXES = [
    " s a p i de c v", " s a s de c v", " s a b de c v",
    " s de r l de c v", " s a de c v", " s a p i",
    " s a s", " s a b", " s de r l", " s a", " s c", " a c",
]


# ────────────────────────────────────────────────────────
#  NORMALIZACIÓN Y FUZZY MATCHING
# ────────────────────────────────────────────────────────

def quitar_sufijos_legales(texto: str) -> str:
    """Elimina sufijos legales comunes (S.A. DE C.V., etc.)."""
    cambio = True
    while cambio:
        cambio = False
        for sufijo in LEGAL_SUFFIXES:
            if texto.endswith(sufijo):
                texto = texto[:-len(sufijo)].strip()
                cambio = True
                break
    return texto


def normalizar(texto: str) -> str:
    """Quita acentos, pasa a minúsculas, colapsa espacios, quita sufijos legales."""
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return quitar_sufijos_legales(texto)


def compactar(texto: str) -> str:
    """Elimina espacios de texto ya normalizado."""
    return texto.replace(" ", "")


def score_similitud(a_norm: str, a_compact: str, b_norm: str, b_compact: str) -> float:
    """Combina similitud de secuencia, tokens y compactada."""
    seq = SequenceMatcher(None, a_norm, b_norm).ratio()

    tokens_a = {t for t in a_norm.split() if len(t) > 2}
    tokens_b = {t for t in b_norm.split() if len(t) > 2}
    if tokens_a or tokens_b:
        token_score = len(tokens_a & tokens_b) / max(len(tokens_a | tokens_b), 1)
    else:
        token_score = 0.0

    diff_len = abs(len(a_norm) - len(b_norm)) / max(len(a_norm), len(b_norm), 1)
    penalizacion = diff_len * 0.1

    score = (seq * 0.5) + (token_score * 0.5) - penalizacion

    if a_compact and b_compact:
        compact_score = SequenceMatcher(None, a_compact, b_compact).ratio()
        if compact_score > score:
            score = compact_score
        if a_compact in b_compact or b_compact in a_compact:
            score = max(score, min(1.0, compact_score + 0.08))

    return round(max(score, 0.0), 4)


# ────────────────────────────────────────────────────────
#  DESCARGA Y GESTIÓN DEL CATÁLOGO
# ────────────────────────────────────────────────────────

def _extraer_concesionarios(data) -> list:
    """Parsea la respuesta JSON del endpoint searchBP."""
    if isinstance(data, list):
        if not data:
            return []
        if isinstance(data[0], str):
            return [{"idBp": "", "concesionario": item} for item in data]
        if isinstance(data[0], dict):
            resultado = []
            for item in data:
                nombre = (
                    item.get("concesionario") or item.get("nombre")
                    or item.get("denominacion") or item.get("label")
                    or item.get("value") or item.get("text")
                    or item.get("razon_social")
                )
                idbp = item.get("idBp") or item.get("id") or item.get("id_bp")
                if nombre:
                    resultado.append({
                        "idBp": str(idbp or "").strip(),
                        "concesionario": str(nombre).strip(),
                    })
            return resultado
    if isinstance(data, dict):
        for clave in ("data", "results", "items", "concesionarios", "lista", "nombres"):
            if clave in data:
                return _extraer_concesionarios(data[clave])
    return []


def _fetch_autocomplete(session: requests.Session, query: str) -> list:
    """Petición al endpoint de autocompletado."""
    try:
        resp = session.get(
            AUTOCOMPLETE_URL,
            params={AUTOCOMPLETE_PARAM: query},
            timeout=10,
        )
    except Exception as e:
        log.debug("Error en query '%s': %s", query, e)
        return []
    if resp.status_code != 200:
        return []
    try:
        return _extraer_concesionarios(resp.json())
    except Exception:
        return []


def descargar_catalogo() -> list:
    """Descarga el catálogo completo de concesionarios por prefijos adaptativos."""
    catalogo = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    # Visita inicial para cookies
    try:
        session.get("https://rpc.ift.org.mx/vrpc/", timeout=10)
    except Exception:
        pass

    # Detectar límite
    muestras = ["A", "E", "T", "TE", "TEL"]
    conteos = []
    for pre in muestras:
        items = _fetch_autocomplete(session, pre)
        conteos.append(len(items))
        time.sleep(SLEEP_SECONDS)

    limite = max(conteos) if conteos else 0
    if limite >= 20:
        log.info("📏 Límite detectado: %d por consulta", limite)
    else:
        limite = 0

    cola = deque(BASE_PREFIXES)
    vistos = set(BASE_PREFIXES)

    log.info("📥 Descargando catálogo RPC (%d prefijos base)...", len(BASE_PREFIXES))

    while cola:
        prefijo = cola.popleft()
        items = _fetch_autocomplete(session, prefijo)
        if not items:
            continue

        nuevos = 0
        for item in items:
            nombre = item.get("concesionario", "").strip()
            idbp = item.get("idBp", "").strip()
            if not nombre:
                continue
            key = idbp if idbp else f"NOID:{nombre}"
            if key not in catalogo:
                catalogo[key] = {"idBp": idbp, "concesionario": nombre}
                nuevos += 1

        if nuevos:
            log.info("  %s: +%d (total: %d)", prefijo, nuevos, len(catalogo))

        if limite and len(items) >= limite and len(prefijo) < MAX_PREFIX_LEN:
            for ch in ALPHABET:
                nuevo = prefijo + ch
                if nuevo not in vistos:
                    vistos.add(nuevo)
                    cola.append(nuevo)

        time.sleep(SLEEP_SECONDS)

    catalogo_lista = sorted(catalogo.values(), key=lambda x: x["concesionario"])
    log.info("✅ Catálogo descargado: %d nombres únicos", len(catalogo_lista))
    return catalogo_lista


def guardar_catalogo(catalogo: list) -> None:
    """Guarda el catálogo en JSON y CSV."""
    with open(CACHE_JSON, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=2)
    log.info("💾 Catálogo guardado en %s", CACHE_JSON)

    try:
        import csv
        with open(CACHE_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["idBp", "concesionario"])
            writer.writeheader()
            writer.writerows(catalogo)
    except Exception:
        pass


def cargar_catalogo(force_rebuild: bool = False) -> list:
    """Carga el catálogo desde cache o lo descarga."""
    if not force_rebuild:
        # Intentar cargar desde cache
        for path in [CACHE_JSON, CACHE_JSON_ALT]:
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        catalogo = json.load(f)
                    log.info("🗂️  Catálogo cargado: %s (%d registros)", path.name, len(catalogo))
                    return catalogo
                except Exception:
                    continue

    # Descargar nuevo catálogo
    log.info("🔄 Descargando catálogo nuevo del RPC...")
    catalogo = descargar_catalogo()
    if catalogo:
        guardar_catalogo(catalogo)
    return catalogo


def preparar_catalogo(catalogo: list) -> list:
    """Pre-computa normalizaciones para matching rápido."""
    preparado = []
    for item in catalogo:
        nombre = item.get("concesionario", "").strip()
        if not nombre:
            continue
        norm = normalizar(nombre)
        preparado.append({
            "idBp": item.get("idBp", "").strip(),
            "concesionario": nombre,
            "norm": norm,
            "compact": compactar(norm),
        })
    return preparado


# ────────────────────────────────────────────────────────
#  CONSTRUCCIÓN DE RUTA
# ────────────────────────────────────────────────────────

def construir_ruta(nombre: str, id_bp: str) -> str:
    r"""
    Construye la ruta estandarizada para el Excel.
    Formato: <idBp>_<nombre_limpio>\01 EN\VE
    """
    nombre_limpio = nombre.lower()
    nombre_limpio = re.sub(r'[^a-záéíóúñ0-9\s]', ' ', nombre_limpio)
    nombre_limpio = re.sub(r'\s+', '_', nombre_limpio.strip())
    return f"{id_bp}_{nombre_limpio}\\01 EN\\VE"


# ────────────────────────────────────────────────────────
#  FUNCIÓN PRINCIPAL
# ────────────────────────────────────────────────────────

def buscar_en_rpc(nombre_operador: str, catalogo: list | None = None, force_rebuild: bool = False) -> dict | None:
    """
    Busca un operador en el RPC.

    Retorna dict con:
      - nombre_completo: str
      - folio_rpc: str (si disponible) — requiere Playwright para obtenerlo
      - numero_rpc: str (idBp del catálogo)
      - idBp: str
      - ruta: str
      - score: float
      - ok: bool
    O None si no se encuentra.
    """
    log.info("🌐 Buscando en RPC: %s", nombre_operador[:80])

    # ── Paso 1: Búsqueda directa por API ──
    session = requests.Session()
    session.headers.update(HEADERS)

    # Visita inicial para cookies
    try:
        session.get("https://rpc.ift.org.mx/vrpc/", timeout=10)
    except Exception:
        pass

    # Intentar búsqueda directa con primeras palabras
    tokens = nombre_operador.split()
    for n_tokens in [len(tokens), min(5, len(tokens)), min(3, len(tokens))]:
        query = " ".join(tokens[:n_tokens])
        items = _fetch_autocomplete(session, query)
        if items:
            log.info("📡 API retornó %d resultados para '%s'", len(items), query[:50])
            # Buscar match directo
            query_norm = normalizar(nombre_operador)
            query_compact = compactar(query_norm)

            best_score = 0.0
            best_item = None

            for item in items:
                nombre = item.get("concesionario", "")
                b_norm = normalizar(nombre)
                b_compact = compactar(b_norm)
                s = score_similitud(query_norm, query_compact, b_norm, b_compact)
                if s > best_score:
                    best_score = s
                    best_item = item

            if best_item and best_score >= SCORE_MINIMO:
                id_bp = best_item.get("idBp", "")
                nombre_completo = best_item["concesionario"]
                ruta = construir_ruta(nombre_completo, id_bp)

                log.info("✅ Match directo (API): %.0f%% → %s", best_score * 100, nombre_completo[:70])
                return {
                    "nombre_completo": nombre_completo,
                    "folio_rpc": "",
                    "numero_rpc": id_bp,
                    "idBp": id_bp,
                    "ruta": ruta,
                    "score": best_score,
                    "ok": True,
                }
            break  # Si hubo resultados pero ninguno bueno, pasar al catálogo

    # ── Paso 2: Fuzzy matching contra catálogo local ──
    log.info("🔍 Buscando en catálogo local...")

    if catalogo is None:
        catalogo = cargar_catalogo(force_rebuild=force_rebuild)

    if not catalogo:
        log.error("❌ No hay catálogo disponible")
        return None

    catalogo_prep = preparar_catalogo(catalogo)

    a_norm = normalizar(nombre_operador)
    a_compact = compactar(a_norm)

    resultados = []
    for item in catalogo_prep:
        s = score_similitud(a_norm, a_compact, item["norm"], item["compact"])
        resultados.append((s, item))

    resultados.sort(key=lambda x: x[0], reverse=True)
    top = resultados[:5]

    # Log top 3
    for i, (s, item) in enumerate(top[:3], 1):
        icono = "✅" if s >= 0.85 else ("⚠️" if s >= 0.80 else "❌")
        log.info("  %d. %s %.0f%% → %s", i, icono, s * 100, item["concesionario"][:60])

    best_score, best_item = top[0] if top else (0.0, None)

    if best_item and best_score >= SCORE_MINIMO:
        id_bp = best_item.get("idBp", "")
        nombre_completo = best_item["concesionario"]
        ruta = construir_ruta(nombre_completo, id_bp)

        log.info("✅ Mejor match (catálogo): %.0f%% → %s", best_score * 100, nombre_completo[:70])
        return {
            "nombre_completo": nombre_completo,
            "folio_rpc": "",
            "numero_rpc": id_bp,
            "idBp": id_bp,
            "ruta": ruta,
            "score": best_score,
            "ok": True,
        }

    log.warning("⚠️  Sin match suficiente (mejor: %.0f%%)", best_score * 100)
    return None


# ────────────────────────────────────────────────────────
#  CLI
# ────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python Parte3_rpc.py <nombre_operador>")
        print("  Ejemplo: python Parte3_rpc.py \"TELECOMUNICACIÓN Y MERCADOTECNIA DE MONTERREY\"")
        print("  Flags:   --rebuild-catalogo  (reconstruir catálogo)")
        sys.exit(1)

    rebuild = "--rebuild-catalogo" in sys.argv or "--rebuild" in sys.argv
    nombres = [a for a in sys.argv[1:] if not a.startswith("--")]

    if rebuild:
        log.info("🔄 Reconstruyendo catálogo RPC...")
        catalogo = descargar_catalogo()
        if catalogo:
            guardar_catalogo(catalogo)
        if not nombres:
            sys.exit(0)

    for nombre in nombres:
        print(f"\n{'=' * 60}")
        print(f"  Buscando: {nombre}")
        print(f"{'=' * 60}")

        resultado = buscar_en_rpc(nombre, force_rebuild=False)

        if resultado:
            print(json.dumps(resultado, ensure_ascii=False, indent=2))
        else:
            print("❌ No se encontró coincidencia suficiente")
