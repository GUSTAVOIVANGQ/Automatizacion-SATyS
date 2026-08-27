from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main_procesar
import Parte4_excel
from estado_descargas import (
    auditar_carpeta_descarga,
    carpeta_descarga_esta_completa,
    registro_esta_completo,
)


def _metadata_ok(nombre: str = "documento.pdf") -> dict:
    return {
        "estado": "OK",
        "coincide": True,
        "documentos_portal_completos": True,
        "total_archivos_encontrados": 1,
        "total_archivos_ok": 1,
        "total_archivos_error": 0,
        "archivos": [{"archivo": nombre, "ok": True}],
    }


class AuditoriaDescargasTests(unittest.TestCase):
    def test_descarga_completa_exige_metadata_conteos_y_archivo_fisico(self):
        with tempfile.TemporaryDirectory() as td:
            carpeta = Path(td) / "CRT26-000001"
            carpeta.mkdir()
            (carpeta / "documento.pdf").write_bytes(b"real")
            (carpeta / "metadata_completo.json").write_text(
                json.dumps(_metadata_ok()),
                encoding="utf-8",
            )

            auditoria = auditar_carpeta_descarga(carpeta)

            self.assertTrue(auditoria["completo"])
            self.assertEqual(auditoria["motivos"], [])
            self.assertTrue(carpeta_descarga_esta_completa(carpeta))
            self.assertTrue(registro_esta_completo(carpeta.parent, carpeta.name))

    def test_vacia_o_solo_json_es_incompleta_y_nunca_se_borra(self):
        with tempfile.TemporaryDirectory() as td:
            vacia = Path(td) / "CRT26-000002"
            solo_json = Path(td) / "CRT26-000003"
            vacia.mkdir()
            solo_json.mkdir()
            (solo_json / "metadata_satys.json").write_text("{}", encoding="utf-8")

            auditoria_vacia = auditar_carpeta_descarga(vacia)
            auditoria_json = auditar_carpeta_descarga(solo_json)

            self.assertIn("carpeta_vacia", auditoria_vacia["motivos"])
            self.assertIn("sin_archivos_reales", auditoria_json["motivos"])
            self.assertIn("metadata_completo_ausente", auditoria_json["motivos"])
            self.assertTrue(vacia.exists())
            self.assertTrue((solo_json / "metadata_satys.json").exists())

    def test_detecta_temporales_vacios_zip_y_metadata_corrupta(self):
        with tempfile.TemporaryDirectory() as td:
            carpeta = Path(td) / "CRT26-000004"
            carpeta.mkdir()
            (carpeta / "documento.pdf").write_bytes(b"real")
            (carpeta / "pendiente.crdownload").write_bytes(b"parcial")
            (carpeta / "vacio.docx").write_bytes(b"")
            (carpeta / "pendiente.zip").write_bytes(b"zip")
            (carpeta / "metadata_completo.json").write_text("{", encoding="utf-8")

            motivos = auditar_carpeta_descarga(carpeta)["motivos"]

            self.assertIn("archivo_temporal_pendiente", motivos)
            self.assertIn("archivo_real_vacio", motivos)
            self.assertIn("zip_pendiente_de_extraer", motivos)
            self.assertIn("metadata_completo_ilegible_o_corrupto", motivos)

    def test_detecta_estado_conteos_errores_y_archivo_fisico_faltante(self):
        with tempfile.TemporaryDirectory() as td:
            carpeta = Path(td) / "CRT26-000005"
            carpeta.mkdir()
            (carpeta / "otro.pdf").write_bytes(b"real")
            metadata = {
                "estado": "PARCIAL",
                "coincide": False,
                "documentos_portal_completos": False,
                "total_archivos_encontrados": 3,
                "total_archivos_ok": 2,
                "total_archivos_error": 1,
                "archivos": [
                    {"archivo": "faltante.pdf", "ok": True},
                    {"archivo": "fallido.pdf", "ok": False},
                ],
            }
            (carpeta / "metadata_completo.json").write_text(
                json.dumps(metadata),
                encoding="utf-8",
            )

            motivos = auditar_carpeta_descarga(carpeta)["motivos"]

            esperados = {
                "estado_metadata_no_ok",
                "metadata_no_coincide",
                "recorrido_documentos_portal_incompleto",
                "uno_o_mas_archivos_reportados_con_error",
                "conteo_total_no_coincide",
                "conteo_ok_no_coincide",
                "conteo_reporta_errores",
                "no_todos_los_archivos_quedaron_ok",
                "archivo_reportado_ok_no_existe_fisicamente",
            }
            self.assertTrue(esperados.issubset(set(motivos)), motivos)

    def test_descubrimiento_procesa_carpetas_existentes_aunque_estén_vacias(self):
        with tempfile.TemporaryDirectory() as td:
            descargas = Path(td) / "descargas"
            registro = descargas / "CRT26-000006"
            interno = descargas / "internos" / "Atendidos" / "190006"
            registro.mkdir(parents=True)
            interno.mkdir(parents=True)

            with patch.object(main_procesar, "DESCARGA_BASE", descargas):
                normales = main_procesar.descubrir_descargas_procesables()
                internos = main_procesar.descubrir_descargas_internos()

            self.assertIn(registro, [item[0] for item in normales])
            self.assertIn(interno, [item[0] for item in internos])

    def test_proteccion_impide_eliminar_cualquier_expediente_de_descargas(self):
        with tempfile.TemporaryDirectory() as td:
            descargas = Path(td) / "descargas"
            expediente = descargas / "CRT26-000007"
            expediente.mkdir(parents=True)
            (expediente / "documento.pdf").write_bytes(b"real")

            with patch.object(Parte4_excel, "DESCARGA_BASE", descargas):
                with self.assertRaises(ValueError):
                    Parte4_excel.eliminar_arbol_robusto(expediente)

            self.assertTrue(expediente.exists())
            self.assertTrue((expediente / "documento.pdf").exists())


if __name__ == "__main__":
    unittest.main()
