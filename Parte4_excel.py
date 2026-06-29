#!/usr/bin/env python3
r"""
=============================================================
  PARTE 4 — ACTUALIZACIÓN DE EXCEL Y ORGANIZACIÓN DE ARCHIVOS
=============================================================
Actualiza el archivo TrámitesCRT.xlsx con los datos extraídos
y organiza los archivos descargados en carpetas estandarizadas.

Uso como módulo:
  from Parte4_excel import actualizar_excel, organizar_archivos

Uso independiente:
  .\python_portable\python.exe Parte4_excel.py  (no se usa solo normalmente)
=============================================================
"""

import sys
import io
import re
import shutil
import logging
import traceback
from pathlib import Path
from datetime import datetime

# Forzar UTF-8 (solo si no está ya configurado)
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, 'encoding', '') != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.drawing.image import Image as XLImage
except ImportError:
    print("ERROR: Instala openpyxl con:")
    print("  .\\python_portable\\python.exe -m pip install openpyxl")
    sys.exit(1)

# ════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ════════════════════════════════════════════════════════

DESCARGA_BASE = Path("descargas")
OUTPUT_BASE = Path("output")
EXCEL_PATH = Path("TrámitesCRT.xlsx")
SHEET_NAME = "Turnados recibidos"

ORGANIZAR_DESCARGAS = True
BORRAR_CARPETA_FOLIO_VACIA = False

EXCEL_EXTS = {".xls", ".xlsx", ".xlsm", ".xlsb", ".csv"}
WORD_EXTS = {".doc", ".docx"}

# ════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SATyS-Excel")


# ────────────────────────────────────────────────────────
#  NOTAS VÍCTOR (tipos de archivo)
# ────────────────────────────────────────────────────────

def obtener_nota_victor(carpeta: Path) -> str:
    """
    Devuelve nota sobre formatos entregados, solo si hay archivos
    distintos de .pdf, .csv y .png (imágenes de sello).

    Ejemplo de salida:
      "Formato entregado en .docx"
      "Formato entregado en .xlsx, .docx"
      ""  (si solo hay PDF/CSV o la carpeta está vacía)
    """
    # Extensiones de archivo que son "solo infraestructura" del pipeline
    _EXCLUIR = {".pdf", ".csv", ".png", ".jpg", ".jpeg", ".tmp"}

    exts_relevantes = set()
    for archivo in carpeta.glob("*"):
        if archivo.is_file():
            ext = archivo.suffix.lower()
            if ext and ext not in _EXCLUIR:
                exts_relevantes.add(ext)

    if not exts_relevantes:
        return ""

    exts_ordenadas = ", ".join(sorted(exts_relevantes))
    return f"Formato entregado en {exts_ordenadas}"


# ────────────────────────────────────────────────────────
#  ORGANIZACIÓN DE ARCHIVOS
# ────────────────────────────────────────────────────────

def _ruta_a_path(ruta: str) -> Path:
    """Convierte una ruta con separadores mixtos a Path."""
    partes = [p for p in re.split(r"[\\/]+", ruta) if p]
    return Path(*partes)


def _destino_sin_colision(destino: Path, item: Path) -> Path:
    """Retorna la ruta destino. En esta versión, siempre sobrescribe el archivo original."""
    return destino / item.name


def organizar_archivos(carpeta_folio: Path, ruta: str) -> Path | None:
    """
    Mueve los archivos de la carpeta del folio a la ruta estandarizada.
    Retorna la ruta destino o None.
    """
    if not ruta:
        return None

    destino = OUTPUT_BASE / _ruta_a_path(ruta)
    destino.mkdir(parents=True, exist_ok=True)

    copiados = 0
    for item in carpeta_folio.iterdir():
        if item.resolve() == destino.resolve():
            continue
            
        # Excluir archivos JSON de la organización
        if item.suffix.lower() == ".json":
            continue

        target = _destino_sin_colision(destino, item)
        try:
            # Si el destino ya existe, lo eliminamos para que shutil.copy sobrescriba limpiamente
            if target.exists():
                if target.is_file():
                    target.unlink()
                else:
                    shutil.rmtree(target)
            
            if item.is_file():
                shutil.copy2(str(item), str(target))
            else:
                shutil.copytree(str(item), str(target), dirs_exist_ok=True)
            copiados += 1
        except Exception as e:
            log.error("❌ No se pudo copiar %s → %s: %s", item.name, target, e)

    if copiados:
        log.info("📁 %d archivos copiados a: %s", copiados, destino)

    return destino


# ────────────────────────────────────────────────────────
#  BÚSQUEDA DE FILA EN EXCEL
# ────────────────────────────────────────────────────────

