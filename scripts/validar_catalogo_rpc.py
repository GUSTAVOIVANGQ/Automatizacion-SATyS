#!/usr/bin/env python3
"""Valida y mide la carga secuencial del catálogo RPC sin modificar datos."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from buscar_concesionario import (  # noqa: E402
    cargar_catalogo_desde_excel,
    preparar_catalogo_para_matching,
)


def encontrar_excel_rpc() -> Path | None:
    candidatos: list[Path] = []
    for carpeta in (
        PROJECT_DIR / "base_de_datos_rpc",
        PROJECT_DIR / "buscar_concesionario" / "Area _de_descargas",
    ):
        if carpeta.exists():
            candidatos.extend(carpeta.glob("03_concesiones_permisos_autorizaciones_*.xlsx"))
    return max(candidatos, key=lambda p: p.stat().st_mtime) if candidatos else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mide la carga del catálogo RPC y valida su cantidad de operadores."
    )
    parser.add_argument(
        "--excel",
        type=Path,
        help="Ruta del XLSX RPC. Si se omite, usa el archivo local más reciente.",
    )
    parser.add_argument("--hoja", default="copeau", help="Nombre de la hoja RPC.")
    parser.add_argument(
        "--esperados",
        type=int,
        help="Cantidad esperada de operadores; devuelve código 2 si no coincide.",
    )
    parser.add_argument(
        "--solo-vigentes",
        action="store_true",
        help="Aplica el filtro de vigencia existente en el proyecto.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    excel = args.excel.expanduser().resolve() if args.excel else encontrar_excel_rpc()
    if excel is None or not excel.exists():
        print(
            "ERROR: no se encontró el Excel RPC. Indícalo con --excel /ruta/archivo.xlsx",
            file=sys.stderr,
        )
        return 1

    inicio = time.perf_counter()
    catalogo = cargar_catalogo_desde_excel(
        excel,
        args.hoja,
        solo_vigentes=args.solo_vigentes,
    )
    preparado = preparar_catalogo_para_matching(catalogo)
    duracion = time.perf_counter() - inicio

    print("\nVALIDACIÓN CATÁLOGO RPC")
    print(f"Archivo:       {excel}")
    print(f"Hoja:          {args.hoja}")
    print(f"Operadores:    {len(catalogo)}")
    print(f"Preparados:    {len(preparado)}")
    print(f"Duración:      {duracion:.2f} segundos")

    if len(catalogo) != len(preparado):
        print("ERROR: la preparación cambió la cantidad de operadores.", file=sys.stderr)
        return 3

    if args.esperados is not None and len(catalogo) != args.esperados:
        print(
            f"ERROR: se esperaban {args.esperados} operadores y se obtuvieron {len(catalogo)}.",
            file=sys.stderr,
        )
        return 2

    print("Resultado:     OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
