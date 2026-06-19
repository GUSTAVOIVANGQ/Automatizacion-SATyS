#!/usr/bin/env python3
r"""
=============================================================
  PARTE 2 — EXTRACCIÓN DE DATOS DEL PDF
=============================================================
Extrae datos de los PDFs descargados por Parte1.

Dos modos de extracción:
  - Azure AI Document Intelligence (si hay credenciales)
  - pdfplumber + regex (fallback sin costo)

Datos extraídos:
  - Nombre o razón social del Operador
  - Representante Legal
  - Formatos marcados con "X" (R001–R027)

Uso como módulo:
  from Parte2_extraer import extraer_datos_pdf
  datos = extraer_datos_pdf(Path("descargas/6407"))

Uso independiente:
  .\python_portable\python.exe Parte2_extraer.py descargas\6407
=============================================================
"""

import sys
import io
import os
import re
import json
import logging
from pathlib import Path

# Forzar UTF-8 en consola Windows (solo si no está ya configurado)
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, 'encoding', '') != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ════════════════════════════════════════════════════════

# Credenciales Azure AI Document Intelligence
AZURE_ENDPOINT = os.environ.get(
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
    "https://foundrycenac.cognitiveservices.azure.com/"
)
AZURE_KEY = os.environ.get(
    "AZURE_DOCUMENT_INTELLIGENCE_KEY",
    ""
)

# Carpeta donde se guardan las imágenes de sellos (fuera de las carpetas de folio)
SELLOS_DIR = Path.home() / "Downloads" / "SATyS" / "sellos"

# Formatos R001–R027 que se buscan en el PDF
FORMATOS = [f"R{str(i).zfill(3)}" for i in range(1, 28)]

# Diccionario de meses en español (incluye errores OCR frecuentes)
_MESES_OCR = {
    "ENE": "01", "FEB": "02", "MAR": "03", "ABR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AGO": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DIC": "12",
    # Variantes por errores OCR comunes
    "MAP": "03", "MAF": "03", "MRA": "03",
    "ENR": "01", "EHE": "01",
    # Nombres completos
    "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04",
    "MAYO": "05", "JUNIO": "06", "JULIO": "07", "AGOSTO": "08",
    "SEPTIEMBRE": "09", "OCTUBRE": "10", "NOVIEMBRE": "11", "DICIEMBRE": "12",
}

# ════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SATyS-Extraer")

# Silenciar logs verbosos de Azure SDK
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)


# ────────────────────────────────────────────────────────
#  UTILIDADES COMUNES
# ────────────────────────────────────────────────────────

def encontrar_pdf(carpeta: Path) -> Path | None:
    """Encuentra el PDF principal (preferentemente CRT*) en la carpeta."""
    pdfs = list(carpeta.glob("*.pdf"))
    if not pdfs:
        log.warning("⚠️  No se encontró PDF en %s", carpeta)
        return None
    crt = [p for p in pdfs if p.stem.upper().startswith("CRT")]
    pdf = crt[0] if crt else pdfs[0]
    log.info("📄 PDF encontrado: %s", pdf.name)
    return pdf


