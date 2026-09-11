"""Fixtures compartidos para las pruebas.

Evitan que la suite dependa de sistemas reales: filas de ejemplo de BD
(con la misma forma dict-like que devuelve ``connection.execute(...).mappings()``
en db/queries.py) y una ``Settings`` de prueba que NO lee el ``.env`` real
del proyecto (para no arrastrar credenciales reales a los tests, y para
que "falta una variable requerida" sea reproducible sin depender del
entorno de quien corre la suite).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bp_process_monitor.config import Settings

Row = dict[str, Any]

REQUIRED_SETTINGS_ENV: dict[str, str] = {
    "BP_DB_SERVER": "test-server",
    "BP_DB_NAME": "test-db",
    "BP_DB_USER": "test-user",
    "BP_DB_PASSWORD": "test-password",
    "SMARTSHEET_API_KEY": "test-api-key",
    "SMARTSHEET_SHEET_ID": "1234567890",
}


def make_session_row(
    *,
    session_id: int = 1,
    process_id: int = 1,
    process_name: str = "Proceso Demo",
    resource_id: int = 1,
    resource_name: str = "RUNTIME-1",
    statusid: int = 1,
    start: datetime | None = None,
    end: datetime | None = None,
    termination_reason: str | None = None,
) -> Row:
    """Fila con la forma que devuelve ``db.queries.get_latest_sessions``."""
    return {
        "sessionid": session_id,
        "processid": process_id,
        "process_name": process_name,
        "resourceid": resource_id,
        "resource_name": resource_name,
        "statusid": statusid,
        "startdatetime": start or (datetime.now(UTC) - timedelta(minutes=5)),
        "enddatetime": end,
        "terminationreason": termination_reason,
    }


def make_resource_row(*, resource_id: int = 1, name: str = "RUNTIME-1") -> Row:
    """Fila con la forma que devuelve ``db.queries.get_resources``."""
    return {"resourceid": resource_id, "name": name, "attributeid": 0}


@pytest.fixture
def session_row_factory():
    """Fábrica de filas de sesión (ver :func:`make_session_row`)."""
    return make_session_row


@pytest.fixture
def resource_row_factory():
    """Fábrica de filas de resource (ver :func:`make_resource_row`)."""
    return make_resource_row


@pytest.fixture
def test_settings() -> Settings:
    """``Settings`` de prueba, con ``_env_file=None`` para NO leer el ``.env``
    real del proyecto (que puede tener credenciales reales de un ambiente productivo).
    """
    return Settings(_env_file=None, **REQUIRED_SETTINGS_ENV)
