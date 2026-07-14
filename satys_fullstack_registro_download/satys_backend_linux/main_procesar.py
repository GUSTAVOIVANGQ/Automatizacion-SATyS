#!/usr/bin/env python3
r"""
=============================================================
  PROYECTO SATyS — ORQUESTADOR PRINCIPAL
=============================================================
Ejecuta el flujo completo de procesamiento:

  Parte 1 → Descarga automática desde SATyS (Playwright)
  Metadatos → Lectura directa de metadata_satys.json / metadata_tramite_nuevo.json
  Parte 3 → Búsqueda en RPC (API REST, sin Playwright)
  Parte 4 → Actualización de Excel y organización de archivos

  Nota: Parte2_extraer.py NO se llama en el flujo de producción Linux.

Uso:
  python main_procesar.py                    # Procesa descargas existentes con metadatos + Parte 3 + Parte 4
  python main_procesar.py 6407 6801          # Folios específicos con metadatos + Parte 3 + Parte 4
  python main_procesar.py --descarga         # Parte 1 + metadatos + Parte 3 + Parte 4
  python main_procesar.py --descarga 6407    # Parte 1 + metadatos + Parte 3 + Parte 4
  .\python_portable\python.exe main_procesar.py --rebuild-catalogo # Reconstruir catálogo RPC
=============================================================
"""

import sys
import io
import os
import re
import json
import logging
import argparse
import shutil
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
# ║        Edita SOLO esta sección para personalizar             ║
# ╚══════════════════════════════════════════════════════════════╝

# ──── Rutas de archivos ────
DESCARGA_BASE = Path("descargas")
OUTPUT_BASE = Path("output")
EXCEL_PATH = Path("TrámitesCRT.xlsx")

# ──── Folios por defecto (si no se pasan por argumento) ────
FOLIOS_DEFAULT = ["6407", "6801", "6802"]

# ──── PARTE 1: Descarga (Playwright) ────
# Solo se ejecuta si pasas --descarga
SATYS_USUARIO = os.getenv("SATYS_USER", "david.palestina@ift.org.mx")
SATYS_PASSWORD = os.getenv("SATYS_PASS", "Crt20261234**")
HEADLESS = False  # False = ver navegador | True = sin ventana

# ──── PARTE 2: Extracción de datos ────
# Producción Linux: se usan metadatos extraídos desde SATyS/JSON y lógica local.
# La extracción en nube queda deshabilitada por decisión de despliegue.
MODO_EXTRACCION = os.getenv("SATYS_MODO_EXTRACCION", "metadata_satys")

# ──── PARTE 3: Búsqueda en RPC ────
# El catálogo se descarga automáticamente la primera vez
# Usa --rebuild-catalogo para reconstruirlo

# ──── PARTE 4: Excel y archivos ────
ORGANIZAR_DESCARGAS = True  # True = mover archivos a carpetas RPC

# ──── PARTE 5: Carpeta compartida/salida consolidada ────
# En Linux no se usa unidad de red de Windows. Si se requiere copia final, definir:
#   export SATYS_CARPETA_COMPARTIDA=/data/satys/compartido
# Si no se define, no se sincroniza fuera del proyecto.
CARPETA_COMPARTIDA = (
    Path(os.getenv("SATYS_CARPETA_COMPARTIDA")).expanduser()
    if os.getenv("SATYS_CARPETA_COMPARTIDA", "").strip()
    else None
)
# ════════════════════════════════════════════════════════════════


# ──── Imports de los módulos ────
from Parte3_rpc import buscar_en_rpc, cargar_catalogo
from Parte4_excel import actualizar_excel, organizar_archivos, obtener_nota_victor
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


def es_registro_pendiente(registro: str) -> bool:
    """
    Determina si un registro necesita ser (re)descargado.

    Un registro se considera PENDIENTE si cumple CUALQUIERA de estas condiciones:
      1. Su carpeta en descargas/ no existe.
      2. Su carpeta existe pero está vacía (sin archivos).
      3. metadata_satys.json o metadata_tramite_nuevo.json tienen los campos
         'id_solicitante', 'asunto' o 'representante_legal' vacíos o null.

    Retorna True si el registro debe descargarse/reprocesarse.
    """
    carpeta = DESCARGA_BASE / registro

    # Condición 1: carpeta no existe
    if not carpeta.exists():
        return True

    # Condición 2: carpeta vacía de archivos reales.
    # Los JSON generados por el programa (metadata_*.json, metadata_completo.json)
    # NO cuentan como descarga; solo PDFs u otros archivos descargados cuentan.
    JSON_GENERADOS = {"metadata_completo.json", "metadata_satys.json", "metadata_tramite_nuevo.json"}
    archivos_reales = [
        f for f in carpeta.glob("*")
        if f.is_file() and f.name not in JSON_GENERADOS
    ]
    if not archivos_reales:
        return True

    # Condición 3: revisar campos críticos en los JSON de metadatos
    CAMPOS_CRITICOS = ("id_representante_legal", "id_solicitante")

    def _campos_incompletos(json_path: Path) -> bool:
        """Retorna True si el JSON tiene algún campo crítico vacío o null."""
        if not json_path.exists():
            return False  # Si no existe el JSON, no bloqueamos por este archivo
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            for campo in CAMPOS_CRITICOS:
                valor = meta.get(campo)
                if valor is None or str(valor).strip() == "":
                    return True
            return False
        except Exception:
            return True  # Si no se puede leer, marcar como pendiente

    if _campos_incompletos(carpeta / "metadata_satys.json"):
        return True
    if _campos_incompletos(carpeta / "metadata_tramite_nuevo.json"):
        return True

    return False


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
        if es_registro_pendiente(registro):
            pendientes.append(registro)
        else:
            completos.append(registro)
    return pendientes, completos



