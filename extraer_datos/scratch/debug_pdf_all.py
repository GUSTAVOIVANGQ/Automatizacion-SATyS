import pdfplumber
import os

pdf_path = r'c:\Users\ps.dei\Downloads\SATyS\extraer_datos\doc\CRT26-009493.pdf'
if os.path.exists(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            print(f"--- PAGE {i} START ---")
            print(text)
            print(f"--- PAGE {i} END ---")
else:
    print(f"File not found: {pdf_path}")
