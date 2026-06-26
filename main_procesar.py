#!/usr/bin/env python3
r"""
=============================================================
  PROYECTO SATyS — ORQUESTADOR PRINCIPAL
=============================================================
Ejecuta el flujo completo de procesamiento:

  Parte 1 → Descarga automática desde SATyS (Playwright)
  Parte 2 → Extracción de datos del PDF (Azure AI o pdfplumber)
  Parte 3 → Búsqueda en RPC (API REST, sin Playwright)
  Parte 4 → Actualización de Excel y organización de archivos

Uso:
  .\python_portable\python.exe main_procesar.py                    # Partes 2-4 (todos los folios)
  .\python_portable\python.exe main_procesar.py 6407 6801          # Partes 2-4 (folios específicos)
  .\python_portable\python.exe main_procesar.py --descarga         # Parte 1 + 2-4
  .\python_portable\python.exe main_procesar.py --descarga 6407    # Parte 1 (folio) + 2-4
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
SATYS_PASSWORD = os.getenv("SATYS_PASS", "Crt20261234*")
HEADLESS = False  # False = ver navegador | True = sin ventana

# ──── PARTE 2: Extracción de PDFs ────
# Cambia esta línea para elegir el modo de extracción:
#   "azure"      → Usa Azure AI Document Intelligence (más preciso, requiere internet)
#   "pdfplumber" → Usa pdfplumber + regex local (gratis, sin internet, menos preciso)
MODO_EXTRACCION = "azure"

# Credenciales Azure AI (solo necesarias si MODO_EXTRACCION = "azure")
AZURE_ENDPOINT = "https://foundrycenac.cognitiveservices.azure.com/"
AZURE_KEY = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "")

# ──── PARTE 3: Búsqueda en RPC ────
# El catálogo se descarga automáticamente la primera vez
# Usa --rebuild-catalogo para reconstruirlo

# ──── PARTE 4: Excel y archivos ────
ORGANIZAR_DESCARGAS = True  # True = mover archivos a carpetas RPC
# ════════════════════════════════════════════════════════════════


# ──── Imports de los módulos ────
from Parte3_rpc import buscar_en_rpc, cargar_catalogo
from Parte4_excel import actualizar_excel, organizar_archivos, obtener_nota_victor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SATyS-Main")


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
    # BUCLE DE REINTENTOS AUTOMÁTICOS — ACTIVO
    # Para desactivarlo: comenta el bloque 'while' de abajo y descomenta el bloque
    # 'EJECUCIÓN ÚNICA' que le sigue.
    # ═══════════════════════════════════════════════════════════════════════════
    while folios_actuales:
        intento += 1
        if intento > 1:
            log.warning("⚠️  [REINTENTO %d] Descargando %d folios incompletos...", intento, len(folios_actuales))
            time.sleep(5)

        try:
            # Guardar y restaurar sys.argv para que Parte1 use nuestros folios
            original_argv = sys.argv
            sys.argv = ["Parte1_descarga.py"] + headless_flag + ["--workers", str(workers), "--folios"] + folios_actuales
            Parte1_descarga.main()
            sys.argv = original_argv
        except Exception as e:
            log.error("❌ Error en descarga: %s", e)
            sys.argv = original_argv
            return False

        # Leer resumen_global.json para ver si hay incompletos
        resumen_path = DESCARGA_BASE / "resumen_global.json"
        if not resumen_path.exists():
            log.error("❌ No se generó resumen_global.json")
            return False

        try:
            with open(resumen_path, "r", encoding="utf-8") as f:
                resumen = json.load(f)
        except Exception as e:
            log.error("❌ Error leyendo resumen_global.json: %s", e)
            return False

        folios_incompletos_count = resumen.get("folios_incompletos", 0)
        if folios_incompletos_count == 0:
            log.info("✅ Todos los folios procesados sin errores incompletos.")
            break

        # Extraer cuáles fueron los folios incompletos.
        # Parte1_descarga.guardar_resumen_global() escribe la clave "estado"
        # (no "err" ni "no_encontrado", que no existen en el JSON real).
        nuevos_folios = []
        for d in resumen.get("detalle_folios", []):
            if d.get("estado") == "INCOMPLETO":
                nuevos_folios.append(str(d.get("folio")))

        if not nuevos_folios:
            break

        folios_actuales = nuevos_folios
    # ═══════════════════════════════════════════════════════════════════════════
    # FIN BUCLE DE REINTENTOS
    # ═══════════════════════════════════════════════════════════════════════════

    # ───────────────────────────────────────────────────────────────────────────
    # EJECUCIÓN ÚNICA (sin reintentos automáticos) — ACTUALMENTE DESACTIVADO
    # Para activar: comenta el bloque 'while' de arriba y descomenta este bloque.
    # ───────────────────────────────────────────────────────────────────────────
    # try:
    #     original_argv = sys.argv
    #     sys.argv = ["Parte1_descarga.py"] + headless_flag + ["--workers", str(workers), "--folios"] + folios_actuales
    #     Parte1_descarga.main()
    #     sys.argv = original_argv
    # except Exception as e:
    #     log.error("❌ Error en descarga: %s", e)
    #     sys.argv = original_argv
    #     return False
    # log.info("✅ Descarga completada (ejecución única sin reintentos automáticos).")
    # ───────────────────────────────────────────────────────────────────────────
    # FIN EJECUCIÓN ÚNICA
    # ───────────────────────────────────────────────────────────────────────────

    return True



# ────────────────────────────────────────────────────────
#  PARTES 2-4: Procesamiento
# ────────────────────────────────────────────────────────

def procesar_folio(
    folio: str,
    catalogo: list,
    modo_extraccion: str = "azure",
    azure_endpoint: str = "",
    azure_key: str = "",
) -> dict:
    """
    Procesa un folio completo: PDF → RPC → Excel.
    """
    resultado = {
        "folio": folio,
        "pdf_encontrado": False,
        "nombre_operador": None,
        "representante_legal": None,
        "formatos": {},
        "imagen_sello": None,
        "fecha_sello": None,
        "rpc_ok": False,
        "excel_ok": False,
        "organizado_ok": False,
        "modo_extraccion": None,
    }

    carpeta = DESCARGA_BASE / folio
    if not carpeta.exists():
        log.error("❌ Carpeta no existe: %s", carpeta)
        return resultado

    # ──── LECTURA DE METADATOS (Omitiendo Parte 2) ────
    log.info("📄 [PARTE 2-OMITIDA] Leyendo metadatos de JSON directamente...")

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
        except Exception as e:
            log.warning("⚠️  No se pudo leer metadatos de %s: %s", meta_path, e)

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
        if rpc_resultado is None:
            nombre_pdf = datos_pdf.get("nombre_operador")
            nombre_web = datos_pdf.get("nombre_operador_web")
            nombres_a_probar = []
            if nombre_web: nombres_a_probar.append((nombre_web, "Web"))
            if nombre_pdf and nombre_pdf != nombre_web: nombres_a_probar.append((nombre_pdf, "PDF"))

            if nombres_a_probar:
                log.info("🌐 [PARTE 3 - FALLBACK] Buscando por similitud de nombre...")
                mejor_score = -1
                mejor_match = None
                empate = False
                for nom, origen in nombres_a_probar:
                    matches = bc.buscar_coincidencias(nom, catalogo, top_n=5)
                    if matches:
                        score, best_match = matches[0]
                        if score > mejor_score:
                            mejor_score = score
                            mejor_match = best_match
                            origen_ganador = origen
                            nombre_original_usado = nom

                            # Revisar empate de IDs distintos con el mismo score
                            empate = False
                            if len(matches) > 1 and matches[1][0] == score:
                                id1 = best_match.get("idBp")
                                for s2, m2 in matches[1:]:
                                    if s2 == score and m2.get("idBp") != id1:
                                        empate = True
                                        break

                SCORE_MINIMO_FUZZY = 0.80
                if mejor_match and mejor_score >= SCORE_MINIMO_FUZZY:
                    rpc_resultado = {
                        "nombre_completo": mejor_match["concesionario"],
                        "numero_rpc":      mejor_match.get("idBp", ""),
                        "idBp":            mejor_match.get("idBp", ""),
                        "ruta":            construir_ruta(mejor_match["concesionario"], mejor_match.get("idBp", "")),
                        "score":           mejor_score,
                        "ok":              not empate,
                        "empate":          empate,
                        "metodo":          "fuzzy_nombre",
                    }
                elif mejor_match:
                    log.warning("⚠️  Mejor score fuzzy %.0f%% < mínimo %.0f%%. Sin coincidencia.",
                                mejor_score * 100, SCORE_MINIMO_FUZZY * 100)
            else:
                log.warning("⚠️  Sin nombre de operador en PDF ni Web, se omite búsqueda RPC")
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
    )
    resultado["excel_ok"] = excel_ok

    # Organizar archivos
    if ORGANIZAR_DESCARGAS:
        if rpc_resultado and rpc_resultado.get("ok"):
            # RPC exitoso → carpeta estandarizada del concesionario + folio
            ruta_destino = f"{rpc_resultado['ruta']}\\{folio}"
            destino = organizar_archivos(carpeta, ruta_destino)
            if destino:
                resultado["organizado_ok"] = True
        elif resultado["pdf_encontrado"]:
            # Sin operador o coincidencia insuficiente → copiar carpeta a output\_sin_operador\{folio}
            sin_op_dir = OUTPUT_BASE / "_sin_operador" / folio
            sin_op_dir.mkdir(parents=True, exist_ok=True)
            archivos_copiados = []
            for archivo in carpeta.iterdir():
                if archivo.is_file() and archivo.suffix.lower() != ".json":
                    try:
                        shutil.copy2(archivo, sin_op_dir / archivo.name)
                        archivos_copiados.append(archivo.name)
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
    """Imprime el reporte final con un resumen ejecutivo orientado a la accion."""
    print("\n" + "═" * 70)
    print("  RESUMEN EJECUTIVO — ACCIONES REQUERIDAS")
    print("═" * 70)

    exitosos = [r for r in resultados if r.get('rpc_ok') and r.get('organizado_ok') and r.get('excel_ok')]
    dudosos = [r for r in resultados if r.get('pdf_encontrado') and not r.get('rpc_ok') and not r.get('rpc_resultado', {}).get('empate')]
    empates = [r for r in resultados if r.get('pdf_encontrado') and r.get('rpc_resultado', {}).get('empate')]
    errores = [r for r in resultados if not r.get('pdf_encontrado') or (r.get('rpc_ok') and not r.get('excel_ok'))]

    print(f"\n  🟢 ÉXITO TOTAL ({len(exitosos)} folios):")
    if not exitosos:
        print("       Ninguno.")
    for r in exitosos:
        print(f"       ✓ {r['folio']} -> Organizado en: {r.get('rpc_resultado', {}).get('nombre_completo', 'N/A')}")
        
    print(f"\n  🟠 DUPLICADOS EN RPC ({len(empates)} folios) - REVISIÓN MANUAL:")
    if not empates:
        print("       Ninguno.")
    for r in empates:
        nombre = r.get('rpc_resultado', {}).get('nombre_completo', 'N/A')
        print(f"       ⚠️ {r['folio']} -> El sistema encontró MÁS DE UN '{nombre}' (con distintos IDs) en la BD.")
        print(f"          👉 ACCIÓN: Revisa manualmente en qué carpeta de concesionario debe ir y muévelo desde 'output\\_sin_operador\\{r['folio']}'.")

    print(f"\n  🟡 COINCIDENCIA BAJA ({len(dudosos)} folios) - REVISIÓN MANUAL:")
    if not dudosos:
        print("       Ninguno.")
    for r in dudosos:
        score = r.get('rpc_resultado', {}).get('score', 0) * 100 if r.get('rpc_resultado') else 0
        nombre_web = r.get('nombre_operador', 'N/A')
        print(f"       ⚠️ {r['folio']}")
        print(f"          Coincidencia insuficiente: {score:.0f}% (El sistema detectó '{nombre_web}')")
        print(f"          👉 ACCIÓN: Mueve los archivos desde 'output\\_sin_operador\\{r['folio']}' a la carpeta correcta.")

    print(f"\n  🔴 ERRORES ({len(errores)} folios):")
    if not errores:
        print("       Ninguno.")
    for r in errores:
        if not r.get('pdf_encontrado'):
            print(f"       ✗ {r['folio']} -> No se descargó PDF. Revisa el portal SATyS.")
        else:
            print(f"       ✗ {r['folio']} -> Error al organizar o al actualizar el Excel.")

    print("\n" + "═" * 70 + "\n")



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
  main_procesar.py --solo-procesar      Partes 2-4 con todos los folios en descargas/
  main_procesar.py --solo-procesar 6407 Partes 2-4 con folio específico
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
    parser.add_argument("--headless", action="store_true",
                        help="Ocultar navegador de Playwright (ejecución en segundo plano).")
    args = parser.parse_args()

    # Configuración local
    global ORGANIZAR_DESCARGAS
    if args.no_organizar:
        ORGANIZAR_DESCARGAS = False

    # Banner
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + "  SATyS — PROCESAMIENTO COMPLETO (PARTES 1-4)  ".center(68) + "║")
    modo_label = "Azure AI" if MODO_EXTRACCION == "azure" else "pdfplumber (local)"
    print("║" + f"  Extracción: {modo_label} • RPC: API REST • Fuzzy Matching  ".center(68) + "║")
    print("╚" + "═" * 68 + "╝\n")

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

    # ──── Procesar cada folio (Partes 2-4) ────
    resultados = []
    for i, folio in enumerate(folios, 1):
        print(f"\n{'─' * 70}")
        print(f"  [{i}/{len(folios)}] PROCESANDO FOLIO: {folio}")
        print(f"{'─' * 70}")

        resultado = procesar_folio(
            folio=folio,
            catalogo=catalogo,
            modo_extraccion=MODO_EXTRACCION,
            azure_endpoint=AZURE_ENDPOINT,
            azure_key=AZURE_KEY,
        )
        resultados.append(resultado)

    # Reporte
    imprimir_reporte(resultados)

    # Guardar log de resultados
    log_path = DESCARGA_BASE / "procesamiento_log.json"
    try:
        log_data = {
            "fecha_ejecucion": datetime.now().isoformat(),
            "modo_extraccion": MODO_EXTRACCION,
            "total_folios": len(resultados),
            "total_exitosos": sum(1 for r in resultados if r["excel_ok"]),
            "resultados": resultados,
        }
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2, default=str)
        log.info("📄 Log guardado en: %s", log_path)
    except Exception:
        pass


if __name__ == "__main__":
    main()