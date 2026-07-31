#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

import extraer_registros_documentos as extractor


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def advance_ms(self, milliseconds):
        self.value += milliseconds / 1000.0


class FakePage:
    def __init__(self, clock):
        self.clock = clock

    def wait_for_timeout(self, milliseconds):
        self.clock.advance_ms(milliseconds)


class EsperaRobustaTablaSatysTest(unittest.TestCase):
    @staticmethod
    def estado(*, registros=None, info="Mostrando 0 a 0 de 0 tramites", year=2026,
               ready=True, active_page=1, mutation=0, has_next=False):
        registros = list(registros or [])
        return {
            "registros": registros,
            "info": info,
            "hasNext": has_next,
            "found": True,
            "pageKey": registros[0] if registros else "",
            "activePage": active_page,
            "selectedYear": year,
            "mutationCounter": mutation,
            "ready": ready,
            "error": "",
            "recordsDisplay": len(registros),
            "recordsTotal": len(registros),
            "dataTableReady": True,
            "draw": 1,
            "realRowCount": len(registros),
            "invalidRegistroCount": 0,
            "zeroUi": not registros,
            "emptyConfirmed": False,
        }

    def test_no_acepta_cero_y_continua_hasta_aparecer_un_registro(self):
        clock = FakeClock()
        page = FakePage(clock)
        estados = [
            self.estado(),
            self.estado(),
            self.estado(
                registros=["CRT26-000001"],
                info="Mostrando 1 a 1 de 1 tramites",
                mutation=1,
            ),
        ]

        def leer(_page):
            return estados.pop(0) if estados else self.estado(
                registros=["CRT26-000001"],
                info="Mostrando 1 a 1 de 1 tramites",
                mutation=1,
            )

        with patch.object(extractor.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(extractor, "leer_estado_tabla", side_effect=leer), \
             patch.object(extractor, "screenshot"):
            resultado = extractor.esperar_tabla_registros_lista(
                page,
                timeout_ms=120_000,
                anio_esperado=2026,
                contexto="prueba",
            )

        self.assertEqual(resultado["registros"], ["CRT26-000001"])
        self.assertEqual(clock.value, 2.0)

    def test_falla_al_agotar_timeout_sin_aceptar_cero(self):
        clock = FakeClock()
        page = FakePage(clock)
        estado_cero = self.estado()

        with patch.object(extractor.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(extractor, "leer_estado_tabla", return_value=estado_cero), \
             patch.object(extractor, "screenshot"):
            with self.assertRaisesRegex(RuntimeError, "dentro de 3 segundos"):
                extractor.esperar_tabla_registros_lista(
                    page,
                    timeout_ms=3_000,
                    anio_esperado=2026,
                    contexto="prueba timeout",
                )

        self.assertEqual(clock.value, 3.0)

    def test_paginacion_rechaza_pagina_duplicada_hasta_que_avanza(self):
        clock = FakeClock()
        page = FakePage(clock)
        anterior = self.estado(
            registros=["CRT26-000001"],
            info="Mostrando 1 a 100 de 200 tramites",
            active_page=1,
            has_next=True,
        )
        repetida = dict(anterior)
        avanzada = self.estado(
            registros=["CRT26-000101"],
            info="Mostrando 101 a 200 de 200 tramites",
            active_page=2,
            has_next=False,
        )
        estados = [repetida, avanzada]

        with patch.object(extractor.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(extractor, "leer_estado_tabla", side_effect=lambda _p: estados.pop(0)), \
             patch.object(extractor, "screenshot"):
            resultado = extractor.esperar_tabla_registros_lista(
                page,
                timeout_ms=120_000,
                anio_esperado=2026,
                firma_anterior=extractor.firma_estado_tabla(anterior),
                desde_minimo=101,
                contexto="página siguiente",
            )

        self.assertEqual(resultado["activePage"], 2)
        self.assertEqual(resultado["registros"], ["CRT26-000101"])
        self.assertEqual(clock.value, 1.0)

    def test_acepta_cero_solo_despues_de_estabilidad_confirmada(self):
        clock = FakeClock()
        page = FakePage(clock)
        estado_cero = self.estado()

        with patch.object(extractor.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(extractor, "leer_estado_tabla", return_value=estado_cero), \
             patch.object(extractor, "screenshot"):
            resultado = extractor.esperar_tabla_registros_lista(
                page,
                timeout_ms=10_000,
                anio_esperado=2026,
                permitir_vacio_confirmado=True,
                vacio_estable_segundos=3,
                contexto="año vacío",
            )

        self.assertTrue(resultado["emptyConfirmed"])
        self.assertGreaterEqual(clock.value, 3.0)

    def test_concilia_filas_y_duplicados_sin_ocultar_faltantes(self):
        page = object()
        estado = self.estado(
            registros=["CRT26-000001", "CRT26-000001"],
            info="Mostrando 1 a 2 de 2 tramites",
        )
        estado["realRowCount"] = 2
        estado["recordsDisplay"] = 2
        estado["recordsTotal"] = 2
        with patch.object(extractor, "esperar_tabla_registros_lista", return_value=estado):
            detalle = extractor.extraer_registros_detallado(page, anio_label="2026")
        self.assertEqual(detalle["estado"], "ENCONTRADOS_COMPLETOS")
        self.assertEqual(detalle["filas_leidas"], 2)
        self.assertEqual(detalle["registros_unicos"], 1)
        self.assertEqual(detalle["duplicados_internos"], 1)

    def test_falla_si_satys_reporta_mas_filas_de_las_leidas(self):
        page = object()
        estado = self.estado(
            registros=["CRT26-000001", "CRT26-000002"],
            info="Mostrando 1 a 2 de 3 tramites",
        )
        estado["realRowCount"] = 2
        estado["recordsDisplay"] = 3
        estado["recordsTotal"] = 3
        with patch.object(extractor, "esperar_tabla_registros_lista", return_value=estado):
            with self.assertRaisesRegex(RuntimeError, "paginacion no llego al final|EXTRACCIÓN INCOMPLETA"):
                extractor.extraer_registros_detallado(page, anio_label="2026")

    def test_cambio_de_anio_se_confirma_antes_de_cambiar_mostrar(self):
        page = object()
        detalle_ok = {
            "estado": "ENCONTRADOS_COMPLETOS",
            "registros": ["CRT25-000001"],
            "total_esperado": 1,
            "filas_leidas": 1,
            "registros_unicos": 1,
            "duplicados_internos": 0,
            "filas_invalidas": 0,
            "paginas_leidas": 1,
            "primera_info": "Mostrando 1 a 1 de 1 tramites",
            "ultima_info": "Mostrando 1 a 1 de 1 tramites",
        }
        orden = []

        def confirmar(*args, **kwargs):
            orden.append("confirmar_anio")
            return {"registros": ["CRT25-000001"], "emptyConfirmed": False}

        def mostrar(*args, **kwargs):
            orden.append("mostrar_100")
            return True

        def extraer(*args, **kwargs):
            orden.append("extraer")
            return detalle_ok

        with patch.object(extractor, "descubrir_anios_disponibles", return_value=[{"year": 2025, "value": "2025"}]), \
             patch.object(extractor, "seleccionar_anio", return_value={
                 "changed": True, "mutationCounterAntes": 4, "drawAntes": 7
             }), \
             patch.object(extractor, "esperar_tabla_registros_lista", side_effect=confirmar) as esperar, \
             patch.object(extractor, "cambiar_mostrar_a_100", side_effect=mostrar), \
             patch.object(extractor, "extraer_registros_detallado", side_effect=extraer), \
             patch.object(extractor, "screenshot"):
            detalle, _ = extractor.extraer_un_anio_con_reintentos(
                page,
                2025,
                max_paginas=100,
                timeout_primera_pagina_ms=120_000,
                intentos_anio=1,
                intentos_pagina=3,
            )

        self.assertEqual(orden, ["confirmar_anio", "mostrar_100", "extraer"])
        self.assertEqual(detalle["registros"], ["CRT25-000001"])
        self.assertEqual(esperar.call_args.kwargs["mutation_minima"], 5)
        self.assertEqual(esperar.call_args.kwargs["draw_minimo"], 8)

    def test_reintenta_solo_el_anio_fallido_y_conserva_el_flujo(self):
        page = object()
        detalle_ok = {
            "registros": ["CRT26-000001"],
            "total_esperado": 1,
            "paginas_leidas": 1,
            "primera_info": "Mostrando 1 a 1 de 1 tramites",
            "ultima_info": "Mostrando 1 a 1 de 1 tramites",
        }

        with patch.object(extractor, "descubrir_anios_disponibles", return_value=[{"year": 2026, "value": "2026"}]), \
             patch.object(extractor, "seleccionar_anio", return_value={"changed": False}), \
             patch.object(extractor, "obtener_anio_seleccionado", return_value=None) as obtener, \
             patch.object(extractor, "cambiar_mostrar_a_100", return_value=True), \
             patch.object(extractor, "extraer_registros_detallado", side_effect=[RuntimeError("cero temporal"), detalle_ok]) as extraer, \
             patch.object(extractor, "reabrir_tablero_para_reintento") as reabrir, \
             patch.object(extractor, "screenshot"):
            detalle, historial = extractor.extraer_un_anio_con_reintentos(
                page,
                2026,
                max_paginas=100,
                timeout_primera_pagina_ms=120_000,
                intentos_anio=3,
                intentos_pagina=3,
            )

        self.assertEqual(detalle["registros"], ["CRT26-000001"])
        self.assertEqual(detalle["intentos_anio"], 2)
        self.assertEqual(len(historial), 2)
        self.assertFalse(historial[0]["ok"])
        self.assertTrue(historial[1]["ok"])
        self.assertEqual(extraer.call_count, 2)
        reabrir.assert_called_once_with(page)
        obtener.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
