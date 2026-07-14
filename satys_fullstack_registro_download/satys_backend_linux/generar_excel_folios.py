import os
import json
from pathlib import Path
import openpyxl
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("excel_exporter")

EXCEL_OUTPUT_PATH = Path("output") / "Folios_Datos_Completos.xlsx"
DESCARGAS_PATH = Path("descargas")

CAMPOS_ESPERADOS = [
    "folio",
    "registro",
    "asunto",
    "nombre_operador",
    "representante_legal",
    "id_representante_legal",
    "id_solicitante",
    "solicitante",
    "tipo_tramite",
    "fecha_registro",
    "fecha_ejecucion",
    "fecha_folio_opc",
    "descripcion"
]

def consolidar_datos_folio(carpeta_folio: Path) -> dict:
    """Lee los dos JSONs y consolida los datos."""
    datos_consolidados = {campo: "" for campo in CAMPOS_ESPERADOS}
    
    # Archivos a leer
    archivos_json = [
        carpeta_folio / "metadata_satys.json",
        carpeta_folio / "metadata_tramite_nuevo.json"
    ]
    
    for archivo in archivos_json:
        if archivo.exists():
            try:
                with open(archivo, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for key, value in data.items():
                        if key in CAMPOS_ESPERADOS:
                            # Preferimos valores no vacios
                            if value and str(value).strip():
                                datos_consolidados[key] = str(value).strip()
            except Exception as e:
                log.error("Error leyendo %s en %s: %s", archivo.name, carpeta_folio.name, e)
                
    return datos_consolidados

def descubrir_subcarpetas_folio(folio: str) -> list[Path]:
    """Descubre todas las carpetas que corresponden a un folio."""
    carpetas = []
    
    # Carpeta original
    carpeta_base = DESCARGAS_PATH / folio
    if carpeta_base.exists() and carpeta_base.is_dir():
        carpetas.append(carpeta_base)
        
    # Carpetas de tramites multiples
    n = 1
    while True:
        carpeta_extra = DESCARGAS_PATH / f"{folio}_{n}"
        if not carpeta_extra.exists():
            break
        # Las carpetas extra tienen subcarpetas por registro
        for subcarpeta in carpeta_extra.iterdir():
            if subcarpeta.is_dir():
                carpetas.append(subcarpeta)
        n += 1
        
    return carpetas

def agregar_folios_a_excel(folios: list[str]):
    """
    Recibe una lista de folios, extrae sus datos y los agrega a un Excel.
    Si el Excel no existe, lo crea.
    """
    if not folios:
        return
        
    log.info("📊 Exportando datos de %d folios a Excel...", len(folios))
    
    filas_a_agregar = []
    
    for folio in folios:
        folio_str = str(folio)
        carpetas = descubrir_subcarpetas_folio(folio_str)
        
        for carpeta in carpetas:
            datos = consolidar_datos_folio(carpeta)
            # Asegurar que el folio este en los datos si no venia en el JSON
            if not datos.get("folio"):
                datos["folio"] = folio_str
                
            fila = [datos.get(campo, "") for campo in CAMPOS_ESPERADOS]
            filas_a_agregar.append(fila)
            
    if not filas_a_agregar:
        log.warning("No se encontraron datos de JSON para los folios proporcionados.")
        return
        
    # Asegurar que la carpeta output existe
    EXCEL_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    workbook = None
    sheet = None
    
    # Cargar o crear el Excel
    if EXCEL_OUTPUT_PATH.exists():
        try:
            workbook = openpyxl.load_workbook(EXCEL_OUTPUT_PATH)
            sheet = workbook.active
        except Exception as e:
            log.error("Error cargando el Excel existente: %s", e)
            return
    else:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Datos Folios"
        # Agregar encabezados
        sheet.append([c.upper() for c in CAMPOS_ESPERADOS])
        # Formato basico a encabezados
        for cell in sheet[1]:
            cell.font = openpyxl.styles.Font(bold=True)
            
    # Agregar las filas
    for fila in filas_a_agregar:
        sheet.append(fila)
        
    # Ajustar ancho de columnas basico
    for col in sheet.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50) # Max width de 50
        sheet.column_dimensions[column].width = adjusted_width

    # Guardar
    try:
        workbook.save(EXCEL_OUTPUT_PATH)
        log.info("✅ Excel actualizado exitosamente: %s", EXCEL_OUTPUT_PATH)
    except PermissionError:
        log.error("❌ Error de permisos: El archivo %s esta abierto. Cierralo para poder actualizarlo.", EXCEL_OUTPUT_PATH.name)
    except Exception as e:
        log.error("❌ Error guardando el Excel: %s", e)

if __name__ == "__main__":
    # Prueba basica si se ejecuta directamente
    import sys
    if len(sys.argv) > 1:
        agregar_folios_a_excel(sys.argv[1:])
    else:
        print("Pasa al menos un folio como argumento.")
