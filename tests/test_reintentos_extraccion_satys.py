#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import automatizar_registros_diario as monitor


class ReintentosExtraccionSatysTest(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.output = self.tmp / "registros_satys.txt"
        self.log = self.tmp / "monitor.log"

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def escribir_resumen_valido(self, registros, *, vacio=False):
        total = 0 if vacio else len(registros)
        estado = "VACIO_CONFIRMADO" if vacio else "ENCONTRADOS_COMPLETOS"
        contenido = {
            "estado": "COMPLETO",
            "integridad": "VALIDADA",
            "vacio_confirmado": vacio,
            "total_filas_satys": total,
            "total_registros": len(registros),
            "por_anio": [{
                "anio": 2026,
                "estado": estado,
                "total_reportado_satys": total,
                "filas_leidas": total,
                "total_guardados_anio": len(registros),
                "filas_invalidas": 0,
            }],
            "internos": {
                "estado": "COMPLETO",
                "integridad": "VALIDADA",
                "vacio_confirmado": True,
                "total_folios": 0,
                "total_filas_satys": 0,
                "folios": [],
                "por_bandeja": [
                    {
                        "bandeja": bandeja,
                        "estado": "VACIO_CONFIRMADO",
                        "total_reportado_satys": 0,
                        "filas_leidas": 0,
                        "folios": [],
                        "filas_invalidas": 0,
                    }
                    for bandeja in (
                        "Recibidos",
                        "En proceso",
                        "Copias Marcadas",
                        "Atendidos",
                        "Ultimos Movimientos",
                        "Fuera de tiempo",
                    )
                ],
            },
        }
        self.output.with_suffix(".txt.json").write_text(
            __import__("json").dumps(contenido), encoding="utf-8"
        )

    def test_reintenta_cero_error_y_luego_acepta_registros(self):
        llamadas = []

        def fake_ejecutar(cmd, cwd, log_path, titulo, estado=None, etapa=""):
            llamadas.append(titulo)
            numero = len(llamadas)
            if numero == 1:
                # Primer intento correcto técnicamente, pero SATyS devuelve cero.
                self.output.write_text("", encoding="utf-8")
                self.output.with_suffix(".txt.json").write_text(
                    '{"total_registros": 0}', encoding="utf-8"
                )
                return 0
            if numero == 2:
                # Segundo intento reproduce una excepción del extractor.
                return 1
            registros = ["CRT26-000001", "CRT26-000002"]
            self.output.write_text("\n".join(registros) + "\n", encoding="utf-8")
            self.escribir_resumen_valido(registros)
            return 0

        with patch.object(monitor, "ejecutar_comando", side_effect=fake_ejecutar), \
             patch.object(monitor.time, "sleep") as dormir:
            registros, historial = monitor.extraer_registros_satys_con_reintentos(
                cmd_extraer=["python", "extraer.py"],
                output_path=self.output,
                cwd=self.tmp,
                log_path=self.log,
                estado=None,
                reintentos=2,
                espera_segundos=0,
            )

        self.assertEqual(registros, ["CRT26-000001", "CRT26-000002"])
        self.assertEqual(len(historial), 3)
        self.assertIn("certificado de integridad inválido", historial[0]["resultado"])
        self.assertEqual(historial[1]["return_code"], 1)
        self.assertEqual(historial[2]["resultado"], "ok")
        self.assertEqual(len(llamadas), 3)
        dormir.assert_not_called()

    def test_no_reintenta_cuando_el_primer_intento_tiene_registros(self):
        def fake_ejecutar(cmd, cwd, log_path, titulo, estado=None, etapa=""):
            registros = ["CRT26-123456"]
            self.output.write_text("CRT26-123456\n", encoding="utf-8")
            self.escribir_resumen_valido(registros)
            return 0

        with patch.object(monitor, "ejecutar_comando", side_effect=fake_ejecutar) as ejecutar, \
             patch.object(monitor.time, "sleep") as dormir:
            registros, historial = monitor.extraer_registros_satys_con_reintentos(
                cmd_extraer=["python", "extraer.py"],
                output_path=self.output,
                cwd=self.tmp,
                log_path=self.log,
                estado=None,
                reintentos=2,
                espera_segundos=0,
            )

        self.assertEqual(registros, ["CRT26-123456"])
        self.assertEqual(len(historial), 1)
        self.assertEqual(ejecutar.call_count, 1)
        dormir.assert_not_called()

    def test_acepta_cero_cuando_el_resumen_lo_confirma(self):
        def fake_ejecutar(cmd, cwd, log_path, titulo, estado=None, etapa=""):
            self.output.write_text("", encoding="utf-8")
            self.escribir_resumen_valido([], vacio=True)
            return 0

        with patch.object(monitor, "ejecutar_comando", side_effect=fake_ejecutar) as ejecutar:
            registros, historial = monitor.extraer_registros_satys_con_reintentos(
                cmd_extraer=["python", "extraer.py"],
                output_path=self.output,
                cwd=self.tmp,
                log_path=self.log,
                estado=None,
                reintentos=2,
                espera_segundos=0,
            )

        self.assertEqual(registros, [])
        self.assertTrue(historial[0]["vacio_confirmado"])
        self.assertEqual(historial[0]["resultado"], "VACIO_CONFIRMADO")
        self.assertEqual(ejecutar.call_count, 1)

    def test_falla_despues_de_tres_intentos_sin_registros(self):
        def fake_ejecutar(cmd, cwd, log_path, titulo, estado=None, etapa=""):
            self.output.write_text("", encoding="utf-8")
            return 0

        with patch.object(monitor, "ejecutar_comando", side_effect=fake_ejecutar) as ejecutar, \
             patch.object(monitor.time, "sleep") as dormir:
            with self.assertRaisesRegex(RuntimeError, "después de 3 intento"):
                monitor.extraer_registros_satys_con_reintentos(
                    cmd_extraer=["python", "extraer.py"],
                    output_path=self.output,
                    cwd=self.tmp,
                    log_path=self.log,
                    estado=None,
                    reintentos=2,
                    espera_segundos=0,
                )

        self.assertEqual(ejecutar.call_count, 3)
        dormir.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
