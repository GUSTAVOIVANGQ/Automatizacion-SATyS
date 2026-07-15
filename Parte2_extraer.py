#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parte2_extraer.py
=================

Archivo conservado solo por compatibilidad con copias antiguas del proyecto.
El flujo de producción Linux NO llama este módulo.

Flujo autorizado:
  automatizar_registros_diario.py -> main_procesar.py -> Parte1_descarga.py -> Parte3_rpc.py -> Parte4_excel.py

main_procesar.py lee directamente:
  - metadata_satys.json
  - metadata_tramite_nuevo.json

generados por Parte1_descarga.py, por lo que no usa Azure, pdfplumber ni OCR.
"""

from __future__ import annotations

import json
from pathlib import Path


def extraer_datos_pdf(carpeta: Path, *_, **__) -> dict:
    """
    Compatibilidad: devuelve metadatos JSON locales sin analizar PDF.
    No se usa en producción; existe para evitar ImportError si algún script viejo lo importa.
    """
    carpeta = Path(carpeta)
    data = {}
    for nombre in ("metadata_satys.json", "metadata_tramite_nuevo.json"):
        path = carpeta / nombre
        if not path.exists():
            continue
        try:
            contenido = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(contenido, dict):
                data.update(contenido)
        except Exception:
            pass

    pdfs = list(carpeta.glob("*.pdf"))
    pdf_nombre = pdfs[0].stem if pdfs else None
    asunto = str(data.get("asunto", ""))
    formatos = {token.upper(): True for token in __import__("re").findall(r"R\d{3}", asunto, flags=__import__("re").IGNORECASE)}

    return {
        "pdf_nombre": pdf_nombre,
        "nombre_operador": data.get("nombre_operador", ""),
        "nombre_operador_web": data.get("nombre_operador", ""),
        "representante_legal": data.get("representante_legal", ""),
        "id_solicitante": data.get("id_solicitante", ""),
        "formatos": formatos,
        "imagen_sello": None,
        "fecha_sello": data.get("fecha_registro", ""),
        "registro": data.get("registro", ""),
        "modo": "metadata_satys_json",
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python Parte2_extraer.py <carpeta>")
        raise SystemExit(2)
    print(json.dumps(extraer_datos_pdf(Path(sys.argv[1])), ensure_ascii=False, indent=2))
