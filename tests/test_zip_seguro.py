from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import Parte1_descarga as descarga


class ZipSeguroTests(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def test_acorta_ruta_larga_preserva_contenido_y_elimina_zip(self):
        archivo = self.tmp / "documentos.zip"
        miembro = (
            "Operador/"
            + "R020. Informacion Estadistica sobre el Servicio Mayorista de Coubicacion/"
            + "Anexo R020. Informacion Estadistica sobre el Servicio Mayorista de Coubicacion.docx"
        )
        with zipfile.ZipFile(archivo, "w") as zf:
            zf.writestr(miembro, b"contenido-prueba")

        with patch.object(descarga, "ZIP_RUTA_RELATIVA_MAX", 100):
            total = descarga.descomprimir_todos_zips_en_carpeta(self.tmp)

        self.assertEqual(total, 1)
        self.assertFalse(archivo.exists())
        extraidos = list(self.tmp.rglob("*.docx"))
        self.assertEqual(len(extraidos), 1)
        self.assertEqual(extraidos[0].read_bytes(), b"contenido-prueba")
        self.assertLessEqual(len(str(extraidos[0].relative_to(self.tmp))), 100)

    def test_zip_corrupto_se_intenta_una_vez_y_no_genera_bucle(self):
        archivo = self.tmp / "corrupto.zip"
        archivo.write_bytes(b"esto no es un zip")

        original = descarga._extraer_contenido_zip
        with patch.object(descarga, "_extraer_contenido_zip", wraps=original) as extractor:
            total = descarga.descomprimir_todos_zips_en_carpeta(self.tmp)

        self.assertEqual(total, 0)
        self.assertTrue(archivo.exists())
        self.assertEqual(extractor.call_count, 1)

    def test_limita_componentes_utf8_para_linux(self):
        archivo = self.tmp / "unicode.zip"
        miembro = f"{'á' * 150}/archivo.txt"
        with zipfile.ZipFile(archivo, "w") as zf:
            zf.writestr(miembro, "ok")

        with patch.object(descarga, "ZIP_RUTA_RELATIVA_MAX", 400):
            total = descarga.descomprimir_todos_zips_en_carpeta(self.tmp)

        self.assertEqual(total, 1)
        extraido = next(self.tmp.rglob("archivo.txt"))
        for componente in extraido.relative_to(self.tmp).parts:
            self.assertLessEqual(len(componente.encode("utf-8")), 220)

    def test_procesa_zip_anidado_en_iteraciones_acotadas(self):
        buffer_interno = io.BytesIO()
        with zipfile.ZipFile(buffer_interno, "w") as zf:
            zf.writestr("resultado.txt", "ok")
        archivo = self.tmp / "exterior.zip"
        with zipfile.ZipFile(archivo, "w") as zf:
            zf.writestr("nivel/interior.zip", buffer_interno.getvalue())

        total = descarga.descomprimir_todos_zips_en_carpeta(self.tmp)

        self.assertEqual(total, 2)
        self.assertFalse(list(self.tmp.rglob("*.zip")))
        self.assertEqual((self.tmp / "nivel" / "resultado.txt").read_text(), "ok")

    def test_rechaza_zip_slip_sin_escribir_fuera_de_destino(self):
        archivo = self.tmp / "inseguro.zip"
        with zipfile.ZipFile(archivo, "w") as zf:
            zf.writestr("../escape.txt", "no")

        total = descarga.descomprimir_todos_zips_en_carpeta(self.tmp)

        self.assertEqual(total, 0)
        self.assertTrue(archivo.exists())
        self.assertFalse((self.tmp.parent / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