def _buscar_fila(ws, folio: str, registro: str = None, col_registro: int = None) -> int | None:
    """
    Busca la fila que corresponde a un (folio, registro).

    Un mismo folio puede tener MAS DE UN tramite/registro asociado en SATyS
    (ej. folio 1660 con registros CRT26-020606 y CRT26-002483, cada uno con
    su propio operador/representante). Por eso, cuando se pasa 'registro':

      1. Si ya existe una fila para este folio CON ESE MISMO registro
         -> se reutiliza esa fila (idempotente: volver a correr el script
         no duplica filas).
      2. Si existe una fila para este folio pero SIN registro asignado aun
         -> se reutiliza esa fila (primera vez que se completa el dato).
      3. Si todas las filas existentes de este folio ya tienen un registro
         DISTINTO -> se devuelve None para que el caller cree una fila nueva
         (es un tramite distinto del mismo folio, necesita su propia fila).

    Si no se pasa 'registro', se conserva el comportamiento original:
    primera fila cuyo texto en columna D o E contenga el folio.
    """
    candidatos = []  # (fila, valor_registro_en_esa_fila)
    for row in range(2, ws.max_row + 1):
        # Columna D (1711)
        celda_d = ws.cell(row=row, column=4).value
        coincide = bool(celda_d and str(folio) in str(celda_d).upper())
        # Columna E (Memo/Volante)
        celda_e = ws.cell(row=row, column=5).value
        coincide = coincide or bool(celda_e and str(folio) in str(celda_e))

        if coincide:
            valor_registro = None
            if col_registro:
                valor_registro = ws.cell(row=row, column=col_registro).value
            candidatos.append((row, valor_registro))

    if not candidatos:
        return None

    if not registro:
        # Comportamiento original: primera coincidencia por folio.
        return candidatos[0][0]

    registro_norm = str(registro).strip().upper()

    # 1) Coincidencia exacta de registro -> misma fila (idempotente)
    for row, valor_registro in candidatos:
        if valor_registro and str(valor_registro).strip().upper() == registro_norm:
            return row

    # 2) Fila de este folio que aun no tiene registro asignado -> reusar
    for row, valor_registro in candidatos:
        if not valor_registro:
            return row

    # 3) Todas las filas de este folio ya tienen un registro DISTINTO
    #    -> es un tramite distinto del mismo folio, necesita fila nueva.
    return None


# ────────────────────────────────────────────────────────
#  ACTUALIZACIÓN DEL EXCEL
# ────────────────────────────────────────────────────────

def actualizar_excel(
    folio: str,
    registro: str = "",
    nombre_operador: str = "",
    representante_legal: str = "",
    formatos: dict = None,
    rpc_resultado: dict = None,
    nota_victor: str = "",
    imagen_sello: Path = None,
    fecha_sello: str = "",
    excel_path: Path = None,
    sheet_name: str = None,
) -> bool:
    """
    Actualiza la fila correspondiente al folio en el Excel.
    Retorna True si fue exitoso.
    """
    excel = excel_path or EXCEL_PATH
    sheet = sheet_name or SHEET_NAME
    formatos = formatos or {}

    try:
        wb = openpyxl.load_workbook(excel)
        ws = wb[sheet]

        # Leer encabezados dinámicamente
        encabezados = {}
        for col in range(1, ws.max_column + 1):
            header = ws.cell(row=1, column=col).value
            if header:
                encabezados[str(header).strip()] = col

        # Mapeo de columnas
        col_1711        = encabezados.get("1711", 4)
        col_memo        = encabezados.get("Memo/Volante", 5)
        col_solicitante = encabezados.get("Solicitante Promovente", 6)
        col_rep         = encabezados.get("Representante Legal", 7)
        col_fecha       = encabezados.get("Fecha de creación", 10)
        col_ruta        = encabezados.get("Ruta", 13)
        col_notas       = encabezados.get("NOTAS_VICTOR", 42)

        # Buscar fila (distinguiendo por registro si un folio tiene varios tramites)
        fila = _buscar_fila(ws, folio, registro=registro, col_registro=col_1711)
        if fila is None:
            try:
                fila = _buscar_fila(ws, str(int(folio)), registro=registro, col_registro=col_1711)
            except (TypeError, ValueError):
                pass

        if fila is None:
            fila = ws.max_row + 1
            log.info("➕ Agregando nueva fila %d para folio %s (registro %s)", fila, folio, registro or "N/A")
            ws.cell(row=fila, column=col_memo, value=folio)
        else:
            log.info("📝 Actualizando fila %d para folio %s (registro %s)", fila, folio, registro or "N/A")

        # Escribir datos
        if registro:
            ws.cell(row=fila, column=col_1711, value=registro)
        if nombre_operador:
            ws.cell(row=fila, column=col_solicitante, value=nombre_operador)
        if representante_legal:
            ws.cell(row=fila, column=col_rep, value=representante_legal)
        if fecha_sello:
            fecha_sello = fecha_sello.replace("-", "/")
            ws.cell(row=fila, column=col_fecha, value=fecha_sello)
            log.info("   📅 Fecha sello → col %s: %s", get_column_letter(col_fecha), fecha_sello)
        if rpc_resultado and rpc_resultado.get("ok"):
            ws.cell(row=fila, column=col_ruta, value=rpc_resultado["ruta"])

        # Formatos R001–R027
        for fmt, presente in formatos.items():
            if presente and fmt in encabezados:
                col = encabezados[fmt]
                ws.cell(row=fila, column=col, value=1)
                log.info("   ✅ Marcado %s en columna %s", fmt, get_column_letter(col))

        # NOTAS_VICTOR: solo formato entregado (nunca score RPC)
        if nota_victor:
            valor_actual = ws.cell(row=fila, column=col_notas).value
            if valor_actual and nota_victor not in str(valor_actual):
                nota_victor = f"{valor_actual}; {nota_victor}"
            elif valor_actual and nota_victor in str(valor_actual):
                nota_victor = str(valor_actual)  # ya está, no duplicar
            ws.cell(row=fila, column=col_notas, value=nota_victor)

        wb.save(excel)
        wb.close()
        log.info("💾 Excel guardado: %s", excel)
        return True

    except PermissionError:
        log.error("❌ El archivo Excel está abierto. Ciérralo e inténtalo de nuevo.")
        return False
    except Exception as e:
        log.error("❌ Error actualizando Excel: %s", e)
        traceback.print_exc()
        return False


# ────────────────────────────────────────────────────────
#  CLI
# ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Parte4_excel.py — Módulo de actualización de Excel.")
    print("Normalmente se ejecuta desde main_procesar.py")
    print()
    print("Funciones disponibles:")
    print("  actualizar_excel(folio, pdf_nombre, nombre_operador, ...)")
    print("  organizar_archivos(carpeta_folio, ruta)")
    print("  obtener_nota_victor(carpeta)")