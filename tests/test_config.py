"""Pruebas de config.py: que Settings valide correctamente las variables de entorno."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bp_process_monitor.config import Settings

# Duplicado deliberadamente (no importado de conftest.py): tests/ no es un
# paquete (sin __init__.py), así que "import tests.conftest" depende del
# modo de import de pytest y es frágil. Mantener esta lista aquí, junto a
# los tests que la usan, es más simple que resolver ese acoplamiento.
REQUIRED_ENV: dict[str, str] = {
    "BP_DB_SERVER": "test-server",
    "BP_DB_NAME": "test-db",
    "BP_DB_USER": "test-user",
    "BP_DB_PASSWORD": "test-password",
    "SMARTSHEET_API_KEY": "test-api-key",
    "SMARTSHEET_SHEET_ID": "1234567890",
}


def test_settings_falla_si_falta_una_variable_requerida(monkeypatch):
    # Limpia el entorno real por si alguna de estas variables ya está
    # exportada en la máquina de quien corre la prueba -- si no se hace
    # esto, la prueba podría "pasar por accidente" leyendo del entorno
    # real en vez de detectar la ausencia que queremos forzar.
    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)

    incompleto = {k: v for k, v in REQUIRED_ENV.items() if k != "BP_DB_PASSWORD"}

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **incompleto)

    assert "bp_db_password" in str(exc_info.value).lower()


def test_settings_usa_valores_por_defecto(test_settings):
    assert test_settings.log_level == "INFO"
    assert test_settings.log_dir == "logs"
    assert test_settings.log_backup_weeks == 4
    assert test_settings.run_interval_minutes == 15
    assert test_settings.bp_db_driver == "ODBC Driver 17 for SQL Server"


def test_settings_lee_valores_explicitos():
    settings = Settings(
        _env_file=None,
        **REQUIRED_ENV,
        LOG_LEVEL="DEBUG",
        LOG_BACKUP_WEEKS=8,
        RUN_INTERVAL_MINUTES=30,
    )

    assert settings.log_level == "DEBUG"
    assert settings.log_backup_weeks == 8
    assert settings.run_interval_minutes == 30
