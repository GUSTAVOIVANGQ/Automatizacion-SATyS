import os
import json
from pathlib import Path

def merge_existing_metadata(descargas_dir: str):
    base_path = Path(descargas_dir)
    if not base_path.exists():
        print(f"Directory {descargas_dir} does not exist.")
        return

    count = 0
    for subdir in base_path.iterdir():
        if not subdir.is_dir():
            continue

        satys_file = subdir / "metadata_satys.json"
        tramite_file = subdir / "metadata_tramite_nuevo.json"
        completo_file = subdir / "metadata_completo.json"

        if satys_file.exists() and tramite_file.exists():
            print(f"Processing {subdir.name}...")
            
            with open(satys_file, "r", encoding="utf-8") as f:
                meta_satys = json.load(f)
                
            with open(tramite_file, "r", encoding="utf-8") as f:
                meta_tramite = json.load(f)

            # Merge
            meta_satys.update(meta_tramite)
            
            # Save metadata_satys.json
            with open(satys_file, "w", encoding="utf-8") as f:
                json.dump(meta_satys, f, ensure_ascii=False, indent=2)
            
            # Update metadata_completo.json if it exists
            if completo_file.exists():
                with open(completo_file, "r", encoding="utf-8") as f:
                    meta_completo = json.load(f)
                    
                meta_completo["metadatos_satys"] = meta_satys
                
                with open(completo_file, "w", encoding="utf-8") as f:
                    json.dump(meta_completo, f, ensure_ascii=False, indent=2)
            
            count += 1

    print(f"Done! Merged metadata for {count} folders.")

if __name__ == "__main__":
    merge_existing_metadata("descargas")
