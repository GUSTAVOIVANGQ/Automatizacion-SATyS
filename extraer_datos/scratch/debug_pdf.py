import pdfplumber
import os

pdf_path = r'c:\Users\ps.dei\Downloads\SATyS\extraer_datos\doc\CRT26-009493.pdf'
if os.path.exists(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text()
        print("--- RAW TEXT START ---")
        print(text)
        print("--- RAW TEXT END ---")
else:
    print(f"File not found: {pdf_path}")
