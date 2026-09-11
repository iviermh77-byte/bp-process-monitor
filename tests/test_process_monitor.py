"""Pruebas de services/process_monitor.py: transformación de datos crudos a modelos.

Las funciones privadas de process_monitor.py son puras (ver docstring del
módulo), así que se prueban directamente con datos de ejemplo, sin mockear
SQLAlchemy ni tocar la BD real. ``build_report`` sí orquesta BD + lógica, por
lo que se prueba con las funciones de ``db/queries.py`` monkeypateadas.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from bp_process_monitor.models import RunStatus
from bp_process_monitor.services import process_monitor

LOGGER_NAME = process_monitor.logger.name


# --- _map_status -------------------------------------------------------


@pytest.mark.parametrize(
    "raw_status_id, expected",
    [
        (1, RunStatus.RUNNING),
        (2, RunStatus.TERMINATED),
        (3, RunStatus.STOPPED),
        (4, RunStatus.COMPLETED),
        (5, RunStatus.WARNING),
    ],
)
def test_map_status_valores_conocidos(raw_status_id, expected):
    assert process_monitor._map_status(raw_status_id) == expected


def test_map_status_valor_desconocido_devuelve_unknown_y_loguea_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        status = process_monitor._map_status(999)

    assert status == RunStatus.UNKNOWN
    assert "999" in caplog.text


# --- _to_process_record --------------------------------------------------


def test_to_process_record_mapea_todos_los_campos(session_row_factory):
    row = session_row_factory(statusid=5)  # Warning

    record = process_monitor._to_process_record(row)

    assert record.process_id == row["processid"]
    assert record.process_name == row["process_name"]
    assert record.resource_id == row["resourceid"]
    assert record.resource_name == row["resource_name"]
    assert record.status == RunStatus.WARNING
    assert record.raw_status_id == 5
    assert record.is_alert is True


def test_to_process_record_running_no_es_alerta(session_row_factory):
    row = session_row_factory(statusid=1)  # Running
    record = process_monitor._to_process_record(row)
    assert record.is_alert is False


# --- _resources_without_recent_activity -----------------------------------


def test_resources_without_recent_activity(resource_row_factory, session_row_factory):
    activo = resource_row_factory(resource_id=1, name="RUNTIME-1")
    inactivo = resource_row_factory(resource_id=2, name="RUNTIME-2")
    sesiones = [session_row_factory(resource_id=1)]

    sin_actividad = process_monitor._resources_without_recent_activity(
        [activo, inactivo], sesiones
    )

    assert sin_actividad == [inactivo]


def test_resources_without_recent_activity_todos_activos(
    resource_row_factory, session_row_factory
):
    activo = resource_row_factory(resource_id=1, name="RUNTIME-1")
    sesiones = [session_row_factory(resource_id=1)]

    assert process_monitor._resources_without_recent_activity([activo], sesiones) == []


# --- _to_resource_activity_records -----------------------------------------


def test_to_resource_activity_records_combina_actividad_y_ultima_corrida(
    resource_row_factory, session_row_factory
):
    activo = resource_row_factory(resource_id=1, name="RUNTIME-1")
    inactivo = resource_row_factory(resource_id=2, name="RUNTIME-2")
    sesiones = [session_row_factory(resource_id=1)]
    ultima_actividad = datetime.now(UTC) - timedelta(days=3)
    last_activity_rows = [{"resourceid": 2, "last_start_time": ultima_actividad}]

    records = process_monitor._to_resource_activity_records(
        [activo, inactivo], sesiones, last_activity_rows
    )
    by_id = {r.resource_id: r for r in records}

    assert by_id[1].has_recent_activity is True
    assert by_id[1].is_alert is False
    assert by_id[2].has_recent_activity is False
    assert by_id[2].is_alert is True
    assert by_id[2].last_activity_at == ultima_actividad


# --- build_report (orquestación) --------------------------------------------


@contextmanager
def _fake_connection(engine=None):
    yield None


def test_build_report_arma_snapshot_completo(
    monkeypatch, resource_row_factory, session_row_factory, caplog
):
    resources = [
        resource_row_factory(resource_id=1, name="RUNTIME-1"),
        resource_row_factory(resource_id=2, name="RUNTIME-2"),
    ]
    sessions = [session_row_factory(resource_id=1, statusid=5)]  # Warning
    last_activity_rows = [{"resourceid": 2, "last_start_time": None}]

    monkeypatch.setattr(process_monitor, "get_connection", _fake_connection)
    monkeypatch.setattr(process_monitor, "get_latest_sessions", lambda connection: sessions)
    monkeypatch.setattr(process_monitor, "get_resources", lambda connection: resources)
    monkeypatch.setattr(
        process_monitor,
        "get_last_session_time_for_resources",
        lambda connection, ids: last_activity_rows,
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        report = process_monitor.build_report()

    assert len(report.processes) == 1
    assert len(report.resource_activity) == 2
    assert len(report.process_alerts) == 1  # RUNTIME-1 en Warning
    assert len(report.resource_alerts) == 1  # RUNTIME-2 sin actividad reciente
    assert "RUNTIME-1" in caplog.text
    assert "RUNTIME-2" in caplog.text


def test_build_report_sin_alertas_no_loguea_warnings(
    monkeypatch, resource_row_factory, session_row_factory, caplog
):
    resources = [resource_row_factory(resource_id=1, name="RUNTIME-1")]
    sessions = [session_row_factory(resource_id=1, statusid=1)]  # Running

    monkeypatch.setattr(process_monitor, "get_connection", _fake_connection)
    monkeypatch.setattr(process_monitor, "get_latest_sessions", lambda connection: sessions)
    monkeypatch.setattr(process_monitor, "get_resources", lambda connection: resources)
    monkeypatch.setattr(
        process_monitor, "get_last_session_time_for_resources", lambda connection, ids: []
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        report = process_monitor.build_report()

    assert report.process_alerts == ()
    assert report.resource_alerts == ()
    assert caplog.text == ""
