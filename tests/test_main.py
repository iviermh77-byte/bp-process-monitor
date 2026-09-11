"""Pruebas de main.py: el entrypoint debe loguear con traceback y salir con
código de error ante cualquier excepción no controlada -- es la señal que
usará el mecanismo de scheduling del paso 8 para detectar una corrida fallida."""

from __future__ import annotations

import logging

import pytest

from bp_process_monitor import main as main_module


def test_main_loguea_y_sale_con_codigo_de_error_ante_excepcion_no_controlada(
    monkeypatch, caplog
):
    def _run_que_falla() -> None:
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(main_module, "run", _run_que_falla)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as exc_info:
            main_module.main()

    assert exc_info.value.code == 1
    assert "fallo simulado" in caplog.text
