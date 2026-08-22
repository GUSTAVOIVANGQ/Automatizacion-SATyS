#!/usr/bin/env python3
"""Smoke test paralelo de Internos IFT sin abrir folios ni descargar archivos."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import Parte1_descarga as satys  # noqa: E402


def inspeccionar_bandeja(_context, page, bandeja, max_pasadas=3, folios_objetivo=None):
    if not satys.seleccionar_bandeja_internos(page, bandeja):
        return satys._resultado_error_bandeja_internos(
            bandeja,
            "ERROR_SMOKE_BANDEJA",
            "No se pudo activar la bandeja.",
        )

    tab_id = satys.INTERNOS_BANDEJA_IDS[satys._normalizar_nombre_internos(bandeja)]
    estado_texto = page.evaluate(
        """(id) => {
            const tab = document.getElementById(id);
            const contadorTexto = (tab?.querySelector('span')?.textContent || '')
                .replace(/[,\s]/g, '');
            const texto = document.body.innerText || '';
            const matches = Array.from(texto.matchAll(
                /Mostrando\s+(\d+)\s+a\s+(\d+)\s+de\s+([\d,.]+)\s+tr[aá]mites/gi
            ));
            const ultimo = matches.length ? matches[matches.length - 1] : null;
            return JSON.stringify({
                tab_id: id,
                contador: /^\d+$/.test(contadorTexto) ? Number(contadorTexto) : null,
                mostrado_hasta: ultimo ? Number(ultimo[2]) : null,
                total_paginacion: ultimo
                    ? Number(ultimo[3].replace(/[,\.]/g, ''))
                    : null
            });
        }""",
        tab_id,
    )
    estado = json.loads(estado_texto)
    contador = estado["contador"]
    total = estado["total_paginacion"]
    return [{
        "folio": str(total),
        "archivo": "SMOKE_NAVEGACION",
        "tipo": "SMOKE_SIN_DESCARGAS",
        "ruta": json.dumps(estado, ensure_ascii=True),
        "tamano_kb": 0,
        "ok": contador == total,
        "fuente": "INTERNOS_SMOKE",
        "bandeja_internos": bandeja,
        "registro": "",
        "carpeta": "",
        "error": "" if contador == total else "contador_y_paginacion_no_coinciden",
    }]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=12, help="Navegadores paralelos; default: 12.")
    parser.add_argument("--visible", action="store_true", help="Muestra los navegadores.")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers debe ser un entero positivo")

    os.environ["SATYS_INTERNOS_SMOKE_IN_PROCESS"] = "1"
    satys.HEADLESS = not args.visible
    satys.procesar_bandeja_internos = inspeccionar_bandeja
    satys.guardar_resumen_global = lambda _resultados, _carpeta: None
    satys.habilitar_api_discovery = lambda _context: None

    inicio = time.monotonic()
    resultados = satys.descargar_internos_ift(
        bandejas=satys.BANDEJAS_INTERNOS_DEFAULT,
        workers=args.workers,
    )
    duracion = time.monotonic() - inicio

    print(f"Smoke Internos paralelo: {duracion:.1f}s")
    for item in resultados:
        print(
            f"  {item.get('bandeja_internos', '')}: "
            f"ok={item.get('ok')} total={item.get('folio', '')} "
            f"estado={item.get('ruta') or item.get('error', '')}"
        )

    ok = len(resultados) == len(satys.BANDEJAS_INTERNOS_DEFAULT) and all(
        item.get("ok") for item in resultados
    )
    print("SMOKE_OK" if ok else "SMOKE_ERROR")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
