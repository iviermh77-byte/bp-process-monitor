"""Lógica de negocio: arma el reporte de estado de procesos y runtimes.

Separación deliberada de responsabilidades (paso 5 del plan):

- Las funciones privadas de este módulo (``_map_status``,
  ``_to_process_record``, ``_resources_without_recent_activity``,
  ``_to_resource_activity_records``) son PURAS: reciben filas ya traídas
  de la BD (o datos de ejemplo en un test) y devuelven modelos de
  ``models.py``. No abren conexiones ni importan nada de
  ``db/connection.py``. Esto permite testear toda la lógica de negocio
  (paso 7) con fixtures, sin mockear SQLAlchemy ni tocar la BD real.
- :func:`build_report` es la ÚNICA función que orquesta BD + lógica: abre
  la conexión, llama a ``db/queries.py`` y delega la interpretación a las
  funciones puras de arriba.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from bp_process_monitor.db.connection import get_connection
from bp_process_monitor.db.queries import (
    Row,
    get_last_session_time_for_resources,
    get_latest_sessions,
    get_resources,
)
from bp_process_monitor.models import (
    ProcessStatusRecord,
    ResourceActivityRecord,
    RunStatus,
    StatusReport,
)

logger = logging.getLogger(__name__)

# Mapeo confirmado contra una BD productiva real de Blue Prism cruzando
# `SELECT DISTINCT statusid FROM BPASession` con el patrón de
# enddatetime/terminationreason por statusid (ver docs/architecture.md,
# sección "Alcance de datos", para el detalle completo de cómo se validó
# cada valor). Antes de reutilizar este mapeo contra OTRA instalación de
# Blue Prism, hay que repetir esa validación: el mapeo no es un estándar
# publicado por Blue Prism.
STATUS_MAP: dict[int, RunStatus] = {
    1: RunStatus.RUNNING,
    2: RunStatus.TERMINATED,
    3: RunStatus.STOPPED,
    4: RunStatus.COMPLETED,
    5: RunStatus.WARNING,
}


def _map_status(raw_status_id: int) -> RunStatus:
    """Traduce un ``statusid`` crudo a :class:`RunStatus`.

    Si aparece un código no visto antes (nueva versión de Blue Prism,
    dato inesperado, etc.) no se adivina: se clasifica como ``UNKNOWN`` y
    se deja un warning en el log. Preferible que un estado "desconocido"
    sea visible en el reporte a que se clasifique silenciosamente como
    algo que no es.
    """
    status = STATUS_MAP.get(raw_status_id)
    if status is None:
        logger.warning(
            "statusid %s sin mapear en STATUS_MAP; se reporta como UNKNOWN",
            raw_status_id,
        )
        return RunStatus.UNKNOWN
    return status


def _to_process_record(row: Row) -> ProcessStatusRecord:
    """Convierte una fila cruda de :func:`get_latest_sessions` en un modelo."""
    return ProcessStatusRecord(
        process_id=row["processid"],
        process_name=row["process_name"],
        resource_id=row["resourceid"],
        resource_name=row["resource_name"],
        status=_map_status(row["statusid"]),
        raw_status_id=row["statusid"],
        start_time=row["startdatetime"],
        end_time=row["enddatetime"],
        termination_reason=row["terminationreason"],
    )


def _resources_without_recent_activity(
    resources: Sequence[Row], sessions: Sequence[Row]
) -> list[Row]:
    """Filas de ``resources`` cuyo ``resourceid`` no aparece en ninguna
    sesión reciente.

    Los runtimes del ambiente trabajan 24/7: la ausencia total de
    sesiones en la ventana de lookback es en sí misma la señal de alerta
    (resource desconectado, scheduler caído, etc.), sin importar qué
    proceso específico debería haber corrido ahí. Por eso se evalúa a
    nivel resource y no cruzando cada proceso contra cada resource.
    """
    resource_ids_con_actividad = {row["resourceid"] for row in sessions}
    return [row for row in resources if row["resourceid"] not in resource_ids_con_actividad]


def _to_resource_activity_records(
    resources: Sequence[Row],
    sessions: Sequence[Row],
    last_activity_rows: Sequence[Row],
) -> list[ResourceActivityRecord]:
    """Combina el universo de resources con su actividad reciente y, para
    los que no tienen, su última actividad histórica.
    """
    resource_ids_con_actividad = {row["resourceid"] for row in sessions}
    last_activity_by_resource = {
        row["resourceid"]: row["last_start_time"] for row in last_activity_rows
    }
    return [
        ResourceActivityRecord(
            resource_id=row["resourceid"],
            resource_name=row["name"],
            has_recent_activity=row["resourceid"] in resource_ids_con_actividad,
            last_activity_at=last_activity_by_resource.get(row["resourceid"]),
        )
        for row in resources
    ]


def build_report(engine: Engine | None = None) -> StatusReport:
    """Arma el snapshot completo de una corrida: procesos + actividad de resources.

    Única función de este módulo que toca la BD; toda la interpretación
    de los datos ya vive en las funciones puras de arriba, testeables
    por separado.
    """
    with get_connection(engine) as connection:
        sessions = get_latest_sessions(connection)
        resources = get_resources(connection)

        sin_actividad = _resources_without_recent_activity(resources, sessions)
        sin_actividad_ids = [row["resourceid"] for row in sin_actividad]
        last_activity_rows = get_last_session_time_for_resources(connection, sin_actividad_ids)

    process_records = [_to_process_record(row) for row in sessions]
    resource_records = _to_resource_activity_records(resources, sessions, last_activity_rows)

    report = StatusReport(
        generated_at=datetime.now(UTC),
        processes=tuple(process_records),
        resource_activity=tuple(resource_records),
    )

    if report.process_alerts:
        logger.warning(
            "%d proceso(s) en estado de alerta: %s",
            len(report.process_alerts),
            ", ".join(
                f"{p.process_name}@{p.resource_name}={p.status.value}"
                for p in report.process_alerts
            ),
        )
    if report.resource_alerts:
        logger.warning(
            "%d runtime(s) sin actividad reciente: %s",
            len(report.resource_alerts),
            ", ".join(r.resource_name for r in report.resource_alerts),
        )

    return report