def limpiar_candidato(texto: str) -> str:
    """Limpia ruidos comunes de un candidato de nombre."""
    texto = re.sub(r"[_|\-]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" \t-:;")
    return texto.strip()


def clean_azure_value(val: str) -> str:
    """Limpia ruidos del valor extraído por Azure AI."""
    if not val:
        return ""
    val = val.replace('\n', ' ')
    val = re.sub(r'[\._\-]{2,}', ' ', val)
    val = val.strip(' \t\n\r|-_.,^©—<>«»*&#?()[]{}')
    val = re.sub(r'\s{2,}', ' ', val)
    return val.strip()


# ────────────────────────────────────────────────────────
#  CAPTURA DE IMAGEN DEL SELLO CRT
# ────────────────────────────────────────────────────────

def _detectar_sello_por_color(pil_image):
    """
    Detecta el bounding box del sello CRT escaneando TODA la página.

    Estrategia de componentes conectados:
      1. Trabaja con miniatura al 20% para velocidad.
      2. Marca píxeles azules (canal B dominante, no muy oscuro ni blanco).
      3. Agrupa píxeles vecinos en "blobs" usando flood-fill simple.
      4. Elige el blob más grande → ese es el sello.
      5. Convierte a coordenadas originales con padding mínimo (15px).

    Retorna (x1, y1, x2, y2) en coordenadas originales, o None.
    """
    img_w, img_h = pil_image.size

    # Miniatura al 20% para velocidad (5× más rápido que al 25%)
    escala = 5
    thumb_w, thumb_h = img_w // escala, img_h // escala
    thumb = pil_image.resize((thumb_w, thumb_h)).convert("RGB")

    # Paso 1: crear mapa binario de píxeles azules en TODA la página
    blue = [[False] * thumb_w for _ in range(thumb_h)]
    for y in range(thumb_h):
        for x in range(thumb_w):
            r, g, b = thumb.getpixel((x, y))
            # Azul CRT: B dominante, no demasiado oscuro (<30 total = negro),
            # no blanco (suma < 640), y B supera R y G por margen
            if (b > 85
                    and b > r + 18
                    and b > g + 8
                    and (r + g + b) > 60      # no negro puro
                    and (r + g + b) < 660):   # no blanco
                blue[y][x] = True

    # Paso 2: componentes conectados (4-vecinos) con BFS
    visited = [[False] * thumb_w for _ in range(thumb_h)]
    blobs = []   # lista de listas de (x,y)

    for y0 in range(thumb_h):
        for x0 in range(thumb_w):
            if blue[y0][x0] and not visited[y0][x0]:
                # BFS desde este píxel
                comp = []
                queue = [(x0, y0)]
                visited[y0][x0] = True
                while queue:
                    cx, cy = queue.pop()
                    comp.append((cx, cy))
                    for nx, ny in ((cx+1,cy),(cx-1,cy),(cx,cy+1),(cx,cy-1)):
                        if 0 <= nx < thumb_w and 0 <= ny < thumb_h:
                            if blue[ny][nx] and not visited[ny][nx]:
                                visited[ny][nx] = True
                                queue.append((nx, ny))
                blobs.append(comp)

    if not blobs:
        return None

    # Paso 3: elegir el blob más grande (el sello tiene muchos px azules)
    blob = max(blobs, key=len)

    # Mínimo de píxeles para considerarlo sello (no un px de texto azul)
    if len(blob) < 25:
        return None

    xs = [p[0] for p in blob]
    ys = [p[1] for p in blob]

    # Paso 4: escalar a coordenadas originales con padding mínimo (3px en miniatura = 15px real)
    pad = 3
    x1 = max(0, (min(xs) - pad) * escala)
    y1 = max(0, (min(ys) - pad) * escala)
    x2 = min(img_w, (max(xs) + pad + 1) * escala)
    y2 = min(img_h, (max(ys) + pad + 1) * escala)

    if (x2 - x1) < 40 or (y2 - y1) < 25:
        return None

    return (x1, y1, x2, y2)





def _parsear_fecha_hora(texto: str, fuente: str) -> str | None:
    """
    Extrae fecha + hora de un texto OCR del sello CRT.
    Fecha impresa:  '10 MAR. 2026'  →  DD/MM/YYYY
    Hora manuscrita: '11:14'        →  HH:MM:00  (00:00:00 si no hay)
    """
    # ── Fecha ──────────────────────────────────────────────────────
    m_fecha = re.search(
        r'(\d{1,2})\s+([A-ZÁÉÍÓÚ]{3,10})\.?\s+(\d{4})',
        texto.upper()
    )
    if not m_fecha:
        log.warning("⚠️  [%s] No se encontró fecha en el texto del sello", fuente)
        return None

    dia_int = int(m_fecha.group(1)) if m_fecha.group(1).isdigit() else 0
    mes_num = _MESES_OCR.get(m_fecha.group(2)[:3])
    anio    = m_fecha.group(3)

    if not (1 <= dia_int <= 31 and mes_num):
        log.warning("⚠️  [%s] Fecha inválida: día=%s mes=%s", fuente, dia_int, m_fecha.group(2)[:3])
        return None

    fecha_str = f"{str(dia_int).zfill(2)}/{mes_num}/{anio}"
    log.info("📅 [%s] Fecha sello: %s", fuente, fecha_str)

    # ── Hora manuscrita ────────────────────────────────────────────
    hora_str = "00:00:00"
    
    # Limpiamos el texto para quitar comillas o ruidos comunes al final
    texto_limpio = re.sub(r'["\']', '', texto)
    
    # Buscamos patrones de hora, tolerando errores OCR comunes:
    # Ejemplos: 11:114 -> 11:11, 1: 11" -> 01:11, 14:39 -> 14:39
    patrones = [
        r'\b(\d{1,2})\s*[:;.]\s*(\d{2})\b',
        r'(\d{1,2})\s*[:;.]\s*(\d{2})'
    ]
    
    encontrado = False
    for patron in patrones:
        for m_hora in re.finditer(patron, texto_limpio):
            hh, mm = int(m_hora.group(1)), int(m_hora.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                hora_str = f"{str(hh).zfill(2)}:{str(mm).zfill(2)}:00"
                log.info("🕐 [%s] Hora sello (manuscrita): %s", fuente, hora_str)
                encontrado = True
                break
        if encontrado:
            break

    return f"{fecha_str} {hora_str}"





def _leer_fecha_azure_read(png_path: Path) -> str | None:
    """
    Intento 2 (fallback): Azure AI Vision 'Read' API.
    Modelo especializado en manuscritos mezclados con texto impreso.
    Usa las mismas credenciales de Azure ya configuradas en el proyecto.
    """
    try:
        from azure.cognitiveservices.vision.computervision import ComputerVisionClient
        from azure.cognitiveservices.vision.computervision.models import OperationStatusCodes
        from msrest.authentication import CognitiveServicesCredentials
        import time

        # Reusar endpoint/key de Azure ya configurados
        endpoint = AZURE_ENDPOINT.rstrip("/")
        key      = AZURE_KEY

        if not endpoint or not key:
            log.warning("⚠️  Azure Read: sin credenciales configuradas")
            return None

        client = ComputerVisionClient(endpoint, CognitiveServicesCredentials(key))

        with open(png_path, "rb") as f:
            read_response = client.read_in_stream(f, raw=True)

        # Obtener operation-location del header
        op_url      = read_response.headers["Operation-Location"]
        op_id       = op_url.split("/")[-1]

        # Esperar resultado (máx 10 segundos)
        for _ in range(10):
            result = client.get_read_result(op_id)
            if result.status not in [OperationStatusCodes.running,
                                     OperationStatusCodes.not_started]:
                break
            time.sleep(1)

        if result.status != OperationStatusCodes.succeeded:
            log.warning("⚠️  Azure Read: operación no exitosa (%s)", result.status)
            return None

        # Unir todas las líneas detectadas
        lineas = []
        for page in result.analyze_result.read_results:
            for line in page.lines:
                lineas.append(line.text)
        texto = "\n".join(lineas)
        log.debug("🔍 Azure Read texto:\n%s", texto)
        return _parsear_fecha_hora(texto, "Azure Read")

    except ImportError:
        log.warning("⚠️  azure-cognitiveservices-vision-computervision no instalado:")
        log.warning("     pip install azure-cognitiveservices-vision-computervision msrest")
        return None
    except Exception as e:
        log.warning("⚠️  Error con Azure Read: %s", e)
        return None


def _leer_fecha_del_sello(png_path: Path) -> str | None:
    """
    Lee fecha + hora del sello CRT desde el PNG recortado.

    Estrategia:
      1. Azure AI Vision 'Read'
    """
    fecha = _leer_fecha_azure_read(png_path)
    if fecha:
        return fecha

    log.warning("⚠️  No se pudo leer la fecha del sello %s", png_path.name)
    return None


def capturar_imagen_sello_pdf(pdf_path: Path, output_dir: Path = None) -> tuple[Path, str | None] | None:
    """
    Renderiza la primera página COMPLETA del PDF, detecta el sello CRT
    por color azul en toda la página, recorta esa zona y la guarda como PNG.

    Si no detecta el sello por color, usa fallback: cuadrante superior-derecho.

    Retorna (ruta_png, fecha_sello) o None si falla.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        log.warning("⚠️  pypdfium2 no instalado — no se puede capturar imagen del sello")
        return None

    try:
        # Renderizar página 1 completa a escala 2× (mayor resolución para imagen final)
        pdf_doc = pdfium.PdfDocument(str(pdf_path))
        page = pdf_doc[0]
        bitmap = page.render(scale=2)
        pil_image = bitmap.to_pil()
        pdf_doc.close()

        img_w, img_h = pil_image.size
        log.debug("📄 Página renderizada: %d×%d px", img_w, img_h)

        # Detectar sello por color azul — escaneo de TODA la página
        bbox = _detectar_sello_por_color(pil_image)

        if bbox:
            x1, y1, x2, y2 = bbox
            log.info("🔵 Sello detectado: (%d,%d)–(%d,%d)  tamaño=%d×%d px",
                     x1, y1, x2, y2, x2-x1, y2-y1)
            crop = pil_image.crop(bbox)
        else:
            # Fallback: cuadrante superior-derecho
            log.warning("⚠️  Sello no detectado — fallback superior-derecho")
            crop = pil_image.crop((
                int(img_w * 0.50),
                int(img_h * 0.03),
                img_w,
                int(img_h * 0.35),
            ))

        # Guardar PNG
        dest_dir = output_dir or pdf_path.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / f"sello_{pdf_path.stem}.png"
        crop.save(str(out_path), format="PNG")
        log.info("🖼️  Imagen sello guardada: %s  (%dx%d)", out_path.name, *crop.size)
        fecha = _leer_fecha_del_sello(out_path)
        return out_path, fecha

    except Exception as e:
        log.warning("⚠️  Error capturando imagen del sello (%s): %s", pdf_path.name, e)
        return None






# ────────────────────────────────────────────────────────
#  MODO A: EXTRACCIÓN CON AZURE AI DOCUMENT INTELLIGENCE
# ────────────────────────────────────────────────────────

def _rasterizar_primera_pagina(pdf_path: Path, dpi: int = 300) -> Path | None:
    """
    Rasteriza la primera página del PDF a un PNG temporal usando pypdfium2.

    Esto fuerza a Azure a hacer su propio OCR sobre una imagen limpia,
    ignorando completamente el OCR defectuoso embebido en el PDF del IFT.

    Args:
        pdf_path: Ruta al PDF.
        dpi: Resolución del PNG (default 300 DPI — óptimo para Azure AI).

    Returns:
        Path al PNG temporal, o None si falla.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        log.warning("⚠️  pypdfium2 no instalado — se enviará el PDF directo a Azure")
        return None

    try:
        # 72 DPI es la resolución base de pdfplumber/PDF; scale = dpi/72
        scale = dpi / 72.0
        pdf_doc = pdfium.PdfDocument(str(pdf_path))
        page = pdf_doc[0]
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()
        pdf_doc.close()

        # Guardar PNG temporal junto al PDF (se borrará tras el análisis)
        png_path = pdf_path.with_suffix(".tmp_azure.png")
        pil_image.save(str(png_path), format="PNG")
        log.info("🖼️  Rasterizado pág. 1 → %s (%dx%d @%dDPI)",
                 png_path.name, pil_image.width, pil_image.height, dpi)
        return png_path
    except Exception as e:
        log.warning("⚠️  Error rasterizando PDF: %s — se enviará el PDF directo", e)
        return None


def _azure_disponible() -> bool:
    """Verifica si Azure AI está configurado y la librería instalada."""
    if not AZURE_ENDPOINT or not AZURE_KEY:
        return False
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient  # noqa: F401
        return True
    except ImportError:
        return False


def extraer_con_azure(pdf_path: Path, endpoint: str = "", key: str = "") -> dict:
    """
    Extrae datos usando Azure AI Document Intelligence.

    Estrategia:
      1. Rasteriza la pág. 1 del PDF a PNG con pypdfium2 (@300 DPI)
      2. Envía el PNG a Azure → Azure hace OCR propio de alta calidad
         (ignora el OCR defectuoso embebido en el PDF del IFT)
      3. Si falla la rasterización, envía el PDF directamente como fallback
    """
    from azure.core.credentials import AzureKeyCredential
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import DocumentAnalysisFeature

    ep = endpoint or AZURE_ENDPOINT
    k = key or AZURE_KEY

    client = DocumentIntelligenceClient(
        endpoint=ep,
        credential=AzureKeyCredential(k),
    )

    # ── Intentar rasterizar pág. 1 para enviar imagen limpia a Azure ──
    png_tmp = _rasterizar_primera_pagina(pdf_path)
    _usar_png = png_tmp is not None and png_tmp.exists()

    try:
        if _usar_png:
            log.info("☁️  Enviando PNG rasterizado a Azure AI (OCR propio sobre imagen)...")
            with open(png_tmp, "rb") as f:
                poller = client.begin_analyze_document(
                    "prebuilt-layout",
                    body=f,
                    features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS],
                    content_type="image/png",
                )
        else:
            log.info("☁️  Enviando PDF a Azure AI (sin rasterizar)...")
            with open(pdf_path, "rb") as f:
                poller = client.begin_analyze_document(
                    "prebuilt-layout",
                    body=f,
                    features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS],
                    content_type="application/octet-stream",
                )
    finally:
        # Borrar PNG temporal independientemente del resultado
        if _usar_png and png_tmp and png_tmp.exists():
            try:
                png_tmp.unlink()
            except Exception:
                pass

    result = poller.result()
    campos = {}

    # ── Key-Value Pairs de Azure ──
    # Guardar también los sub-campos de nombre para reconstrucción
    nombre_s = ""
    primer_apellido = ""
    segundo_apellido = ""

    if result.key_value_pairs:
        for kvp in result.key_value_pairs:
            if kvp.key and kvp.value and kvp.key.content and kvp.value.content:
                key_text = kvp.key.content.strip().strip(":")
                val_text = clean_azure_value(kvp.value.content)

                if "Nombre o razón social" in key_text:
                    key_text = "nombre_operador"
                elif "Representante legal" in key_text:
                    key_text = "representante_legal"

                # Capturar sub-campos para reconstruir nombre completo
                key_lower = key_text.lower()
                if key_lower in ("nombre (s)", "nombre(s)", "nombres", "nombre"):
                    nombre_s = val_text
                elif "primer apellido" in key_lower:
                    primer_apellido = val_text
                elif "segundo apellido" in key_lower:
                    segundo_apellido = val_text

                if val_text and len(key_text) > 2:
                    if key_text not in campos or len(val_text) > len(campos.get(key_text, "")):
                        campos[key_text] = val_text

    # ── Reconstruir representante legal si está incompleto ──
    # Azure a veces solo captura los apellidos porque "Nombre(s)" está en otra celda
    rep_actual = campos.get("representante_legal", "")
    
    # Método 1: Reconstruir desde sub-campos (Nombre(s) + Primer apellido + Segundo apellido)
    if nombre_s or primer_apellido or segundo_apellido:
        nombre_reconstruido = " ".join(p for p in [nombre_s, primer_apellido, segundo_apellido] if p)
        nombre_reconstruido = clean_azure_value(nombre_reconstruido)
        if len(nombre_reconstruido) > len(rep_actual):
            campos["representante_legal"] = nombre_reconstruido

    # Método 2: Buscar "Nombre: XXXX" cerca de la firma en el texto completo
    if result.content:
        content_flat = result.content.replace('\n', ' ')
        
        # Buscar patrón "Nombre: NOMBRE COMPLETO" (zona de firma)
        m_firma = re.search(
            r'Nombre:\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+(?:\s[A-ZÁÉÍÓÚÑ]+){1,4})',
            content_flat,
        )
        if m_firma:
            nombre_firma = clean_azure_value(m_firma.group(1))
            rep_actual = campos.get("representante_legal", "")
            # Si el nombre de la firma es más largo/completo, usar ese
            if len(nombre_firma) > len(rep_actual):
                campos["representante_legal"] = nombre_firma

    # ── Respaldo: regex en texto libre (para oficios) ──
    if "nombre_operador" not in campos and result.content:
        content_flat = result.content.replace('\n', ' ')
        m = re.search(
            r'en nombre y representaci[oó]n de\s+(.+?)(?:,?\s*personalidad|\.\s|quien\s|comparezco)',
            content_flat, re.I,
        )
        if m:
            val = clean_azure_value(m.group(1))
            if len(val) > 5:
                campos["nombre_operador"] = val

    # ── Detección de formatos en el texto ──
    if result.content:
        formatos = _detectar_formatos(result.content)
        campos["formatos"] = formatos

        # ── Buscar fecha del sello CRT en el texto (RECIBIDO ...) ──
        # El sello suele decir "RECIBIDO DD MMM YYYY HH:MM:SS"
        # Buscamos patrones como "15 FEB 2026" o "15/02/2026" cerca de la palabra RECIBIDO o en el texto
        content_upper = result.content.upper()
        
        # Patrón 1: DD MMM YYYY (ej: 15 FEB 2026)
        m_fecha = re.search(
            r'(\d{1,2})\s+([A-ZÁÉÍÓÚ]{3,10})\.?\s+(\d{4})',
            content_upper
        )
        if m_fecha:
            dia_raw = m_fecha.group(1)
            mes_texto = m_fecha.group(2)[:3]
            anio = m_fecha.group(3)
            
            dia_int = int(dia_raw) if dia_raw.isdigit() else 0
            if 1 <= dia_int <= 31:
                dia = str(dia_int).zfill(2)
                mes_num = _MESES_OCR.get(mes_texto)
                if mes_num:
                    campos["fecha_sello"] = f"{dia}/{mes_num}/{anio} 00:00:00"
                    log.info("📅 Fecha sello detectada en texto (Azure): %s", campos["fecha_sello"])

    return campos


