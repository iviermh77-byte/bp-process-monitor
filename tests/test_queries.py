"""Pruebas de db/queries.py.

Las consultas usan sintaxis específica de T-SQL (DATEADD, GETUTCDATE,
ROW_NUMBER, bindparam con ``expanding=True``), así que no tiene sentido
correrlas contra un motor genérico en memoria (p. ej. SQLite): el dialecto
no coincide y el resultado no probaría nada real. Aquí se cubre lo que SÍ
es independiente del motor -- la guarda de "solo SELECT" y el atajo sin
BD cuando la lista de resources está vacía -- y se deja marcada como
integración (excluida por defecto) la validación contra una BD real.
"""

from __future__ import annotations

import pytest

from bp_process_monitor.db import queries


def test_select_rechaza_sentencias_que_no_son_select():
    with pytest.raises(ValueError, match="solo puede ejecutar sentencias SELECT"):
        queries._select(connection=None, sql="DELETE FROM BPASession")


def test_select_acepta_cte_con_with(monkeypatch):
    llamada = {}

    def fake_execute(connection, sql, params):
        llamada["sql"] = sql
        llamada["params"] = params
        return []

    monkeypatch.setattr(queries, "_execute", fake_execute)

    resultado = queries._select(connection=None, sql="WITH x AS (SELECT 1 AS n) SELECT n FROM x")

    assert resultado == []
    assert llamada["sql"].strip().upper().startswith("WITH")


def test_get_last_session_time_for_resources_sin_ids_no_consulta_bd(monkeypatch):
    def falla_si_se_llama(*args, **kwargs):
        raise AssertionError("no debería ejecutar ninguna consulta si resource_ids está vacío")

    monkeypatch.setattr(queries, "_execute_stmt", falla_si_se_llama)

    resultado = queries.get_last_session_time_for_resources(connection=None, resource_ids=[])

    assert resultado == []


@pytest.mark.integration
def test_get_latest_sessions_contra_bd_real():
    """Validación real del SQL contra una BD de Blue Prism de desarrollo.

    Excluida por defecto (ver ``addopts`` en pyproject.toml). Para correrla:
    comentar el ``pytest.skip`` de abajo, apuntar ``BP_DB_*`` en el entorno
    a la BD de dev, y ejecutar ``pytest -m integration``.
    """
    pytest.skip(
        "Requiere una conexión real a la BD de desarrollo de Blue Prism; "
        "activar manualmente (ver docstring) para validar el SQL contra datos reales."
    )
