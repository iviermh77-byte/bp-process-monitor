"""Punto de entrada del proyecto.

Orquesta una corrida completa:
1. Carga configuración (config.py)
2. Configura logging estructurado con rotación semanal (logging_config.py)
3. Consulta la BD de Blue Prism y arma el reporte (db/ + services/process_monitor.py)
4. Sincroniza el reporte hacia Smartsheet (services/smartsheet_sync.py)

``run()`` contiene el flujo real y se deja separado de ``main()`` para que
sea testeable sin involucrarse con ``sys.exit``. ``main()`` es el entrypoint
del script (ver ``[project.scripts]`` en pyproject.toml): envuelve ``run()``
en un try/except que loguea cualquier excepción no controlada con
traceback completo y termina el proceso con código de salida distinto de
cero -- es la señal que el mecanismo de scheduling del paso 8 (Task
Scheduler, servicio o loop propio) usará para detectar que una corrida
falló.
"""

from __future__ import annotations

import logging
import sys

from bp_process_monitor.config import get_settings
from bp_process_monitor.logging_config import setup_logging
from bp_process_monitor.services.process_monitor import build_report
from bp_process_monitor.services.smartsheet_sync import sync_report

logger = logging.getLogger(__name__)


def run() -> None:
    """Ejecuta una corrida completa del monitor (sin manejo de excepciones)."""
    settings = get_settings()
    setup_logging(
        log_dir=settings.log_dir,
        level=settings.log_level,
        backup_weeks=settings.log_backup_weeks,
    )

    logger.info("Iniciando corrida del monitor de procesos Blue Prism.")

    report = build_report()
    logger.info(
        "Reporte generado: %d proceso(s) en ventana de lookback, %d runtime(s) registrados "
        "(%d en alerta, %d sin actividad reciente).",
        len(report.processes),
        len(report.resource_activity),
        len(report.process_alerts),
        len(report.resource_alerts),
    )

    sync_report(report, settings)
    logger.info("Corrida completada.")


def main() -> None:
    """Entrypoint del script: ejecuta ``run()`` y garantiza un exit code útil."""
    try:
        run()
    except Exception:
        logger.exception("La corrida del monitor terminó con un error no controlado.")
        sys.exit(1)


if __name__ == "__main__":
    main()
