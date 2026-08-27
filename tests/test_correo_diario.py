from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import automatizar_registros_diario as diario
import notificar_email


class CorreoDiarioTests(unittest.TestCase):
    def test_conteos_consolidados_separan_exito_revision_y_error(self):
        resultados = [
            {
                "registro": "1001",
                "bandeja_internos": "Atendidos",
                "rpc_ok": True,
                "organizado_ok": True,
                "excel_ok": True,
                "rpc_resultado": {"fuente": "excel_rpc", "metodo": "id_exacto_excel"},
            },
            {
                "registro": "CRT26-000001",
                "rpc_ok": True,
                "organizado_ok": True,
                "excel_ok": True,
                "rpc_resultado": {
                    "fuente": "rpc_online_resultados",
                    "metodo": "nombre_exacto_rpc_resultados",
                },
            },
            {
                "registro": "CRT26-000002",
                "rpc_ok": False,
                "organizado_ok": True,
                "excel_ok": True,
                "output_dir": "output/_sin_operador/(correos)/CRT26-000002",
                "rpc_resultado": {"motivo": "sin_coincidencia_exacta"},
            },
            {
                "registro": "CRT26-000003",
                "es_correo": True,
                "folio_opc": "CORREO-271",
                "rpc_ok": True,
                "organizado_ok": True,
                "excel_ok": True,
                "output_dir": "output/_sin_operador/(correos)/CRT26-000003",
                "rpc_resultado": {"fuente": "excel_rpc", "metodo": "id_exacto_excel"},
            },
            {
                "registro": "CRT26-000004",
                "rpc_ok": True,
                "organizado_ok": True,
                "excel_ok": False,
            },
        ]

        conteos = notificar_email.conteos_desde_resultados(resultados)

        self.assertEqual(conteos["total"], 5)
        self.assertEqual(conteos["exitosos"], 2)
        self.assertEqual(conteos["sin_operador"], 2)
        self.assertEqual(conteos["errores"], 1)
        self.assertEqual(conteos["internos"], 1)
        self.assertEqual(conteos["oficialia_otros"], 4)
        self.assertEqual(conteos["correos"], 1)
        self.assertEqual(conteos["rpc_excel"], 2)
        self.assertEqual(conteos["rpc_online"], 1)

        html = notificar_email.construir_html(
            "2026-08-27T12:00:00",
            "CORRIDA DIARIA CONSOLIDADA",
            conteos,
            resultados,
            {"Carpeta output": "C:/satys/output", "Carpeta descargas": "C:/satys/descargas"},
        )
        self.assertIn("Pendientes que requieren atención", html)
        self.assertIn("Excel oficial primero", html)
        self.assertNotIn("Registros procesados</h2>", html)

    def test_carga_solo_log_actual_y_agrega_fallido_controlado_una_vez(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "procesamiento.json"
            path.write_text(
                json.dumps({"resultados": [{"registro": "CRT26-000001"}]}),
                encoding="utf-8",
            )
            resultados = diario.cargar_resultados_procesamiento(
                path,
                origen="oficialia",
                mtime_minimo=time.time(),
            )
            self.assertEqual(resultados[0]["_origen_proceso"], "oficialia")

            consolidados = diario.agregar_fallidos_controlados(
                resultados,
                ["CRT26-000001", "CRT26-000002"],
            )
            self.assertEqual(len(consolidados), 2)
            self.assertTrue(consolidados[1]["_fallido_controlado"])

            self.assertEqual(
                diario.cargar_resultados_procesamiento(
                    path,
                    origen="oficialia",
                    mtime_minimo=time.time() + 60,
                ),
                [],
            )

    def test_resumen_diario_realiza_una_sola_llamada_de_correo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_path = root / "monitor.log"
            excel_path = root / "TrámitesCRT.xlsx"
            log_path.write_text("ok", encoding="utf-8")
            excel_path.write_bytes(b"xlsx")
            with (
                patch.object(diario, "ruta_configurada", side_effect=lambda clave, default: root / default),
                patch.object(diario, "carpeta_compartida", return_value=root / "depi"),
                patch.object(diario._email_mod, "enviar_notificacion", return_value=True) as enviar,
            ):
                ok = diario.enviar_resumen_email_diario(
                    resultados=[],
                    log_path=log_path,
                    excel_path=excel_path,
                )

            self.assertTrue(ok)
            enviar.assert_called_once()
            outputs = enviar.call_args.kwargs["outputs"]
            self.assertIn("Carpeta output", outputs)
            self.assertIn("Carpeta descargas", outputs)
            self.assertIn("Folios_Datos_Completos_Internos.xlsx", outputs)


if __name__ == "__main__":
    unittest.main()