# ────────────────────────────────────────────────────────
#  MODO B: EXTRACCIÓN CON PDFPLUMBER + REGEX
# ────────────────────────────────────────────────────────

def _extraer_texto_pdfplumber(pdf_path: Path) -> str:
    """Extrae todo el texto del PDF usando pdfplumber."""
    import pdfplumber

    texto = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            n_pages = len(pdf.pages)
            for pagina in pdf.pages:
                t = pagina.extract_text()
                if t:
                    texto += t + "\n"
        log.info("📖 Texto extraído: %d páginas", n_pages)
    except Exception as e:
        log.error("❌ Error extrayendo texto de %s: %s", pdf_path.name, e)
    return texto


def _extraer_nombre_operador(texto: str) -> str | None:
    """Extrae 'Nombre o razón social del Operador' con regex.

    Maneja 3 layouts que genera pdfplumber según cómo el motor renderiza la tabla:
      Caso A: "Nombre o razón social del Operador: EMPRESA X"   (todo en 1 línea)
      Caso B: "Nombre o razón social del EMPRESA X" + "Operador:" (valor antes de Operador)
      Caso C: "Nombre o razón social del" + "EMPRESA X" + "Operador" (valor en línea separada)
    """
    lineas = [l.strip() for l in texto.split('\n') if l.strip()]

    # Palabras de formulario que NO son el valor buscado
    _INVALIDO = re.compile(
        r'^('
        r'operador[:\s]*|representante(\s+legal)?|domicilio|nombre\s*\(s?\)|'
        r'primer\s+apellido|segundo\s+apellido|dd/?mm|aaaa|d\s*d/m\s*m/aaaa|'
        r'tel[eé]fono|correo|colonia|municipio|entidad\s+federativa|'
        r'c[oó]digo\s+postal|datos\s+generales(\s+del\s+operador)?|secci[oó]n\s+\d|'
        r'indique\s+el\s+nombre|autorizados'
        r')$',
        re.IGNORECASE
    )

    def _es_invalido(s: str) -> bool:
        """True si el candidato es ruido de formulario o demasiado corto."""
        s_limpio = s.strip(' _|-:;.')
        if not s_limpio or len(s_limpio) < 4:
            return True
        alnum = sum(1 for c in s_limpio if c.isalnum())
        if alnum < 4:
            return True
        return bool(_INVALIDO.match(s_limpio))

    for i, linea in enumerate(lineas):
        if "Indique el nombre completo" in linea:
            continue

        # ── Caso A: etiqueta + valor completo en la misma línea ──────────
        # "Nombre o razón social del Operador: EMPRESA X"
        m = re.search(
            r"(?i)nombre\s+o\s+raz[oó]n\s+social\s+del\s+operador\s*[:\-]?\s*(.+)$",
            linea,
        )
        if m:
            c = limpiar_candidato(m.group(1))
            if not _es_invalido(c):
                return c

        # ── Detectar línea ancla: SOLO "Nombre o razón social" ───────────
        # "Datos generales del Operador" es encabezado de sección, NO es ancla de valor
        es_ancla = bool(re.search(
            r'(?i)nombre\s+o\s+raz[oó]n\s+social',
            linea
        ))
        if not es_ancla:
            continue

        # ── Caso B: valor en la misma línea, sin la palabra "Operador" ───
        # "Nombre o razón social del EMPRESA X"  (split por 'del ' captura el resto)
        partes = re.split(r'(?i)(?:Operador\s*:?|(?<!\w)del\s+|(?<!\w)dei\s+)', linea)
        if len(partes) > 1:
            c = limpiar_candidato(partes[-1])
            if not _es_invalido(c):
                return c

        # ── Caso C: valor en línea(s) separadas (ventana de +3 líneas) ───
        # "Nombre o razón social del"   ← línea ancla detectada arriba
        # "EMPRESA X"                   ← valor buscado (aquí)
        # "Operador"                    ← resto de la etiqueta (se salta)
        for j in range(i + 1, min(i + 4, len(lineas))):
            nl_raw = lineas[j]
            # Saltar líneas que solo contienen la continuación de la etiqueta
            if re.match(r'(?i)^operador[:\s]*$', nl_raw.strip()):
                continue
            # Detener en encabezados de sub-tabla o siguiente sección
            if re.match(r'(?i)^(nombre\s*\(s\)|primer|segundo|representante|secci[oó]n\s+\d)', nl_raw.strip()):
                break
            nl = limpiar_candidato(re.sub(r'[_|\-^.;]', ' ', nl_raw))
            if not _es_invalido(nl):
                return nl
            # Si la línea tiene contenido real pero es inválida (no es "Operador"), parar
            if not re.match(r'(?i)^operador', nl_raw.strip()):
                break

    return None


