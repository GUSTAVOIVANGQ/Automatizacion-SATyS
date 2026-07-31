from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reparar_id_solicitante import (
    crear_estado_nuevo,
    escanear_faltantes,
    estado_id_registro,
    resumen_estado,
)


class ReparacionIdTests(unittest.TestCase):
    def test_scan_detecta_null_vacio_y_clave_ausente(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            casos = {
                "CRT26-000001": {"registro": "CRT26-000001", "id_solicitante": None},
                "CRT26-000002": {"registro": "CRT26-000002", "id_solicitante": "  "},
                "CRT26-000003": {"registro": "CRT26-000003"},
                "CRT26-000004": {"registro": "CRT26-000004", "id_solicitante": "ID-4"},
            }
            for registro, data in casos.items():
                folder = root / registro
                folder.mkdir()
                (folder / "metadata_satys.json").write_text(json.dumps(data), encoding="utf-8")

            queue, paths, errors = escanear_faltantes(root)
            self.assertEqual(queue, ["CRT26-000001", "CRT26-000002", "CRT26-000003"])
            self.assertEqual(set(paths), set(queue))
            self.assertEqual(errors, [])

    def test_registro_con_varios_json_exige_todos_resueltos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, value in enumerate(("ID-OK", None)):
                folder = root / f"carpeta_{index}" / "CRT26-000010"
                folder.mkdir(parents=True)
                (folder / "metadata_satys.json").write_text(
                    json.dumps({"registro": "CRT26-000010", "id_solicitante": value}),
                    encoding="utf-8",
                )
            result = estado_id_registro(root, "CRT26-000010")
            self.assertFalse(result["resolved"])
            self.assertEqual(len(result["metadata"]), 2)

    def test_checkpoint_calcula_pendientes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            state = crear_estado_nuevo(
                state_path,
                root,
                ["CRT26-000001", "CRT26-000002"],
                {},
                [],
                3,
                True,
            )
            state["completed"] = ["CRT26-000001"]
            state["resolved"] = ["CRT26-000001"]
            self.assertEqual(
                resumen_estado(state),
                {"detected": 2, "processed": 1, "resolved": 1, "unresolved": 0, "pending": 1},
            )


if __name__ == "__main__":
    unittest.main()
