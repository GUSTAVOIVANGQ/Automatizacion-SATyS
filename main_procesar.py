#!/usr/bin/env python3
r"""
=============================================================
  PROYECTO SATyS — ORQUESTADOR PRINCIPAL
=============================================================
Pipeline de producción:

  Parte 1 → Descarga automática desde SATyS (Playwright)
  Parte 3 → Búsqueda segura por ID/nombre exacto en RPC
  Parte 4 → Actualización de Excel y organización de archivos

La Parte 2 no forma parte del pipeline. Los datos se leen directamente de
metadata_satys.json y metadata_tramite_nuevo.json generados por Parte 1.
=============================================================
"""

import sys
import io
import os
import re
import csv
import json
import logging
import argparse
import shutil
import signal
from pathlib import Path
from datetime import datetime

# Forzar UTF-8 en consola Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, 'encoding', '') != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Asegurar que el directorio del script esté en sys.path
_script_dir = str(Path(__file__).resolve().parent)
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)


# ╔══════════════════════════════════════════════════════════════╗
# ║                    CONFIGURACIÓN GENERAL                     ║
# ╚══════════════════════════════════════════════════════════════╝

from configuracion_local import (
    carpeta_compartida,
    configuracion_procesamiento,
    credenciales_satys,
    ruta_configurada,
)
from estado_descargas import (
    auditar_carpeta_descarga,
    carpeta_tiene_descarga_real,
    depurar_json_output,
    iter_archivos_publicables_output,
    registro_esta_completo,
    slug_bandeja_internos,
)
from sincronizacion_depi import sincronizar_salidas
from rutas_salida import (
    carpeta_sin_operador,
    destino_sin_operador,
    es_folio_opc_correo,
    ruta_relativa_sin_operador,
)

DESCARGA_BASE = ruta_configurada("descargas", "descargas")
OUTPUT_BASE = ruta_configurada("output", "output")
EXCEL_PATH = ruta_configurada("excel", "TrámitesCRT.xlsx")
LOGS_BASE = ruta_configurada("logs", "logs")
CARPETA_COMPARTIDA = carpeta_compartida()

SATYS_USUARIO, SATYS_PASSWORD = credenciales_satys()
HEADLESS = False
FOLIOS_DEFAULT = ["6407", "6801", "6802"]
ORGANIZAR_DESCARGAS = True

_PROCESAMIENTO_CFG = configuracion_procesamiento()
WORKERS_DEFAULT = int(_PROCESAMIENTO_CFG.get("workers", 10))
INTERNOS_WORKERS_DEFAULT = int(_PROCESAMIENTO_CFG.get("internos_workers", 12))
TIMEOUT_REGISTRO_DEFAULT = int(_PROCESAMIENTO_CFG.get("timeout_registro", 900))
REINTENTOS_REGISTRO_DEFAULT = int(_PROCESAMIENTO_CFG.get("reintentos_registro", 2))
WORKERS_REINTENTO_DEFAULT = int(_PROCESAMIENTO_CFG.get("workers_reintento", 2))

# ──── Imports de los módulos ────
from Parte3_rpc import cargar_catalogo
from Parte4_excel import (
    actualizar_excel,
    arbol_publicable_copiado_completo,
    copiar_archivos_publicables_output,
    copiar_archivo_robusto,
    consolidar_todas_carpetas_operadores,
    eliminar_arbol_robusto,
    organizar_archivos,
    organizar_correo_exclusivo,
    obtener_nota_victor,
)
from proceso_lock import ProcesoLock, LockOcupadoError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SATyS-Main")


REGISTRO_RE = re.compile(r"\b[A-Z]{2,6}\d{2}-\d{3,}\b", re.IGNORECASE)

def normalizar_registro_satys(valor: str) -> str:
    """Normaliza un número de Registro SATyS, por ejemplo CRT26-027838."""
    valor = (valor or "").strip().upper()
    valor = re.sub(r"\s+", "", valor)
    m = REGISTRO_RE.search(valor)
    return m.group(0).upper() if m else valor

def cargar_registros_desde_archivo(path: str | Path) -> list[str]:
    """
    Lee registros desde TXT aceptando saltos de línea, espacios, comas o punto y coma.
    Esto evita que un archivo con todos los registros en una sola línea se lea como 1 solo registro.
    """
    path = Path(path)
    texto = path.read_text(encoding="utf-8-sig", errors="replace")
    candidatos = REGISTRO_RE.findall(texto)
    if not candidatos:
        # Fallback: separar por cualquier whitespace/coma/punto y coma.
        candidatos = re.split(r"[\s,;]+", texto)

    registros: list[str] = []
    vistos: set[str] = set()
    for candidato in candidatos:
        registro = normalizar_registro_satys(candidato)
        if registro and registro not in vistos and REGISTRO_RE.fullmatch(registro):
            vistos.add(registro)
            registros.append(registro)
    return registros


def cargar_folios_internos_desde_archivo(path: str | Path) -> list[str]:
    """Lee folios numéricos de Internos desde CSV o TXT, conservando el orden."""
    ruta = Path(path)
    folios: list[str] = []
    vistos: set[str] = set()
    with ruta.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
        for fila in csv.reader(stream):
            for celda in fila:
                valor = str(celda or "").strip()
                if re.fullmatch(r"\d+\.0+", valor):
                    valor = valor.split(".", 1)[0]
                if not re.fullmatch(r"\d{3,}", valor) or valor in vistos:
                    continue
                vistos.add(valor)
                folios.append(valor)
    return folios


def extraer_nombre_operador_texto_fila(*metadatos: dict) -> str:
    """Recupera el concesionario de la sexta columna tabulada de Internos."""
    for metadata in metadatos:
        if not isinstance(metadata, dict):
            continue
        texto = str(metadata.get("texto_fila") or "")
        if not texto:
            continue
        # Algunos exportadores dejaron los dos caracteres ``\\t`` en vez de
        # tabuladores reales; se admiten ambos formatos.
        if "\t" not in texto and "\\t" in texto:
            texto = texto.replace("\\t", "\t")
        columnas = [re.sub(r"\s+", " ", valor).strip() for valor in texto.split("\t")]
        if len(columnas) < 6:
            continue
        candidato = columnas[5].strip(" ,;")
        if (
            candidato
            and candidato not in {"-", "N/A", "NA"}
            and not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", candidato)
        ):
            return candidato
    return ""


def es_registro_pendiente(registro: str) -> bool:
    """La única fuente de verdad es descargas/<REGISTRO>/."""
    return not registro_esta_completo(DESCARGA_BASE, registro)


def filtrar_registros_pendientes(registros: list[str]) -> tuple[list[str], list[str]]:
    """
    Filtra la lista de registros y separa los pendientes de los ya completos.

    Retorna: (pendientes, completos)
      - pendientes: lista de registros que necesitan descarga/reprocesamiento.
      - completos:  lista de registros que ya tienen descarga correcta.
    """
    pendientes = []
    completos = []
    for registro in registros:
        auditoria = auditar_carpeta_descarga(DESCARGA_BASE / registro)
        if not auditoria["completo"]:
            pendientes.append(registro)
            log.info(
                "📥 Registro %s pendiente: %s",
                registro,
                ", ".join(auditoria["motivos"]) or "auditoría incompleta",
            )
        else:
            completos.append(registro)
    return pendientes, completos



# ────────────────────────────────────────────────────────
#  DESCUBRIMIENTO DE DESCARGAS LOCALES PARA PARTES 3-4
# ────────────────────────────────────────────────────────

def carpeta_tiene_archivos_reales(carpeta: Path) -> bool:
    """Compatibilidad interna con la regla única de descargas reales."""
    return carpeta_tiene_descarga_real(carpeta)


def leer_metadata_descarga(carpeta: Path) -> dict:
    """Lee metadata_satys.json y metadata_tramite_nuevo.json si existen."""
    data: dict = {}
    for nombre in ("metadata_satys.json", "metadata_tramite_nuevo.json"):
        path = carpeta / nombre
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                contenido = json.load(f)
            if isinstance(contenido, dict):
                data.update(contenido)
        except Exception as e:
            log.warning("⚠️  No se pudo leer metadata %s: %s", path, e)
    if not (data.get("nombre_operador") or data.get("concesionario")):
        recuperado = extraer_nombre_operador_texto_fila(data)
        if recuperado:
            data["nombre_operador"] = recuperado
            data["concesionario"] = recuperado
            data["fuente_nombre_operador"] = "texto_fila"
    return data


def folio_excel_desde_metadata(carpeta: Path, fallback: str) -> str:
    """
    Determina el folio que debe escribirse en Excel para una carpeta local.
    Prioridad:
      1) metadata['folio_tabla_internos'] (folio real de la bandeja Internos)
      2) metadata['folio']
      3) número extraído de metadata['folio_opc']
      4) metadata['memo_folio_opc']
      5) nombre de carpeta / fallback
    """
    meta = leer_metadata_descarga(carpeta)
    folio_directo = meta.get("folio_tabla_internos") or meta.get("folio")
    if not folio_directo:
        folio_opc = meta.get("folio_opc", "") or ""
        if folio_opc:
            numeros = re.sub(r"[^0-9]", "", str(folio_opc))
            folio_directo = numeros if numeros else None
    return str(folio_directo or meta.get("memo_folio_opc") or fallback).strip()


def registro_desde_metadata_o_nombre(carpeta: Path) -> str:
    """Devuelve el Registro CRT si está en metadata o en el nombre de carpeta."""
    meta = leer_metadata_descarga(carpeta)
    for key in ("registro", "numero_registro", "1711"):
        valor = normalizar_registro_satys(meta.get(key, ""))
        if valor:
            return valor
    return normalizar_registro_satys(carpeta.name)


def descubrir_descargas_procesables(incluir_subcarpetas: bool = True) -> list[tuple[Path, str, str]]:
    """
    Escanea descargas/ y devuelve TODAS las carpetas procesables.

    Retorna tuplas:
      (carpeta_fisica, folio_id_para_output, registro_detectado_o_nombre)

    Esto permite que, al terminar Parte1, Partes 3-4 trabajen sobre el
    estado real de descargas/ y no solo sobre la lista que se acaba
    de bajar en esta ejecución.
    """
    if not DESCARGA_BASE.exists():
        return []

    candidatos: list[tuple[Path, str, str]] = []
    vistos: set[str] = set()

    def tiene_metadata(carpeta: Path) -> bool:
        return any((carpeta / nombre).exists() for nombre in ("metadata_satys.json", "metadata_tramite_nuevo.json"))

    def agregar(carpeta: Path, folio_id: str) -> None:
        try:
            key = str(carpeta.resolve()).lower()
        except Exception:
            key = str(carpeta).lower()
        if key in vistos or not carpeta.is_dir():
            return

        # Una carpeta existente con identidad de Registro entra a Partes 3-4
        # aunque esté vacía o contenga sólo metadata. Así se conserva en Excel
        # y reportes mientras la descarga estricta continúa pendiente.
        registro_ref = registro_desde_metadata_o_nombre(carpeta)
        if not registro_ref or not REGISTRO_RE.fullmatch(registro_ref):
            log.debug("Omitiendo subcarpeta sin Registro CRT válido: %s", carpeta)
            return

        vistos.add(key)
        candidatos.append((carpeta, folio_id, registro_ref))

    for carpeta in sorted([p for p in DESCARGA_BASE.iterdir() if p.is_dir()], key=lambda p: p.name.upper()):
        # Caso normal: descargas/<REGISTRO>/archivos... El JSON vive en la raíz;
        # sus subdirectorios son contenido de ZIP y NO deben reprocesarse.
        agregar(carpeta, carpeta.name)

        # Caso especial heredado: un contenedor sin metadata puede tener una
        # subcarpeta por Registro. Solo se aceptan subcarpetas con metadata propia.
        if incluir_subcarpetas and not tiene_metadata(carpeta):
            for sub in sorted([p for p in carpeta.iterdir() if p.is_dir()], key=lambda p: p.name.upper()):
                if tiene_metadata(sub):
                    agregar(sub, f"{carpeta.name}__{sub.name}")

    return candidatos



