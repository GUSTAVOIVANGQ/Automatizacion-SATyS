from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reconciliar_metadata_global import construir_resultados
from rutas_salida import carpeta_sin_operador, ruta_relativa_sin_operador
from sincronizacion_depi import validar_destino_compartido


class RutasSalidaTests(unittest.TestCase):
    def test_correo_2408_va_a_carpeta_separada(self):
        self.assertEqual(carpeta_sin_operador("CORREO-2408"), "sin_operador_CORREO")
        self.assertEqual(carpeta_sin_operador("correo-2408-anexo"), "sin_operador_CORREO")
        self.assertEqual(
            ruta_relativa_sin_operador("CRT26-000001", "CORREO-2408"),
            r"sin_operador_CORREO\CRT26-000001",
        )

    def test_otros_folios_conservan_sin_operador(self):
        self.assertEqual(carpeta_sin_operador("VE-185606"), "_sin_operador")

    def test_reconciliacion_calcula_ruta_por_id_y_migra_correo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            descargas = root / "descargas"
            output = root / "output"
            descargas.mkdir()

            correo = descargas / "CRT26-000001"
            correo.mkdir()
            (correo / "metadata_satys.json").write_text(
                json.dumps({
                    "registro": "CRT26-000001",
                    "folio_opc": "CORREO-2408",
                    "id_solicitante": "",
                }),
                encoding="utf-8",
            )
            (correo / "documento.pdf").write_bytes(b"demo")

            operador = descargas / "CRT26-000002"
            operador.mkdir()
            (operador / "metadata_satys.json").write_text(
                json.dumps({
                    "registro": "CRT26-000002",
                    "folio_opc": "VE-1",
                    "id_solicitante": "123",
                }),
                encoding="utf-8",
            )

            resultados, stats = construir_resultados(
                descargas,
                output,
                {"123": {"idBp": "123", "nombre_completo": "OPERADOR DEMO"}},
                migrar_correos=True,
            )
            por_registro = {r["registro"]: r for r in resultados}
            self.assertTrue(por_registro["CRT26-000002"]["rpc_ok"])
            self.assertIn("123_operador_demo", por_registro["CRT26-000002"]["output_dir"])
            self.assertTrue(
                (output / "sin_operador_CORREO" / "CRT26-000001" / "documento.pdf").exists()
            )
            self.assertEqual(stats["sin_operador_correo"], 1)

    def test_reconciliacion_global_excluye_metadata_de_internos(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            descargas = root / "descargas"
            output = root / "output"
            normal = descargas / "CRT26-000001"
            interno = descargas / "Internos" / "En_proceso" / "148326"
            normal.mkdir(parents=True)
            interno.mkdir(parents=True)

            (normal / "metadata_satys.json").write_text(
                json.dumps({"registro": "CRT26-000001", "id_solicitante": "123"}),
                encoding="utf-8",
            )
            (interno / "metadata_satys.json").write_text(
                json.dumps({
                    "registro": "CRT26-034284",
                    "id_solicitante": "123",
                    "satys_flujo": "internos",
                    "bandeja_internos": "En proceso",
                    "folio_tabla_internos": "148326",
                }),
                encoding="utf-8",
            )

            resultados, stats = construir_resultados(
                descargas,
                output,
                {"123": {"idBp": "123", "nombre_completo": "OPERADOR DEMO"}},
                migrar_correos=False,
            )

            self.assertEqual([r["registro"] for r in resultados], ["CRT26-000001"])
            self.assertEqual(stats["metadata"], 1)

    def test_bloquea_depi_local_sin_montaje(self):
        # En el entorno de pruebas /depi no es un CIFS montado; debe impedirse
        # escribir allí para evitar un falso éxito de sincronización.
        self.assertIsNotNone(validar_destino_compartido(Path("/depi/satys_mount_inexistente/SATyS")))


if __name__ == "__main__":
    unittest.main()
