"""Sincronización del reporte de estado hacia Smartsheet (paso 6 del plan).

Refleja en una hoja de Smartsheet el estado actual de cada runtime
resource, para que el equipo lo consulte en un dashboard sin necesidad de
tener Blue Prism abierto.

Decisiones de diseño (acordadas con el usuario):

- Automatización nativa de Smartsheet: este módulo SOLO actualiza datos.
  La alerta al equipo la dispara una Alert Rule configurada en la propia
  hoja de Smartsheet (Automation -> cuando "Estado" cambia a "Warning").
  "Terminated" no dispara alerta de Smartsheet porque Blue Prism ya lo
  notifica directamente por su cuenta.
- Idempotente por diseño: la celda "Estado" solo se incluye en una
  actualización cuando el valor cambió respecto al que ya está en el
  sheet. De esto depende que la Alert Rule (disparada por "cambio de
  valor") no se re-dispare en cada corrida si el estado sigue igual, y
  reduce además las llamadas a la API.
- Una fila por runtime resource, no por combinación proceso+resource: el
  sheet es un dashboard operativo para el equipo, no un log de sesiones.
  La columna "Runtime" (BPAResource.name) es la llave de emparejamiento
  (upsert): si el nombre no existe en el sheet se crea la fila: si ya
  existe, se actualiza.
- Plantilla multi-cliente: los IDs de columna de Smartsheet NO se
  hardcodean. Cada copia del sheet (un cliente nuevo) genera IDs de
  columna nuevos aunque el título sea idéntico -- por eso se resuelven
  por TÍTULO en cada corrida, a partir de la misma llamada a
  ``get_sheet`` que de todos modos se necesita para leer las filas
  existentes (no cuesta una llamada extra a la API).

Un resource sin NINGUNA sesión en la ventana de lookback (ver
``StatusReport.resource_alerts``) también se sincroniza: se refleja como
"Sin actividad" en "Estado" (valor agregado al PICKLIST de Smartsheet
junto con Running/Terminated/Stopped/Completed/Warning). En ese caso no
se toca la celda "Proceso": no hay ningún ``ProcessStatusRecord`` del
cual tomar un nombre, y sobreescribirla a vacío borraría sin necesidad
el último proceso conocido que corrió ahí.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

import smartsheet
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from bp_process_monitor.config import Settings
from bp_process_monitor.models import (
    ProcessStatusRecord,
    ResourceActivityRecord,
    RunStatus,
    StatusReport,
)

logger = logging.getLogger(__name__)

# Títulos de columna esperados en el sheet -- es un contrato con la
# PLANTILLA de Smartsheet (el nombre de cada columna), no con un
# sheet_id concreto. Si algún día cambia el nombre de una columna en el
# sheet, hay que actualizarlo aquí también.
COL_RUNTIME = "Runtime"
COL_PROCESO = "Proceso"
COL_ESTADO = "Estado"
COL_ULTIMA_ACTUALIZACION = "Última actualización"
COL_ULTIMA_VEZ_WARNING = "Última vez en Warning"

REQUIRED_COLUMNS = (
    COL_RUNTIME,
    COL_PROCESO,
    COL_ESTADO,
    COL_ULTIMA_ACTUALIZACION,
    COL_ULTIMA_VEZ_WARNING,
)

# Valor de "Estado" para un runtime sin ninguna sesión en la ventana de
# lookback (ver ResourceActivityRecord.has_recent_activity). No es un
# RunStatus: no viene de un statusid de BPASession, es una conclusion de
# process_monitor.py sobre AUSENCIA total de actividad. Debe existir como
# opcion en el PICKLIST "Estado" del sheet (se agrega a mano una sola vez
# por sheet/cliente, igual que las demas opciones del picklist).
SIN_ACTIVIDAD = "Sin actividad"

TZ = ZoneInfo("America/Mexico_City")


class SmartsheetSyncError(RuntimeError):
    """Error de configuración/estructura del sheet -- no tiene sentido reintentar."""


def _is_retryable_smartsheet_error(exc: BaseException) -> bool:
    """Decide si vale la pena reintentar un error de Smartsheet.

    El propio SDK ya distingue esto por nosotros: ``ApiError.should_retry``
    viene en ``True`` para los errores que Smartsheet documenta como
    transitorios (rate limit, timeout de servidor, mantenimiento). Un
    ``HttpError`` (falla a nivel de transporte: timeout de red, conexión
    caída) también es candidato a reintento. Un ``ApiError`` con
    ``should_retry=False`` (token inválido, sheet_id inexistente, columna
    faltante) NO se reintenta: no se arregla solo, y hay que fallar rápido
    y visible -- mismo principio que ``db/connection.py`` aplica con
    ``DBAPIError`` vs. errores de sintaxis/permisos.
    """
    if isinstance(exc, smartsheet.exceptions.HttpError):
        return True
    if isinstance(exc, smartsheet.exceptions.ApiError):
        return bool(exc.should_retry)
    return False


smartsheet_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retryable_smartsheet_error),
    before_sleep=lambda retry_state: logger.warning(
        "Reintentando operación contra Smartsheet (intento %d) tras error: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception() if retry_state.outcome else None,
    ),
)


def _now_str() -> str:
    """Fecha/hora actual como texto, zona horaria del negocio (CDMX).

    Smartsheet no tiene un tipo de columna datetime real (ver
    docs/architecture.md); se guarda como texto en formato ISO
    ``YYYY-MM-DD HH:MM:SS`` para que ordene correctamente si alguien
    filtra u ordena la columna en el dashboard.
    """
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _cell(column_id: int, value: object) -> smartsheet.models.Cell:
    cell = smartsheet.models.Cell()
    cell.column_id = column_id
    cell.value = value
    return cell


def _cell_value(row: smartsheet.models.Row, column_id: int) -> object | None:
    # cast(): el SDK de Smartsheet no trae stubs de tipos (ver override en
    # pyproject.toml), asi que ``cell.value`` resuelve a ``Any`` -- devolver
    # ``Any`` donde se declaro un tipo concreto (``object | None``) es
    # exactamente lo que ``--strict`` marca con no-any-return. El cast no
    # cambia nada en tiempo de ejecucion, solo declara la frontera de tipos
    # en el punto donde los datos "no tipados" entran a nuestro codigo.
    for cell in row.cells:
        if cell.column_id == column_id:
            return cast(object, cell.value)
    return None


def _select_representative(records: Sequence[ProcessStatusRecord]) -> ProcessStatusRecord:
    """Elige qué proceso representa el estado de un runtime que tuvo varias
    sesiones recientes.

    Un mismo runtime puede haber corrido más de un proceso dentro de la
    ventana de lookback (:func:`db.queries.get_latest_sessions` es por
    combinación proceso+resource, no por resource). Como el sheet
    muestra una sola fila por runtime, hay que elegir un representante:

    1. Si alguno de los procesos de ese runtime está en alerta
       (Warning/Terminated), se prioriza el más reciente DE ESOS --
       así el dashboard nunca esconde una alerta detrás de un proceso
       sano que arrancó después.
    2. Si ninguno está en alerta, se muestra el más reciente de todos,
       porque refleja mejor "qué está haciendo el runtime ahora mismo".
    """
    return max(records, key=lambda r: (r.is_alert, r.start_time))


@dataclass(frozen=True, slots=True)
class _RuntimeView:
    """Vista unificada de un runtime, lista para convertirse en fila.

    Envuelve los dos orígenes posibles de un runtime en el reporte: un
    ``ProcessStatusRecord`` (tuvo actividad) o el caso "sin actividad"
    (``ResourceActivityRecord`` con ``has_recent_activity=False``), para
    que ``SmartsheetClient`` no tenga que conocer esa distinción.

    ``proceso_nombre`` en ``None`` significa "no hay dato de proceso que
    reportar" -- la celda "Proceso" no se toca (ni se crea con valor ni
    se sobreescribe), para no borrar sin necesidad el último proceso
    conocido de un runtime que dejó de tener actividad.
    """

    proceso_nombre: str | None
    estado: str


def _build_resource_view(report: StatusReport) -> dict[str, _RuntimeView]:
    """Arma la vista por runtime que se sincroniza a Smartsheet.

    Dos fuentes, sin solape entre sí: un runtime con al menos una sesión
    en la ventana de lookback aparece en ``report.processes`` (y NO en
    ``resource_alerts``, ver ``ResourceActivityRecord.is_alert``);
    ``resource_activity`` cubre TODOS los runtimes, así que de ahí solo
    se toman los que ``build_report`` (paso 5) ya marcó sin actividad
    reciente.
    """
    by_resource: dict[str, list[ProcessStatusRecord]] = {}
    for record in report.processes:
        by_resource.setdefault(record.resource_name, []).append(record)

    view: dict[str, _RuntimeView] = {}
    for name, records in by_resource.items():
        representative = _select_representative(records)
        view[name] = _RuntimeView(
            proceso_nombre=representative.process_name,
            estado=representative.status.value,
        )

    resource: ResourceActivityRecord
    for resource in report.resource_activity:
        if not resource.has_recent_activity:
            view[resource.resource_name] = _RuntimeView(proceso_nombre=None, estado=SIN_ACTIVIDAD)

    return view


class SmartsheetClient:
    """Wrapper delgado sobre el SDK de Smartsheet para el sync del paso 6."""

    def __init__(self, api_key: str, sheet_id: str | int):
        self._client = smartsheet.Smartsheet(api_key)
        self._client.errors_as_exceptions(True)
        self._sheet_id = sheet_id

    @smartsheet_retry
    def _get_sheet(self) -> smartsheet.models.Sheet:
        return self._client.Sheets.get_sheet(self._sheet_id)

    @staticmethod
    def _resolve_column_ids(sheet: smartsheet.models.Sheet) -> dict[str, int]:
        by_title = {column.title: column.id for column in sheet.columns}
        faltantes = [titulo for titulo in REQUIRED_COLUMNS if titulo not in by_title]
        if faltantes:
            raise SmartsheetSyncError(
                f"Faltan columnas requeridas en el sheet de Smartsheet: {faltantes}. "
                "Verifica que la hoja siga la plantilla esperada (ver docs/architecture.md)."
            )
        return by_title

    def _new_row(
        self,
        cols: dict[str, int],
        runtime_nombre: str,
        view: _RuntimeView,
        now: str,
    ) -> smartsheet.models.Row:
        cells = [
            _cell(cols[COL_RUNTIME], runtime_nombre),
            _cell(cols[COL_ESTADO], view.estado),
            _cell(cols[COL_ULTIMA_ACTUALIZACION], now),
            _cell(cols[COL_ULTIMA_VEZ_WARNING], now if view.estado == RunStatus.WARNING.value else ""),
        ]
        if view.proceso_nombre is not None:
            cells.append(_cell(cols[COL_PROCESO], view.proceso_nombre))

        row = smartsheet.models.Row()
        row.to_bottom = True
        row.cells = cells
        return row

    @smartsheet_retry
    def _add_rows(self, rows: list[smartsheet.models.Row]) -> None:
        self._client.Sheets.add_rows(self._sheet_id, rows)

    @smartsheet_retry
    def _update_rows(self, rows: list[smartsheet.models.Row]) -> None:
        self._client.Sheets.update_rows(self._sheet_id, rows)

    def sync(self, resource_view: dict[str, _RuntimeView]) -> None:
        """Aplica el upsert: crea filas para runtimes nuevos, actualiza las existentes."""
        sheet = self._get_sheet()
        cols = self._resolve_column_ids(sheet)
        index = {
            _cell_value(row, cols[COL_RUNTIME]): row
            for row in sheet.rows
            if _cell_value(row, cols[COL_RUNTIME])
        }

        now = _now_str()
        to_add: list[smartsheet.models.Row] = []
        to_update: list[smartsheet.models.Row] = []
        nuevos: list[str] = []
        cambios_estado: list[str] = []

        for runtime_nombre, view in resource_view.items():
            existing = index.get(runtime_nombre)

            if existing is None:
                to_add.append(self._new_row(cols, runtime_nombre, view, now))
                nuevos.append(runtime_nombre)
                continue

            # "Última actualización" se refresca siempre -- el equipo
            # debe poder confiar en que el monitor sigue corriendo.
            # "Proceso" solo se toca si hay un dato real que escribir
            # (ver _RuntimeView): un runtime "Sin actividad" no trae
            # proceso, y no tiene sentido borrar el último conocido.
            cells = [_cell(cols[COL_ULTIMA_ACTUALIZACION], now)]
            if view.proceso_nombre is not None:
                cells.append(_cell(cols[COL_PROCESO], view.proceso_nombre))

            estado_actual = _cell_value(existing, cols[COL_ESTADO])
            if estado_actual != view.estado:
                # Solo se toca "Estado" si realmente cambió: esto es lo
                # que dispara (o no) la Alert Rule nativa de Smartsheet.
                cells.append(_cell(cols[COL_ESTADO], view.estado))
                if view.estado == RunStatus.WARNING.value:
                    cells.append(_cell(cols[COL_ULTIMA_VEZ_WARNING], now))
                cambios_estado.append(f"{runtime_nombre}={view.estado}")

            update_row = smartsheet.models.Row()
            update_row.id = existing.id
            update_row.cells = cells
            to_update.append(update_row)

        if to_add:
            self._add_rows(to_add)
            logger.info("Runtime(s) nuevo(s) agregado(s) al sheet: %s", ", ".join(sorted(nuevos)))
        if to_update:
            self._update_rows(to_update)
        if cambios_estado:
            logger.info("Cambio de Estado sincronizado: %s", ", ".join(sorted(cambios_estado)))


def sync_report(report: StatusReport, settings: Settings) -> None:
    """Punto de entrada del paso 6: sincroniza un ``StatusReport`` hacia Smartsheet.

    Es la función que ``main.py`` llama después de
    :func:`bp_process_monitor.services.process_monitor.build_report`.
    """
    resource_view = _build_resource_view(report)
    if not resource_view:
        logger.info(
            "Sin procesos en la ventana de lookback; no hay nada que sincronizar a Smartsheet."
        )
        return

    client = SmartsheetClient(settings.smartsheet_api_key, settings.smartsheet_sheet_id)
    client.sync(resource_view)
    logger.info("Sincronización con Smartsheet completada: %d runtime(s).", len(resource_view))