# ────────────────────────────────────────────────────────
#  PARTE 1: Descarga (importa Parte1_descarga.py)
# ────────────────────────────────────────────────────────

def descubrir_descargas_internos() -> list[tuple[Path, str, str]]:
    """Devuelve todo expediente Internos existente, incluso vacío o parcial."""
    base = DESCARGA_BASE / "internos"
    if not base.exists():
        return []

    candidatos: list[tuple[Path, str, str]] = []
    vistos: set[str] = set()
    for carpeta in sorted([p for p in base.rglob("*") if p.is_dir()], key=lambda p: str(p).upper()):
        tiene_metadata = any(
            (carpeta / nombre).exists()
            for nombre in ("metadata_satys.json", "metadata_tramite_nuevo.json")
        )
        try:
            profundidad = len(carpeta.relative_to(base).parts)
        except ValueError:
            continue
        identificador_carpeta = str(carpeta.name).strip()
        parece_expediente = bool(
            re.fullmatch(r"\d{3,}(?:_\d+)?", identificador_carpeta)
            or REGISTRO_RE.fullmatch(normalizar_registro_satys(identificador_carpeta))
        )
        # Sin metadata sólo se admiten las ubicaciones conocidas
        # internos/<folio> e internos/<bandeja>/<folio>; una carpeta más
        # profunda puede ser contenido extraído de un ZIP.
        if not tiene_metadata and (not parece_expediente or profundidad > 2):
            continue
        meta = leer_metadata_descarga(carpeta)
        try:
            key = str(carpeta.resolve()).lower()
        except Exception:
            key = str(carpeta).lower()
        if key in vistos:
            continue
        vistos.add(key)

        registro_ref = (
            normalizar_registro_satys(meta.get("registro", ""))
            or str(meta.get("folio") or carpeta.name).strip()
        )
        bandeja_default = "Sin bandeja" if carpeta.parent == base else carpeta.parent.name
        bandeja = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            str(meta.get("bandeja_internos") or bandeja_default),
        ).strip("_")
        folio_id = f"internos__{bandeja or 'bandeja'}__{carpeta.name}"
        candidatos.append((carpeta, folio_id, registro_ref))
    return candidatos


def cargar_objetivos_internos(path: str | Path) -> list[dict]:
    """Load and validate the daily [{bandeja, folio}] Internos target list."""
    archivo = Path(path)
    data = json.loads(archivo.read_text(encoding="utf-8-sig"))
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


