"""Configuración de logging estructurado con rotación de archivos.

Rotación por TIEMPO, no por tamaño: con corridas cada 30 min el volumen de
log por ejecución es predecible y bajo (no hay picos raros de tráfico como
en un servicio web), así que lo que importa gestionar es cuánto historial
conservar, no cuánto pesa cada archivo individual.

Se rota semanalmente, los domingos, a medianoche (comportamiento por
defecto de ``TimedRotatingFileHandler`` para ``when="W6"``), conservando
``backup_weeks`` archivos históricos (ver ``Settings.log_backup_weeks``,
por defecto 4 semanas ≈ 1 mes).

``setup_logging`` se llama una sola vez desde ``main.py``, antes de
cualquier otra operación del pipeline, para que hasta el primer log de
"iniciando corrida" quede en el archivo rotado.
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_FILENAME = "bp_process_monitor.log"


def setup_logging(log_dir: Path | str, level: str = "INFO", backup_weeks: int = 4) -> None:
    """Configura el logger raíz con salida a archivo (rotación semanal) y consola.

    Idempotente: si se llama más de una vez en el mismo proceso (por
    ejemplo, en una prueba que corre el flujo completo dos veces) no
    duplica handlers ni escribe cada línea varias veces.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    if any(isinstance(handler, TimedRotatingFileHandler) for handler in root.handlers):
        return

    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = TimedRotatingFileHandler(
        log_dir / LOG_FILENAME,
        when="W6",  # rota los domingos (0=lunes ... 6=domingo)
        interval=1,  # cada 1 semana
        backupCount=backup_weeks,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