# ────────────────────────────────────────────────────────
#  DESCUBRIMIENTO DE DESCARGAS LOCALES PARA PARTES 2-4
# ────────────────────────────────────────────────────────

JSON_GENERADOS_DESCARGA = {
    "metadata_completo.json",
    "metadata_satys.json",
    "metadata_tramite_nuevo.json",
}


def carpeta_tiene_archivos_reales(carpeta: Path) -> bool:
    """
    True si la carpeta tiene al menos un archivo real descargado.

    Los JSON de metadatos NO cuentan como descarga; sirven para procesar,
    pero no prueban que el expediente tenga documentos.
    """
    if not carpeta.exists() or not carpeta.is_dir():
        return False
    for archivo in carpeta.iterdir():
        if archivo.is_file() and archivo.name not in JSON_GENERADOS_DESCARGA:
            return True
    return False


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
    return data


def folio_excel_desde_metadata(carpeta: Path, fallback: str) -> str:
    """
    Determina el folio que debe escribirse en Excel para una carpeta local.
    Prioridad:
      1) metadata['folio']
      2) número extraído de metadata['folio_opc']
      3) metadata['memo_folio_opc']
      4) nombre de carpeta / fallback
    """
    meta = leer_metadata_descarga(carpeta)
    folio_directo = meta.get("folio")
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

    Esto permite que, al terminar Parte1, el procesamiento local (metadatos + Parte 3 + Parte 4) trabaje sobre el
    estado real de C:\\...\\descargas y no solo sobre la lista que se acaba
    de bajar en esta ejecución.
    """
    if not DESCARGA_BASE.exists():
        return []

    candidatos: list[tuple[Path, str, str]] = []
    vistos: set[str] = set()

    def agregar(carpeta: Path, folio_id: str) -> None:
        try:
            key = str(carpeta.resolve()).lower()
        except Exception:
            key = str(carpeta).lower()
        if key in vistos:
            return
        if not carpeta_tiene_archivos_reales(carpeta):
            return
        vistos.add(key)
        registro_ref = registro_desde_metadata_o_nombre(carpeta) or carpeta.name
        candidatos.append((carpeta, folio_id, registro_ref))

    for carpeta in sorted([p for p in DESCARGA_BASE.iterdir() if p.is_dir()], key=lambda p: p.name.upper()):
        # Caso normal: descargas/<folio_o_registro>/archivos...
        agregar(carpeta, carpeta.name)

        # Caso especial: descargas/<folio>_1/<registro>/archivos...
        if incluir_subcarpetas:
            for sub in sorted([p for p in carpeta.iterdir() if p.is_dir()], key=lambda p: p.name.upper()):
                agregar(sub, f"{carpeta.name}__{sub.name}")

    return candidatos



# ────────────────────────────────────────────────────────
#  PARTE 1: Descarga (importa Parte1_descarga.py)
# ────────────────────────────────────────────────────────

def ejecutar_descarga(folios: list[str], workers: int = 10, headless: bool = False):
    """Ejecuta la Parte 1: descarga automática desde SATyS."""
    try:
        import Parte1_descarga
    except ImportError as e:
        log.error("❌ No se encontró Parte1_descarga.py: %s", e)
        return False

    log.info("📥 [PARTE 1] Iniciando descarga automática...")
    log.info("📋 Folios a descargar: %s", ", ".join(folios))

    # Configurar Parte1 con nuestros valores
    Parte1_descarga.USUARIO = SATYS_USUARIO
    Parte1_descarga.PASSWORD = SATYS_PASSWORD
    Parte1_descarga.HEADLESS = headless
    Parte1_descarga.FOLIOS_DEFAULT = folios
    Parte1_descarga.DESCARGA_BASE = DESCARGA_BASE

    import time

    MAX_REINTENTOS = 3
    headless_flag = ["--headless"] if headless else ["--visible"]

    folios_actuales = list(folios)
    intento = 0

    # ═══════════════════════════════════════════════════════════════════════════
    # BUCLE DE REINTENTOS AUTOMÁTICOS — COMENTADO
    # Fue desactivado porque cada archivo ya tiene 3 intentos propios dentro de
    # Parte1_descarga (MAX_INTENTOS_ARCHIVO=3). Si el archivo sigue fallando
    # después de esos 3 intentos, es un error del servidor y no tiene sentido
    # volver a intentarlo desde aquí.
    # Para reactivar: descomenta el bloque 'while' y comenta la sección
    # 'EJECUCIÓN ÚNICA' que está debajo.
    # ═══════════════════════════════════════════════════════════════════════════
    # while folios_actuales and intento < MAX_REINTENTOS:
    #     intento += 1
    #     if intento > 1:
    #         log.warning("⚠️  [REINTENTO %d/%d] Descargando %d folios con errores de red...",
    #                     intento, MAX_REINTENTOS, len(folios_actuales))
    #         time.sleep(5)
    #     try:
    #         original_argv = sys.argv
    #         sys.argv = ["Parte1_descarga.py"] + headless_flag + ["--workers", str(workers), "--folios"] + folios_actuales
    #         Parte1_descarga.main()
    #         sys.argv = original_argv
    #     except Exception as e:
    #         log.error("❌ Error en descarga: %s", e)
    #         sys.argv = original_argv
    #         return False
    #     resumen_path = DESCARGA_BASE / "resumen_global.json"
    #     if not resumen_path.exists():
    #         log.error("❌ No se generó resumen_global.json")
    #         return False
    #     try:
    #         with open(resumen_path, "r", encoding="utf-8") as f:
    #             resumen = json.load(f)
    #     except Exception as e:
    #         log.error("❌ Error leyendo resumen_global.json: %s", e)
    #         return False
    #     nuevos_folios = []
    #     folios_solo_servidor = []
    #     for d in resumen.get("detalle_folios", []):
    #         if d.get("estado") == "INCOMPLETO":
    #             folio_id = str(d.get("folio"))
    #             archivos_folio = [
    #                 a for a in resumen.get("archivos", [])
    #                 if str(a.get("folio")) == folio_id and not a.get("ok")
    #             ]
    #             solo_servidor = all(
    #                 a.get("tipo") == "ERROR_SERVIDOR" for a in archivos_folio
    #             ) if archivos_folio else False
    #             if solo_servidor:
    #                 folios_solo_servidor.append(folio_id)
    #                 log.warning("⏭️  Folio %s omitido en reintento: ERROR_SERVIDOR.", folio_id)
    #             else:
    #                 nuevos_folios.append(folio_id)
    #     if folios_solo_servidor:
    #         log.info("ℹ️  %d folio(s) con errores de servidor (sin reintento): %s",
    #                  len(folios_solo_servidor), ", ".join(folios_solo_servidor))
    #     if not nuevos_folios:
    #         log.info("✅ Sin folios pendientes de reintento por red. Terminando bucle.")
    #         break
    #     folios_actuales = nuevos_folios
    # if intento >= MAX_REINTENTOS and folios_actuales:
    #     log.warning("⚠️  Se alcanzó el máximo de %d reintentos. Folios pendientes: %s",
    #                 MAX_REINTENTOS, ", ".join(folios_actuales))
    # ═══════════════════════════════════════════════════════════════════════════
    # FIN BUCLE DE REINTENTOS (comentado)
    # ═══════════════════════════════════════════════════════════════════════════

    # ───────────────────────────────────────────────────────────────────────────
    # EJECUCIÓN ÚNICA — ACTIVA
    # Cada archivo tiene 3 intentos propios dentro de Parte1_descarga.
    # Si falla tras 3 intentos → ERROR_SERVIDOR (problema externo, sin reintento).
    # ───────────────────────────────────────────────────────────────────────────
    try:
        original_argv = sys.argv
        sys.argv = ["Parte1_descarga.py"] + headless_flag + ["--workers", str(workers), "--folios"] + folios_actuales
        Parte1_descarga.main()
        sys.argv = original_argv
    except Exception as e:
        log.error("❌ Error en descarga: %s", e)
        sys.argv = original_argv
        return False
    log.info("✅ Descarga completada (ejecución única; reintentos por archivo manejados internamente).")
    # ───────────────────────────────────────────────────────────────────────────
    # FIN EJECUCIÓN ÚNICA
    # ───────────────────────────────────────────────────────────────────────────

    return True



# ────────────────────────────────────────────────────────
#  PARTES 2-4: Procesamiento
# ────────────────────────────────────────────────────────

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


def procesar_folio(
    folio: str,
    catalogo: list,
    modo_extraccion: str = "metadata_satys",
    carpeta: Path = None,
    folio_id: str = None,
) -> dict:
    """
    Procesa un folio completo: PDF → RPC → Excel.

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
        "modo_extraccion": None,
    }

    carpeta = carpeta if carpeta is not None else (DESCARGA_BASE / folio)
    if not carpeta.exists():
        log.error("❌ Carpeta no existe: %s", carpeta)
        return resultado

    # ──── LECTURA DE METADATOS (sin llamar Parte2_extraer.py) ────
    log.info("📄 [METADATOS] Leyendo metadata_satys.json / metadata_tramite_nuevo.json directamente.")

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
    nombre_operador = ""
    representante_legal = ""
    asunto = ""
    fecha_registro = ""
    registro_val = ""
    id_solicitante = ""  # Campo clave para búsqueda exacta en RPC
    tipo_tramite = ""
    fecha_limite = ""  # Plazo de atención (solo existe en metadata_tramite_nuevo.json)

    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                nombre_operador = meta.get("nombre_operador", "")
                representante_legal = meta.get("representante_legal", "")
                asunto = meta.get("asunto", "")
                fecha_registro = meta.get("fecha_registro", "")
                registro_val = meta.get("registro", "")
                id_solicitante = meta.get("id_solicitante", "")  # ID para lookup exacto
                tipo_tramite = meta.get("tipo_tramite", "")
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
                    nombre_operador = meta_tn.get("nombre_operador", "")
                if not representante_legal:
                    representante_legal = meta_tn.get("representante_legal", "")
                if not asunto:
                    asunto = meta_tn.get("asunto", "")
                if not tipo_tramite:
                    tipo_tramite = meta_tn.get("tipo_tramite", "")
                if not fecha_registro:
                    fecha_registro = meta_tn.get("fecha_registro", "")
        except Exception as e:
            log.warning("⚠️  No se pudo leer metadatos de %s: %s", meta_tramite_nuevo_path, e)

    if not pdf_nombre and not nombre_operador:
        log.warning("⚠️  No se encontró PDF ni nombre de operador en %s", carpeta)
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
    resultado["representante_legal"] = representante_legal
    resultado["id_solicitante"] = id_solicitante   # Guardar para el reporte
    resultado["formatos"] = formatos_dict
    resultado["imagen_sello"] = None
    resultado["fecha_sello"] = fecha_registro
    resultado["modo_extraccion"] = "lectura_json"

    # Tipos de archivo descargados
    nota_victor = obtener_nota_victor(carpeta)

    # ──── PARTE 3: Búsqueda RPC ────
    rpc_resultado = None
    origen_ganador = ""
    nombre_original_usado = datos_pdf.get("nombre_operador", "")

    es_catalogo_bc = bool(catalogo and "norm" in catalogo[0])

    if es_catalogo_bc:
        import buscar_concesionario as bc
        from Parte3_rpc import construir_ruta

        # ── MÉTODO PRIMARIO: Búsqueda exacta por id_solicitante ──────────────
        # Compara el campo 'id_solicitante' del metadata_satys.json con la
        # columna 'ID OPERADOR' (idBp) del Excel del RPC-IFT.
        # Score = 1.0 (100%) cuando hay coincidencia exacta.
        if id_solicitante:
            log.info("🆔 [PARTE 3] Buscando por id_solicitante='%s' en catálogo RPC...", id_solicitante)
            match_id = bc.buscar_por_id_solicitante(id_solicitante, catalogo)
            if match_id:
                rpc_resultado = {
                    "nombre_completo": match_id["nombre_completo"],
                    "numero_rpc":      match_id["idBp"],
                    "idBp":            match_id["idBp"],
                    "ruta":            construir_ruta(match_id["nombre_completo"], match_id["idBp"]),
                    "score":           1.0,
                    "ok":              True,
                    "empate":          False,
                    "metodo":          "id_exacto",
                }
                origen_ganador = "ID"
                log.info("✅ Coincidencia exacta por ID: %s", match_id["nombre_completo"][:60])
            else:
                log.warning("⚠️  id_solicitante='%s' NO encontrado en catálogo. Se intentará fuzzy por nombre.", id_solicitante)
        else:
            log.warning("⚠️  No hay id_solicitante en metadata. Se usará búsqueda fuzzy por nombre.")

        # ── FALLBACK: Similitud fuzzy por nombre (solo si ID falló) ──────────
        # Se conserva el código de similitud anterior como respaldo.
        # Se activa cuando:
        #   - El JSON no tiene id_solicitante, O
        #   - El id_solicitante no se encontró en el catálogo.
        # if rpc_resultado is None:
        #     nombre_pdf = datos_pdf.get("nombre_operador")
        #     nombre_web = datos_pdf.get("nombre_operador_web")
        #     nombres_a_probar = []
        #     if nombre_web: nombres_a_probar.append((nombre_web, "Web"))
        #     if nombre_pdf and nombre_pdf != nombre_web: nombres_a_probar.append((nombre_pdf, "PDF"))
        # 
        #     if nombres_a_probar:
        #         log.info("🌐 [PARTE 3 - FALLBACK] Buscando por similitud de nombre...")
        #         mejor_score = -1
        #         mejor_match = None
        #         empate = False
        #         for nom, origen in nombres_a_probar:
        #             matches = bc.buscar_coincidencias(nom, catalogo, top_n=5)
        #             if matches:
        #                 score, best_match = matches[0]
        #                 if score > mejor_score:
        #                     mejor_score = score
        #                     mejor_match = best_match
        #                     origen_ganador = origen
        #                     nombre_original_usado = nom
        # 
        #                     # Revisar empate de IDs distintos con el mismo score
        #                     empate = False
        #                     if len(matches) > 1 and matches[1][0] == score:
        #                         id1 = best_match.get("idBp")
        #                         for s2, m2 in matches[1:]:
        #                             if s2 == score and m2.get("idBp") != id1:
        #                                 empate = True
        #                                 break
        # 
        #         SCORE_MINIMO_FUZZY = 0.80
        #         if mejor_match and mejor_score >= SCORE_MINIMO_FUZZY:
        #             rpc_resultado = {
        #                 "nombre_completo": mejor_match["concesionario"],
        #                 "numero_rpc":      mejor_match.get("idBp", ""),
        #                 "idBp":            mejor_match.get("idBp", ""),
        #                 "ruta":            construir_ruta(mejor_match["concesionario"], mejor_match.get("idBp", "")),
        #                 "score":           mejor_score,
        #                 "ok":              not empate,
        #                 "empate":          empate,
        #                 "metodo":          "fuzzy_nombre",
        #             }
        #         elif mejor_match:
        #             log.warning("⚠️  Mejor score fuzzy %.0f%% < mínimo %.0f%%. Sin coincidencia.",
        #                         mejor_score * 100, SCORE_MINIMO_FUZZY * 100)
        #     else:
        #         log.warning("⚠️  Sin nombre de operador en PDF ni Web, se omite búsqueda RPC")
    else:
        # Catálogo sin 'norm' → usar Parte3_rpc directamente (sin Excel)
        nombre_pdf = datos_pdf.get("nombre_operador", "")
        origen_ganador = "API"
        nombre_original_usado = nombre_pdf
        if nombre_pdf:
            rpc_resultado = buscar_en_rpc(nombre_pdf, catalogo=catalogo)

    # ── Reporte de resultado RPC ─────────────────────────────────────────────
    if rpc_resultado and rpc_resultado.get("ok"):
        resultado["rpc_ok"] = True
        resultado["rpc_resultado"] = rpc_resultado
        score_exactitud = rpc_resultado.get("score", 0) * 100
        metodo = rpc_resultado.get("metodo", "")
        etiqueta_metodo = "ID exacto" if metodo == "id_exacto" else f"Fuzzy ({origen_ganador})"

        log.info("✅ RPC [%s]: %s (exactitud: %.0f%%)",
                 etiqueta_metodo,
                 rpc_resultado.get("nombre_completo", "")[:60],
                 score_exactitud)

        print(f"\n   🎯 PORCENTAJE DE EXACTITUD ({etiqueta_metodo}): {score_exactitud:.2f}%")
        if metodo == "id_exacto":
            print(f"      id_solicitante usado    : {id_solicitante}")
            print(f"      ID OPERADOR en catálogo : {rpc_resultado.get('idBp', '')}")
        else:
            print(f"      Nombre usado ({origen_ganador}) : {nombre_original_usado}")
        print(f"      Nombre Oficial Catálogo  : {rpc_resultado['nombre_completo']}")

        # Actualizar nombre_operador al nombre oficial del catálogo
        resultado["nombre_operador"] = rpc_resultado["nombre_completo"]
        log.info("🔧 Nombre actualizado al oficial del catálogo.")
    elif rpc_resultado and not rpc_resultado.get("ok"):
        # Hay resultado pero con empate u otro problema
        resultado["rpc_resultado"] = rpc_resultado
        score_exactitud = rpc_resultado.get("score", 0) * 100
        log.warning("⚠️  RPC encontrado pero con problema (empate u otro): %.0f%%", score_exactitud)
        print(f"\n   ⚠️  EXACTITUD: {score_exactitud:.2f}% — Revisión manual requerida")

    nombre_final = resultado.get("nombre_operador") or ""

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
        asunto=asunto,
        tipo_tramite=tipo_tramite,
        fecha_limite=fecha_limite,
    )
    resultado["excel_ok"] = excel_ok

    # Organizar archivos
    if ORGANIZAR_DESCARGAS:
        if rpc_resultado and rpc_resultado.get("ok"):
            # RPC exitoso → carpeta estandarizada del concesionario
            ruta_destino = f"{rpc_resultado['ruta']}"
            destino = organizar_archivos(carpeta, ruta_destino)
            if destino:
                resultado["organizado_ok"] = True
        else:
            # Sin operador o coincidencia insuficiente → copiar carpeta a output\_sin_operador\{folio_id}
            # Usa rglob para copiar recursivamente (incluye archivos en subcarpetas de ZIPs extraídos)
            sin_op_dir = OUTPUT_BASE / "_sin_operador" / folio_id
            sin_op_dir.mkdir(parents=True, exist_ok=True)
            archivos_copiados = []
            for archivo in carpeta.rglob("*"):
                if archivo.is_file() and archivo.suffix.lower() != ".json":
                    # Reconstruir la ruta relativa para preservar subcarpetas
                    ruta_relativa = archivo.relative_to(carpeta)
                    destino_archivo = sin_op_dir / ruta_relativa
                    destino_archivo.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(archivo, destino_archivo)
                        archivos_copiados.append(str(ruta_relativa))
                    except Exception as e_copy:
                        log.warning("⚠️  No se pudo copiar %s: %s", archivo.name, e_copy)
            resultado["archivos_pendientes"] = archivos_copiados
            resultado["sin_operador_dir"] = str(sin_op_dir)
            if archivos_copiados:
                log.info("📂 Folio %s copiado a: %s (%d archivos)",
                         folio, sin_op_dir, len(archivos_copiados))
            else:
                log.warning("⚠️  Folio %s: no se copiaron archivos a _sin_operador", folio)

    return resultado


