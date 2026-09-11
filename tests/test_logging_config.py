"""Pruebas de logging_config.py: rotación semanal configurada correctamente
y ausencia de handlers duplicados si se llama más de una vez."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

from bp_process_monitor.logging_config import LOG_FILENAME, setup_logging


def _reset_root_logger() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


def test_setup_logging_configura_rotacion_semanal_los_domingos(tmp_path):
    _reset_root_logger()
    try:
        setup_logging(log_dir=tmp_path, level="DEBUG", backup_weeks=4)

        root = logging.getLogger()
        assert root.level == logging.DEBUG

        file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
        assert len(file_handlers) == 1

        handler = file_handlers[0]
        assert handler.when == "W6"
        assert handler.interval == 7 * 24 * 60 * 60  # semanal, normalizado a segundos
        assert handler.backupCount == 4
        assert (tmp_path / LOG_FILENAME).exists()
    finally:
        _reset_root_logger()


def test_setup_logging_es_idempotente_no_duplica_handlers(tmp_path):
    _reset_root_logger()
    try:
        setup_logging(log_dir=tmp_path)
        setup_logging(log_dir=tmp_path)

        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
        assert len(file_handlers) == 1
    finally:
        _reset_root_logger()
