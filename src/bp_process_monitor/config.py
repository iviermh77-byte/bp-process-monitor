"""Configuración de la aplicación.

Define una clase `Settings` basada en pydantic-settings que lee las
variables de entorno definidas en `.env` (ver `.env.example` para la
plantilla). Si falta una variable requerida, la aplicación falla
inmediatamente al arrancar (fail fast) en vez de fallar a mitad de una
corrida — esto se logra dejando que pydantic valide en tiempo de
instanciación.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuración centralizada, leída desde variables de entorno / .env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",  # si hay una variable en .env que no está declarada aquí, falla
    )

    # --- Base de datos de Blue Prism (usuario de solo lectura) ---
    bp_db_server: str = Field(alias="BP_DB_SERVER")
    bp_db_name: str = Field(alias="BP_DB_NAME")
    bp_db_user: str = Field(alias="BP_DB_USER")
    bp_db_password: str = Field(alias="BP_DB_PASSWORD")
    bp_db_driver: str = Field(default="ODBC Driver 17 for SQL Server", alias="BP_DB_DRIVER")

    # --- Smartsheet ---
    smartsheet_api_key: str = Field(alias="SMARTSHEET_API_KEY")
    smartsheet_sheet_id: str = Field(alias="SMARTSHEET_SHEET_ID")

    # --- Ejecución ---
    run_interval_minutes: int = Field(default=15, alias="RUN_INTERVAL_MINUTES")

    # --- Logging ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_dir: str = Field(default="logs", alias="LOG_DIR")
    # Rotación semanal (logging_config.py usa TimedRotatingFileHandler,
    # when="W6"): cuántas semanas de historial conservar. 4 semanas = ~1 mes.
    log_backup_weeks: int = Field(default=4, alias="LOG_BACKUP_WEEKS")


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de Settings.

    Se usa @lru_cache para que Settings se lea del entorno una sola vez
    por proceso, en vez de releer y revalidar el .env en cada llamada.
    """
    return Settings()
