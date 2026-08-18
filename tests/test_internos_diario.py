#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import automatizar_registros_diario as monitor
import main_procesar
import Parte1_descarga
import Parte4_excel


class InternosDiarioTest(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def test_objetivos_conservan_primera_bandeja_y_se_comparten_entre_etapas(self):
        resumen = {
            "por_bandeja": [
                {"bandeja": "En proceso", "folios": ["148326", "147390"]},
                {"bandeja": "Atendidos", "folios": ["148326", "190823"]},
            ]
        }
        objetivos = monitor.construir_objetivos_internos(
            resumen,
            ["148326", "190823"],
        )
        self.assertEqual(objetivos, [
            {"bandeja": "En proceso", "folio": "148326"},
            {"bandeja": "Atendidos", "folio": "190823"},
        ])

        path = self.tmp / "objetivos.json"
        monitor.guardar_objetivos_internos(path, objetivos)
        self.assertEqual(main_procesar.cargar_objetivos_internos(path), objetivos)
        self.assertEqual(Parte1_descarga._cargar_objetivos_internos(path), objetivos)

    def test_todos_internos_activa_solo_el_recorrido_completo(self):
        args = SimpleNamespace(
            todos_internos=True,
            internos=False,
            internos_bandejas=None,
            internos_objetivos="",
            solo_procesar=False,
            folios=[],
            archivo_folios="",
            archivo_registro="",
            buscar=0,
        )

        main_procesar.aplicar_modo_todos_internos(args)

        self.assertTrue(args.internos)
        self.assertIsNone(args.internos_bandejas)
        self.assertEqual(args.internos_objetivos, "")

    def test_todos_internos_rechaza_objetivos_parciales(self):
        args = SimpleNamespace(
            todos_internos=True,
            internos=False,
            internos_bandejas=None,
            internos_objetivos="pendientes.json",
            solo_procesar=False,
            folios=[],
            archivo_folios="",
            archivo_registro="",
            buscar=0,
        )

        with self.assertRaisesRegex(ValueError, "--internos-objetivos"):
            main_procesar.aplicar_modo_todos_internos(args)

    def test_selecciona_bandeja_internos_por_id_estable(self):
        class Page:
            def __init__(self):
                self.tab_id = None

            def evaluate(self, _script, tab_id):
                self.tab_id = tab_id
                return "CLICK"

        page = Page()
        with patch.object(
            Parte1_descarga,
            "_esperar_estado_bandeja_internos",
            return_value=True,
        ) as esperar:
            ok = Parte1_descarga.seleccionar_bandeja_internos(
                page,
                "Últimos Movimientos",
            )

        self.assertTrue(ok)
        self.assertEqual(page.tab_id, "5")
        esperar.assert_called_once_with(page, tab_id="5", timeout_ms=90_000)

    def test_extrae_fila_internos_desde_json_textual(self):
        class Fila:
            def evaluate(self, _script):
                return json.dumps({
                    "headers": ["Folio", "Acciones", "Tipo Trámite"],
                    "cells": ["148326", "Revisar", "CGPE-01-008"],
                    "columnas": {
                        "Folio": "148326",
                        "Acciones": "Revisar",
                        "Tipo Trámite": "CGPE-01-008",
                    },
                })

            def inner_text(self):
                return "148326 Revisar CGPE-01-008"

        meta = Parte1_descarga._extraer_datos_fila_internos(Fila())

        self.assertEqual(meta["folio"], "148326")
        self.assertEqual(meta["tipo_tramite"], "CGPE-01-008")

    def test_paraleliza_bandejas_y_conserva_el_orden_del_resumen(self):
        activas = 0
        max_activas = 0
        lock = threading.Lock()

        def worker(bandeja, _objetivos):
            nonlocal activas, max_activas
            with lock:
                activas += 1
                max_activas = max(max_activas, activas)
            time.sleep(0.03)
            with lock:
                activas -= 1
            return bandeja, [{"folio": bandeja, "ok": True}]

        bandejas = ["Recibidos", "En proceso", "Atendidos"]
        with patch.object(Parte1_descarga, "_validar_sesion_internos", return_value=True), \
             patch.object(Parte1_descarga, "_worker_bandeja_internos", side_effect=worker), \
             patch.object(Parte1_descarga, "guardar_resumen_global"):
            resultados = Parte1_descarga.descargar_internos_ift(
                bandejas=bandejas,
                workers=2,
            )

        self.assertEqual(max_activas, 2)
        self.assertEqual([item["folio"] for item in resultados], bandejas)

    def test_main_procesar_propaga_workers_de_internos(self):
        argv_capturado = []

        def main_falso():
            argv_capturado.extend(sys.argv)
            return 0

        with patch.object(Parte1_descarga, "main", side_effect=main_falso):
            rc = main_procesar.ejecutar_descarga_internos(
                bandejas=["Atendidos"],
                headless=True,
                workers=4,
            )

        self.assertEqual(rc, 0)
        indice = argv_capturado.index("--internos-workers")
        self.assertEqual(argv_capturado[indice + 1], "4")

    def test_reporta_fallo_al_preparar_sesion_sin_crear_workers(self):
        with patch.object(
            Parte1_descarga,
            "_validar_sesion_internos",
            side_effect=RuntimeError("Chromium no disponible"),
        ), patch.object(Parte1_descarga, "_worker_bandeja_internos") as worker:
            resultados = Parte1_descarga.descargar_internos_ift(
                bandejas=["Atendidos"],
                workers=1,
            )

        worker.assert_not_called()
        self.assertEqual(resultados[0]["archivo"], "ERROR_PREPARACION_SESION_INTERNOS")
        self.assertIn("Chromium no disponible", resultados[0]["error"])

    def test_cierra_popups_sin_cerrar_la_pagina_principal(self):
        class Pagina:
            def __init__(self, cerrada=False):
                self.cerrada = cerrada
                self.close_calls = 0

            def is_closed(self):
                return self.cerrada

            def close(self, run_before_unload=False):
                self.close_calls += 1
                self.cerrada = True

        principal = Pagina()
        popup_abierto = Pagina()
        popup_ya_cerrado = Pagina(cerrada=True)
        context = SimpleNamespace(pages=[principal, popup_abierto, popup_ya_cerrado])

        cerradas = Parte1_descarga._cerrar_paginas_emergentes(
            context,
            principal,
            motivo="prueba",
        )

        self.assertEqual(cerradas, 1)
        self.assertEqual(principal.close_calls, 0)
        self.assertEqual(popup_abierto.close_calls, 1)
        self.assertEqual(popup_ya_cerrado.close_calls, 0)

    def test_certificado_valida_las_seis_bandejas_y_sus_totales(self):
        output = self.tmp / "registros.txt"
        output.write_text("CRT26-000001\n", encoding="utf-8")
        bandejas = []
        for nombre in (
            "Recibidos",
            "En proceso",
            "Copias Marcadas",
            "Atendidos",
            "Ultimos Movimientos",
            "Fuera de tiempo",
        ):
            folios = ["148326", "147390"] if nombre == "En proceso" else []
            bandejas.append({
                "bandeja": nombre,
                "estado": "ENCONTRADOS_COMPLETOS" if folios else "VACIO_CONFIRMADO",
                "total_reportado_satys": len(folios),
                "filas_leidas": len(folios),
                "folios": folios,
                "filas_invalidas": 0,
            })
        resumen = {
            "estado": "COMPLETO",
            "integridad": "VALIDADA",
            "vacio_confirmado": False,
            "total_filas_satys": 1,
            "total_registros": 1,
            "por_anio": [{
                "anio": 2026,
                "estado": "ENCONTRADOS_COMPLETOS",
                "total_reportado_satys": 1,
                "filas_leidas": 1,
                "total_guardados_anio": 1,
                "filas_invalidas": 0,
            }],
            "internos": {
                "estado": "COMPLETO",
                "integridad": "VALIDADA",
                "vacio_confirmado": False,
                "total_folios": 2,
                "total_filas_satys": 2,
                "folios": ["148326", "147390"],
                "por_bandeja": bandejas,
            },
        }
        output.with_suffix(".txt.json").write_text(
            json.dumps(resumen),
            encoding="utf-8",
        )

        validacion = monitor.validar_resumen_extractor(output, ["CRT26-000001"])
        self.assertTrue(validacion["ok"], validacion)
        self.assertEqual(validacion["folios_internos"], ["148326", "147390"])

    def test_metadata_internos_conserva_el_folio_de_la_tabla(self):
        carpeta = self.tmp / "148326"
        metadata = Parte1_descarga._actualizar_metadata_internos(
            carpeta=carpeta,
            bandeja="En proceso",
            firma="firma-prueba",
            row_meta={"folio": "148326", "tipo_tramite": "CGPE-01-008"},
            meta={"registro": "CRT26-034284", "concesionario": "Operador de Prueba"},
            resultados=[],
        )

        self.assertEqual(metadata["folio_tabla_internos"], "148326")
        self.assertEqual(metadata["registro"], "CRT26-034284")
        persisted = json.loads((carpeta / "metadata_satys.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["folio_tabla_internos"], "148326")

    def test_excel_conserva_dos_folios_internos_con_el_mismo_registro(self):
        import openpyxl

        excel = self.tmp / "TramitesCRT.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Internos"
        headers = [
            "Control", "Estado", "Origen", "1711", "Memo/Volante",
            "Solicitante Promovente", "Representante Legal",
        ]
        for col, header in enumerate(headers, 1):
            ws.cell(row=1, column=col, value=header)
        wb.save(excel)
        wb.close()

        for folio_interno in ("148326", "190823"):
            self.assertTrue(Parte4_excel.actualizar_excel(
                folio=folio_interno,
                folio_internos=folio_interno,
                registro="CRT26-034284",
                excel_path=excel,
                sheet_name="Internos",
            ))

        wb = openpyxl.load_workbook(excel, read_only=True, data_only=True)
        ws = wb["Internos"]
        headers = {str(cell.value): cell.column for cell in ws[1] if cell.value}
        valores = [
            str(ws.cell(row=row, column=headers["Folio Internos"]).value)
            for row in range(2, ws.max_row + 1)
        ]
        registros = [
            ws.cell(row=row, column=headers["1711"]).value
            for row in range(2, ws.max_row + 1)
        ]
        wb.close()

        self.assertEqual(valores, ["148326", "190823"])
        self.assertEqual(registros, ["CRT26-034284", "CRT26-034284"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