def imprimir_reporte(resultados: list):
    """Imprime el reporte final con un resumen ejecutivo orientado a la accion.

    Categorías (mutuamente excluyentes, en orden de prioridad):
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
    # 1) Exitosos: RPC encontrado, Excel actualizado y archivos organizados
    exitosos = [
        r for r in resultados
        if r.get('rpc_ok') and r.get('organizado_ok') and r.get('excel_ok')
    ]

    # Los no-exitosos se subdividen por si tienen nombre_operador o no
    no_exitosos = [r for r in resultados if r not in exitosos]

    # 2) Sin operador en catálogo: SATyS sí entregó el nombre del operador
    #    pero el id_solicitante no está en el catálogo RPC.
    #    Tienen sus archivos en output/_sin_operador/ y necesitan revisión manual.
    sin_operador = [
        r for r in no_exitosos
        if r.get('nombre_operador')  # hay nombre extraído de SATyS
    ]

    # 3) Errores reales: SATyS no devolvió nombre de operador en ninguna fuente
    errores = [
        r for r in no_exitosos
        if not r.get('nombre_operador')
    ]

    # ── Imprimir secciones ─────────────────────────────────────────────────
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
        "exitosos": len(exitosos),
        "sin_operador": len(sin_operador),
        "errores": len(errores),
    }



# ════════════════════════════════════════════════════════
#  FUNCIÓN PRINCIPAL
# ════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="SATyS — Procesamiento completo (Partes 1-4)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  main_procesar.py                      Partes 1-4 con folios por defecto
  main_procesar.py 6407 6801            Partes 1-4 con folios específicos
  main_procesar.py --solo-procesar      Procesa todos los folios en descargas/ con metadatos + Parte 3 + Parte 4
  main_procesar.py --solo-procesar 6407 Procesa folio específico con metadatos + Parte 3 + Parte 4
  main_procesar.py --rebuild-catalogo   Reconstruir catálogo RPC
        """,
    )
    parser.add_argument("folios", nargs="*",
                        help="Folios a procesar (si vacío, usa FOLIOS_DEFAULT)")
    parser.add_argument("--solo-procesar", action="store_true",
                        help="Omitir Parte 1 (descarga) y solo procesar archivos locales")
    parser.add_argument("--rebuild-catalogo", action="store_true",
                        help="Reconstruir el catálogo RPC desde cero")
    parser.add_argument("--no-organizar", action="store_true",
                        help="No mover archivos a carpetas RPC")
    parser.add_argument("--buscar", type=int, default=0,
                        help="Cantidad de folios existentes a buscar y procesar (ej: 27)")
    parser.add_argument("--desde", type=int, default=6407,
                        help="Folio inicial para la búsqueda (ej: 6407)")
    parser.add_argument("--archivo-folios", type=str, default="",
                        help="Ruta a un archivo .txt con la lista de folios a procesar (uno por línea)")
    parser.add_argument("--workers", type=int, default=10,
                        help="Número de ventanas de navegador a usar en Playwright (Parte 1)")
    parser.add_argument("--timeout-registro", type=int, default=900,
                        help="Timeout duro por Registro en segundos durante la descarga. Default: 900 (15 min).")
    parser.add_argument("--reintentos-registro", type=int, default=2,
                        help="Reintentos automáticos solo para registros incompletos. 2 = hasta 3 intentos totales.")
    parser.add_argument("--workers-reintento", type=int, default=2,
                        help="Workers usados en reintentos de registros fallidos/incompletos. Default: 2.")
    parser.add_argument("--headless", action="store_true",
                        help="Ocultar navegador de Playwright (ejecución en segundo plano).")
    parser.add_argument("--archivo-registro", type=str, default="",
                        help="Ruta a un archivo .txt con la lista de números de Registro a procesar "
                             "(uno por línea o separados por espacios/comas, ej. CRT26-002483). Activa el modo de búsqueda por Registro.")
    args = parser.parse_args()

    # ──── Bloqueo compartido: evita que 2+ laptops corran el proceso a la vez ────
    # Si otro equipo (u otra ejecución en esta misma laptop) ya está corriendo
    # main_procesar.py, se cancela aquí con un mensaje claro en vez de arriesgar
    # colisiones en SATyS, en TrámitesCRT.xlsx o en /output y /descargas.
    _lock = ProcesoLock(proceso="main_procesar.py")
    try:
        _lock.adquirir()
    except LockOcupadoError as e:
        log.error("🔒 %s", e)
        log.error("   Esta laptop no iniciará el proceso. Intenta de nuevo más tarde.")
        return

    # Configuración local
    global ORGANIZAR_DESCARGAS
    if args.no_organizar:
        ORGANIZAR_DESCARGAS = False

    # Banner
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + "  SATyS — PROCESAMIENTO COMPLETO (PARTES 1-4)  ".center(68) + "║")
    modo_label = "metadatos SATyS / JSON local"
    print("║" + f"  Extracción: {modo_label} • RPC: API REST • Fuzzy Matching  ".center(68) + "║")
    print("╚" + "═" * 68 + "╝\n")

    # ────────────────────────────────────────────────────────────────────────
    # MODO REGISTRO: buscar y descargar por número de Registro
    # ────────────────────────────────────────────────────────────────────────
    if args.archivo_registro:
        try:
            registros = cargar_registros_desde_archivo(args.archivo_registro)
            print(f"📄 Cargados {len(registros)} registro(s) desde {args.archivo_registro}")
        except Exception as e:
            log.error("❌ Error leyendo archivo de registros %s: %s", args.archivo_registro, e)
            return

        if not registros:
            log.error("❌ El archivo de registros está vacío o no contiene registros con formato CRT26-000000")
            return

        # Guardar la lista original solo como referencia.
        # El procesamiento local ya no se limitará a lo descargado en este intento;
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

        if registros_pendientes:
            # Usar solo los registros pendientes para la descarga.
            # Los ya completos NO se descargan de nuevo, pero SÍ se procesarán
            # después porque el procesamiento local escaneará descargas/ completo.
            registros = registros_pendientes

            # ── Ejecutar Parte 1 en modo registro ───────────────────────────
            try:
                import Parte1_descarga
            except ImportError as e:
                log.error("❌ No se encontró Parte1_descarga.py: %s", e)
                return

            Parte1_descarga.USUARIO   = SATYS_USUARIO
            Parte1_descarga.PASSWORD  = SATYS_PASSWORD
            Parte1_descarga.HEADLESS  = args.headless
            Parte1_descarga.DESCARGA_BASE = DESCARGA_BASE

            headless_flag = ["--headless"] if args.headless else ["--visible"]
            try:
                original_argv = sys.argv
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
                Parte1_descarga.main()
                sys.argv = original_argv
            except Exception as e:
                log.error("❌ Error en descarga por registro: %s", e)
                sys.argv = original_argv
                return
        else:
            log.info("✅ No hay registros pendientes para descargar. Se omite Parte 1 y se procesará descargas/ completo.")

        # Después de Parte1, procesar con metadatos + Parte 3 + Parte 4 TODO lo que está realmente
        # descargado en descargas/. Esto incluye:
        #   - registros que ya estaban completos antes de esta corrida,
        #   - registros recuperados en esta corrida,
        #   - carpetas cuyo nombre real es folio/VE aunque hayan venido de un registro CRT.
        carpetas_para_procesar = descubrir_descargas_procesables()

        if not carpetas_para_procesar:
            log.error("❌ No se encontraron carpetas procesables en %s. No se ejecuta procesamiento local.", DESCARGA_BASE)
            sincronizar_carpeta_compartida()
            return

        log.info(
            "✅ Procesamiento local procesará %d carpeta(s) descargada(s) detectada(s) en %s, no solo las de esta ejecución.",
            len(carpetas_para_procesar), DESCARGA_BASE,
        )

        # ── Cargar catálogo RPC para Parte 3 ─────────────────────────
        log.info("🗂️  Cargando catálogo RPC...")
        catalogo_r = []
        try:
            sys.path.append(os.path.join(str(_script_dir), "buscar_concesionario"))
            import buscar_concesionario as bc_r
            from descargar_concesiones_rpc import descargar_bd as descargar_bd_r
            from datetime import datetime as _dt_r

            bd_dir_r = Path(_script_dir) / "base_de_datos_rpc"
            bd_dir_r.mkdir(exist_ok=True)

            def _cat_reciente_r(bd):
                archivos = sorted(
                    bd.glob("03_concesiones_permisos_autorizaciones_*.xlsx"),
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
                return archivos[0] if archivos else None

            xlsx_r = _cat_reciente_r(bd_dir_r)
            if xlsx_r:
                cat_excel_r = bc_r.cargar_catalogo_desde_excel(str(xlsx_r), "copeau", solo_vigentes=False)
                catalogo_r = bc_r.preparar_catalogo_para_matching(cat_excel_r)
                log.info("✅ Catálogo RPC listo: %d concesionarios", len(catalogo_r))
        except Exception as e_cat:
            log.warning("⚠️  Catálogo RPC no disponible: %s", e_cat)

        # ── Verificar Excel ──────────────────────────────────────────
        if not EXCEL_PATH.exists():
            log.error("❌ No se encontró el Excel: %s", EXCEL_PATH)
            return

        # ── Metadatos + Parte 3 + Parte 4 para cada carpeta descargada ─
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
                modo_extraccion=MODO_EXTRACCION,
                carpeta=carpeta_reg,
                folio_id=folio_id_reg,
            )
            resultados_r.append(resultado_r)

        imprimir_reporte(resultados_r)

        # Guardar log de resultados
        log_path_r = DESCARGA_BASE / "procesamiento_log_registros.json"
        try:
            conteos_r = {
                "exitosos":     sum(1 for r in resultados_r if r.get('rpc_ok') and r.get('organizado_ok') and r.get('excel_ok')),
                "sin_operador": sum(1 for r in resultados_r if not (r.get('rpc_ok') and r.get('organizado_ok') and r.get('excel_ok')) and r.get('nombre_operador')),
                "errores":      sum(1 for r in resultados_r if not (r.get('rpc_ok') and r.get('organizado_ok') and r.get('excel_ok')) and not r.get('nombre_operador')),
            }
            log_data_r = {
                "fecha_ejecucion":  datetime.now().isoformat(),
                "modo":             "registro",
                "total_carpetas_descargas_procesadas": len(resultados_r),
                "registros_archivo_original": len(registros_archivo_original),
                "total_exitosos":   conteos_r["exitosos"],
                "total_sin_operador": conteos_r["sin_operador"],
                "total_errores":    conteos_r["errores"],
                "resultados": resultados_r,
            }
            with open(log_path_r, "w", encoding="utf-8") as f_log_r:
                json.dump(log_data_r, f_log_r, ensure_ascii=False, indent=2, default=str)
            log.info("📄 Log de registros guardado en: %s", log_path_r)
            log.info("📊 Resumen: %d exitosos | %d sin operador en catálogo | %d errores",
                     conteos_r["exitosos"], conteos_r["sin_operador"], conteos_r["errores"])
        except Exception:
            pass

        # Sincronizar output/ y Excel con la carpeta compartida de red
        sincronizar_carpeta_compartida()

        return  # Terminar sin continuar al flujo de folios

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
        log.warning("⚠️  Falló carga desde buscar_concesionario (%s). Usando Parte3_rpc...", e)
        catalogo = cargar_catalogo(force_rebuild=args.rebuild_catalogo)
        if catalogo:
            log.info("✅ Catálogo Parte3_rpc listo: %d concesionarios", len(catalogo))
        else:
            log.warning("⚠️  Sin catálogo — la búsqueda RPC usará solo API directa")

    # ──── Procesar cada folio (metadatos + Parte 3 + Parte 4) ────
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
                modo_extraccion=MODO_EXTRACCION,
                carpeta=carpeta_folio,
                folio_id=folio_id,
            )
            resultados.append(resultado)

    # Reporte
    imprimir_reporte(resultados)

    # Guardar log de resultados
    log_path = DESCARGA_BASE / "procesamiento_log.json"
    try:
        conteos = {
            "exitosos":     sum(1 for r in resultados if r.get('rpc_ok') and r.get('organizado_ok') and r.get('excel_ok')),
            "sin_operador": sum(1 for r in resultados if not (r.get('rpc_ok') and r.get('organizado_ok') and r.get('excel_ok')) and r.get('nombre_operador')),
            "errores":      sum(1 for r in resultados if not (r.get('rpc_ok') and r.get('organizado_ok') and r.get('excel_ok')) and not r.get('nombre_operador')),
        }
        log_data = {
            "fecha_ejecucion":    datetime.now().isoformat(),
            "modo_extraccion":    MODO_EXTRACCION,
            "total_folios":       len(resultados),
            "total_exitosos":     conteos["exitosos"],
            "total_sin_operador": conteos["sin_operador"],
            "total_errores":      conteos["errores"],
            "resultados": resultados,
        }
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2, default=str)
        log.info("📄 Log guardado en: %s", log_path)
        log.info("📊 Resumen: %d exitosos | %d sin operador en catálogo | %d errores",
                 conteos["exitosos"], conteos["sin_operador"], conteos["errores"])
    except Exception:
        pass

    # -- Generar / Actualizar Excel de Datos Consolidados --
    try:
        import generar_excel_folios
        generar_excel_folios.agregar_folios_a_excel(folios)
    except Exception as e:
        log.error("❌ Error al generar el Excel de folios procesados: %s", e)

    # -- Sincronizar con carpeta compartida de red --
    sincronizar_carpeta_compartida()


