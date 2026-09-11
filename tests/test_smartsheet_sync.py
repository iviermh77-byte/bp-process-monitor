"""Pruebas de services/smartsheet_sync.py: upsert idempotente hacia Smartsheet.

El cliente real de Smartsheet (``smartsheet.Smartsheet``) se reemplaza por
un ``MagicMock``: ninguna de estas pruebas debe hacer una llamada de red.
El estado EXISTENTE del sheet (columnas y filas ya en Smartsheet) se
simula con objetos ligeros (``SimpleNamespace``) que solo exponen los
atributos que el código de producción realmente lee (``.title``/``.id``
en columnas, ``.column_id``/``.value`` en celdas, ``.cells``/``.id`` en
filas) -- no hace falta reproducir las clases reales del SDK para los
datos de ENTRADA. Lo que el código de producción CONSTRUYE para enviar
(``smartsheet.models.Cell``/``Row`` dentro de ``_new_row`` y en el update)
sí usa las clases reales del SDK, igual que en producción.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bp_process_monitor.models import ProcessStatusRecord, RunStatus, StatusReport
from bp_process_monitor.services import smartsheet_sync as ss

COLUMN_IDS = {
    ss.COL_RUNTIME: 1,
    ss.COL_PROCESO: 2,
    ss.COL_ESTADO: 3,
    ss.COL_ULTIMA_ACTUALIZACION: 4,
    ss.COL_ULTIMA_VEZ_WARNING: 5,
}


def make_column(title: str) -> SimpleNamespace:
    return SimpleNamespace(title=title, id=COLUMN_IDS[title])


def make_cell(title: str, value: object) -> SimpleNamespace:
    return SimpleNamespace(column_id=COLUMN_IDS[title], value=value)


def make_sheet(rows: list, columns=tuple(COLUMN_IDS)) -> SimpleNamespace:
    return SimpleNamespace(columns=[make_column(t) for t in columns], rows=rows)


def make_process_record(
    *,
    process_name: str = "Proceso Demo",
    resource_name: str = "RUNTIME-1",
    status: RunStatus = RunStatus.RUNNING,
    start_time: datetime | None = None,
) -> ProcessStatusRecord:
    return ProcessStatusRecord(
        process_id=1,
        process_name=process_name,
        resource_id=1,
        resource_name=resource_name,
        status=status,
        raw_status_id=1,
        start_time=start_time or datetime.now(UTC),
        end_time=None,
        termination_reason=None,
    )


@pytest.fixture
def client() -> ss.SmartsheetClient:
    instance = ss.SmartsheetClient(api_key="test-key", sheet_id="12345")
    instance._client = MagicMock()
    return instance


# --- _resolve_column_ids ---------------------------------------------------


def test_resolve_column_ids_falla_si_falta_una_columna():
    sheet = make_sheet(rows=[], columns=[ss.COL_RUNTIME, ss.COL_ESTADO])  # faltan 3

    with pytest.raises(ss.SmartsheetSyncError, match="Faltan columnas"):
        ss.SmartsheetClient._resolve_column_ids(sheet)


def test_resolve_column_ids_ok_con_todas_las_columnas():
    sheet = make_sheet(rows=[])
    assert ss.SmartsheetClient._resolve_column_ids(sheet) == COLUMN_IDS


# --- _select_representative -------------------------------------------------


def test_select_representative_prioriza_alerta_sobre_mas_reciente():
    ahora = datetime.now(UTC)
    sano_mas_reciente = make_process_record(status=RunStatus.RUNNING, start_time=ahora)
    en_alerta_mas_viejo = make_process_record(
        status=RunStatus.WARNING, start_time=ahora - timedelta(hours=1)
    )

    representante = ss._select_representative([sano_mas_reciente, en_alerta_mas_viejo])

    assert representante is en_alerta_mas_viejo


def test_select_representative_sin_alertas_elige_el_mas_reciente():
    viejo = make_process_record(
        status=RunStatus.RUNNING, start_time=datetime.now(UTC) - timedelta(hours=2)
    )
    reciente = make_process_record(status=RunStatus.COMPLETED, start_time=datetime.now(UTC))

    representante = ss._select_representative([viejo, reciente])

    assert representante is reciente


# --- SmartsheetClient.sync: upsert ------------------------------------------


def test_sync_crea_fila_nueva_si_el_runtime_no_existe(client):
    client._client.Sheets.get_sheet.return_value = make_sheet(rows=[])

    resource_view = {
        "RUNTIME-1": ss._RuntimeView(proceso_nombre="Proceso Demo", estado=RunStatus.RUNNING.value)
    }
    client.sync(resource_view)

    client._client.Sheets.add_rows.assert_called_once()
    client._client.Sheets.update_rows.assert_not_called()

    nueva_fila = client._client.Sheets.add_rows.call_args[0][1][0]
    valores = {cell.column_id: cell.value for cell in nueva_fila.cells}
    assert valores[COLUMN_IDS[ss.COL_RUNTIME]] == "RUNTIME-1"
    assert valores[COLUMN_IDS[ss.COL_ESTADO]] == RunStatus.RUNNING.value
    assert valores[COLUMN_IDS[ss.COL_PROCESO]] == "Proceso Demo"


def test_sync_actualiza_fila_existente_cuando_cambia_el_estado(client):
    fila_existente = SimpleNamespace(
        id=99,
        cells=[
            make_cell(ss.COL_RUNTIME, "RUNTIME-1"),
            make_cell(ss.COL_ESTADO, RunStatus.RUNNING.value),
        ],
    )
    client._client.Sheets.get_sheet.return_value = make_sheet(rows=[fila_existente])

    resource_view = {
        "RUNTIME-1": ss._RuntimeView(proceso_nombre="Proceso Demo", estado=RunStatus.WARNING.value)
    }
    client.sync(resource_view)

    client._client.Sheets.add_rows.assert_not_called()
    client._client.Sheets.update_rows.assert_called_once()

    fila_actualizada = client._client.Sheets.update_rows.call_args[0][1][0]
    assert fila_actualizada.id == 99
    valores = {cell.column_id: cell.value for cell in fila_actualizada.cells}
    assert valores[COLUMN_IDS[ss.COL_ESTADO]] == RunStatus.WARNING.value
    assert COLUMN_IDS[ss.COL_ULTIMA_VEZ_WARNING] in valores


def test_sync_no_toca_estado_si_no_cambio_idempotencia(client):
    """El corazón del diseño del paso 6: si el Estado no cambió, la celda
    "Estado" no debe ni siquiera incluirse en el update -- de eso depende
    que la Alert Rule de Smartsheet (disparada por cambio de valor) no se
    re-dispare en cada corrida aunque el runtime siga en el mismo estado.
    """
    fila_existente = SimpleNamespace(
        id=99,
        cells=[
            make_cell(ss.COL_RUNTIME, "RUNTIME-1"),
            make_cell(ss.COL_ESTADO, RunStatus.RUNNING.value),
        ],
    )
    client._client.Sheets.get_sheet.return_value = make_sheet(rows=[fila_existente])

    resource_view = {
        "RUNTIME-1": ss._RuntimeView(proceso_nombre="Proceso Demo", estado=RunStatus.RUNNING.value)
    }
    client.sync(resource_view)

    fila_actualizada = client._client.Sheets.update_rows.call_args[0][1][0]
    columnas_tocadas = {cell.column_id for cell in fila_actualizada.cells}

    assert COLUMN_IDS[ss.COL_ULTIMA_ACTUALIZACION] in columnas_tocadas
    assert COLUMN_IDS[ss.COL_ESTADO] not in columnas_tocadas


def test_sync_runtime_sin_actividad_no_toca_columna_proceso(client):
    client._client.Sheets.get_sheet.return_value = make_sheet(rows=[])

    resource_view = {"RUNTIME-2": ss._RuntimeView(proceso_nombre=None, estado=ss.SIN_ACTIVIDAD)}
    client.sync(resource_view)

    nueva_fila = client._client.Sheets.add_rows.call_args[0][1][0]
    columnas_tocadas = {cell.column_id for cell in nueva_fila.cells}

    assert COLUMN_IDS[ss.COL_PROCESO] not in columnas_tocadas
    assert COLUMN_IDS[ss.COL_ESTADO] in columnas_tocadas


def test_sync_report_sin_runtimes_no_llama_a_smartsheet():
    """Con un StatusReport vacío, sync_report ni siquiera debe intentar
    construir un cliente real de Smartsheet (mucho menos llamar a la API)."""
    settings = SimpleNamespace(smartsheet_api_key="k", smartsheet_sheet_id="1")
    report = StatusReport(generated_at=datetime.now(UTC), processes=(), resource_activity=())

    ss.sync_report(report, settings)  # no debe lanzar ninguna excepción


# --- _is_retryable_smartsheet_error -----------------------------------------


def test_is_retryable_devuelve_false_para_excepcion_no_relacionada():
    # Solo se cubre el caso "seguro" de probar (una excepción que no es ni
    # ApiError ni HttpError). Construir instancias reales de esas dos
    # clases del SDK para probar las ramas True/False de should_retry
    # requiere reproducir su estructura interna de respuesta -- de bajo
    # valor frente al riesgo de un test frágil acoplado a detalles del SDK
    # que no son responsabilidad de este proyecto.
    assert ss._is_retryable_smartsheet_error(ValueError("algo")) is False
