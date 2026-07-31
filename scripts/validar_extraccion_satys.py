#!/usr/bin/env python3
"""Valida el TXT y el certificado JSON producido por el extractor SATyS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from automatizar_registros_diario import (  # noqa: E402
    leer_registros_txt,
    validar_resumen_extractor,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida conciliación e integridad de una extracción SATyS."
    )
    parser.add_argument("txt", type=Path, help="TXT generado por extraer_registros_documentos.py")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registros = leer_registros_txt(args.txt)
    validacion = validar_resumen_extractor(args.txt, registros)
    salida = {
        "ok": bool(validacion.get("ok")),
        "txt": str(args.txt),
        "total_registros_txt": len(registros),
        "vacio_confirmado": bool(validacion.get("vacio_confirmado")),
        "error": validacion.get("error", ""),
        "resumen_json": validacion.get("path"),
    }
    resumen = validacion.get("resumen")
    if isinstance(resumen, dict):
        salida.update({
            "estado": resumen.get("estado"),
            "integridad": resumen.get("integridad"),
            "total_filas_satys": resumen.get("total_filas_satys"),
            "total_registros_resumen": resumen.get("total_registros"),
            "anios": [
                {
                    "anio": item.get("anio"),
                    "estado": item.get("estado"),
                    "total_reportado_satys": item.get("total_reportado_satys"),
                    "filas_leidas": item.get("filas_leidas"),
                    "registros_unicos": item.get("registros_unicos"),
                    "duplicados_internos": item.get("duplicados_internos"),
                    "filas_invalidas": item.get("filas_invalidas"),
                }
                for item in resumen.get("por_anio", [])
                if isinstance(item, dict)
            ],
        })
    print(json.dumps(salida, ensure_ascii=False, indent=2))
    return 0 if validacion.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