def cargar_catalogo_rpc_exacto(force_rebuild: bool = False) -> list:
    """Carga el catalogo oficial RPC desde Excel para cruces exactos."""
    log.info("🗂️  Cargando catálogo RPC exacto desde Excel oficial...")
    try:
        sys.path.append(os.path.join(str(_script_dir), "buscar_concesionario"))
        import buscar_concesionario as bc
        from descargar_concesiones_rpc import descargar_bd

        bd_dir = Path(_script_dir) / "base_de_datos_rpc"
        bd_dir.mkdir(exist_ok=True)

        def _cat_reciente(bd: Path):
            archivos = sorted(
                bd.glob("03_concesiones_permisos_autorizaciones_*.xlsx"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            return archivos[0] if archivos else None

        # El portal ha publicado archivos dañados en ocasiones. Para que una
        # corrida reproducible no sustituya un catálogo local que sí abre, sólo
        # se descarga cuando no existe ninguno o el usuario lo pide con
        # --rebuild-catalogo. La actualidad se cubre con el RPC en línea.
        xlsx = _cat_reciente(bd_dir)
        if force_rebuild or xlsx is None:
            log.info("⬇️  Descargando la base RPC por solicitud o ausencia local...")
            descargado = descargar_bd(str(bd_dir))
            if descargado:
                xlsx = Path(descargado)
        if not xlsx or not xlsx.exists():
            raise FileNotFoundError("No se encontró Excel oficial RPC en base_de_datos_rpc")

        cat_excel = bc.cargar_catalogo_desde_excel(str(xlsx), "copeau", solo_vigentes=False)
        catalogo = bc.preparar_catalogo_para_matching(cat_excel)
        log.info("✅ Catálogo RPC exacto listo: %d concesionarios", len(catalogo))
        return catalogo
    except Exception as exc:
        log.error(
            "❌ Catálogo Excel RPC no disponible: %s. "
            "Los registros con nombre usarán el RPC en línea exacto.",
            exc,
        )
        return []


def ejecutar_descarga(folios: list[str], workers: int = WORKERS_DEFAULT, headless: bool = False):
    """Ejecuta Parte 1; sus reintentos por archivo permanecen dentro del descargador."""
    try:
        import Parte1_descarga
    except ImportError as e:
        log.error("❌ No se encontró Parte1_descarga.py: %s", e)
        return False

    log.info("📥 [PARTE 1] Iniciando descarga automática...")
    log.info("📋 Folios a descargar: %s", ", ".join(folios))

    Parte1_descarga.USUARIO = SATYS_USUARIO
    Parte1_descarga.PASSWORD = SATYS_PASSWORD
    Parte1_descarga.HEADLESS = headless
    Parte1_descarga.FOLIOS_DEFAULT = folios
    Parte1_descarga.DESCARGA_BASE = DESCARGA_BASE

    headless_flag = ["--headless"] if headless else ["--visible"]
    original_argv = sys.argv
    try:
        sys.argv = ["Parte1_descarga.py"] + headless_flag + ["--workers", str(workers), "--folios"] + list(folios)
        Parte1_descarga.main()
    except Exception as e:
        log.error("❌ Error en descarga: %s", e)
        return False
    finally:
        sys.argv = original_argv

    log.info("✅ Descarga completada; reintentos por archivo manejados internamente.")
    return True


# ────────────────────────────────────────────────────────
#  PARTES 3-4: Procesamiento
# ────────────────────────────────────────────────────────

def ejecutar_descarga_internos(
    bandejas: list[str] | None = None,
    headless: bool = False,
    objetivos_path: str | Path | None = None,
    workers: int = INTERNOS_WORKERS_DEFAULT,
    timeout_registro: int = TIMEOUT_REGISTRO_DEFAULT,
    reintentos_registro: int = REINTENTOS_REGISTRO_DEFAULT,
) -> int:
    """Ejecuta Parte 1 en modo Internos IFT y devuelve el codigo de salida."""
    try:
        import Parte1_descarga
    except ImportError as e:
        log.error("❌ No se encontró Parte1_descarga.py: %s", e)
        return 1

    log.info("📥 [PARTE 1] Iniciando descarga de Internos IFT...")
    Parte1_descarga.USUARIO = SATYS_USUARIO
    Parte1_descarga.PASSWORD = SATYS_PASSWORD
    Parte1_descarga.HEADLESS = headless
    Parte1_descarga.DESCARGA_BASE = DESCARGA_BASE

    headless_flag = ["--headless"] if headless else ["--visible"]
    original_argv = sys.argv
    try:
        sys.argv = (
            ["Parte1_descarga.py"]
            + headless_flag
            + [
                "--internos",
                "--internos-workers", str(workers),
                "--timeout-registro", str(timeout_registro),
                "--reintentos-registro", str(reintentos_registro),
            ]
        )
        if bandejas:
            sys.argv += ["--internos-bandejas"] + list(bandejas)
        if objetivos_path:
            sys.argv += ["--internos-objetivos", str(objetivos_path)]
        return int(Parte1_descarga.main() or 0)
    except Exception as e:
        log.error("❌ Error en descarga de Internos IFT: %s", e)
        return 1
    finally:
        sys.argv = original_argv


def descubrir_carpetas_de_folio(folio: str) -> list[tuple[Path, str]]:
    """
    Descubre TODAS las carpetas de descarga que corresponden a un folio.

    Normalmente un folio = una carpeta (descargas/<folio>/). Pero cuando
    Parte1_descarga.py encuentra varios tramites/registros distintos para
    el mismo folio (ej. folio 1660 con registros CRT26-020606 y
    CRT26-002483), crea carpetas adicionales:

        descargas/<folio>/                  (1er registro -- sin cambios)
        descargas/<folio>_1/<registro>/     (2do registro)
        descargas/<folio>_2/<registro>/     (3er registro)
        ...

    Devuelve una lista de (carpeta, folio_id). 'folio_id' es solo un
    identificador interno para nombrar las rutas de SALIDA (output/,
    _sin_operador/) y que no se mezclen entre tramites -- es "<folio>"
    para el primero y "<folio>_<n>" para los adicionales. El folio
    "real" que se escribe en el Excel siempre es el mismo (folio).
    """
    carpetas = []

    carpeta_base = DESCARGA_BASE / folio
    if carpeta_base.exists():
        carpetas.append((carpeta_base, folio))

    n = 1
    while True:
        carpeta_extra = DESCARGA_BASE / f"{folio}_{n}"
        if not carpeta_extra.exists():
            break
        subcarpetas = [p for p in carpeta_extra.iterdir() if p.is_dir()]
        if subcarpetas:
            # Cada carpeta_extra contiene UNA subcarpeta nombrada por el registro
            for sub in subcarpetas:
                carpetas.append((sub, f"{folio}_{n}"))
        else:
            carpetas.append((carpeta_extra, f"{folio}_{n}"))
        n += 1

    return carpetas


def limpiar_revision_manual_resuelta(
    revision: Path,
    destino: Path,
    carpeta_descarga: Path | None = None,
) -> bool:
    """Fusiona y retira una copia vieja de ``sin_operador`` ya resuelta.

    Si existe una colisión de nombre, la fuente vigente de ``descargas`` se
    vuelve a copiar al final. La rutina nunca mueve ni elimina esa fuente.
    """
    if not revision.exists() or not revision.is_dir():
        return False

    objetivo = revision.resolve()
    raices_permitidas = {
        (OUTPUT_BASE / carpeta_sin_operador("")).resolve(),
        (OUTPUT_BASE / carpeta_sin_operador("CORREO")).resolve(),
    }
    if not any(objetivo != raiz and objetivo.is_relative_to(raiz) for raiz in raices_permitidas):
        log.warning("⚠️  No se limpia ruta fuera de sin_operador: %s", objetivo)
        return False

    try:
        # Conserva cualquier archivo adicional de una ejecución previa antes
        # de retirar la carpeta de revisión manual.
        copiar_archivos_publicables_output(objetivo, destino)
        if not arbol_publicable_copiado_completo(objetivo, destino):
            log.warning("⚠️  No se retiró %s: la verificación de copia falló.", objetivo)
            return False
        if carpeta_descarga is not None:
            copiar_archivos_publicables_output(carpeta_descarga, destino)
            if not arbol_publicable_copiado_completo(carpeta_descarga, destino):
                log.warning(
                    "⚠️  No se retiró %s: falló la verificación final contra descargas.",
                    objetivo,
                )
                return False
        eliminar_arbol_robusto(objetivo)
        log.info("🧹 Revisión manual resuelta y retirada: %s", objetivo)
        return True
    except Exception as exc:
        log.warning("⚠️  No se pudo retirar revisión manual resuelta %s: %s", objetivo, exc)
        return False


def procesar_folio(
    folio: str,
    catalogo: list,
    carpeta: Path = None,
    folio_id: str = None,
    modo_internos: bool = False,
    sheet_name: str = None,
) -> dict:
    """
    Procesa un folio completo: metadata SATyS → RPC → Excel.

    'carpeta' es la carpeta de descarga a usar (por defecto descargas/<folio>/,
    pero puede ser una carpeta separada si este folio tiene varios tramites/
    registros distintos -- ver descubrir_carpetas_de_folio).
    'folio_id' es solo para nombrar rutas de salida sin que choquen entre
    tramites del mismo folio; el folio "real" para el Excel sigue siendo 'folio'.
    """
    folio_id = folio_id or folio
    resultado = {
        "folio": folio,
        "folio_id": folio_id,
        "pdf_encontrado": False,
        "nombre_operador": None,
        "representante_legal": None,
        "id_solicitante": "",    # ID del solicitante para búsqueda exacta en catálogo RPC
        "formatos": {},
        "imagen_sello": None,
        "fecha_sello": None,
        "rpc_ok": False,
        "excel_ok": False,
        "organizado_ok": False,
        "fuente_metadatos": "satys_json",
        "modo_internos": modo_internos,
    }

    carpeta = carpeta if carpeta is not None else (DESCARGA_BASE / folio)
    resultado["descargas_dir"] = str(carpeta)
    if not carpeta.exists():
        log.error("❌ Carpeta no existe: %s", carpeta)
        return resultado

    # Lectura directa de los JSON generados por Parte 1.
    log.info("📄 [PIPELINE] Leyendo metadatos SATyS desde JSON...")

    # Buscar si existe un PDF en la carpeta
    pdfs = list(carpeta.glob("*.pdf"))
    pdf_nombre = ""
    if pdfs:
        # Preferir archivo que empiece con CRT
        crt = [p for p in pdfs if p.stem.upper().startswith("CRT")]
        pdf_archivo = crt[0] if crt else pdfs[0]
        pdf_nombre = pdf_archivo.name
        resultado["pdf_encontrado"] = True

    # Leer metadatos extraídos por Parte 1
    meta_path = carpeta / "metadata_satys.json"
    meta = {}
    meta_tn = {}
    nombre_operador = ""
    representante_legal = ""
    concesionario = ""
    promovente = ""
    info_adicional = ""
    bandeja_internos = ""
    folio_tabla_internos = ""
    asunto = ""
    fecha_registro = ""
    registro_val = ""
    id_solicitante = ""  # Campo clave para búsqueda exacta en RPC
    tipo_tramite = ""
    folio_opc = ""
    fecha_limite = ""  # Plazo de atención (solo existe en metadata_tramite_nuevo.json)
    fuente_nombre_operador = "campos_metadata"

    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                nombre_operador = meta.get("nombre_operador", "") or meta.get("concesionario", "")
                representante_legal = meta.get("representante_legal", "") or meta.get("promovente", "")
                concesionario = meta.get("concesionario", "") or nombre_operador
                promovente = meta.get("promovente", "") or representante_legal
                info_adicional = meta.get("info_adicional", "") or meta.get("asunto", "")
                asunto = meta.get("asunto", "") or info_adicional
                fecha_registro = meta.get("fecha_registro", "")
                registro_val = meta.get("registro", "")
                id_solicitante = meta.get("id_solicitante", "")  # ID para lookup exacto
                tipo_tramite = meta.get("tipo_tramite", "")
                folio_opc = str(meta.get("folio_opc", "") or "").strip()
                bandeja_internos = meta.get("bandeja_internos", "")
                folio_tabla_internos = str(meta.get("folio_tabla_internos", "") or "").strip()
        except Exception as e:
            log.warning("⚠️  No se pudo leer metadatos de %s: %s", meta_path, e)

    # Leer plazo_atencion de metadata_tramite_nuevo.json (solo generado en Trámites Nuevos)
    meta_tramite_nuevo_path = carpeta / "metadata_tramite_nuevo.json"
    if meta_tramite_nuevo_path.exists():
        try:
            with open(meta_tramite_nuevo_path, "r", encoding="utf-8") as f:
                meta_tn = json.load(f)
                plazo_raw = meta_tn.get("plazo_atencion", "")
                if plazo_raw:
                    fecha_limite = str(plazo_raw).strip()
                    log.info("📅 plazo_atencion encontrado en metadata_tramite_nuevo.json: %s", fecha_limite)
                # También completar campos vacíos desde metadata_tramite_nuevo si no vinieron de metadata_satys
                if not nombre_operador:
                    nombre_operador = meta_tn.get("nombre_operador", "") or meta_tn.get("concesionario", "")
                if not representante_legal:
                    representante_legal = meta_tn.get("representante_legal", "") or meta_tn.get("promovente", "")
                if not concesionario:
                    concesionario = meta_tn.get("concesionario", "") or nombre_operador
                if not promovente:
                    promovente = meta_tn.get("promovente", "") or representante_legal
                if not info_adicional:
                    info_adicional = meta_tn.get("info_adicional", "") or meta_tn.get("asunto", "")
                if not asunto:
                    asunto = meta_tn.get("asunto", "") or info_adicional
                if not tipo_tramite:
                    tipo_tramite = meta_tn.get("tipo_tramite", "")
                if not fecha_registro:
                    fecha_registro = meta_tn.get("fecha_registro", "")
                if not id_solicitante:
                    id_solicitante = meta_tn.get("id_solicitante", "")
                if not folio_opc:
                    folio_opc = str(meta_tn.get("folio_opc", "") or "").strip()
                if not bandeja_internos:
                    bandeja_internos = meta_tn.get("bandeja_internos", "")
                if not folio_tabla_internos:
                    folio_tabla_internos = str(meta_tn.get("folio_tabla_internos", "") or "").strip()
        except Exception as e:
            log.warning("⚠️  No se pudo leer metadatos de %s: %s", meta_tramite_nuevo_path, e)

    if not (concesionario or nombre_operador):
        recuperado_texto_fila = extraer_nombre_operador_texto_fila(meta, meta_tn)
        if recuperado_texto_fila:
            nombre_operador = recuperado_texto_fila
            concesionario = recuperado_texto_fila
            fuente_nombre_operador = "texto_fila"
            log.info(
                "✅ Concesionario recuperado desde texto_fila: %s",
                recuperado_texto_fila,
            )

    # Aunque SATyS no haya devuelto PDF u operador, un Registro CRT válido
    # debe conservarse en el maestro. Folios_Datos_Completos.xlsx puede contener
    # metadata parcial útil (por ejemplo tipo de trámite) y la reconciliación
    # final completará la fila sin perder el registro.
    if not registro_val:
        registro_val = registro_desde_metadata_o_nombre(carpeta)
    if (
        modo_internos
        and str(registro_val or "").strip() == "100"
        and re.fullmatch(r"\d{3,}", folio_tabla_internos)
    ):
        # Algunas descargas legacy guardaron el valor fijo "100" tanto en
        # folio como en registro. El Folio mostrado por la tabla y usado como
        # nombre de carpeta es la identidad real de ese expediente.
        registro_val = folio_tabla_internos
        resultado["registro_corregido_desde"] = "placeholder_legacy_100"
    resultado["registro"] = registro_val
    if not pdf_nombre and not nombre_operador and not modo_internos:
        if REGISTRO_RE.fullmatch(str(registro_val or "").strip().upper()):
            log.warning("⚠️  Registro %s con metadata parcial; se actualizará Excel con los campos disponibles.", registro_val)
        else:
            log.warning("⚠️  No se encontró PDF, operador ni Registro CRT válido en %s", carpeta)
            return resultado

    # 1. Extract RXXX format from asunto
    formatos_dict = {}
    if asunto:
        for m in re.finditer(r"(R\d{3})", asunto, re.IGNORECASE):
            formatos_dict[m.group(1).upper()] = True

    datos_pdf = {
        "pdf_nombre": pdf_nombre,
        "nombre_operador": nombre_operador,
        "nombre_operador_web": nombre_operador,
        "representante_legal": representante_legal,
        "formatos": formatos_dict,
        "imagen_sello": None,
        "fecha_sello": fecha_registro,
        "registro": registro_val,
        "modo": "lectura_json"
    }

    resultado["pdf_encontrado"] = bool(pdf_nombre)
    resultado["nombre_operador"] = nombre_operador
    resultado["fuente_nombre_operador"] = fuente_nombre_operador
    resultado["representante_legal"] = representante_legal
    resultado["concesionario"] = concesionario or nombre_operador
    resultado["promovente"] = promovente or representante_legal
    resultado["info_adicional"] = info_adicional
    resultado["bandeja_internos"] = bandeja_internos
    resultado["folio_tabla_internos"] = folio_tabla_internos
    resultado["id_solicitante"] = id_solicitante   # Guardar para el reporte
    resultado["formatos"] = formatos_dict
    resultado["imagen_sello"] = None
    resultado["fecha_sello"] = fecha_registro
    resultado["fuente_metadatos"] = "satys_json"
    resultado["folio_opc"] = folio_opc
    resultado["es_correo"] = es_folio_opc_correo(folio_opc)

    # Tipos de archivo descargados
    nota_victor = obtener_nota_victor(carpeta)

    # ──── PARTE 3: Búsqueda RPC ────
    rpc_resultado = None
    import buscar_concesionario as bc
    from Parte3_rpc import construir_ruta, construir_ruta_operadores

    nombre_rpc = concesionario or nombre_operador
    permitir_rpc_online = os.getenv("SATYS_RPC_CONSULTA_ONLINE", "1").strip() != "0"
    rpc_resultado = bc.resolver_operador_seguro(
        id_solicitante=id_solicitante,
        nombre_operador=nombre_rpc,
        catalogo=catalogo or [],
        permitir_rpc_online=permitir_rpc_online,
    )

    identificador_archivos = (
        folio_id
        if modo_internos
        else (registro_val or folio_id)
    )
    if rpc_resultado.get("ok"):
        if rpc_resultado.get("operadores"):
            rpc_resultado["ruta"] = construir_ruta_operadores(
                rpc_resultado["operadores"],
                identificador_archivos,
            )
        else:
            rpc_resultado["ruta"] = construir_ruta(
                rpc_resultado["nombre_completo"],
                rpc_resultado["idBp"],
                identificador_archivos,
            )
        log.info(
            "✅ Operador resuelto por %s: %s (%s)",
            rpc_resultado.get("metodo", "evidencia exacta"),
            rpc_resultado["nombre_completo"][:60],
            rpc_resultado["idBp"],
        )
    else:
        rpc_resultado["ruta"] = ""
        log.warning(
            "⚠️  Operador no resuelto de forma segura: %s",
            rpc_resultado.get("motivo", "sin_coincidencia_exacta"),
        )

    # ── Reporte de resultado RPC ─────────────────────────────────────────────
    if rpc_resultado and rpc_resultado.get("ok"):
        resultado["rpc_ok"] = True
        resultado["rpc_resultado"] = rpc_resultado
        score_exactitud = rpc_resultado.get("score", 0) * 100
        metodo = rpc_resultado.get("metodo", "")
        if metodo == "razones_sociales_multiples_parcial":
            etiqueta_metodo = "Múltiples razones sociales (resolución parcial)"
        elif metodo.startswith("razones_sociales_multiples"):
            etiqueta_metodo = "Múltiples razones sociales verificadas"
        elif metodo.startswith("id_exacto"):
            etiqueta_metodo = "ID exacto"
        elif metodo == "nombre_exacto_rpc_resultados":
            etiqueta_metodo = "Nombre exacto en resultados RPC"
        elif metodo.startswith("nombre_base_legal_rpc"):
            etiqueta_metodo = "Nombre base legal RPC"
        elif metodo.startswith("nombre_alta_confianza_rpc"):
            etiqueta_metodo = "Nombre de alta confianza RPC"
        elif metodo == "nombre_exacto_rpc_online":
            etiqueta_metodo = "Nombre exacto RPC en línea"
        elif metodo.startswith("nombre_exacto_excel"):
            etiqueta_metodo = "Nombre exacto Excel RPC"
        else:
            etiqueta_metodo = metodo or "Evidencia exacta"

        log.info("✅ RPC [%s]: %s (exactitud: %.0f%%)",
                 etiqueta_metodo,
                 rpc_resultado.get("nombre_completo", "")[:60],
                 score_exactitud)

        print(f"\n   🎯 PORCENTAJE DE EXACTITUD ({etiqueta_metodo}): {score_exactitud:.2f}%")
        if metodo.startswith("razones_sociales_multiples"):
            ids_confirmados = ", ".join(rpc_resultado.get("ids_operador") or [])
            print(f"      ID OPERADOR confirmados : {ids_confirmados or 'N/A'}")
            if rpc_resultado.get("razones_sin_id"):
                print(
                    "      Razones sin ID confirmado: "
                    + " | ".join(rpc_resultado["razones_sin_id"])
                )
        elif metodo.startswith("id_exacto"):
            print(f"      id_solicitante usado    : {id_solicitante}")
            print(f"      ID OPERADOR en catálogo : {rpc_resultado.get('idBp', '')}")
        elif metodo.startswith("nombre_exacto"):
            print(f"      Concesionario SATyS     : {concesionario or nombre_operador}")
            print(f"      NOMBRE OPERADOR RPC     : {rpc_resultado.get('nombre_completo', '')}")
            print(f"      ID OPERADOR confirmado  : {rpc_resultado.get('idBp', '')}")
        print(f"      Nombre Oficial Catálogo  : {rpc_resultado['nombre_completo']}")

        # Actualizar nombre_operador al nombre oficial del catálogo
        resultado["nombre_operador"] = rpc_resultado["nombre_completo"]
        log.info("🔧 Nombre actualizado al oficial del catálogo.")
    elif rpc_resultado and not rpc_resultado.get("ok"):
        # Sin evidencia exacta/unívoca: 0% y revisión manual.
        resultado["rpc_resultado"] = rpc_resultado
        score_exactitud = rpc_resultado.get("score", 0) * 100
        motivo = rpc_resultado.get("motivo", "sin_coincidencia_exacta")
        log.warning(
            "⚠️  RPC sin coincidencia segura: similitud diagnóstica %.0f%% (%s)",
            score_exactitud,
            motivo,
        )
        print(
            f"\n   ⚠️  SIMILITUD DIAGNÓSTICA (NO APROBADA): "
            f"{score_exactitud:.2f}%"
        )
        print(f"      id_solicitante usado    : {id_solicitante or 'N/A'}")
        print("      ID OPERADOR en catálogo : NO ENCONTRADO")
        print(f"      Resultado               : {carpeta_sin_operador(folio_opc)} / revisión manual")

    nombre_final = resultado.get("nombre_operador") or ""
    identificador_revision = (
        str(registro_val or folio_id)
        if resultado["es_correo"]
        else folio_id
    )
    resultado["identificador_correo"] = (
        identificador_revision if resultado["es_correo"] else ""
    )
    ruta_revision_manual = ruta_relativa_sin_operador(
        identificador_revision,
        folio_opc,
    )
    destino_revision_manual = destino_sin_operador(
        OUTPUT_BASE,
        identificador_revision,
        folio_opc,
    )
    resultado["carpeta_revision_manual"] = carpeta_sin_operador(folio_opc)
    ruta_operador_rpc = ""
    if resultado["es_correo"]:
        # La clasificación CORREO es la decisión final y tiene prioridad sobre
        # una coincidencia RPC. Se conserva la ruta calculada sólo para retirar
        # una posible copia anterior en la carpeta del operador.
        ruta_operador_rpc = str(rpc_resultado.get("ruta") or "")
        rpc_resultado["ruta_operador_rpc"] = ruta_operador_rpc
        rpc_resultado["ruta"] = ruta_revision_manual
        log.info(
            "✉️  Folio OPC %s clasificado para destino exclusivo: %s",
            folio_opc,
            ruta_revision_manual,
        )

    # ──── PARTE 4: Actualizar Excel ────
    log.info("📊 [PARTE 4] Actualizando Excel...")
    excel_ok = actualizar_excel(
        folio=folio,
        registro=datos_pdf.get("registro", ""),
        nombre_operador=nombre_final,
        representante_legal=datos_pdf.get("representante_legal", ""),
        formatos=datos_pdf.get("formatos", {}),
        rpc_resultado=rpc_resultado,
        nota_victor=nota_victor,
        imagen_sello=datos_pdf.get("imagen_sello"),
        fecha_sello=datos_pdf.get("fecha_sello", ""),
        excel_path=EXCEL_PATH,
        sheet_name=sheet_name,
        asunto=asunto,
        tipo_tramite=tipo_tramite,
        fecha_limite=fecha_limite,
        folio_internos=folio_tabla_internos if modo_internos else "",
        ruta_salida=(
            ruta_revision_manual
            if resultado["es_correo"]
            else (
                rpc_resultado.get("ruta", "")
                if rpc_resultado and rpc_resultado.get("ok")
                else ruta_revision_manual
            )
        ),
    )
    resultado["excel_ok"] = excel_ok

    # Organizar archivos
    if ORGANIZAR_DESCARGAS:
        if resultado["es_correo"]:
            organizacion_correo = organizar_correo_exclusivo(
                carpeta,
                OUTPUT_BASE,
                identificador_revision,
                folio_opc=folio_opc,
                ruta_operador=ruta_operador_rpc,
                identificadores_legacy=(folio_id, identificador_archivos),
            )
            destino_correo = organizacion_correo["destino"]
            resultado["archivos_pendientes"] = organizacion_correo["archivos_copiados"]
            resultado["sin_operador_dir"] = str(destino_correo)
            resultado["output_dir"] = str(destino_correo)
            resultado["organizado_ok"] = organizacion_correo["verificado"]
            resultado["duplicados_correo_retirados"] = organizacion_correo[
                "duplicados_retirados"
            ]
            resultado["errores_organizacion_correo"] = organizacion_correo["errores"]
            if organizacion_correo["verificado"]:
                log.info(
                    "✉️  Correo %s consolidado exclusivamente en %s (%d archivos; %d duplicados retirados)",
                    folio_opc,
                    destino_correo,
                    len(organizacion_correo["archivos_copiados"]),
                    len(organizacion_correo["duplicados_retirados"]),
                )
            else:
                log.error(
                    "❌ No se pudo garantizar la organización exclusiva del correo %s: %s",
                    folio_opc,
                    " | ".join(organizacion_correo["errores"]),
                )
        elif rpc_resultado and rpc_resultado.get("ok"):
            # RPC exitoso → carpeta estandarizada del concesionario
            ruta_destino = f"{rpc_resultado['ruta']}"
            destino = organizar_archivos(carpeta, ruta_destino)
            if destino:
                resultado["organizado_ok"] = True
                resultado["output_dir"] = str(destino)
                resultado["revision_manual_limpiada"] = limpiar_revision_manual_resuelta(
                    destino_revision_manual,
                    destino,
                    carpeta,
                )
        else:
            # Una coincidencia no resuelta permanece directamente en
            # output/_sin_operador. La rutina fusiona copias antiguas y sólo
            # retira duplicados después de verificar contra descargas/.
            organizacion_revision = organizar_correo_exclusivo(
                carpeta,
                OUTPUT_BASE,
                identificador_revision,
                folio_opc=folio_opc,
                identificadores_legacy=(folio_id, identificador_archivos),
            )
            sin_op_dir = organizacion_revision["destino"]
            archivos_copiados = organizacion_revision["archivos_copiados"]
            resultado["archivos_pendientes"] = archivos_copiados
            resultado["sin_operador_dir"] = str(sin_op_dir)
            resultado["output_dir"] = str(sin_op_dir)
            resultado["organizado_ok"] = organizacion_revision["verificado"]
            resultado["duplicados_revision_retirados"] = organizacion_revision[
                "duplicados_retirados"
            ]
            resultado["errores_organizacion_revision"] = organizacion_revision["errores"]
            if organizacion_revision["verificado"]:
                log.info(
                    "📂 Folio %s consolidado en revisión manual: %s (%d archivos; %d duplicados retirados)",
                    folio,
                    sin_op_dir,
                    len(archivos_copiados),
                    len(organizacion_revision["duplicados_retirados"]),
                )
            else:
                log.error(
                    "❌ No se pudo verificar la organización de %s en %s: %s",
                    folio,
                    sin_op_dir,
                    " | ".join(organizacion_revision["errores"]),
                )

    return resultado


def imprimir_reporte(resultados: list):
    """Imprime el reporte final con un resumen ejecutivo orientado a la accion.

    Categorías (mutuamente excluyentes, en orden de prioridad):
    - correos     : folio_opc empieza con CORREO
                    → destino exclusivo output/_sin_operador/(correos)
    - exitosos    : rpc_ok=True + organizado_ok=True + excel_ok=True
                    → archivos descargados y organizados en carpeta del operador
    - sin_operador: NO exitoso, tiene nombre_operador (capturado de SATyS) pero
                    el id_solicitante no se encontró en el catálogo RPC
                    → archivos en _sin_operador/, requiere acción manual
    - errores     : NO exitoso, NO tiene nombre_operador de ninguna fuente
                    → SATyS no devolvió datos del tramite, revisar el portal
    """
    print("\n" + "═" * 70)
    print("  RESUMEN EJECUTIVO — ACCIONES REQUERIDAS")
    print("═" * 70)

    # ── Categorías mutuamente excluyentes ──────────────────────────────────
    # 1) Correos: la clasificación OPC tiene prioridad sobre el operador RPC.
    correos = [r for r in resultados if r.get("es_correo")]

    # 2) Exitosos: RPC encontrado, Excel actualizado y archivos organizados
    exitosos = [
        r for r in resultados
        if not r.get("es_correo")
        and r.get('rpc_ok') and r.get('organizado_ok') and r.get('excel_ok')
    ]

    # Los no-exitosos se subdividen por si tienen nombre_operador o no
    no_exitosos = [r for r in resultados if r not in exitosos and r not in correos]

    # 3) Sin operador en catálogo: SATyS sí entregó el nombre del operador
    #    pero el id_solicitante no está en el catálogo RPC.
    #    Tienen sus archivos en output/_sin_operador/ y necesitan revisión manual.
    sin_operador = [
        r for r in no_exitosos
        if r.get('nombre_operador')  # hay nombre extraído de SATyS
    ]

    # 4) Errores reales: SATyS no devolvió nombre de operador en ninguna fuente
    errores = [
        r for r in no_exitosos
        if not r.get('nombre_operador')
    ]

    # ── Imprimir secciones ─────────────────────────────────────────────────
    print(f"\n  ✉️  CORREOS ({len(correos)} folios) - EN _sin_operador\\(correos):")
    if not correos:
        print("       Ninguno.")
    for r in correos:
        estado = "verificado" if r.get("organizado_ok") else "con error de copia"
        print(
            f"       ✉️  {r['folio']} ({r.get('folio_opc', '')}) -> "
            f"{r.get('output_dir', 'N/A')} [{estado}]"
        )

    print(f"\n  🟢 ÉXITO TOTAL ({len(exitosos)} folios):")
    if not exitosos:
        print("       Ninguno.")
    for r in exitosos:
        print(f"       ✓ {r['folio']} -> Organizado en: {r.get('rpc_resultado', {}).get('nombre_completo', 'N/A')}")

    print(f"\n  📁 SIN OPERADOR EN CATÁLOGO ({len(sin_operador)} folios) - EN _sin_operador:")
    if not sin_operador:
        print("       Ninguno.")
    for r in sin_operador:
        id_sol = r.get('id_solicitante', 'N/A')
        nombre_op = r.get('nombre_operador', 'N/A')
        sin_op = r.get('sin_operador_dir', f'output\\_sin_operador\\{r["folio"]}')
        print(f"       📂 {r['folio']} -> Operador SATyS: '{nombre_op}'")
        print(f"          id_solicitante={id_sol} no encontrado en catálogo RPC.")
        print(f"          👉 ACCIÓN: Mueve los archivos desde '{sin_op}' a la carpeta del operador correcto.")

    print(f"\n  🔴 ERRORES ({len(errores)} folios):")
    if not errores:
        print("       Ninguno.")
    for r in errores:
        print(f"       ✗ {r['folio']} -> No se encontró nombre de operador en ninguna fuente. Revisa el portal SATyS.")

    print("\n" + "═" * 70 + "\n")

    return {
        "correos": len(correos),
        "exitosos": len(exitosos),
        "sin_operador": len(sin_operador),
        "errores": len(errores),
    }


def _conteos_resultados(resultados: list[dict]) -> dict:
    """Conteos consistentes para reporte, JSON y correo."""
    return {
        "correos": sum(1 for r in resultados if r.get("es_correo")),
        "exitosos": sum(
            1 for r in resultados
            if not r.get("es_correo")
            and r.get("rpc_ok") and r.get("organizado_ok") and r.get("excel_ok")
        ),
        "sin_operador": sum(
            1 for r in resultados
            if not r.get("es_correo")
            and not (r.get("rpc_ok") and r.get("organizado_ok") and r.get("excel_ok"))
            and r.get("nombre_operador")
        ),
        "errores": sum(
            1 for r in resultados
            if not r.get("es_correo")
            and not (r.get("rpc_ok") and r.get("organizado_ok") and r.get("excel_ok"))
            and not r.get("nombre_operador")
        ),
    }


def _generar_reportes_operadores(resultados: list[dict], modo: str) -> dict:
    """Genera CSV de auditoría/sin operador sin interrumpir la corrida."""
    try:
        from reporte_operadores import generar_reportes_operadores

        reporte = generar_reportes_operadores(
            resultados,
            modo=modo,
            logs_dir=LOGS_BASE,
        )
        log.info("📋 Auditoría de operadores: %s", reporte["auditoria_csv"])
        log.info("📋 Pendientes sin operador: %s", reporte["sin_operador_csv"])
        return reporte
    except Exception as exc:
        log.warning("⚠️  No se pudo generar el reporte CSV de operadores: %s", exc)
        return {"error": str(exc)}


def _enviar_email_fin_proceso(
    *,
    resultados: list[dict],
    modo: str,
    log_path: Path | None = None,
    excel_metadata_path: Path | None = None,
    sin_email: bool = False,
    email_to: str = "",
) -> None:
    """Envía la notificación final SATyS sin romper la corrida si falla el correo.

    La notificación usa config/configuracion_local.json para sus credenciales.
    Siempre incluye las cuatro salidas principales: Folios_Datos_Completos.xlsx,
    output/, descargas/ y TrámitesCRT.xlsx.
    """
    if sin_email:
        log.info("ℹ️  Correo deshabilitado por --sin-email.")
        return

    try:
        from notificar_email import enviar_notificacion
    except Exception as e:
        log.warning("⚠️  Módulo notificar_email no disponible; correo no enviado: %s", e)
        return

    conteos = _conteos_resultados(resultados)
    excel_metadata_path = excel_metadata_path or (OUTPUT_BASE / "Folios_Datos_Completos.xlsx")

    # Rutas absolutas para que el correo indique claramente dónde quedó cada salida.
    project_root = Path.cwd()
    outputs = {
        "Folios_Datos_Completos.xlsx": str((project_root / excel_metadata_path).resolve() if not excel_metadata_path.is_absolute() else excel_metadata_path.resolve()),
        "Carpeta output": str((project_root / OUTPUT_BASE).resolve() if not OUTPUT_BASE.is_absolute() else OUTPUT_BASE.resolve()),
        "Carpeta descargas": str((project_root / DESCARGA_BASE).resolve() if not DESCARGA_BASE.is_absolute() else DESCARGA_BASE.resolve()),
        "TrámitesCRT.xlsx": str((project_root / EXCEL_PATH).resolve() if not EXCEL_PATH.is_absolute() else EXCEL_PATH.resolve()),
    }
    if CARPETA_COMPARTIDA is not None:
        outputs["Carpeta compartida"] = str(CARPETA_COMPARTIDA)

    try:
        enviar_notificacion(
            total_registros=len(resultados),
            exitosos=conteos["exitosos"],
            sin_operador=conteos["sin_operador"],
            errores=conteos["errores"],
            registros=resultados,
            fecha_ejecucion=datetime.now().isoformat(),
            destinatarios=email_to or None,
            modo=modo,
            outputs=outputs,
            log_path=str(log_path) if log_path else None,
            project_root=project_root,
            descargas_base=DESCARGA_BASE,
            output_base=OUTPUT_BASE,
            excel_path=EXCEL_PATH,
            excel_metadata_path=excel_metadata_path,
            carpeta_compartida=CARPETA_COMPARTIDA,
        )
    except Exception as e:
        log.warning("⚠️  Error no crítico enviando correo de notificación: %s", e)



# ════════════════════════════════════════════════════════
#  FUNCIÓN PRINCIPAL
# ════════════════════════════════════════════════════════

def aplicar_modo_todos_internos(args):
    """Activa el recorrido completo de Internos y rechaza filtros parciales."""
    if not getattr(args, "todos_internos", False):
        return args

    conflictos = []
    if getattr(args, "internos_bandejas", None):
        conflictos.append("--internos-bandejas")
    if getattr(args, "internos_objetivos", ""):
        conflictos.append("--internos-objetivos")
    if getattr(args, "internos_registros", ""):
        conflictos.append("--internos-registros")
    if getattr(args, "solo_procesar", False):
        conflictos.append("--solo-procesar")
    if getattr(args, "folios", None):
        conflictos.append("folios posicionales")
    if getattr(args, "archivo_folios", ""):
        conflictos.append("--archivo-folios")
    if getattr(args, "archivo_registro", ""):
        conflictos.append("--archivo-registro")
    if getattr(args, "buscar", 0):
        conflictos.append("--buscar")
    if conflictos:
        raise ValueError(
            "--todos-internos recorre las seis bandejas completas y no admite: "
            + ", ".join(conflictos)
        )

    args.internos = True
    args.internos_bandejas = None
    args.internos_objetivos = ""
    return args


def main():
    parser = argparse.ArgumentParser(
        description="SATyS — Pipeline de producción (Partes 1, 3 y 4)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  main_procesar.py                      Partes 1, 3 y 4 con folios por defecto
  main_procesar.py 6407 6801            Partes 1, 3 y 4 con folios específicos
  main_procesar.py --solo-procesar      Partes 3-4 con todos los folios en descargas/
  main_procesar.py --solo-procesar 6407 Partes 3-4 con folio específico
  main_procesar.py --todos-internos --headless
                                        Solo Internos IFT: seis bandejas, descarga, RPC y hoja Internos
  main_procesar.py --rebuild-catalogo   Reconstruir catálogo RPC
        """,
    )
    parser.add_argument("folios", nargs="*",
                        help="Folios a procesar (si vacío, usa FOLIOS_DEFAULT)")
    parser.add_argument("--solo-procesar", action="store_true",
                        help="Omitir Parte 1 (descarga) y solo procesar archivos locales")
    parser.add_argument("--rebuild-catalogo", action="store_true",
                        help="Reconstruir el catálogo RPC desde cero")
    rpc_group = parser.add_mutually_exclusive_group()
    rpc_group.add_argument(
        "--rpc-online",
        dest="rpc_online",
        action="store_true",
        default=None,
        help=(
            "Fuerza el respaldo en el buscador público del RPC cuando el Excel "
            "no resuelve el operador (es el comportamiento de la corrida diaria)."
        ),
    )
    rpc_group.add_argument(
        "--sin-rpc-online",
        dest="rpc_online",
        action="store_false",
        help="Desactiva explícitamente la consulta alternativa al buscador público RPC.",
    )
    parser.add_argument("--no-organizar", action="store_true",
                        help="No mover archivos a carpetas RPC")
    parser.add_argument("--buscar", type=int, default=0,
                        help="Cantidad de folios existentes a buscar y procesar (ej: 27)")
    parser.add_argument("--desde", type=int, default=6407,
                        help="Folio inicial para la búsqueda (ej: 6407)")
    parser.add_argument("--archivo-folios", type=str, default="",
                        help="Ruta a un archivo .txt con la lista de folios a procesar (uno por línea)")
    parser.add_argument("--workers", type=int, default=WORKERS_DEFAULT,
                        help="Número de ventanas de navegador a usar en Playwright (Parte 1)")
    parser.add_argument("--timeout-registro", type=int, default=TIMEOUT_REGISTRO_DEFAULT,
                        help="Timeout duro por Registro en segundos durante la descarga. Default: 900 (15 min).")
    parser.add_argument("--reintentos-registro", type=int, default=REINTENTOS_REGISTRO_DEFAULT,
                        help="Reintentos automáticos solo para registros incompletos. 2 = hasta 3 intentos totales.")
    parser.add_argument("--workers-reintento", type=int, default=WORKERS_REINTENTO_DEFAULT,
                        help="Workers usados en reintentos de registros fallidos/incompletos. Default: 2.")
    parser.add_argument("--headless", action="store_true",
                        help="Ocultar navegador de Playwright (ejecución en segundo plano).")
    parser.add_argument("--archivo-registro", type=str, default="",
                        help="Ruta a un archivo .txt con la lista de números de Registro a procesar "
                             "(uno por línea o separados por espacios/comas, ej. CRT26-002483). Activa el modo de búsqueda por Registro.")
    parser.add_argument("--internos", action="store_true",
                        help="Procesa Administracion solicitudes +TyS/SIGEDO/Internos IFT y escribe en la hoja Internos.")
    parser.add_argument("--todos-internos", action="store_true",
                        help="Ejecuta exclusivamente todos los Folios de las seis bandejas de Internos IFT.")
    parser.add_argument("--internos-bandejas", nargs="+", default=None,
                        help="Bandejas de Internos IFT a procesar. Default: las seis bandejas del tablero.")
    parser.add_argument("--internos-workers", type=int, default=INTERNOS_WORKERS_DEFAULT,
                        help="Navegadores paralelos para Internos. Default: 12; sin tope artificial; 0 usa uno por bandeja.")
    parser.add_argument("--internos-objetivos", type=str, default="",
                        help="JSON con pares bandeja/folio; limita la descarga, pero Partes 3-4 conservan todas las carpetas locales.")
    parser.add_argument("--internos-registros", type=str, default="",
                        help="CSV/TXT con folios numéricos de Internos; limita Partes 3-4 a esas carpetas locales.")
    parser.add_argument("--sin-email", action="store_true",
                        help="No enviar notificación por correo al finalizar esta corrida.")
    parser.add_argument("--sin-sincronizar", action="store_true",
                        help="No copiar TrámitesCRT.xlsx, output/ ni descargas/ a la carpeta compartida.")
    parser.add_argument("--email-to", type=str, default="",
                        help="Destinatarios de correo separados por coma. Si se omite, usa los destinatarios configurados por el proyecto.")
    parser.add_argument("--sin-lock", action="store_true",
                        help="No tomar el lock compartido. Usar solo cuando un proceso padre, como automatizar_registros_diario.py, ya tomó el lock.")
    args = parser.parse_args()
    try:
        aplicar_modo_todos_internos(args)
    except ValueError as exc:
        parser.error(str(exc))
    if args.internos_workers < 0:
        parser.error("--internos-workers debe ser 0 o un entero positivo")
    if args.internos_registros and not args.internos:
        parser.error("--internos-registros requiere --internos")
    if args.rpc_online is not None:
        os.environ["SATYS_RPC_CONSULTA_ONLINE"] = "1" if args.rpc_online else "0"

    # ──── Bloqueo compartido: evita que 2+ corridas SATyS se empalmen ────
    # main_procesar.py toma el lock en corridas manuales, desde UI o API.
    # En corrida diaria, automatizar_registros_diario.py toma el lock externo
    # y llama main_procesar.py con --sin-lock para evitar bloquearse a sí mismo.
    _lock = None
    if not args.sin_lock:
        _lock = ProcesoLock(proceso="main_procesar.py")
        try:
            _lock.adquirir()
        except LockOcupadoError as e:
            log.error("🔒 %s", e)
            log.error("   Esta ejecución no iniciará. Intenta de nuevo más tarde.")
            return 1

        def _salir_limpiamente(signum, frame):
            try:
                if _lock is not None:
                    _lock.liberar()
            finally:
                raise SystemExit(128 + signum)

        try:
            signal.signal(signal.SIGTERM, _salir_limpiamente)
            signal.signal(signal.SIGINT, _salir_limpiamente)
        except Exception:
            pass
    else:
        log.info("🔒 Lock compartido omitido por --sin-lock; se asume que el proceso padre ya lo tomó.")

    try:

        # Configuración local
        global ORGANIZAR_DESCARGAS
        if args.no_organizar:
            ORGANIZAR_DESCARGAS = False

        json_output_eliminados = depurar_json_output(OUTPUT_BASE)
        if json_output_eliminados:
            log.info(
                "🧹 Se retiraron %d JSON heredado(s) de output; los metadatos permanecen en descargas.",
                len(json_output_eliminados),
            )
        if ORGANIZAR_DESCARGAS:
            consolidacion_operadores = consolidar_todas_carpetas_operadores(OUTPUT_BASE)
            if consolidacion_operadores["estructuras_retiradas"]:
                log.info(
                    "🧹 Organización inicial: %d estructura(s) heredada(s) fusionada(s) "
                    "en %d operador(es) bajo 01 EN/VE.",
                    consolidacion_operadores["estructuras_retiradas"],
                    consolidacion_operadores["operadores"],
                )
            for error_consolidacion in consolidacion_operadores["errores"]:
                log.warning("⚠️  No se pudo consolidar una salida de operador: %s", error_consolidacion)

        # Banner
        print("\n" + "╔" + "═" * 68 + "╗")
        print("║" + "  SATyS — PIPELINE DE PRODUCCIÓN (PARTES 1, 3 Y 4)  ".center(68) + "║")
        print("║" + "  RPC: ID exacto + nombre canónico único 0/100  ".center(68) + "║")
        print("╚" + "═" * 68 + "╝\n")

        # ────────────────────────────────────────────────────────────────────────
        # MODO REGISTRO: buscar y descargar por número de Registro
        # ────────────────────────────────────────────────────────────────────────
        if args.internos:
            print("\n" + "-" * 70)
            print("  MODO INTERNOS IFT: DESCARGA + RPC EXACTO SEGURO + HOJA INTERNOS")
            if args.todos_internos:
                print("  RECORRIDO COMPLETO: LAS SEIS BANDEJAS, SIN OFICIALIA")
            print("-" * 70)
            objetivos_i: list[dict] = []
            claves_objetivo_i: set[tuple[str, str]] = set()
            folios_objetivo_i: set[str] = set()
            if args.internos_registros:
                try:
                    folios_objetivo_i = set(
                        cargar_folios_internos_desde_archivo(args.internos_registros)
                    )
                except Exception as exc:
                    log.error(
                        "❌ No se pudo leer --internos-registros %s: %s",
                        args.internos_registros,
                        exc,
                    )
                    return 1
                if not folios_objetivo_i:
                    log.error("❌ --internos-registros no contiene folios numéricos válidos.")
                    return 1
                log.info("📋 Folios Internos solicitados desde archivo: %d", len(folios_objetivo_i))
            if args.internos_objetivos:
                try:
                    objetivos_i = cargar_objetivos_internos(args.internos_objetivos)
                except Exception as exc:
                    log.error("❌ No se pudo leer --internos-objetivos %s: %s", args.internos_objetivos, exc)
                    return 1
                if not objetivos_i:
                    log.info("✅ El JSON de objetivos Internos no contiene pendientes.")
                    return 0
                claves_objetivo_i = {
                    (slug_bandeja_internos(item["bandeja"]), item["folio"])
                    for item in objetivos_i
                }
                args.internos_bandejas = list(dict.fromkeys(
                    item["bandeja"] for item in objetivos_i
                ))
                log.info("📋 Objetivos Internos nuevos: %d", len(objetivos_i))

            rc_descarga_internos = 0
            if not args.solo_procesar:
                rc_descarga_internos = ejecutar_descarga_internos(
                    bandejas=args.internos_bandejas,
                    headless=args.headless,
                    objetivos_path=args.internos_objetivos or None,
                    workers=args.internos_workers,
                    timeout_registro=args.timeout_registro,
                    reintentos_registro=args.reintentos_registro,
                )
                if rc_descarga_internos:
                    log.error(
                        "❌ Parte 1 Internos terminó con código %d. "
                        "Se continuará con Partes 3-4 para lo que sí tenga metadata local.",
                        rc_descarga_internos,
                    )
            else:
                log.info("✅ --solo-procesar activo: se omite descarga y se procesa descargas/internos/.")

            carpetas_internos = descubrir_descargas_internos()
            if claves_objetivo_i:
                claves_encontradas_i = set()
                for candidato in carpetas_internos:
                    meta_candidato = leer_metadata_descarga(candidato[0])
                    folio_tabla = str(
                        meta_candidato.get("folio_tabla_internos")
                        or meta_candidato.get("folio")
                        or candidato[0].name
                        or ""
                    ).strip()
                    clave = (
                        slug_bandeja_internos(
                            str(
                                meta_candidato.get("bandeja_internos")
                                or candidato[0].parent.name
                                or ""
                            )
                        ),
                        folio_tabla,
                    )
                    if clave in claves_objetivo_i:
                        claves_encontradas_i.add(clave)
                faltantes_i = claves_objetivo_i - claves_encontradas_i
                if faltantes_i:
                    rc_descarga_internos = rc_descarga_internos or 1
                    log.error(
                        "❌ %d objetivo(s) Internos no generaron metadata local: %s",
                        len(faltantes_i),
                        ", ".join(f"{b}/{f}" for b, f in sorted(faltantes_i)[:30]),
                    )
                log.info(
                    "✅ Objetivos de descarga verificados; Partes 3-4 conservarán las %d carpeta(s) Internos locales.",
                    len(carpetas_internos),
                )
            if folios_objetivo_i:
                filtradas_i = []
                encontrados_i: set[str] = set()
                for candidato in carpetas_internos:
                    carpeta_candidato, _, registro_ref_candidato = candidato
                    meta_candidato = leer_metadata_descarga(carpeta_candidato)
                    identificadores = {
                        str(carpeta_candidato.name).strip(),
                        str(registro_ref_candidato or "").strip(),
                        str(meta_candidato.get("folio") or "").strip(),
                        str(meta_candidato.get("folio_tabla_internos") or "").strip(),
                        str(meta_candidato.get("registro") or "").strip(),
                    }
                    coincidencias = folios_objetivo_i & identificadores
                    if coincidencias:
                        filtradas_i.append(candidato)
                        encontrados_i.update(coincidencias)
                carpetas_internos = filtradas_i
                faltantes_archivo_i = folios_objetivo_i - encontrados_i
                if faltantes_archivo_i:
                    log.warning(
                        "⚠️  %d folio(s) del archivo no tienen carpeta local: %s",
                        len(faltantes_archivo_i),
                        ", ".join(sorted(faltantes_archivo_i)[:30]),
                    )
                log.info(
                    "✅ Filtro --internos-registros: %d carpeta(s), %d folio(s) localizados.",
                    len(carpetas_internos),
                    len(encontrados_i),
                )
            if not carpetas_internos:
                log.error("❌ No se encontraron carpetas procesables en %s.", DESCARGA_BASE / "internos")
                if objetivos_i:
                    excel_faltantes_i = OUTPUT_BASE / "Folios_Datos_Completos_Internos.xlsx"
                    try:
                        from generar_excel_metadata_json import generar_excel_metadata_json
                        generar_excel_metadata_json(
                            resultados=[],
                            descargas_base=DESCARGA_BASE,
                            output_base=OUTPUT_BASE,
                            excel_salida=excel_faltantes_i,
                            project_root=Path.cwd(),
                            objetivos_esperados=objetivos_i,
                        )
                        log.warning(
                            "📘 Excel Internos generado con %d objetivo(s) FALTANTE(s): %s",
                            len(objetivos_i),
                            excel_faltantes_i,
                        )
                    except Exception as exc_excel_faltantes:
                        log.error(
                            "❌ No se pudo generar el Excel de objetivos Internos faltantes: %s",
                            exc_excel_faltantes,
                        )
                _sincronizar_si_corresponde(args)
                return 1

            if not EXCEL_PATH.exists():
                log.error("❌ No se encontró el Excel: %s", EXCEL_PATH)
                return 1

            catalogo_i = cargar_catalogo_rpc_exacto(force_rebuild=args.rebuild_catalogo)
            resultados_i = []
            total_i = len(carpetas_internos)
            for idx_i, (carpeta_int, folio_id_int, registro_ref_int) in enumerate(carpetas_internos, 1):
                print(f"\n{'-' * 70}")
                print(f"  [{idx_i}/{total_i}] PROCESANDO INTERNO: {carpeta_int.name}")
                print(f"      Carpeta : {carpeta_int}")
                print(f"      Ref     : {registro_ref_int}")
                print(f"{'-' * 70}")

                folio_para_excel = folio_excel_desde_metadata(
                    carpeta_int,
                    registro_ref_int or carpeta_int.name,
                )
                resultado_i = procesar_folio(
                    folio=folio_para_excel,
                    catalogo=catalogo_i,
                    carpeta=carpeta_int,
                    folio_id=folio_id_int,
                    modo_internos=True,
                    sheet_name="Internos",
                )
                resultados_i.append(resultado_i)

            imprimir_reporte(resultados_i)
            reportes_i = _generar_reportes_operadores(resultados_i, "internos")

            log_path_i = DESCARGA_BASE / "internos" / "procesamiento_log_internos.json"
            try:
                log_path_i.parent.mkdir(parents=True, exist_ok=True)
                conteos_i = _conteos_resultados(resultados_i)
                log_data_i = {
                    "fecha_ejecucion": datetime.now().isoformat(),
                    "modo": "internos",
                    "sheet": "Internos",
                    "total_carpetas_descargas_procesadas": len(resultados_i),
                    "total_correos": conteos_i["correos"],
                    "total_exitosos": conteos_i["exitosos"],
                    "total_sin_operador": conteos_i["sin_operador"],
                    "total_errores": conteos_i["errores"],
                    "reportes_csv": reportes_i,
                    "resultados": resultados_i,
                }
                log_path_i.write_text(
                    json.dumps(log_data_i, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                log.info("📄 Log de Internos guardado en: %s", log_path_i)
            except Exception as e_log_i:
                log.warning("⚠️  No se pudo guardar log de Internos: %s", e_log_i)

            excel_metadata_i = OUTPUT_BASE / "Folios_Datos_Completos_Internos.xlsx"
            try:
                from generar_excel_metadata_json import generar_excel_metadata_json
                excel_metadata_i = generar_excel_metadata_json(
                    resultados=resultados_i,
                    descargas_base=DESCARGA_BASE,
                    output_base=OUTPUT_BASE,
                    excel_salida=excel_metadata_i,
                    project_root=Path.cwd(),
                    objetivos_esperados=objetivos_i,
                )
                log.info("📘 Excel consolidado Internos guardado en: %s", excel_metadata_i)
            except Exception as e_meta_i:
                log.error("❌ Error al generar Excel consolidado Internos: %s", e_meta_i)
                rc_descarga_internos = 1

            _sincronizar_si_corresponde(args)
            _enviar_email_fin_proceso(
                resultados=resultados_i,
                modo="internos",
                log_path=log_path_i,
                excel_metadata_path=excel_metadata_i,
                sin_email=args.sin_email,
                email_to=args.email_to,
            )
            return rc_descarga_internos

        if args.archivo_registro:
            try:
                registros = cargar_registros_desde_archivo(args.archivo_registro)
                print(f"📄 Cargados {len(registros)} registro(s) desde {args.archivo_registro}")
            except Exception as e:
                log.error("❌ Error leyendo archivo de registros %s: %s", args.archivo_registro, e)
                return 1

            if not registros:
                log.error("❌ El archivo de registros está vacío o no contiene registros con formato CRT26-000000")
                return 1

            # Guardar la lista original solo como referencia.
            # Las Partes 3-4 no se limitarán a lo descargado en este intento;
            # al terminar Parte1 se escaneará descargas/ completo.
            registros_archivo_original = list(registros)

            # ── Filtrar registros pendientes ─────────────────────────────────
            log.info("🔍 Analizando qué registros ya fueron descargados correctamente...")
            registros_pendientes, registros_completos = filtrar_registros_pendientes(registros)

            print("\n" + "─" * 70)
            print("  MODO REGISTRO: DESCARGA POR NÚMERO DE REGISTRO")
            print("─" * 70)
            print(f"  📋 Total en archivo     : {len(registros)}")
            print(f"  ✅ Ya completos (skip)  : {len(registros_completos)}")
            print(f"  📥 Pendientes (a bajar) : {len(registros_pendientes)}")
            print("─" * 70)

            if registros_pendientes:
                muestra = ", ".join(registros_pendientes[:30])
                if len(registros_pendientes) > 30:
                    muestra += f", ... (+{len(registros_pendientes) - 30} más)"
                print(f"  Pendientes: {muestra}")
            else:
                print("  🎉 ¡Todos los registros ya están completos! No hay nada que descargar.")
            print("─" * 70 + "\n")

            rc_descarga_registros = 0
            if registros_pendientes:
                # Usar solo los registros pendientes para la descarga.
                # Los ya completos NO se descargan de nuevo, pero SÍ se procesarán
                # después porque las Partes 3-4 escanearán descargas/ completo.
                registros = registros_pendientes

                # ── Ejecutar Parte 1 en modo registro ───────────────────────────
                try:
                    import Parte1_descarga
                except ImportError as e:
                    log.error("❌ No se encontró Parte1_descarga.py: %s", e)
                    return 1

                Parte1_descarga.USUARIO   = SATYS_USUARIO
                Parte1_descarga.PASSWORD  = SATYS_PASSWORD
                Parte1_descarga.HEADLESS  = args.headless
                Parte1_descarga.DESCARGA_BASE = DESCARGA_BASE

                headless_flag = ["--headless"] if args.headless else ["--visible"]
                original_argv = sys.argv
                try:
                    sys.argv = (
                        ["Parte1_descarga.py"]
                        + headless_flag
                        + ["--workers", str(args.workers)]
                        + ["--timeout-registro", str(args.timeout_registro)]
                        + ["--reintentos-registro", str(args.reintentos_registro)]
                        + ["--workers-reintento", str(args.workers_reintento)]
                        + ["--modo-registro"]
                        + ["--registros"] + registros
                    )
                    rc_descarga_registros = int(Parte1_descarga.main() or 0)
                    if rc_descarga_registros:
                        log.error(
                            "❌ Parte 1 terminó con registros incompletos (código %d). "
                            "Se continuarán Partes 3-4 para las carpetas que sí tengan descargas reales.",
                            rc_descarga_registros,
                        )
                except Exception as e:
                    log.error("❌ Error en descarga por registro: %s", e)
                    rc_descarga_registros = 1
                finally:
                    sys.argv = original_argv
            else:
                log.info("✅ No hay registros pendientes para descargar. Se omite Parte 1 y se procesará descargas/ completo.")

            # Después de Parte1, procesar en Partes 3-4 TODO lo que está realmente
            # descargado en descargas/. Esto incluye:
            #   - registros que ya estaban completos antes de esta corrida,
            #   - registros recuperados en esta corrida,
            #   - carpetas cuyo nombre real es folio/VE aunque hayan venido de un registro CRT.
            carpetas_para_procesar = descubrir_descargas_procesables()

            if not carpetas_para_procesar:
                log.error("❌ No se encontraron carpetas procesables en %s. No se ejecutan Partes 3-4.", DESCARGA_BASE)
                _sincronizar_si_corresponde(args)
                return 1

            log.info(
                "✅ Partes 3-4 procesarán %d carpeta(s) descargada(s) detectada(s) en %s, no solo las de esta ejecución.",
                len(carpetas_para_procesar), DESCARGA_BASE,
            )

            # ── Cargar catálogo RPC para Partes 3-4 ──────────────────────
            log.info("🗂️  Cargando catálogo RPC exacto desde Excel oficial...")
            catalogo_r = []
            try:
                sys.path.append(os.path.join(str(_script_dir), "buscar_concesionario"))
                import buscar_concesionario as bc_r
                from descargar_concesiones_rpc import descargar_bd as descargar_bd_r

                bd_dir_r = Path(_script_dir) / "base_de_datos_rpc"
                bd_dir_r.mkdir(exist_ok=True)

                def _cat_reciente_r(bd):
                    archivos = sorted(
                        bd.glob("03_concesiones_permisos_autorizaciones_*.xlsx"),
                        key=lambda p: p.stat().st_mtime, reverse=True,
                    )
                    return archivos[0] if archivos else None

                def _cat_necesita_actualizacion_r(bd, max_dias: int = 7) -> bool:
                    reciente = _cat_reciente_r(bd)
                    if reciente is None:
                        return True
                    edad_dias = (datetime.now().timestamp() - reciente.stat().st_mtime) / 86400
                    return edad_dias > max_dias

                xlsx_r = None
                if args.rebuild_catalogo or _cat_necesita_actualizacion_r(bd_dir_r):
                    log.info("⬇️  Verificando/Descargando la base RPC más reciente...")
                    descargado_r = descargar_bd_r(str(bd_dir_r))
                    if descargado_r:
                        xlsx_r = Path(descargado_r)
                if xlsx_r is None:
                    xlsx_r = _cat_reciente_r(bd_dir_r)

                if xlsx_r and xlsx_r.exists():
                    cat_excel_r = bc_r.cargar_catalogo_desde_excel(str(xlsx_r), "copeau", solo_vigentes=False)
                    catalogo_r = bc_r.preparar_catalogo_para_matching(cat_excel_r)
                    log.info("✅ Catálogo RPC exacto listo: %d concesionarios", len(catalogo_r))
                else:
                    raise FileNotFoundError("No se encontró Excel oficial RPC en base_de_datos_rpc")
            except Exception as e_cat:
                log.error(
                    "❌ Catálogo RPC exacto no disponible: %s. "
                    "Se intentará nombre exacto en el RPC en línea; nunca fuzzy.",
                    e_cat,
                )
                catalogo_r = []

            # ── Verificar Excel ──────────────────────────────────────────
            if not EXCEL_PATH.exists():
                log.error("❌ No se encontró el Excel: %s", EXCEL_PATH)
                return 1

            # ── Partes 3-4 para cada carpeta realmente descargada ─────────
            resultados_r = []
            total_carpetas_r = len(carpetas_para_procesar)
            for i_r, (carpeta_reg, folio_id_reg, registro_ref) in enumerate(carpetas_para_procesar, 1):
                print(f"\n{'─' * 70}")
                print(f"  [{i_r}/{total_carpetas_r}] PROCESANDO DESCARGA: {carpeta_reg.name}")
                print(f"      Carpeta : {carpeta_reg}")
                print(f"      Registro: {registro_ref}")
                print(f"{'─' * 70}")

                folio_para_excel = folio_excel_desde_metadata(carpeta_reg, registro_ref or carpeta_reg.name)

                resultado_r = procesar_folio(
                    folio=folio_para_excel,
                    catalogo=catalogo_r,
                    carpeta=carpeta_reg,
                    folio_id=folio_id_reg,
                )
                resultados_r.append(resultado_r)

            imprimir_reporte(resultados_r)
            reportes_r = _generar_reportes_operadores(resultados_r, "registros")

            # Guardar log de resultados
            log_path_r = DESCARGA_BASE / "procesamiento_log_registros.json"
            try:
                conteos_r = _conteos_resultados(resultados_r)
                log_data_r = {
                    "fecha_ejecucion":  datetime.now().isoformat(),
                    "modo":             "registro",
                    "total_carpetas_descargas_procesadas": len(resultados_r),
                    "registros_archivo_original": len(registros_archivo_original),
                    "total_correos":     conteos_r["correos"],
                    "total_exitosos":   conteos_r["exitosos"],
                    "total_sin_operador": conteos_r["sin_operador"],
                    "total_errores":    conteos_r["errores"],
                    "reportes_csv": reportes_r,
                    "resultados": resultados_r,
                }
                with open(log_path_r, "w", encoding="utf-8") as f_log_r:
                    json.dump(log_data_r, f_log_r, ensure_ascii=False, indent=2, default=str)
                log.info("📄 Log de registros guardado en: %s", log_path_r)
                log.info("📊 Resumen: %d exitosos | %d sin operador en catálogo | %d errores",
                         conteos_r["exitosos"], conteos_r["sin_operador"], conteos_r["errores"])
            except Exception:
                pass

            # Generar Excel consolidado con todos los campos de metadata_satys.json
            # y metadata_tramite_nuevo.json por cada número de registro.
            excel_metadata_r = OUTPUT_BASE / "Folios_Datos_Completos.xlsx"
            try:
                from generar_excel_metadata_json import generar_excel_metadata_json
                excel_metadata_r = generar_excel_metadata_json(
                    resultados=resultados_r,
                    descargas_base=DESCARGA_BASE,
                    output_base=OUTPUT_BASE,
                    excel_salida=OUTPUT_BASE / "Folios_Datos_Completos.xlsx",
                    project_root=Path.cwd(),
                )
                log.info("📘 Excel consolidado JSON guardado en: %s", excel_metadata_r)

                from reconciliar_tramites_desde_folios import reconciliar
                resumen_reconciliacion = reconciliar(EXCEL_PATH, excel_metadata_r)
                log.info(
                    "✅ TrámitesCRT reconciliado: %d registros, %d agregados, %d filas fantasma eliminadas, %d rutas vacías",
                    resumen_reconciliacion["source_records"],
                    resumen_reconciliacion["appended"],
                    resumen_reconciliacion["phantom_removed"],
                    resumen_reconciliacion["routes_blank"],
                )
            except Exception as e_meta_r:
                log.error("❌ Error al generar/reconciliar Excel consolidado JSON: %s", e_meta_r)
                rc_descarga_registros = 1

            # Sincronizar output/ y Excel con la carpeta compartida de red
            _sincronizar_si_corresponde(args)

            # Notificación final por correo para cualquier corrida en modo registro.
            _enviar_email_fin_proceso(
                resultados=resultados_r,
                modo="registros",
                log_path=log_path_r,
                excel_metadata_path=excel_metadata_r,
                sin_email=args.sin_email,
                email_to=args.email_to,
            )

            return rc_descarga_registros  # Terminar sin continuar al flujo de folios

        # Obtener folios
        folios = []
        if args.archivo_folios:
            try:
                with open(args.archivo_folios, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            folios.append(line)
                print(f"📄 Cargados {len(folios)} folios desde {args.archivo_folios}")
            except Exception as e:
                log.error("❌ Error leyendo archivo de folios %s: %s", args.archivo_folios, e)
                return

        if args.folios:
            folios.extend([f.strip() for f in args.folios])
        
        if not folios and args.buscar > 0:
            # Generar un rango amplio de folios a intentar descargar
            folios = [str(f) for f in range(args.desde, args.desde + 500)]
            os.environ["SATYS_MAX_FOLIOS"] = str(args.buscar)
            print(f"🔍 Configurado para buscar los primeros {args.buscar} folios existentes a partir del {args.desde}")
        elif not folios:
            # Menú interactivo si no se pasan argumentos
            print("\n" + "═" * 70)
            print("  MENÚ INTERACTIVO DE PROCESAMIENTO".center(70))
            print("═" * 70)
            try:
                desde_str = input("👉 Ingresa el folio INICIAL a procesar (ej. 6407): ").strip()
                hasta_str = input("👉 Ingresa el folio FINAL a procesar (ej. 6433): ").strip()
            
                if not desde_str or not hasta_str:
                    print("⚠️  Entradas vacías. Cancelando ejecución.")
                    return
                
                args.desde = int(desde_str)
                hasta = int(hasta_str)
            
                if hasta < args.desde:
                    print("⚠️  El folio final no puede ser menor al inicial. Cancelando.")
                    return
                
                args.buscar = hasta - args.desde + 1
                folios = [str(f) for f in range(args.desde, hasta + 1)]
            
                # Limitar a descargar la cantidad exacta de folios requeridos en Parte1
                os.environ["SATYS_MAX_FOLIOS"] = str(args.buscar)
                print(f"\n🔍 [MENÚ] Procesando {args.buscar} folios: desde el {args.desde} hasta el {hasta}")
            except ValueError:
                print("⚠️  Entrada inválida (deben ser números enteros). Cancelando.")
                return

        # ──── PARTE 1: Descarga ────
        if not args.solo_procesar:
            print("─" * 70)
            print("  PARTE 1: DESCARGA AUTOMÁTICA DESDE SATyS")
            print("─" * 70)
            if not ejecutar_descarga(folios, workers=args.workers, headless=args.headless):
                log.error("❌ La descarga falló o no pudo completar todos los folios. Cancelando el proceso.")
                return
            print()

        def normalizar_folio_local(folio_str: str) -> str:
            m = re.search(r"(\d+)$", str(folio_str).strip())
            return str(int(m.group(1))) if m else str(folio_str).strip()

        # Ahora verificar qué folios tienen carpeta
        if DESCARGA_BASE.exists():
            carpetas_existentes = [
                d.name for d in DESCARGA_BASE.iterdir()
                if d.is_dir()
            ]
        
            if args.solo_procesar and not args.folios and not args.archivo_folios:
                # Si solo procesamos y no dimos folios, procesar TODAS las carpetas
                folios = sorted(carpetas_existentes)
            else:
                # Filtrar para procesar solo los que realmente existen
                folios_normalizados = [normalizar_folio_local(f) for f in folios]
                folios = [f for f in folios_normalizados if f in carpetas_existentes]
        else:
            log.error("❌ No se encontró carpeta descargas/")
            return

        if not folios:
            log.error("❌ No hay folios para procesar")
            return

        log.info("📋 Folios a procesar: %s", ", ".join(folios))

        # Verificar Excel
        if not EXCEL_PATH.exists():
            log.error("❌ No se encontró el Excel: %s", EXCEL_PATH)
            return

        # ──── Cargar catálogo RPC (usando buscar_concesionario si es posible) ────
        log.info("🗂️  Cargando catálogo RPC (buscando Excel de concesionarios)...")
        catalogo = []
        try:
            sys.path.append(os.path.join(str(_script_dir), "buscar_concesionario"))
            import buscar_concesionario as bc
            from descargar_concesiones_rpc import descargar_bd
        
            bd_dir = Path(_script_dir) / "base_de_datos_rpc"
            bd_dir.mkdir(exist_ok=True)

            def _catalogo_existente_mas_reciente(bd_dir: Path):
                archivos = sorted(
                    bd_dir.glob("03_concesiones_permisos_autorizaciones_*.xlsx"),
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
                return archivos[0] if archivos else None

            def _catalogo_necesita_actualizacion(bd_dir: Path, max_dias: int = 7) -> bool:
                mas_reciente = _catalogo_existente_mas_reciente(bd_dir)
                if mas_reciente is None:
                    return True
                edad_dias = (datetime.now().timestamp() - mas_reciente.stat().st_mtime) / 86400
                return edad_dias > max_dias

            excel_path_full = None
            if args.rebuild_catalogo or _catalogo_necesita_actualizacion(bd_dir):
                log.info("⬇️  Verificando/Descargando la base de datos más reciente...")
                descargado_path = descargar_bd(str(bd_dir))
                if descargado_path:
                    excel_path_full = Path(descargado_path)
            else:
                mas_reciente = _catalogo_existente_mas_reciente(bd_dir)
                excel_path_full = mas_reciente
                log.info("✅ Catálogo reciente (%s), se omite la descarga.", mas_reciente.name)

            if excel_path_full is None:
                # Fallback a buscar el xlsx más reciente en la carpeta si falló la descarga
                mas_reciente = _catalogo_existente_mas_reciente(bd_dir)
                if mas_reciente is not None:
                    excel_path_full = mas_reciente
                    log.info("Usando archivo existente (offline fallback): %s", excel_path_full.name)
                else:
                    # Fallback final a la antigua carpeta si base_de_datos_rpc está vacío
                    excel_path_full = Path(_script_dir) / "buscar_concesionario" / "Area _de_descargas" / "03_concesiones_permisos_autorizaciones_250326.xlsx"
        
            if excel_path_full.exists():
                cat_excel = bc.cargar_catalogo_desde_excel(str(excel_path_full), "copeau", solo_vigentes=False)
                catalogo = bc.preparar_catalogo_para_matching(cat_excel)
                log.info("✅ Catálogo CSV/Excel listo: %d concesionarios", len(catalogo))
            else:
                log.warning("⚠️  Excel de buscar_concesionario no encontrado en: %s", excel_path_full)
                raise FileNotFoundError("Excel no encontrado")
        except Exception as e:
            if os.getenv("SATYS_RPC_PERMITIR_FUZZY", "0").strip() == "1":
                log.warning("⚠️  Falló carga exacta desde buscar_concesionario (%s). Usando Parte3_rpc por nombre/API porque SATYS_RPC_PERMITIR_FUZZY=1.", e)
                catalogo = cargar_catalogo(force_rebuild=args.rebuild_catalogo)
                if catalogo:
                    log.info("✅ Catálogo Parte3_rpc listo: %d concesionarios", len(catalogo))
                else:
                    log.warning("⚠️  Sin catálogo — se usará sólo el RPC en línea con igualdad canónica")
            else:
                log.error(
                    "❌ No se pudo cargar el catálogo RPC exacto desde Excel (%s). "
                    "Se intentará el RPC en línea por nombre exacto; nunca fuzzy.",
                    e,
                )
                catalogo = []

        # ──── Procesar cada folio (Partes 3-4) ────
        resultados = []
        for i, folio in enumerate(folios, 1):
            carpetas_folio = descubrir_carpetas_de_folio(folio)
            if not carpetas_folio:
                # Compatibilidad: folio sin carpeta descubierta -> se intenta con
                # la ruta clasica de todos modos (procesar_folio reportara el error).
                carpetas_folio = [(DESCARGA_BASE / folio, folio)]

            if len(carpetas_folio) > 1:
                log.info("🔀 Folio %s tiene %d tramites/registros distintos -- "
                         "se generara una fila de Excel por cada uno.",
                         folio, len(carpetas_folio))

            for carpeta_folio, folio_id in carpetas_folio:
                print(f"\n{'─' * 70}")
                if len(carpetas_folio) > 1:
                    print(f"  [{i}/{len(folios)}] PROCESANDO FOLIO: {folio}  (tramite: {folio_id})")
                else:
                    print(f"  [{i}/{len(folios)}] PROCESANDO FOLIO: {folio}")
                print(f"{'─' * 70}")

                resultado = procesar_folio(
                    folio=folio,
                    catalogo=catalogo,
                    carpeta=carpeta_folio,
                    folio_id=folio_id,
                )
                resultados.append(resultado)

        # Reporte
        imprimir_reporte(resultados)
        reportes = _generar_reportes_operadores(resultados, "folios")

        # Guardar log de resultados
        log_path = DESCARGA_BASE / "procesamiento_log.json"
        try:
            conteos = _conteos_resultados(resultados)
            log_data = {
                "fecha_ejecucion":    datetime.now().isoformat(),
                "fuente_metadatos":  "satys_json",
                "total_folios":       len(resultados),
                "total_correos":      conteos["correos"],
                "total_exitosos":     conteos["exitosos"],
                "total_sin_operador": conteos["sin_operador"],
                "total_errores":      conteos["errores"],
                "reportes_csv":       reportes,
                "resultados": resultados,
            }
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2, default=str)
            log.info("📄 Log guardado en: %s", log_path)
            log.info("📊 Resumen: %d exitosos | %d sin operador en catálogo | %d errores",
                     conteos["exitosos"], conteos["sin_operador"], conteos["errores"])
        except Exception:
            pass

        # -- Generar / Actualizar Excel de Datos Consolidados desde JSON --
        excel_metadata = OUTPUT_BASE / "Folios_Datos_Completos.xlsx"
        try:
            from generar_excel_metadata_json import generar_excel_metadata_json
            excel_metadata = generar_excel_metadata_json(
                resultados=resultados,
                descargas_base=DESCARGA_BASE,
                output_base=OUTPUT_BASE,
                excel_salida=OUTPUT_BASE / "Folios_Datos_Completos.xlsx",
                project_root=Path.cwd(),
            )
            log.info("📘 Excel consolidado JSON guardado en: %s", excel_metadata)

            from reconciliar_tramites_desde_folios import reconciliar
            resumen_reconciliacion = reconciliar(EXCEL_PATH, excel_metadata)
            log.info(
                "✅ TrámitesCRT reconciliado: %d registros, %d agregados, %d filas fantasma eliminadas, %d rutas vacías",
                resumen_reconciliacion["source_records"],
                resumen_reconciliacion["appended"],
                resumen_reconciliacion["phantom_removed"],
                resumen_reconciliacion["routes_blank"],
            )
        except Exception as e:
            log.error("❌ Error al generar/reconciliar el Excel consolidado de metadatos JSON: %s", e)

        # -- Sincronizar con carpeta compartida de red --
        _sincronizar_si_corresponde(args)

        # Notificación final por correo para cualquier corrida en modo folios.
        _enviar_email_fin_proceso(
            resultados=resultados,
            modo="folios",
            log_path=log_path,
            excel_metadata_path=excel_metadata,
            sin_email=args.sin_email,
            email_to=args.email_to,
        )


    finally:
        if _lock is not None:
            try:
                _lock.liberar()
                log.info("🔓 Lock compartido liberado al finalizar main_procesar.py.")
            except Exception as e:
                log.warning("⚠️  No se pudo liberar el lock compartido: %s", e)


def _sincronizar_si_corresponde(args) -> None:
    if getattr(args, "sin_sincronizar", False):
        log.info("🌐 Sincronización DEPI omitida por --sin-sincronizar.")
        return
    sincronizar_carpeta_compartida()


def sincronizar_carpeta_compartida() -> None:
    """Sincroniza Excel, output/ documental y descargas/ con metadata."""
    if CARPETA_COMPARTIDA is None:
        return

    print()
    print("─" * 70)
    print("  SINCRONIZACIÓN NO DESTRUCTIVA CON CRT RECURSO DEPI")
    print("─" * 70)

    resultado = sincronizar_salidas(Path(__file__).resolve().parent, CARPETA_COMPARTIDA)
    for error in resultado.errores:
        log.warning("⚠️  Sincronización: %s", error)

    log.info(
        "🌐 DEPI: %d archivo(s) copiado(s), %d ruta(s) omitida(s), "
        "%d JSON retirado(s) de output, %d error(es). Destino: %s",
        resultado.archivos_copiados,
        resultado.omitidos,
        resultado.json_output_eliminados,
        len(resultado.errores),
        CARPETA_COMPARTIDA,
    )
    if resultado.errores:
        print(f"  ⚠️  Sincronización completada con {len(resultado.errores)} error(es) → {CARPETA_COMPARTIDA}")
    else:
        print(f"  ✅ Sincronización completada; se sobrescribió sin borrar → {CARPETA_COMPARTIDA}")
    print("─" * 70)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
