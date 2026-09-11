"""Modelos de datos del dominio.

Estructuras inmutables que representan lo que se lee de la BD de Blue
Prism, ya traducido a un vocabulario de negocio, y lo que se necesita para
reportar a Smartsheet (paso 6). Este módulo NO contiene lógica de
interpretación: solo define la forma de los datos. La traducción de datos
crudos (statusid, filas de BD) a estas estructuras vive en
``services/process_monitor.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class RunStatus(str, Enum):
    """Estado de negocio de una sesión de Blue Prism.

    Traducido desde ``BPASession.statusid`` (crudo, sin interpretar) por
    ``services/process_monitor.STATUS_MAP``. El mapeo numérico fue
    confirmado contra una BD productiva real de Blue Prism cruzando
    ``SELECT DISTINCT statusid FROM BPASession`` con el patrón de
    ``enddatetime``/``terminationreason`` por statusid (ver
    docs/architecture.md, sección "Alcance de datos", para el detalle):

        1 -> RUNNING     2 -> TERMINATED   3 -> STOPPED
        4 -> COMPLETED   5 -> WARNING

    ``UNKNOWN`` es el fallback deliberado para cualquier ``statusid`` no
    contemplado en el mapeo (por ejemplo, si Blue Prism introduce un
    estado nuevo en una actualización): es preferible que aparezca como
    "desconocido" y visible en el reporte a que se clasifique
    silenciosamente como otra cosa.
    """

    RUNNING = "Running"
    TERMINATED = "Terminated"
    STOPPED = "Stopped"
    COMPLETED = "Completed"
    WARNING = "Warning"
    UNKNOWN = "Unknown"


#: Estados que deben disparar una alerta (paso 6: regla de Smartsheet).
#: Vive junto al enum, no en process_monitor.py, para que tanto la
#: construcción del reporte como la futura integración de Smartsheet
#: (services/smartsheet_sync.py) consulten la misma fuente de verdad en
#: vez de duplicar el ``if status in (...)`` en dos módulos.
ALERT_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.WARNING, RunStatus.TERMINATED})


@dataclass(frozen=True, slots=True)
class ProcessStatusRecord:
    """Estado más reciente de un proceso en un runtime resource específico.

    Una instancia por combinación (proceso, resource) que tuvo al menos una
    sesión dentro de la ventana de lookback de
    :func:`bp_process_monitor.db.queries.get_latest_sessions`. Si un
    proceso no corrió en esa ventana en ningún resource, simplemente no
    genera una instancia aquí (no existe una sesión de la cual partir).
    """

    process_id: int
    process_name: str
    resource_id: int
    resource_name: str
    status: RunStatus
    raw_status_id: int
    start_time: datetime
    end_time: datetime | None
    termination_reason: str | None

    @property
    def is_alert(self) -> bool:
        """True si este estado debe disparar una alerta (Warning/Terminated)."""
        return self.status in ALERT_STATUSES


@dataclass(frozen=True, slots=True)
class ResourceActivityRecord:
    """Actividad reciente de un runtime resource.

    Los runtimes del ambiente operan 24/7: un resource sin
    ninguna sesión dentro de la ventana de lookback es en sí mismo un
    síntoma de problema (desconexión, scheduler caído, resource colgado),
    independientemente del estado de cualquier proceso puntual. Por eso
    esta actividad se evalúa por resource, no cruzando cada proceso
    contra cada resource.
    """

    resource_id: int
    resource_name: str
    has_recent_activity: bool
    last_activity_at: datetime | None  # None solo si el resource jamás tuvo ninguna sesión

    @property
    def is_alert(self) -> bool:
        """True si este resource debería tener actividad 24/7 y no la tiene."""
        return not self.has_recent_activity


@dataclass(frozen=True, slots=True)
class StatusReport:
    """Snapshot completo de una corrida del monitor, listo para el paso 6."""

    generated_at: datetime
    processes: tuple[ProcessStatusRecord, ...]
    resource_activity: tuple[ResourceActivityRecord, ...]

    @property
    def process_alerts(self) -> tuple[ProcessStatusRecord, ...]:
        """Procesos en estado de alerta (Warning/Terminated)."""
        return tuple(p for p in self.processes if p.is_alert)

    @property
    def resource_alerts(self) -> tuple[ResourceActivityRecord, ...]:
        """Runtimes sin actividad reciente (posible desconexión)."""
        return tuple(r for r in self.resource_activity if r.is_alert)