def sincronizar_carpeta_compartida() -> None:
    """
    Copia la carpeta 'output/' y el archivo 'TrámitesCRT.xlsx' al directorio
    definido por SATYS_CARPETA_COMPARTIDA, si esa variable está configurada.

    Estrategia de sincronización:
    - output/: merge inteligente. Los archivos y carpetas del local SOBREESCRIBEN
      los del destino si ya existen. Los archivos del destino que NO estén en el
      local permanecen intactos (no se eliminan). No se hacen backups.
    - TrámitesCRT.xlsx: siempre se sobreescribe con la versión local (la fuente
      de verdad). El Excel de red ya fue actualizado fila-por-fila en
      Parte4_excel.sincronizar_excel_a_red(), por lo que esta copia final
      garantiza que el archivo de red tenga el estado completo al terminar.
    """
    if CARPETA_COMPARTIDA is None:
        return

    print()
    print("─" * 70)
    print("  SINCRONIZACIÓN CON CARPETA COMPARTIDA (output/ + Excel)")
    print("─" * 70)

    destino = Path(CARPETA_COMPARTIDA)

    # Verificar accesibilidad de la unidad de red
    try:
        destino.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.error("❌ No se puede acceder a la carpeta compartida %s: %s", destino, e)
        log.error("   Verifica SATYS_CARPETA_COMPARTIDA y permisos de escritura.")
        return

    errores = []

    # ── Merge inteligente de la carpeta output/ ──
    if OUTPUT_BASE.exists():
        destino_output = destino / "output"
        destino_output.mkdir(parents=True, exist_ok=True)
        copiados = 0
        fallidos = 0
        for item in OUTPUT_BASE.rglob("*"):
            ruta_relativa = item.relative_to(OUTPUT_BASE)
            destino_item = destino_output / ruta_relativa
            try:
                if item.is_dir():
                    destino_item.mkdir(parents=True, exist_ok=True)
                else:
                    destino_item.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(item), str(destino_item))
                    copiados += 1
            except Exception as e:
                log.warning("⚠️  No se pudo copiar %s → %s: %s", item, destino_item, e)
                fallidos += 1
                errores.append(str(e))
        if fallidos == 0:
            log.info("✅ output/ sincronizado (%d archivos) → %s", copiados, destino_output)
        else:
            log.warning("⚠️  output/ sincronizado con %d archivos copiados y %d fallo(s).", copiados, fallidos)
    else:
        log.warning("⚠️  La carpeta output/ no existe localmente; nada que copiar.")

    # ── Copiar el Excel al destino (sobreescribir) ──
    if EXCEL_PATH.exists():
        destino_excel = destino / EXCEL_PATH.name
        try:
            shutil.copy2(str(EXCEL_PATH), str(destino_excel))
            log.info("✅ %s copiado a: %s", EXCEL_PATH.name, destino_excel)
        except PermissionError:
            log.warning(
                "⚠️  El Excel en la red está abierto por otro usuario. "
                "Se omite la copia final del Excel; la última sincronización automática "
                "(fila-por-fila) sigue siendo válida."
            )
        except Exception as e:
            log.error("❌ Error al copiar %s: %s", EXCEL_PATH.name, e)
            errores.append(str(e))
    else:
        log.warning("⚠️  El archivo %s no existe localmente; nada que copiar.", EXCEL_PATH.name)

    print("─" * 70)
    if errores:
        print(f"  ⚠️  Sincronización completada con {len(errores)} error(es) → {destino}")
    else:
        print(f"  ✅ Sincronización completada → {destino}")
    print("─" * 70)


if __name__ == "__main__":
    main()