def _extraer_representante(texto: str) -> str | None:
    """Extrae 'Representante legal' con regex."""
    lineas = [l.strip() for l in texto.split('\n') if l.strip()]
    for i, linea in enumerate(lineas):
        if "Comprende los siguientes campos" in linea:
            continue

        if 'Representante legal' in linea:
            partes = []
            for j in range(1, 4):
                if i + j < len(lineas):
                    nl = lineas[i + j]
                    if "Nombre (s)" in nl or "Primer apellido" in nl or "Comprende" in nl:
                        continue
                    if "Domicilio" in nl or "notificaciones" in nl or "Calle" in nl:
                        break
                    nl = limpiar_candidato(re.sub(r'[_|\-\.<>&^]', ' ', nl))
                    palabras = [w for w in nl.split() if len(w) > 2 or w.isupper()]
                    if palabras:
                        partes.extend(palabras)
            if partes:
                return " ".join(partes)

    return None


def _detectar_formatos(texto: str) -> dict:
    """Detecta qué formatos R001–R027 están marcados con 'X' en el PDF."""
    detectados = {}
    for fmt in FORMATOS:
        if re.search(rf'[Xx]\s*{fmt}\b', texto):
            detectados[fmt] = True
            continue
        if re.search(rf'{fmt}\.\s*Información|{fmt}-\d+\.\w+', texto, re.IGNORECASE):
            detectados[fmt] = True
            continue
        if re.search(rf'[Xx]\s*.*?{fmt}', texto):
            detectados[fmt] = True
            continue

    if re.search(r'eFormato\s+R002', texto, re.IGNORECASE):
        detectados["R002"] = True

    return detectados


