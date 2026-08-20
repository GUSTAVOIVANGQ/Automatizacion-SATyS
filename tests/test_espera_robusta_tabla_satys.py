#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import Mock, patch

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
            "yearSelectDisabled": False,
            "tableLoadingVisible": False,
            "loadError": "",
            "activeTabCount": len(registros),
            "selectedPageLength": 100,
            "mutationCounter": mutation,
            "ready": ready,
            "error": "",
            "recordsDisplay": len(registros),
            "recordsTotal": len(registros),
            "pageLength": 100,
            "dataTableReady": True,
            "draw": 1,
            "realRowCount": len(registros),
            "invalidRegistroCount": 0,
            "zeroUi": not registros,
            "emptyConfirmed": False,
        }

    @staticmethod
    def estado_folios(*, folios=None, info="Mostrando 0 a 0 de 0 tramites", has_next=False):
        folios = list(folios or [])
        return {
            "folios": folios,
            "info": info,
            "hasNext": has_next,
            "found": True,
            "ready": True,
            "activePage": 1,
            "recordsDisplay": len(folios),
            "recordsTotal": len(folios),
            "pageStart": 0,
            "pageEnd": len(folios),
            "pages": 1,
            "draw": 1,
            "dataTableReady": True,
            "realRowCount": len(folios),
            "invalidFolioCount": 0,
            "zeroUi": not folios,
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

    def test_no_acepta_filas_hasta_que_termine_la_solicitud_del_anio(self):
        clock = FakeClock()
        page = FakePage(clock)
        cargando = self.estado(
            registros=["CRT26-000001"],
            info="Mostrando 1 a 1 de 1 tramites",
        )
        cargando["yearSelectDisabled"] = True
        cargando["tableLoadingVisible"] = True
        cargando["ready"] = True  # La espera debe validar el ciclo, no confiar solo en ready.
        listo = dict(cargando)
        listo["yearSelectDisabled"] = False
        listo["tableLoadingVisible"] = False
        estados = [cargando, listo]

        with patch.object(extractor.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(extractor, "leer_estado_tabla", side_effect=lambda _p: estados.pop(0)), \
             patch.object(extractor, "screenshot"):
            resultado = extractor.esperar_tabla_registros_lista(
                page,
                timeout_ms=10_000,
                anio_esperado=2026,
                contexto="refresco 2026",
            )

        self.assertFalse(resultado["yearSelectDisabled"])
        self.assertEqual(clock.value, 1.0)

    def test_no_acepta_total_hasta_que_coincida_con_contador_de_pestana(self):
        clock = FakeClock()
        page = FakePage(clock)
        inconsistente = self.estado(
            registros=["CRT26-000001"],
            info="Mostrando 1 a 1 de 1 tramites",
        )
        inconsistente["activeTabCount"] = 2
        consistente = dict(inconsistente)
        consistente["activeTabCount"] = 1
        estados = [inconsistente, consistente]

        with patch.object(extractor.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(extractor, "leer_estado_tabla", side_effect=lambda _p: estados.pop(0)), \
             patch.object(extractor, "screenshot"):
            resultado = extractor.esperar_tabla_registros_lista(
                page,
                timeout_ms=10_000,
                anio_esperado=2026,
                contexto="contador 2026",
            )

        self.assertEqual(resultado["activeTabCount"], 1)
        self.assertEqual(clock.value, 1.0)

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

    def test_refresco_de_anio_se_confirma_antes_de_cambiar_mostrar_aunque_ya_este_activo(self):
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
                 "changed": False, "refreshRequested": True,
                 "mutationCounterAntes": 4, "drawAntes": 7
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

    def test_recorrido_aisla_el_cache_de_la_pagina_antes_de_cada_anio(self):
        detalle_2026 = {
            "estado": "ENCONTRADOS_COMPLETOS",
            "registros": ["CRT26-000001"],
            "total_esperado": 1,
            "filas_leidas": 1,
            "registros_unicos": 1,
            "duplicados_internos": 0,
            "filas_invalidas": 0,
            "paginas_leidas": 1,
            "primera_info": "Mostrando 1 a 1 de 1 tramites",
            "ultima_info": "Mostrando 1 a 1 de 1 tramites",
            "contador_tab": 1,
            "tamanio_pagina": 100,
            "intentos_anio": 1,
        }
        detalle_2025 = dict(detalle_2026)
        detalle_2025["registros"] = ["CRT25-000001"]

        with patch.object(extractor, "descubrir_anios_disponibles", return_value=[
            {"year": 2026, "value": "2026"},
            {"year": 2025, "value": "2025"},
        ]), patch.object(extractor, "reabrir_tablero_limpio") as reabrir, \
             patch.object(
                 extractor,
                 "extraer_un_anio_con_reintentos",
                 side_effect=[(detalle_2026, []), (detalle_2025, [])],
             ):
            resumen = extractor.extraer_registros_por_anio(object(), intentos_anio=1)

        self.assertEqual(resumen["registros"], ["CRT26-000001", "CRT25-000001"])
        self.assertEqual(
            [call.kwargs["contexto"] for call in reabrir.call_args_list],
            ["el Año 2026", "el Año 2025"],
        )

    def test_limpia_cache_persistente_despues_de_la_carga_inicial(self):
        page = Mock()
        page.evaluate.side_effect = [
            """{
                "disponible": true,
                "year": "2026",
                "cargaActiva": false,
                "selectorDeshabilitado": false,
                "cacheInicialCompleto": true
            }""",
            """{"ok": true, "aniosDescartados": 1}""",
        ]

        resultado = extractor.limpiar_cache_anios_satys(page, contexto="el Año 2025")

        self.assertEqual(resultado["aniosDescartados"], 1)
        self.assertEqual(page.evaluate.call_count, 2)

    def test_reabrir_tablero_tambien_limpia_el_cache_persistente(self):
        page = object()
        with patch.object(extractor, "sesion_activa", return_value=True), \
             patch.object(extractor, "navegar_a_enlace_oficialia", return_value=True), \
             patch.object(extractor, "limpiar_cache_anios_satys") as limpiar:
            extractor.reabrir_tablero_limpio(page, contexto="el Año 2025")

        limpiar.assert_called_once_with(page, contexto="el Año 2025")

    def test_internos_concilia_folios_y_duplicados_de_una_bandeja(self):
        page = object()
        estado = self.estado_folios(
            folios=["148326", "148326"],
            info="Mostrando 1 a 2 de 2 tramites",
        )
        with patch.object(extractor, "seleccionar_bandeja_internos"), \
             patch.object(extractor, "cambiar_mostrar_a_100", return_value=True), \
             patch.object(extractor, "esperar_tabla_folios_lista", return_value=estado):
            detalle = extractor.extraer_folios_bandeja_internos(
                page,
                "En proceso",
                max_paginas=100,
                timeout_ms=120_000,
            )

        self.assertEqual(detalle["estado"], "ENCONTRADOS_COMPLETOS")
        self.assertEqual(detalle["filas_leidas"], 2)
        self.assertEqual(detalle["folios"], ["148326"])
        self.assertEqual(detalle["duplicados_internos"], 1)

    def test_internos_acepta_cero_estable_sin_jquery_global(self):
        clock = FakeClock()
        page = FakePage(clock)
        estado_cero = self.estado_folios()
        estado_cero["dataTableReady"] = False
        estado_cero["recordsDisplay"] = None
        estado_cero["recordsTotal"] = None

        with patch.object(extractor.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(extractor, "leer_estado_tabla_folios", return_value=estado_cero), \
             patch.object(extractor, "VACIO_ESTABLE_SEGUNDOS_DEFAULT", 3), \
             patch.object(extractor, "screenshot"):
            resultado = extractor.esperar_tabla_folios_lista(
                page,
                timeout_ms=10_000,
                permitir_vacio_confirmado=True,
                contexto="Internos/Recibidos sin jQuery global",
            )

        self.assertTrue(resultado["emptyConfirmed"])
        self.assertGreaterEqual(clock.value, 3.0)

    def test_internos_deduplica_folio_repetido_entre_bandejas(self):
        detalles = []
        for indice, bandeja in enumerate(extractor.BANDEJAS_INTERNOS):
            folios = ["190823"] if indice < 2 else []
            detalles.append({
                "bandeja": bandeja,
                "estado": "ENCONTRADOS_COMPLETOS" if folios else "VACIO_CONFIRMADO",
                "folios": folios,
                "total_reportado_satys": len(folios),
                "filas_leidas": len(folios),
                "folios_unicos": len(folios),
                "duplicados_internos": 0,
                "filas_invalidas": 0,
                "paginas_leidas": 1 if folios else 0,
                "primera_info": "",
                "ultima_info": "",
            })

        with patch.object(extractor, "navegar_a_internos_ift", return_value=True), \
             patch.object(extractor, "extraer_folios_bandeja_internos", side_effect=detalles):
            resumen = extractor.extraer_folios_internos(object())

        self.assertEqual(resumen["folios"], ["190823"])
        self.assertEqual(resumen["total_folios"], 1)
        self.assertEqual(resumen["total_filas_satys"], 2)
        self.assertEqual(resumen["duplicados_entre_bandejas"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
