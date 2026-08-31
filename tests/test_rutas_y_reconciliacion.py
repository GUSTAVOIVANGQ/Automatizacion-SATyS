from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main_procesar
from reconciliar_metadata_global import construir_resultados
from rutas_salida import (
    carpeta_sin_operador,
    es_folio_opc_correo,
    ruta_relativa_sin_operador,
)
from sincronizacion_depi import sincronizar_salidas, validar_destino_compartido


class RutasSalidaTests(unittest.TestCase):
    def test_cualquier_prefijo_correo_va_a_subcarpeta_exclusiva(self):
        self.assertTrue(es_folio_opc_correo("CORREO-271"))
        self.assertTrue(es_folio_opc_correo("correo-2408-anexo"))
        self.assertEqual(
            carpeta_sin_operador("CORREO-271"),
            str(Path("_sin_operador") / "(correos)"),
        )
        self.assertEqual(
            ruta_relativa_sin_operador("CRT26-000001", "CORREO-271"),
            r"_sin_operador\(correos)\CRT26-000001",
        )

    def test_sin_operador_general_permanece_en_raiz_separada(self):
        self.assertEqual(carpeta_sin_operador("VE-185606"), "_sin_operador")
        self.assertEqual(
            ruta_relativa_sin_operador("CRT26-009999", "VE-185606"),
            r"_sin_operador\CRT26-009999",
        )

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
                    "folio_opc": "CORREO-271",
                    "id_solicitante": "123",
                    "nombre_operador": "OPERADOR DEMO",
                }),
                encoding="utf-8",
            )
            (correo / "documento.pdf").write_bytes(b"demo")
            duplicado = output / "_sin_operador" / "CRT26-000001"
            duplicado.mkdir(parents=True)
            (duplicado / "archivo_previo.txt").write_text("previo", encoding="utf-8")

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
                (
                    output
                    / "_sin_operador"
                    / "(correos)"
                    / "CRT26-000001"
                    / "documento.pdf"
                ).exists()
            )
            self.assertEqual(list((output / "_sin_operador" / "(correos)").rglob("*.json")), [])
            self.assertTrue(
                (
                    output
                    / "_sin_operador"
                    / "(correos)"
                    / "CRT26-000001"
                    / "archivo_previo.txt"
                ).exists()
            )
            self.assertFalse(duplicado.exists())
            self.assertFalse((output / "123_operador_demo" / "CRT26-000001").exists())
            self.assertTrue(por_registro["CRT26-000001"]["es_correo"])
            self.assertEqual(stats["sin_operador_correo"], 1)

    def test_tres_bandejas_respetan_destino_exclusivo_de_correos(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            descargas = root / "descargas"
            output = root / "output"
            catalogo = [{
                "idBp": "123",
                "nombre_completo": "OPERADOR DEMO",
                "concesionario": "OPERADOR DEMO",
                "norm": "operador demo",
                "compact": "operadordemo",
            }]
            carpeta_compartida_operador = output / "123_operador_demo" / "01 EN" / "VE"
            carpeta_compartida_operador.mkdir(parents=True)
            (carpeta_compartida_operador / "otro_expediente.pdf").write_bytes(
                b"no pertenece al correo"
            )

            with (
                patch.object(main_procesar, "OUTPUT_BASE", output),
                patch.object(main_procesar, "actualizar_excel", return_value=True),
            ):
                for indice, bandeja in enumerate(
                    ("administracion_solicitudes", "tramites_nuevos", "enlace_oficialia"),
                    start=1,
                ):
                    registro = f"CRT26-{indice:06d}"
                    carpeta = descargas / bandeja / registro
                    carpeta.mkdir(parents=True)
                    (carpeta / "metadata_satys.json").write_text(
                        json.dumps({
                            "folio": str(indice),
                            "registro": registro,
                            "folio_opc": f"CORREO-{270 + indice}",
                            "id_solicitante": "123",
                            "nombre_operador": "OPERADOR DEMO",
                        }),
                        encoding="utf-8",
                    )
                    (carpeta / "documento.pdf").write_bytes(b"correo")

                    folio_id = f"{bandeja}__{registro}"
                    duplicado = output / "_sin_operador" / folio_id
                    duplicado.mkdir(parents=True)
                    (duplicado / "metadata_satys.json").write_text(
                        "{}",
                        encoding="utf-8",
                    )

                    resultado = main_procesar.procesar_folio(
                        folio=str(indice),
                        catalogo=catalogo,
                        carpeta=carpeta,
                        folio_id=folio_id,
                    )
                    destino = output / "_sin_operador" / "(correos)" / registro
                    self.assertTrue(resultado["es_correo"], bandeja)
                    self.assertTrue(resultado["organizado_ok"], bandeja)
                    self.assertEqual(Path(resultado["output_dir"]), destino)
                    self.assertTrue((destino / "documento.pdf").exists(), bandeja)
                    self.assertEqual(list(destino.rglob("*.json")), [], bandeja)
                    self.assertFalse(duplicado.exists(), bandeja)
                    self.assertFalse(
                        (output / "123_operador_demo" / registro).exists(),
                        bandeja,
                    )
                    self.assertEqual(
                        (carpeta_compartida_operador / "otro_expediente.pdf").read_bytes(),
                        b"no pertenece al correo",
                        bandeja,
                    )

    def test_sin_operador_copia_documentos_pero_no_metadata_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "output"
            carpeta = root / "descargas" / "CRT26-009999"
            carpeta.mkdir(parents=True)
            (carpeta / "metadata_satys.json").write_text(
                json.dumps({
                    "folio": "9999",
                    "registro": "CRT26-009999",
                    "nombre_operador": "OPERADOR SIN COINCIDENCIA",
                }),
                encoding="utf-8",
            )
            (carpeta / "metadata_completo.json").write_text("{}", encoding="utf-8")
            (carpeta / "documento.pdf").write_bytes(b"real")
            # Simula una clasificación de la release retractada: el expediente
            # normal estaba incorrectamente dentro de (correos).
            destino_legacy = output / "_sin_operador" / "(correos)" / "CRT26-009999"
            destino_legacy.mkdir(parents=True)
            (destino_legacy / "archivo_anterior.txt").write_text("anterior", encoding="utf-8")

            with (
                patch.object(main_procesar, "OUTPUT_BASE", output),
                patch.object(main_procesar, "actualizar_excel", return_value=True),
                patch(
                    "buscar_concesionario.resolver_operador_seguro",
                    return_value={
                        "ok": False,
                        "score": 0.0,
                        "motivo": "sin_coincidencia_exacta",
                    },
                ),
            ):
                resultado = main_procesar.procesar_folio(
                    folio="9999",
                    catalogo=[],
                    carpeta=carpeta,
                    folio_id="CRT26-009999",
                )

            destino = output / "_sin_operador" / "CRT26-009999"
            self.assertEqual(Path(resultado["output_dir"]), destino)
            self.assertTrue(resultado["organizado_ok"])
            self.assertTrue((destino / "documento.pdf").exists())
            self.assertTrue((destino / "archivo_anterior.txt").exists())
            self.assertFalse(destino_legacy.exists())
            self.assertEqual(list(destino.rglob("*.json")), [])

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
        # Este test valida explícitamente el modo seguro por defecto. El runtime
        # portable usa SATYS_REQUIRE_SHARED_MOUNT=0 porque /shared ya es un bind
        # mount validado por el host; esa variable no debe contaminar esta prueba.
        with patch.dict(os.environ, {"SATYS_REQUIRE_SHARED_MOUNT": "1"}):
            self.assertIsNotNone(
                validar_destino_compartido(Path("/depi/satys_mount_inexistente/SATyS"))
            )

    def test_sincronizacion_excluye_json_de_output_y_conserva_json_en_descargas(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proyecto = root / "proyecto"
            compartida = root / "compartida"
            output_expediente = proyecto / "output" / "100_operador" / "CRT26-000001"
            descarga_expediente = proyecto / "descargas" / "CRT26-000001"
            output_expediente.mkdir(parents=True)
            descarga_expediente.mkdir(parents=True)
            (output_expediente / "documento.pdf").write_bytes(b"real")
            (output_expediente / "metadata_satys.json").write_text("{}", encoding="utf-8")
            (descarga_expediente / "documento.pdf").write_bytes(b"real")
            (descarga_expediente / "metadata_satys.json").write_text("{}", encoding="utf-8")

            resultado = sincronizar_salidas(
                proyecto,
                compartida,
                archivos=(),
            )

            self.assertEqual(resultado.errores, [])
            self.assertGreaterEqual(resultado.json_output_eliminados, 1)
            self.assertFalse((output_expediente / "metadata_satys.json").exists())
            self.assertFalse(
                (
                    compartida
                    / "output"
                    / "100_operador"
                    / "CRT26-000001"
                    / "metadata_satys.json"
                ).exists()
            )
            self.assertTrue(
                (compartida / "descargas" / "CRT26-000001" / "metadata_satys.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