def extraer_con_pdfplumber(pdf_path: Path) -> dict:
    """Extrae datos usando pdfplumber + regex (sin costo)."""
    texto = _extraer_texto_pdfplumber(pdf_path)
    if not texto:
        return {}

    campos = {}

    nombre = _extraer_nombre_operador(texto)
    if nombre:
        campos["nombre_operador"] = nombre

    rep = _extraer_representante(texto)
    if rep:
        campos["representante_legal"] = rep

    formatos = _detectar_formatos(texto)
    campos["formatos"] = formatos

    return campos


# ────────────────────────────────────────────────────────
#  CORRECCIÓN DE NOMBRES (OCR)
# ────────────────────────────────────────────────────────

# Importar utilidades LLM (Gemini + fallback regex)
try:
    from llm_utils import limpiar_nombre_representante as _limpiar_representante
    _LLM_DISPONIBLE = True
except ImportError:
    _LLM_DISPONIBLE = False
    log.warning("⚠️  llm_utils.py no encontrado — se usará solo corrección por regex")


# Correcciones para RAZÓN SOCIAL (empresa) — no pasan por LLM
CORRECCIONES_OCR = {
    "TELFCOMUNICACION": "TELECOMUNICACIÓN",
    "TELECOMUNICACION": "TELECOMUNICACIÓN",
    "TELECOMUNICACIÖN": "TELECOMUNICACIÓN",
    "MONTEREY": "MONTERREY",
    "MEXICO": "MÉXICO",
    "S.A DE C.V": "S.A. DE C.V.",
    "S.A. DE C.V": "S.A. DE C.V.",
    "S DE R.L": "S. DE R.L.",
    "SAPI": "S.A.P.I.",
    "MARA": "MARÍA",
    "SNCHEZ": "SÁNCHEZ",
}


