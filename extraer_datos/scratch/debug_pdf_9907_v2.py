import pdfplumber
import os

pdf_path = r'c:\Users\ps.dei\Downloads\SATyS\extraer_datos\doc\CRT26-009907.pdf'
output_path = r'c:\Users\ps.dei\Downloads\SATyS\extraer_datos\scratch\raw_text_9907.txt'

if os.path.exists(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        with open(output_path, 'w', encoding='utf-8') as f:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                f.write(f"--- PAGE {i} START ---\n")
                f.write(text if text else "[NO TEXT]")
                f.write(f"\n--- PAGE {i} END ---\n")
    print(f"Text extracted to {output_path}")
else:
    print(f"File not found: {pdf_path}")
