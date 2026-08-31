from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import buscar_concesionario as bc
import Parte4_excel
import main_procesar
from Parte3_rpc import construir_ruta, construir_ruta_operadores
from reconciliar_metadata_global import cargar_indice_rpc, construir_resultados
from reporte_operadores import generar_reportes_operadores


class ResolucionOperadorTests(unittest.TestCase):
    def setUp(self):
        bc._CACHE_RPC_NOMBRE.clear()

    def test_normalizacion_compacta_acentos_puntuacion_y_espacios(self):
        izquierda = bc.normalizar_nombre("Wal-Mart.S.A.").replace(" ", "")
        derecha = bc.normalizar_nombre("WalMart S.A.").replace(" ", "")
        self.assertEqual(izquierda, "walmartsa")
        self.assertEqual(izquierda, derecha)

    def test_resultados_rpc_extrae_folio_id_y_nombre_sin_duplicar_titulos(self):
        html = """
        <div class="strip_all_tour_list"><h3>FET005427CO-100737 - SISTEMAS SATELITALES DE MÉXICO, S. DE R.L. DE C.V.</h3></div>
        <div class="strip_all_tour_list"><h3>FET009999CO-100737 - SISTEMAS SATELITALES DE MÉXICO, S. DE R.L. DE C.V.</h3></div>
        """
        resultados = bc._extraer_resultados_concesiones_html(html)
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]["idBp"], "100737")
        self.assertEqual(
            resultados[0]["nombre_completo"],
            "SISTEMAS SATELITALES DE MÉXICO, S. DE R.L. DE C.V.",
        )

    def test_rpc_acepta_misma_base_con_distinto_tipo_societario(self):
        diagnostico = bc.seleccionar_candidato_rpc_seguro(
            "BBG COMUNICACIÓN, S.A. DE C.V.",
            [{
                "idBp": "103077",
                "nombre_completo": "BBG COMUNICACIÓN, S. DE R.L. DE C.V.",
            }],
        )
        self.assertTrue(diagnostico["ok"])
        self.assertEqual(diagnostico["metodo"], "nombre_base_legal_rpc")
        self.assertEqual(diagnostico["idBp"], "103077")

    def test_rpc_no_acepta_similitud_si_no_hay_margen_entre_ids(self):
        diagnostico = bc.seleccionar_candidato_rpc_seguro(
            "OPERADORA TELECOMUNICACIONES MEXICO",
            [
                {"idBp": "100", "nombre_completo": "OPERADORA TELECOMUNICACIONES MÉXICO UNO"},
                {"idBp": "200", "nombre_completo": "OPERADORA TELECOMUNICACIONES MÉXICO DOS"},
            ],
            similitud_minima=80,
            margen_minimo=5,
        )
        self.assertFalse(diagnostico["ok"])
        self.assertEqual(diagnostico["estado"], "sin_coincidencia_segura")

    def test_extrae_operador_de_sexta_columna_texto_fila(self):
        metadata = {
            "texto_fila": (
                "176558\t25/08/2026\tCRT26-000001\tAsunto\tPromovente\t"
                "R2A MÉXICO, S.A. DE C.V.\tEn proceso"
            )
        }
        self.assertEqual(
            main_procesar.extraer_nombre_operador_texto_fila(metadata),
            "R2A MÉXICO, S.A. DE C.V.",
        )

    def test_folio_internos_prioriza_folio_real_sobre_placeholder_100(self):
        with tempfile.TemporaryDirectory() as td:
            carpeta = Path(td) / "138668"
            carpeta.mkdir()
            (carpeta / "metadata_satys.json").write_text(
                json.dumps({
                    "folio": "100",
                    "registro": "100",
                    "folio_tabla_internos": "138668",
                }),
                encoding="utf-8",
            )
            folio = main_procesar.folio_excel_desde_metadata(carpeta, carpeta.name)
        self.assertEqual(folio, "138668")

    def test_carga_csv_de_folios_internos_sin_duplicados(self):
        with tempfile.TemporaryDirectory() as td:
            ruta = Path(td) / "folios.csv"
            ruta.write_text(
                "NumeroRegistro,Comentario\n103336,uno\n103336,duplicado\n176558.0,dos\n",
                encoding="utf-8-sig",
            )
            folios = main_procesar.cargar_folios_internos_desde_archivo(ruta)
        self.assertEqual(folios, ["103336", "176558"])

    def test_nombre_exacto_ambiguo_no_elige_el_primer_id(self):
        catalogo = bc.preparar_catalogo_para_matching([
            {"idBp": "100", "concesionario": "OPERADOR, S.A. DE C.V."},
            {"idBp": "200", "concesionario": "OPERADOR S.A. DE C.V."},
        ])
        diagnostico = bc.diagnosticar_nombre_operador_exacto("Operador.S.A. de C.V.", catalogo)
        self.assertFalse(diagnostico["ok"])
        self.assertEqual(diagnostico["estado"], "ambiguo")
        self.assertEqual({c["idBp"] for c in diagnostico["candidatos"]}, {"100", "200"})

    def test_prioriza_id_exacto_del_excel(self):
        catalogo = bc.preparar_catalogo_para_matching([
            {"idBp": "100028", "concesionario": "ADOLFO MERINO MEDINA"},
        ])
        resultado = bc.resolver_operador_seguro("100028.0", "Nombre distinto", catalogo)
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["metodo"], "id_exacto_excel")
        self.assertEqual(resultado["idBp"], "100028")

    def test_sin_id_usa_nombre_exacto_unico_del_excel(self):
        catalogo = bc.preparar_catalogo_para_matching([
            {"idBp": "521142", "concesionario": "CTI CALL S.A. DE C.V."},
        ])
        resultado = bc.resolver_operador_seguro("", "CTI CALL, S.A. DE C.V.", catalogo)
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["metodo"], "nombre_exacto_excel")
        self.assertEqual(resultado["idBp"], "521142")

    def test_id_no_encontrado_puede_resolverse_con_rpc_online_exacto(self):
        respuesta = {
            "ok": True,
            "estado": "coincidencia_unica",
            "idBp": "519448",
            "nombre_completo": "FIBER NETWORK DEL SUR S. DE R.L. DE C.V.",
            "consulta_rpc": "fiber network del sur",
        }
        with patch.object(bc, "buscar_nombre_operador_rpc_online_exacto", return_value=respuesta):
            resultado = bc.resolver_operador_seguro(
                "999999",
                "FIBER NETWORK DEL SUR, S. DE R.L. DE C.V.",
                [],
            )
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["metodo"], "nombre_exacto_rpc_online")
        self.assertEqual(resultado["idBp"], "519448")

    def test_no_encontrado_online_con_id_inconsistente_queda_para_revision(self):
        catalogo = bc.preparar_catalogo_para_matching([
            {"idBp": "100", "concesionario": "OPERADOR DEMO S.A. DE C.V."},
        ])
        respuesta = {
            "ok": False,
            "estado": "sin_coincidencia",
            "motivo": "nombre_no_encontrado_exacto_en_rpc_online",
            "candidatos": [],
        }
        with patch.object(bc, "buscar_nombre_operador_rpc_online_exacto", return_value=respuesta):
            resultado = bc.resolver_operador_seguro(
                "999",
                "OPERADOR DEMO, S.A. DE C.V.",
                catalogo,
            )
        self.assertFalse(resultado["ok"])
        self.assertEqual(resultado["motivo"], "nombre_no_encontrado_exacto_en_rpc_online")

    def test_pendiente_conserva_score_y_margen_solo_para_diagnostico(self):
        respuesta = {
            "ok": False,
            "estado": "sin_coincidencia_segura",
            "motivo": "candidatos_rpc_sin_confianza_suficiente",
            "score": 0.775,
            "margen": 1.25,
            "candidatos": [{"idBp": "526628", "nombre_completo": "SIERRA IG"}],
        }
        with patch.object(bc, "buscar_nombre_operador_rpc_online_exacto", return_value=respuesta):
            resultado = bc.resolver_operador_seguro(
                "",
                "SIERRA NORTE TELEVISIÓN POR CABLE, S.A. DE C.V.",
                [],
            )
        self.assertFalse(resultado["ok"])
        self.assertEqual(resultado["score"], 0.775)
        self.assertEqual(resultado["margen"], 1.25)

    def test_ruta_usa_operador_y_jerarquia_documental_unica(self):
        ruta = construir_ruta("ADOLFO MÉRINO MEDINA", "100028", "CRT25-001350")
        self.assertEqual(ruta, r"100028_adolfo_merino_medina\01 EN\VE")

    def test_varias_razones_sociales_conservan_todos_los_ids_en_orden(self):
        nombres = (
            "AT&T COMUNICACIONES DIGITALES, S. DE R.L. DE C.V., "
            "GRUPO AT&T CELULLAR, S. DE R.L. DE C.V., "
            "AT&T COMERCIALIZACION MOVIL, S. DE R.L. DE C.V., "
            "AT&T CONECTA DE MEXICO, S. DE R.L. DE C.V."
        )
        respuestas = {
            "at t comunicaciones digitales s de r l de c v": (
                "107326",
                "AT&T COMUNICACIONES DIGITALES, S. DE R.L. DE C.V.",
            ),
            "grupo at t celullar s de r l de c v": (
                "107347",
                "GRUPO AT&T CELULLAR, S. DE R.L. DE C.V.",
            ),
            "at t comercializacion movil s de r l de c v": (
                "107348",
                "AT&T COMERCIALIZACIÓN MÓVIL, S. DE R.L. DE C.V.",
            ),
            "at t conecta de mexico s de r l de c v": (
                "521333",
                "AT&T CONECTA DE MÉXICO, S. DE R.L. DE C.V.",
            ),
        }

        def respuesta_rpc(nombre, **_kwargs):
            id_bp, nombre_oficial = respuestas[bc.normalizar_nombre(nombre)]
            return {
                "ok": True,
                "estado": "coincidencia_unica",
                "idBp": id_bp,
                "nombre_completo": nombre_oficial,
                "consulta_rpc": nombre,
                "score": 1.0,
            }

        with patch.object(
            bc,
            "buscar_nombre_operador_rpc_online_exacto",
            side_effect=respuesta_rpc,
        ):
            resultado = bc.resolver_operador_seguro("", nombres, [])

        self.assertTrue(resultado["ok"])
        self.assertEqual(
            resultado["metodo"],
            "razones_sociales_multiples_todas_resueltas",
        )
        self.assertEqual(
            [item["idBp"] for item in resultado["operadores"]],
            ["107326", "107347", "107348", "521333"],
        )
        self.assertEqual(resultado["razones_sin_id"], [])

    def test_ruta_varias_razones_incluye_cada_par_id_nombre(self):
        operadores = [
            {"idBp": "107326", "nombre_completo": "AT&T COMUNICACIONES DIGITALES, S. DE R.L. DE C.V."},
            {"idBp": "107347", "nombre_completo": "GRUPO AT&T CELULLAR, S. DE R.L. DE C.V."},
            {"idBp": "107348", "nombre_completo": "AT&T COMERCIALIZACIÓN MÓVIL, S. DE R.L. DE C.V."},
            {"idBp": "521333", "nombre_completo": "AT&T CONECTA DE MÉXICO, S. DE R.L. DE C.V."},
        ]
        ruta = construir_ruta_operadores(operadores, "INTERNOS_EN_PROCESO_108444")
        self.assertEqual(
            ruta,
            "107326_at_t_comunicaciones_digitales_s_de_r_l_de_c_v__"
            "107347_grupo_at_t_celullar_s_de_r_l_de_c_v__"
            "107348_at_t_comercializacion_movil_s_de_r_l_de_c_v__"
            "521333_at_t_conecta_de_mexico_s_de_r_l_de_c_v\\"
            "01 EN\\VE",
        )

    def test_razon_sin_id_se_conserva_sin_heredar_otro_operador(self):
        def respuesta_rpc(nombre, **_kwargs):
            if nombre.startswith("OPERADOR UNO"):
                return {
                    "ok": True,
                    "idBp": "100001",
                    "nombre_completo": "OPERADOR UNO, S.A. DE C.V.",
                    "score": 1.0,
                }
            return {
                "ok": False,
                "estado": "sin_coincidencia",
                "motivo": "nombre_no_encontrado_exacto_en_rpc_online",
                "candidatos": [],
            }

        with patch.object(
            bc,
            "buscar_nombre_operador_rpc_online_exacto",
            side_effect=respuesta_rpc,
        ):
            resultado = bc.resolver_operador_seguro(
                "",
                "OPERADOR UNO, S.A. DE C.V.; OPERADOR DESCONOCIDO",
                [],
            )

        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["metodo"], "razones_sociales_multiples_parcial")
        self.assertEqual(resultado["operadores"][0]["idBp"], "100001")
        self.assertEqual(resultado["operadores"][1]["idBp"], "")
        self.assertEqual(resultado["razones_sin_id"], ["OPERADOR DESCONOCIDO"])
        self.assertIn(
            "__sin_id_operador_desconocido\\01 EN\\VE",
            construir_ruta_operadores(resultado["operadores"], "INTERNO_1"),
        )

    def test_excel_rpc_corrupto_activa_modo_degradado_sin_abortar(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "03_concesiones_permisos_autorizaciones_999999.xlsx").write_bytes(b"no es xlsx")
            indice, excel = cargar_indice_rpc(base)
        self.assertEqual(indice, {})
        self.assertIsNone(excel)

    def test_descubrimiento_internos_incluye_metadata_legacy_sin_marca(self):
        with tempfile.TemporaryDirectory() as td:
            descargas = Path(td) / "descargas"
            marcado = descargas / "internos" / "Atendidos" / "103336"
            legacy = descargas / "internos" / "CRT25-001350"
            marcado.mkdir(parents=True)
            legacy.mkdir(parents=True)
            (marcado / "metadata_satys.json").write_text(
                json.dumps({
                    "satys_flujo": "internos",
                    "bandeja_internos": "Atendidos",
                    "folio": "103336",
                }),
                encoding="utf-8",
            )
            (legacy / "metadata_satys.json").write_text(
                json.dumps({"registro": "CRT25-001350"}),
                encoding="utf-8",
            )

            with patch.object(main_procesar, "DESCARGA_BASE", descargas):
                encontrados = main_procesar.descubrir_descargas_internos()

        self.assertEqual(len(encontrados), 2)
        self.assertTrue(any(folio_id == "internos__Sin_bandeja__CRT25-001350" for _, folio_id, _ in encontrados))

    def test_reconciliacion_sin_excel_organiza_con_rpc_online_exacto(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            descargas = root / "descargas"
            output = root / "output"
            carpeta = descargas / "CRT25-001350"
            carpeta.mkdir(parents=True)
            (carpeta / "metadata_satys.json").write_text(
                json.dumps({
                    "registro": "CRT25-001350",
                    "nombre_operador": "CTI CALL, S.A. DE C.V.",
                    "id_solicitante": None,
                }),
                encoding="utf-8",
            )
            (carpeta / "documento.pdf").write_bytes(b"documento-real")
            respuesta = {
                "ok": True,
                "estado": "coincidencia_unica",
                "idBp": "521142",
                "nombre_completo": "CTI CALL S.A. DE C.V.",
                "consulta_rpc": "cti call",
            }
            with patch.object(bc, "buscar_nombre_operador_rpc_online_exacto", return_value=respuesta):
                resultados, stats = construir_resultados(descargas, output, {}, migrar_correos=True)

            self.assertEqual(stats["rpc_ok"], 1)
            self.assertTrue(resultados[0]["rpc_ok"])
            self.assertFalse(
                (
                    output
                    / "521142_cti_call_s_a_de_c_v"
                    / "01 EN"
                    / "VE"
                    / "metadata_satys.json"
                ).exists()
            )
            self.assertTrue(
                (
                    output
                    / "521142_cti_call_s_a_de_c_v"
                    / "01 EN"
                    / "VE"
                    / "documento.pdf"
                ).exists()
            )

    def test_organizacion_fusiona_sin_sufijos_y_descarga_vigente_gana(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            salida = root / "output"
            registro_a = root / "descargas" / "CRT25-000001"
            registro_b = root / "descargas" / "CRT25-000002"
            registro_a.mkdir(parents=True)
            registro_b.mkdir(parents=True)
            (registro_a / "metadata_satys.json").write_text('{"registro":"A"}', encoding="utf-8")
            (registro_b / "metadata_satys.json").write_text('{"registro":"B"}', encoding="utf-8")
            (registro_a / "documento_a.pdf").write_bytes(b"A")
            (registro_b / "documento_b.pdf").write_bytes(b"B")
            (registro_a / "archivo.txt").write_bytes(b"version-A")
            (registro_b / "archivo.txt").write_bytes(b"version-B")
            anexos = registro_a / "anexos"
            anexos.mkdir()
            (anexos / "anexo.pdf").write_bytes(b"anexo")

            operador = salida / "100_operador_demo"
            destino = operador / "01 EN" / "VE"
            destino.mkdir(parents=True)
            (destino / "archivo.txt").write_bytes(b"anterior!")
            (destino / "metadata_completo.json").write_text("{}", encoding="utf-8")

            legacy_registro = operador / "CRT25-000000"
            legacy_registro.mkdir()
            (legacy_registro / "archivo_legacy.pdf").write_bytes(b"legacy")
            (legacy_registro / "metadata_satys.json").write_text("{}", encoding="utf-8")

            duplicado_2 = salida / "100_operador_demo_2" / "01 EN" / "VE"
            duplicado_2.mkdir(parents=True)
            (duplicado_2 / "archivo_dos.pdf").write_bytes(b"dos")
            duplicado_3 = salida / "100_operador_demo_3" / "CRT25-000003"
            duplicado_3.mkdir(parents=True)
            (duplicado_3 / "archivo_tres.pdf").write_bytes(b"tres")

            with patch.object(Parte4_excel, "OUTPUT_BASE", salida):
                resumen = Parte4_excel.consolidar_todas_carpetas_operadores(salida)
                Parte4_excel.organizar_archivos(
                    registro_a,
                    r"100_operador_demo\01 EN\VE",
                )
                Parte4_excel.organizar_archivos(
                    registro_a,
                    r"100_operador_demo\01 EN\VE",
                )
                Parte4_excel.organizar_archivos(
                    registro_b,
                    r"100_operador_demo\01 EN\VE",
                )

            self.assertEqual(resumen["errores"], [])
            self.assertEqual(resumen["operadores"], 1)
            self.assertEqual(resumen["estructuras_retiradas"], 3)
            self.assertEqual((destino / "archivo.txt").read_bytes(), b"version-B")
            self.assertTrue((destino / "documento_a.pdf").exists())
            self.assertTrue((destino / "documento_b.pdf").exists())
            self.assertTrue((destino / "anexos" / "anexo.pdf").exists())
            self.assertTrue((destino / "archivo_legacy.pdf").exists())
            self.assertTrue((destino / "archivo_dos.pdf").exists())
            self.assertTrue((destino / "archivo_tres.pdf").exists())
            self.assertEqual([p.name for p in operador.iterdir()], ["01 EN"])
            self.assertFalse((salida / "100_operador_demo_2").exists())
            self.assertFalse((salida / "100_operador_demo_3").exists())
            self.assertEqual(list(destino.glob("archivo_*.txt")), [])
            self.assertEqual(list(salida.rglob("*.json")), [])
            self.assertEqual((registro_a / "archivo.txt").read_bytes(), b"version-A")
            self.assertEqual((registro_b / "archivo.txt").read_bytes(), b"version-B")
            self.assertTrue((registro_a / "metadata_satys.json").exists())
            self.assertTrue((registro_b / "metadata_satys.json").exists())

    def test_reporte_csv_incluye_motivo_de_sin_operador(self):
        with tempfile.TemporaryDirectory() as td:
            reporte = generar_reportes_operadores(
                [{
                    "folio": "148255",
                    "registro": "CRT25-001350",
                    "id_solicitante": "",
                    "nombre_operador": "CTI CALL, S.A. DE C.V.",
                    "rpc_ok": False,
                    "rpc_resultado": {
                        "ok": False,
                        "metodo": "resolucion_exacta_segura",
                        "motivo": "nombre_ambiguo_requiere_revision",
                    },
                    "sin_operador_dir": "output/_sin_operador/CRT25-001350",
                }],
                modo="prueba",
                logs_dir=td,
                fecha=datetime(2026, 8, 26, 12, 0, 0),
            )
            with Path(reporte["sin_operador_csv"]).open(encoding="utf-8-sig", newline="") as stream:
                filas = list(csv.DictReader(stream))
            self.assertEqual(len(filas), 1)
            self.assertEqual(filas[0]["estado"], "sin_operador")
            self.assertEqual(filas[0]["motivo"], "nombre_ambiguo_requiere_revision")


if __name__ == "__main__":
    unittest.main()