def corregir_nombre(nombre: str) -> str:
    """
    Corrige errores comunes de OCR en nombres de operadores (razón social).
    Usa correcciones de diccionario; no llama al LLM (la razón social
    raramente tiene problemas de acentos en nombres propios).
    """
    corregido = nombre
    for mal, bien in CORRECCIONES_OCR.items():
        corregido = re.sub(r'\b' + mal + r'\b', bien, corregido, flags=re.IGNORECASE)

    # Parche para el caso más común de empresa
    try:
        from thefuzz import fuzz
        if fuzz.ratio("TELECOMUNICACION Y MERCADOTECNIA DE MONTERREY", corregido.upper()) > 75:
            corregido = "TELECOMUNICACIÓN Y MERCADOTECNIA DE MONTERREY, S.A. DE C.V."
    except ImportError:
        pass

    corregido = re.sub(r"\s+", " ", corregido).strip(" -:;")

    if corregido != nombre:
        log.info("🔧 Nombre corregido: %s → %s", nombre[:60], corregido[:60])

    return corregido


def corregir_representante_legal(nombre: str) -> str:
    """
    Corrige el nombre del Representante Legal usando LLM (Gemini).
    Elimina tokens OCR inválidos, nombres duplicados y corrige acentos.
    Fallback automático a regex si el LLM no está disponible.
    """
    if not nombre or not nombre.strip():
        return ""
    if _LLM_DISPONIBLE:
        return _limpiar_representante(nombre)
    else:
        # Fallback mínimo: limpiar tokens y mayúsculas
        limpio = re.sub(r":unselected:|:selected:|:checked:|:unchecked:", " ", nombre, flags=re.IGNORECASE)
        limpio = re.sub(r"(?<!\w)\d{1,4}(?!\w)", " ", limpio)
        limpio = re.sub(r"\s{2,}", " ", limpio).strip().upper()
        return limpio


