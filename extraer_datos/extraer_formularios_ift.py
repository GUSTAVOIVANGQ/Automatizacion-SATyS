#!/usr/bin/env python3
r"""
Extrae automáticamente todos los campos "Etiqueta: Valor" de PDFs de
formularios IFT en una carpeta y guarda los resultados en un archivo JSON.

Optimizado para ejecutarse en entornos sin Tesseract ni Poppler.
Usa pdfplumber para extracción directa de texto.

Uso:
    ..\python_portable\python.exe extraer_formularios_ift.py doc
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Configurar salida UTF-8 para evitar errores en consola de Windows
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        # Fallback para versiones antiguas de Python
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    import pdfplumber
except ImportError:
    print("ERROR: Instala pdfplumber con:")
    print("  pip install pdfplumber")
    sys.exit(1)


# ─── Configuración ────────────────────────────────────────────────────────────

# Etiquetas que indican campos vacíos o ruido — se ignoran como valores
VALORES_IGNORAR = re.compile(
    r'^(DD/MM|AAAA|NA|N/A|X|XO|\d{0,2}|[\s\-_\.]+)$',
    re.IGNORECASE
)

# Etiquetas que contienen estas palabras son instrucciones del formulario, no datos
ETIQUETAS_IGNORAR = re.compile(
    r'seleccionar|sugiere|deberá|dispuesto|incurren|declarar|fracción|artículo|https|http|comparezco|expongo|lineamiento|servicios|obligaciones',
    re.IGNORECASE
)


# ─── Utilidades ───────────────────────────────────────────────────────────────

def clean_value(val: str) -> str:
    """Limpia ruidos comunes en formularios (guiones bajos, líneas, espacios)."""
    if not val:
        return ""
    
    # Eliminar ruidos de OCR comunes
    val = re.sub(r'[\|—©®™^<>«»]', ' ', val)
    
    # Eliminar secuencias de caracteres que parecen ruido (ej: .<7-; o similar)
    val = re.sub(r'[\.\-\:_/\\~<>\*&%#\?]{2,}', ' ', val)
    
    # Eliminar secuencias largas de guiones bajos o puntos o guiones
    val = re.sub(r'[\._\-]{2,}', ' ', val)
    
    # Eliminar caracteres raros al inicio/final
    val = val.strip(' \t\n\r|-_.,^©—<>«»*&%#?()[]{}')
    
    # Colapsar espacios
    val = re.sub(r'\s{2,}', ' ', val)
    
    return val.strip()


def normalize_name(name: str) -> str:
    """
    Normaliza un nombre intentando corregir errores de OCR y casing.
    Ej: "LOERA santillAn DANIEL" -> "DANIEL LOERA SANTILLÁN"
    """
    if not name:
        return ""
    
    # Limpiar primero
    name = clean_value(name)
    
    # Corregir artefactos comunes del OCR en este tipo de documentos
    # El '&' suele aparecer entre el nombre y el apellido o en lugar de la 'L'
    name = re.sub(r'([A-Z])&([A-Z])', r'\1 L \2', name, flags=re.I)
    name = re.sub(r'&', ' L ', name)
    
    name = name.upper()
    
    # Corregir colapsos de letras por OCR
    # Ej: DANIEL LOERA -> DANILETOERA o similar
    # Si vemos "DANILE", probablemente es "DANIEL "
    name = name.replace("DANILE", "DANIEL ")
    name = name.replace("DANI L", "DANIEL ")
    
    # Limpieza final tras reemplazos
    name = clean_value(name)

    return name


# ─── Extracción de Texto ──────────────────────────────────────────────────────

def get_pdf_text(pdf_path: str, max_pages: int = 1) -> str:
    """Extrae el texto de las primeras páginas del PDF usando pdfplumber."""
    text_content = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_to_scan = min(max_pages, len(pdf.pages))
            for i in range(pages_to_scan):
                page_text = pdf.pages[i].extract_text()
                if page_text:
                    text_content.append(page_text)
    except Exception as e:
        print(f"    [!] Error leyendo PDF: {e}")
    
    return "\n".join(text_content)


# ─── Procesamiento de campos ──────────────────────────────────────────────────

def post_process_fields(fields: dict) -> dict:
    """
    Limpieza final de los campos extraídos.
    Unifica nombres, corrige duplicados y normaliza valores.
    """
    new_fields = {}
    
    # Mapeo de normalización de etiquetas
    LABEL_MAP = {
        r'Nombre o razón social de[il]\s+Operador': 'Nombre o razón social del Operador',
        r'Representante\s+legal': 'Representante legal',
        r'^Nombre$': 'Nombre'
    }

    for label, val in fields.items():
        processed_label = label
        for pattern, fixed in LABEL_MAP.items():
            if re.search(pattern, label, re.I):
                processed_label = fixed
                break
        
        # Si es un campo de nombre, normalizar el valor
        if any(x in processed_label for x in ['Nombre', 'Representante']):
            val = normalize_name(val)
        
        # Evitar sobreescribir con valores más cortos o vacíos si ya existe
        if processed_label in new_fields:
            if len(val) > len(new_fields[processed_label]):
                new_fields[processed_label] = val
        else:
            new_fields[processed_label] = val
            
    # Heurística de Consistencia de Nombres:
    # Buscar el nombre más completo entre todos los campos que parecen nombres
    name_fields = ['Nombre o razón social del Operador', 'Representante legal', 'Nombre']
    all_name_vals = [new_fields[f] for f in name_fields if f in new_fields and len(new_fields[f]) > 5]
    
    if all_name_vals:
        # El "mejor" nombre es el que sea razonablemente largo y tenga tildes si es posible
        # Pero evitamos los que tienen errores de OCR detectados (como 'ETOERA')
        def name_score(n):
            score = len(n)
            if ' ' in n: score += 10
            if any(c in n for c in 'ÁÉÍÓÚÑ'): score += 5
            # Penalizar si tiene 'DANI&' o 'ETOERA' o 'DANILE'
            if any(bad in n for bad in ['DANILE', 'ETOERA', 'DANI&']): score -= 20
            return score

        best_name = max(all_name_vals, key=name_score)
        
        for f in name_fields:
            if f in new_fields:
                # Si el valor actual es una versión recortada del "mejor" nombre, actualizarlo
                curr = new_fields[f]
                # Si son similares o uno contiene al otro
                if curr in best_name or best_name in curr or len(set(curr.split()) & set(best_name.split())) >= 2:
                    new_fields[f] = best_name

    return new_fields


def extract_fields(text: str) -> dict:
    """
    Extrae todos los pares 'Etiqueta: Valor' del texto extraído.
    """
    fields = {}
    
    # ── Búsqueda en texto libre (útil para escritos libres sin formato) ──
    text_uniline = text.replace('\n', ' ')
    m_free = re.search(r'en nombre y representaci[oó]n de\s+(.+?)(?:,?\s*personalidad|\.\s|quien\s|comparezco)', text_uniline, re.I)
    if m_free:
        val = clean_value(m_free.group(1))
        if len(val) > 5:
            fields['Nombre o razón social del Operador'] = val

    lines = [line.strip() for line in text.split('\n') if line.strip()]

    for i, line in enumerate(lines):
        # ── Identificadores especiales (Folio / Tipo Doc) ──────────────────
        if 'Folio' not in fields:
            folio_m = re.search(r'(CRT\d{2}-\d+)', line)
            if folio_m:
                fields['Folio'] = folio_m.group(1)

        if 'Tipo de documento' not in fields:
            doc_m = re.search(r'R[O0]{0,2}(\d{2,3})[\.\s]', line)
            if doc_m:
                fields['Tipo de documento'] = f"R0{doc_m.group(1)}"

        # ── Caso Especial: Representante Legal / Operador ──────────────────
        # Usamos regex más flexibles para las etiquetas críticas
        if not any(x in fields for x in ['Representante legal', 'Nombre o razón social del Operador']):
            # Buscar "Representante legal" o "Nombre o razón social del Operador" (o solo "del" si se partió la línea)
            m_rep = re.search(r'(Representante\s+legal|Nombre\s+o\s+razón\s+social\s+de[il](?:\s+Operador)?)\s*[:\-\._\s|]*\s*(.*)', line, re.I)
            if m_rep:
                label_found = m_rep.group(1)
                if "Nombre o razón social" in label_found:
                    label_found = "Nombre o razón social del Operador"
                
                val = clean_value(m_rep.group(2))
                
                # Excluir la palabra 'Operador' si se coló
                if val.upper().startswith('OPERADOR'):
                    val = clean_value(val[8:])
                
                # Si el valor está vacío o es puro ruido
                # Heurística: si tiene pocos caracteres alfanuméricos en comparación al total
                alnum_count = sum(1 for c in val if c.isalnum())
                is_noise = len(val) < 5 or (alnum_count / (len(val) or 1) < 0.4) or val.lower() in ['r', 'x', 'n/a', 'operador']
                
                if (not val or is_noise) and i + 1 < len(lines):
                    next_line = lines[i+1]
                    if ':' not in next_line and not ETIQUETAS_IGNORAR.search(next_line):
                        val = clean_value(next_line)
                        # Intentar una segunda línea si parece parte del nombre (mayúsculas o largo)
                        if i + 2 < len(lines) and ':' not in lines[i+2] and (lines[i+2].isupper() or len(val) < 15):
                            val += " " + clean_value(lines[i+2])
                
                if val and not is_noise:
                    fields[label_found] = val

        # ── Pares Etiqueta: Valor (Regex General) ───────────────────────────
        matches = list(re.finditer(
            r'([A-Za-záéíóúñÁÉÍÓÚÑ]'           # inicio con letra
            r'[A-Za-záéíóúñÁÉÍÓÚÑ\s\.\,\(\)º]{1,65}?)'  # resto de etiqueta
            r'\s*[:]\s*\|?\s*',                 # separador ":"
            line
        ))

        for idx, m in enumerate(matches):
            label = m.group(1).strip()
            
            # Valor: desde fin de este match hasta inicio del siguiente
            val_start = m.end()
            val_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
            value_raw = line[val_start:val_end]
            value = clean_value(value_raw)

            # Si el valor está vacío y es una etiqueta corta, intentar línea siguiente
            if not value and len(label) < 40 and i + 1 < len(lines):
                candidate = lines[i+1]
                if ':' not in candidate and len(candidate) > 2 and not ETIQUETAS_IGNORAR.search(candidate):
                    value = clean_value(candidate)

            # Filtrar ruido
            if (len(label) > 3
                    and value
                    and not VALORES_IGNORAR.match(value)
                    and not ETIQUETAS_IGNORAR.search(label)):
                
                # Normalizar etiqueta 'Representante legal'
                if "Representante legal" in label:
                    label = "Representante legal"
                
                # Evitar etiquetas que son puramente minúsculas o muy largas
                if any(c.isupper() for c in label) or len(label) < 30:
                    fields[label] = value

    return fields


# ─── Procesamiento de PDFs ─────────────────────────────────────────────────────

def process_pdf(pdf_path: str, max_pages: int = 1) -> dict:
    """Procesa un PDF y devuelve los campos extraídos."""
    text = get_pdf_text(pdf_path, max_pages=max_pages)
    raw_fields = extract_fields(text)
    fields = post_process_fields(raw_fields)

    return {
        'archivo': os.path.basename(pdf_path),
        'campos': fields,
        'texto_detectado': len(text) > 0
    }


def process_folder(folder: str, output_json: str, max_pages: int = 1):
    """Procesa todos los PDFs en una carpeta y guarda resultados en JSON."""
    pdf_files = sorted(Path(folder).glob('*.pdf'))
    if not pdf_files:
        print(f"No se encontraron archivos PDF en: {folder}")
        sys.exit(1)

    print(f"Encontrados {len(pdf_files)} PDFs en '{folder}'")
    print(f"Modo: Extracción directa (sin OCR)\n")

    results = {}
    for i, pdf_path in enumerate(pdf_files, 1):
        name = pdf_path.name
        print(f"[{i}/{len(pdf_files)}] Procesando: {name}")
        try:
            data = process_pdf(str(pdf_path), max_pages=max_pages)
            results[name] = data
            n = len(data['campos'])
            print(f"    -> {n} campo(s) extraído(s)")
        except Exception as e:
            print(f"    [ERROR] {e}")
            results[name] = {
                'archivo': name,
                'campos': {},
                'error': str(e)
            }

    # Guardar JSON
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nResultados guardados en: {output_json}")
    print(f"Total PDFs procesados: {len(results)}")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Extrae campos de formularios IFT de PDFs en una carpeta.'
    )
    parser.add_argument('carpeta', help='Carpeta con los archivos PDF')
    parser.add_argument(
        '-o', '--output',
        default='resultados.json',
        help='Archivo JSON de salida (default: resultados.json)'
    )
    parser.add_argument(
        '--paginas', type=int, default=1,
        help='Número de páginas a analizar (default: 1)'
    )
    args = parser.parse_args()

    if not os.path.isdir(args.carpeta):
        print(f"ERROR: No existe la carpeta '{args.carpeta}'")
        sys.exit(1)

    process_folder(
        folder=args.carpeta,
        output_json=args.output,
        max_pages=args.paginas
    )


if __name__ == '__main__':
    main()
