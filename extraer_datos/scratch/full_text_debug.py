import pdfplumber
import json

files = ['doc/CRT26-009493.pdf', 'doc/CRT26-009907.pdf']
output = {}

for f in files:
    with pdfplumber.open(f) as pdf:
        output[f] = pdf.pages[0].extract_text()

with open('scratch/full_text_debug.json', 'w', encoding='utf-8') as out:
    json.dump(output, out, ensure_ascii=False, indent=2)