# ────────────────────────────────────────────────────────
#  FUNCIÓN PRINCIPAL
# ────────────────────────────────────────────────────────

def extraer_datos_pdf(carpeta: Path, azure_endpoint: str = "", azure_key: str = "") -> dict:
    """
    Extrae datos del PDF en la carpeta indicada.

    Retorna dict con:
      - pdf_nombre: str (nombre sin extensión)
      - nombre_operador: str | None
      - representante_legal: str | None
      - formatos: dict  ({"R002": True, ...})
      - modo: "azure" | "pdfplumber"
    """
    resultado = {
        "pdf_nombre": None,
        "nombre_operador": None,
        "representante_legal": None,
        "formatos": {},
        "imagen_sello": None,
        "fecha_sello": None,
        "modo": None,
    }

    pdf_path = encontrar_pdf(carpeta)
    if not pdf_path:
        return resultado

    resultado["pdf_nombre"] = pdf_path.stem

    # Decidir modo de extracción
    ep = azure_endpoint or AZURE_ENDPOINT
    key = azure_key or AZURE_KEY
    usar_azure = bool(ep and key)

    if usar_azure:
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient  # noqa: F401
        except ImportError:
            log.warning("⚠️  azure-ai-documentintelligence no instalado, usando pdfplumber")
            usar_azure = False

    if usar_azure:
        log.info("☁️  Extrayendo con Azure AI (rasterización forzada de imagen)...")
        try:
            campos = extraer_con_azure(pdf_path, endpoint=ep, key=key)
            resultado["modo"] = "azure-imagen"
            
            # --- NUEVO: Fallback a pdfplumber si Azure no encuentra los nombres ---
            if not campos.get("nombre_operador") or not campos.get("representante_legal"):
                log.warning("⚠️  Azure AI omitió nombre_operador o representante_legal, intentando con pdfplumber como respaldo...")
                campos_plumber = extraer_con_pdfplumber(pdf_path)
                
                if not campos.get("nombre_operador") and campos_plumber.get("nombre_operador"):
                    campos["nombre_operador"] = campos_plumber["nombre_operador"]
                    log.info("🔧 pdfplumber recuperó nombre_operador: %s", campos["nombre_operador"])
                    
                if not campos.get("representante_legal") and campos_plumber.get("representante_legal"):
                    campos["representante_legal"] = campos_plumber["representante_legal"]
                    log.info("🔧 pdfplumber recuperó representante_legal: %s", campos["representante_legal"])
                    
        except Exception as e:
            log.error("❌ Error con Azure AI: %s — usando pdfplumber como fallback completo", e)
            campos = extraer_con_pdfplumber(pdf_path)
            resultado["modo"] = "pdfplumber"
    else:
        log.info("📄 Extrayendo con pdfplumber (fallback sin nube)...")
        campos = extraer_con_pdfplumber(pdf_path)
        resultado["modo"] = "pdfplumber"

    # ── LEER METADATOS WEB (SATyS) ──────────────────────────
    metadata_path = carpeta / "metadata_satys.json"
    metadata_web = {}
    if metadata_path.exists():
        try:
            import json
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata_web = json.load(f)
            log.info("🌐 Metadatos web cargados desde metadata_satys.json")
        except Exception as e:
            log.error("❌ Error leyendo metadata_satys.json: %s", e)

    # ── Rellenar resultado ──────────────────────────────────
    nombre = campos.get("nombre_operador")
    if nombre:
        # Corrección regex (abreviaciones legales, errores comunes)
        nombre = corregir_nombre(nombre)
        # Corrección LLM (Gemini): normaliza puntuación societaria y artefactos OCR
        try:
            from llm_utils import limpiar_nombre_operador as _limpiar_operador
            nombre = _limpiar_operador(nombre)
        except (ImportError, Exception) as e:
            log.debug("LLM para razón social no disponible: %s", e)
        resultado["nombre_operador"] = nombre
        log.info("✅ Nombre operador (PDF): %s", nombre[:80])
    else:
        log.warning("⚠️  No se extrajo el nombre del operador del PDF")
        
    resultado["nombre_operador_web"] = metadata_web.get("nombre_operador", "")

    # El Representante legal lo tomaremos de Satys y comentar la que se toma del pdf.
    rep_web = metadata_web.get("representante_legal", "")
    if rep_web:
        rep_web = corregir_representante_legal(rep_web)
        resultado["representante_legal"] = rep_web
        log.info("🌐 Representante legal (Web): %s", rep_web)
    else:
        resultado["representante_legal"] = ""
        log.warning("⚠️  Sin Representante legal (Web)")
    
    # rep = campos.get("representante_legal")
    # if rep:
    #     rep = corregir_representante_legal(rep)
    #     resultado["representante_legal"] = rep
    #     log.info("✅ Representante legal: %s", rep)

    formatos = campos.get("formatos", {})
    
    # Extraer formatos también del Asunto web
    asunto_web = metadata_web.get("asunto", "")
    if asunto_web:
        import re
        for m in re.finditer(r'(R-?\d{2,3})', asunto_web, re.IGNORECASE):
            fmt = m.group(1).upper().replace("-", "")
            if len(fmt) == 3:  # Ej: R02 -> R002
                fmt = f"R0{fmt[1:]}"
            formatos[fmt] = True
            log.info("🌐 Formato detectado en Asunto Web: %s", fmt)
            
    resultado["formatos"] = formatos
    if formatos:
        log.info("📋 Formatos finales detectados: %s", ", ".join(formatos.keys()))

    # ── Capturar imagen del sello CRT → carpeta externa ────
    sellos_dir = SELLOS_DIR
    sello_resultado = capturar_imagen_sello_pdf(pdf_path, output_dir=sellos_dir)
    if sello_resultado:
        img_sello, fecha_sello = sello_resultado
        resultado["imagen_sello"] = img_sello
        if fecha_sello:
            resultado["fecha_sello"] = fecha_sello

    # Si aún no hay fecha, usar la detectada por Azure en el texto
    if not resultado["fecha_sello"] and campos.get("fecha_sello"):
        resultado["fecha_sello"] = campos.get("fecha_sello")

    return resultado


# ────────────────────────────────────────────────────────
#  CLI
# ────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python Parte2_extraer.py <carpeta>")
        print("  Ejemplo: python Parte2_extraer.py descargas\\6407")
        sys.exit(1)

    carpeta = Path(sys.argv[1])
    if not carpeta.exists():
        print(f"❌ Carpeta no existe: {carpeta}")
        sys.exit(1)

    # Credenciales opcionales por CLI
    ep = sys.argv[2] if len(sys.argv) > 2 else ""
    key = sys.argv[3] if len(sys.argv) > 3 else ""

    datos = extraer_datos_pdf(carpeta, azure_endpoint=ep, azure_key=key)

    print("\n" + "=" * 60)
    print("  RESULTADO DE EXTRACCIÓN")
    print("=" * 60)
    print(json.dumps(datos, ensure_ascii=False, indent=2, default=str))