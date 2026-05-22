#!/usr/bin/env python3
r"""
Extrae automáticamente datos de PDFs usando Azure AI Document Intelligence.

Uso:
    ..\python_portable\python.exe extraer_azure_ai.py doc --endpoint <TU_ENDPOINT> --key <TU_KEY>
O configurar las variables de entorno AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT y AZURE_DOCUMENT_INTELLIGENCE_KEY.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    from azure.core.credentials import AzureKeyCredential
    from azure.ai.documentintelligence import DocumentIntelligenceClient
except ImportError:
    print("ERROR: Instala las dependencias con:")
    print("  ..\\python_portable\\python.exe -m pip install azure-ai-documentintelligence")
    sys.exit(1)


def clean_value(val: str) -> str:
    """Limpia ruidos comunes y saltos de línea."""
    if not val:
        return ""
    val = val.replace('\n', ' ')
    val = re.sub(r'[\._\-]{2,}', ' ', val)
    val = val.strip(' \t\n\r|-_.,^©—<>«»*&%#?()[]{}')
    val = re.sub(r'\s{2,}', ' ', val)
    return val.strip()


def extract_with_azure(pdf_path: str, endpoint: str, key: str) -> dict:
    """Envía el PDF a Azure y extrae los campos."""
    client = DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))
    
    from azure.ai.documentintelligence.models import DocumentAnalysisFeature
    
    with open(pdf_path, "rb") as f:
        poller = client.begin_analyze_document(
            "prebuilt-layout", 
            body=f, 
            features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS],
            content_type="application/octet-stream"
        )
    
    result = poller.result()
    fields = {}
    
    # ── 1. Respaldo de Texto Libre (para oficios) ──
    if result.content:
        content_uniline = result.content.replace('\n', ' ')
        m_free = re.search(r'en nombre y representaci[oó]n de\s+(.+?)(?:,?\s*personalidad|\.\s|quien\s|comparezco)', content_uniline, re.I)
        if m_free:
            val = clean_value(m_free.group(1))
            if len(val) > 5:
                fields['Nombre o razón social del Operador'] = val

    # ── 2. Extracción de Key-Value Pairs de Azure ──
    if result.key_value_pairs:
        for kvp in result.key_value_pairs:
            if kvp.key and kvp.value and kvp.key.content and kvp.value.content:
                key_text = kvp.key.content.strip().strip(":")
                val_text = kvp.value.content.strip()
                
                # Normalización básica de etiquetas
                if "Nombre o razón social" in key_text:
                    key_text = "Nombre o razón social del Operador"
                elif "Representante legal" in key_text:
                    key_text = "Representante legal"
                
                val_text = clean_value(val_text)
                
                if val_text and len(key_text) > 2:
                    if key_text not in fields or len(val_text) > len(fields.get(key_text, "")):
                        fields[key_text] = val_text
                        
    # Añadir folio y tipo de documento usando regex simple si Azure no los detectó como KVP
    if 'Folio' not in fields and result.content:
        m_folio = re.search(r'(CRT\d{2}-\d+)', result.content)
        if m_folio:
            fields['Folio'] = m_folio.group(1)
            
    if 'Tipo de documento' not in fields and result.content:
        m_doc = re.search(r'R[O0]{0,2}(\d{2,3})', result.content)
        if m_doc:
            fields['Tipo de documento'] = f"R0{m_doc.group(1)}"

    # ── 3. Post-procesamiento y Heurística de Nombres ──
    name_fields = ['Nombre o razón social del Operador', 'Representante legal', 'Nombre']
    all_name_vals = [fields[f] for f in name_fields if f in fields and len(fields[f]) > 5]
    
    if all_name_vals:
        # Encontrar el nombre más largo/completo
        best_name = max(all_name_vals, key=len)
        
        for f in name_fields:
            if f in fields:
                curr = fields[f]
                # Si comparten al menos 2 palabras o uno está contenido en el otro, lo actualizamos al más completo
                if curr in best_name or best_name in curr or len(set(curr.split()) & set(best_name.split())) >= 2:
                    fields[f] = best_name

    return fields


def process_folder(folder: str, output_json: str, endpoint: str, key: str):
    """Procesa todos los PDFs en la carpeta usando Azure."""
    pdf_files = sorted(Path(folder).glob('*.pdf'))
    if not pdf_files:
        print(f"No se encontraron archivos PDF en: {folder}")
        sys.exit(1)

    print(f"Encontrados {len(pdf_files)} PDFs en '{folder}'")
    print(f"Modo: Azure AI Document Intelligence\n")

    results = {}
    for i, pdf_path in enumerate(pdf_files, 1):
        name = pdf_path.name
        print(f"[{i}/{len(pdf_files)}] Enviando a Azure: {name}")
        try:
            data = extract_with_azure(str(pdf_path), endpoint, key)
            results[name] = {
                'archivo': name,
                'campos': data,
                'texto_detectado': True
            }
            print(f"    -> {len(data)} campo(s) extraído(s)")
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


def main():
    parser = argparse.ArgumentParser(description='Extrae datos de PDFs usando Azure AI Document Intelligence.')
    parser.add_argument('carpeta', help='Carpeta con los archivos PDF')
    parser.add_argument('-o', '--output', default='resultados_azure.json', help='Archivo JSON de salida')
    parser.add_argument('--endpoint', help='Azure Document Intelligence Endpoint')
    parser.add_argument('--key', help='Azure Document Intelligence API Key')
    args = parser.parse_args()

    if not os.path.isdir(args.carpeta):
        print(f"ERROR: No existe la carpeta '{args.carpeta}'")
        sys.exit(1)
        
    endpoint = args.endpoint or os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    key = args.key or os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    
    if not endpoint or not key:
        print("ERROR: Debes proporcionar un --endpoint y un --key, o configurar las variables de entorno.")
        sys.exit(1)

    process_folder(
        folder=args.carpeta,
        output_json=args.output,
        endpoint=endpoint,
        key=key
    )

if __name__ == '__main__':
    main()
