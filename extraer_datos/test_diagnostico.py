#!/usr/bin/env python3
"""Diagnóstico de extracción para PDFs problemáticos."""
import sys
sys.path.insert(0, r'C:\Users\ps.dei\Downloads\SATyS')
from pathlib import Path
from Parte2_extraer import extraer_con_pdfplumber

casos = [
    ('6416 / CRT26-009484', r'C:\Users\ps.dei\Downloads\SATyS\CRT26-009484.pdf', 'OPENIP COMUNICACIONES'),
    # "ROBOT COMUNICACIONES" es el núcleo esperado; puntos y abreviaciones varían por OCR
    ('6417 / CRT26-009485', r'C:\Users\ps.dei\Downloads\SATyS\CRT26-009485.pdf', 'ROBOT COMUNICACIONES'),
]

todos_ok = True
for desc, fpath, esperado in casos:
    print(f"{'='*60}")
    print(f"Folio: {desc}")
    print(f"Núcleo esperado: {esperado}")
    campos = extraer_con_pdfplumber(Path(fpath))
    obtenido = campos.get('nombre_operador', 'NO EXTRAÍDO')
    print(f"Obtenido:        {obtenido}")
    ok = esperado.upper() in (obtenido or '').upper()
    print(f"Estado: {'✓ OK' if ok else '✗ FALLA'}")
    if not ok:
        todos_ok = False
    print()

print('='*60)
print(f"Resultado final: {'✓ TODOS OK' if todos_ok else '✗ HAY FALLOS'}")
