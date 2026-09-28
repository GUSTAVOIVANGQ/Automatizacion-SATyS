from __future__ import annotations

import unittest
from unittest.mock import Mock

import buscar_concesionario as bc


class RpcPortalCrtTests(unittest.TestCase):
    def test_url_default_apunta_al_portal_crt_actual(self):
        self.assertEqual(bc.RPC_BASE_URL, "https://rpc.crt.gob.mx/vrpc")
        self.assertIn("rpc.crt.gob.mx/vrpc", bc.RPC_AUTOCOMPLETE_URL)
        self.assertIn("rpc.crt.gob.mx/vrpc", bc.RPC_RESULTADOS_URL)

    def test_matching_base_legal_resuelve_sin_hardcodear_nombre(self):
        r = bc.seleccionar_candidato_rpc_seguro(
            "FEMASEISA",
            [{"idBp": "519999", "nombre_completo": "FEMASEISA S.A. DE C.V."}],
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["idBp"], "519999")
        self.assertEqual(r["nombre_completo"], "FEMASEISA S.A. DE C.V.")
        self.assertEqual(r["metodo"], "nombre_base_legal_rpc")

    def test_matching_base_legal_nombre_largo(self):
        r = bc.seleccionar_candidato_rpc_seguro(
            "ADMINISTRADORA DE SERVICIOS DE INTERNET SANDUR",
            [{"idBp": "519674", "nombre_completo": "ADMINISTRADORA DE SERVICIOS DE INTERNET SANDUR S.A. DE C.V."}],
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["idBp"], "519674")

    def test_parser_resultados_no_depende_de_clase_css_historica(self):
        html = """<html><body><section><h4>FET123-519779 - FEMASEISA S.A. DE C.V.</h4></section></body></html>"""
        items = bc._extraer_resultados_concesiones_html(html)
        self.assertEqual(items[0]["idBp"], "519779")
        self.assertEqual(items[0]["nombre_completo"], "FEMASEISA S.A. DE C.V.")

    def test_parser_autocomplete_acepta_variantes_de_campos(self):
        items = bc._extraer_respuesta_rpc([
            {"idBP": 519623, "denominacionSocial": "AXIOS COMUNICACIONES S. DE R.L. DE C.V."}
        ])
        self.assertEqual(items, [{"idBp": "519623", "nombre_completo": "AXIOS COMUNICACIONES S. DE R.L. DE C.V."}])

    def test_resultados_envia_ambos_nombres_de_campo_del_formulario(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.url = bc.RPC_RESULTADOS_URL
        response.text = '<h3>FET001-519779 - FEMASEISA S.A. DE C.V.</h3>'
        session = Mock()
        session.headers = {}
        session.post.return_value = response
        r = bc.buscar_nombre_operador_rpc_resultados("FEMASEISA", timeout=1, session=session)
        self.assertTrue(r["ok"])
        data = session.post.call_args.kwargs["data"]
        self.assertEqual(data["strConcesionario"], "FEMASEISA")
        self.assertEqual(data["txtBPConcesionario"], "FEMASEISA")


if __name__ == "__main__":
    unittest.main()
