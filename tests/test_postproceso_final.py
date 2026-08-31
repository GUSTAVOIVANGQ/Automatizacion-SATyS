from __future__ import annotations

import inspect
import unittest

import postprocesar_final


class PostprocesoFinalTests(unittest.TestCase):
    def test_orden_postproceso_es_el_requerido(self):
        fuente = inspect.getsource(postprocesar_final.main)
        pos_rem = fuente.index("completar_remitentes_desde_pdfs.py")
        pos_rec = fuente.index("reconciliar_metadata_global.py")
        pos_rpc = fuente.index("resolver_sin_operador_rpc_publico.py")
        pos_sync = fuente.index("sincronizar_salidas(")
        pos_mail = fuente.index("enviar_resumen_email_diario(")
        self.assertLess(pos_rem, pos_rec)
        self.assertLess(pos_rec, pos_rpc)
        self.assertLess(pos_rpc, pos_sync)
        self.assertLess(pos_sync, pos_mail)

    def test_sync_final_es_solo_output_y_excel(self):
        fuente = inspect.getsource(postprocesar_final.main)
        self.assertIn('directorios=("output",)', fuente)
        self.assertIn('archivos=("TrámitesCRT.xlsx",)', fuente)

    def test_tiene_lock_global(self):
        fuente = inspect.getsource(postprocesar_final.main)
        self.assertIn('ProcesoLock(proceso="postprocesar_final.py")', fuente)


if __name__ == "__main__":
    unittest.main()
