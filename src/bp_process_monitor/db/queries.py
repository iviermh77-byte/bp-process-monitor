"""Consultas SQL a la base de datos de Blue Prism.

Todas las consultas son ``SELECT`` parametrizados (nunca f-strings con
valores de usuario, para evitar inyección SQL) y de solo lectura.

Alcance de columnas (ver docs/architecture.md, paso 1 — pendiente de cierre
formal): las consultas de aquí traen las columnas de ``BPAProcess``,
``BPASession`` y ``BPAResource`` más comúnmente usadas para monitoreo. Los
nombres de columna de la BD interna de Blue Prism no están publicados
oficialmente y pueden variar ligeramente entre versiones — antes de correr
esto contra la BD productiva real, valida los nombres con:

    SELECT TOP 5 * FROM BPAProcess;
    SELECT TOP 5 * FROM BPASession;
    SELECT TOP 5 * FROM BPAResource;

Decisión de diseño importante: este módulo devuelve datos CRUDOS —
``statusid`` de BPASession y ``attributeid`` de BPAProcess/BPAResource se
devuelven sin traducir a texto (ni "Terminated", ni "Warning", ni
"Proceso"/"Objeto"). Esa interpretación se hace a propósito en el paso 5
(``services/process_monitor.py`` + ``models.py``) y no aquí, por dos
motivos:

1. Blue Prism no publica oficialmente el mapeo numérico de esos códigos;
   conviene confirmarlo contra tu instalación (por ejemplo corriendo
   ``SELECT DISTINCT statusid FROM BPASession`` y cruzando cada valor con lo
   que muestra Control Room para esa sesión) antes de codificarlo como
   regla de negocio.
2. Mantener la capa de datos "tonta" (solo trae filas) permite testear la
   lógica de negocio del paso 5 con datos de ejemplo, sin mockear SQL.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import TextClause

from bp_process_monitor.db.connection import db_retry

# Ventana por defecto para "sesiones recientes": limita el rango que se
# escanea ANTES de particionar con ROW_NUMBER(), para no barrer todo el
# historial de BPASession (puede tener millones de filas en un ambiente
# productivo con mucho volumen).
DEFAULT_LOOKBACK_HOURS = 24

Row = RowMapping  # alias de sqlalchemy.engine.RowMapping: fila resultado, dict-like


def _select(connection: Connection, sql: str, **params: object) -> Sequence[Row]:
    """Ejecuta un SELECT parametrizado y devuelve las filas como mappings.

    Guarda de seguridad adicional (además del permiso de solo lectura del
    login de BD): rechaza en el propio código cualquier sentencia que no
    empiece con SELECT, como defensa en profundidad.
    """
    normalized = sql.strip().upper()
    if not normalized.startswith("SELECT") and not normalized.startswith("WITH"):
        raise ValueError("db/queries.py solo puede ejecutar sentencias SELECT (o CTE con WITH).")
    return _execute(connection, sql, params)


@db_retry
def _execute(connection: Connection, sql: str, params: Mapping[str, object]) -> Sequence[Row]:
    result = connection.execute(text(sql), params)
    return result.mappings().all()


def get_processes(connection: Connection) -> Sequence[Row]:
    """Trae los procesos y objetos definidos en Blue Prism.

    ``attributeid`` es un bitmask sin traducir (ver nota de módulo):
    confirma en tu instalación qué bit distingue Proceso de Objeto, y si
    existe alguna forma de "borrado lógico" que debas filtrar, antes de
    usar esta columna como filtro en el paso 5.
    """
    sql = """
        SELECT
            processid,
            name,
            attributeid
        FROM BPAProcess
    """
    return _select(connection, sql)


def get_resources(connection: Connection) -> Sequence[Row]:
    """Trae los resources (servers / runtime resources) registrados en Blue Prism.

    No incluye el estado "en vivo" (conectado/desconectado) del resource:
    ese estado depende del mecanismo interno de conexión de Blue Prism
    (Message Broker / Conclave) y no es confiablemente derivable solo desde
    BD. Para monitoreo es más robusto inferir salud a partir de actividad
    reciente en BPASession (ver :func:`get_latest_sessions`) que del estado
    del resource en sí — esa es la razón por la que esta función no intenta
    exponer un campo "online/offline".
    """
    sql = """
        SELECT
            resourceid,
            name,
            attributeid
        FROM BPAResource
    """
    return _select(connection, sql)


def get_latest_sessions(
    connection: Connection,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> Sequence[Row]:
    """Trae la sesión más reciente de cada combinación (proceso, resource).

    Es la consulta principal del monitor: para cada proceso, en cada server
    donde corrió recientemente, devuelve su última sesión con su
    ``statusid`` crudo, horarios y motivo de terminación si aplica.

    Usa ``ROW_NUMBER() OVER (PARTITION BY processid, resourceid ...)`` para
    quedarse solo con la fila más reciente de cada combinación, filtrando
    primero por ``lookback_hours`` (antes de particionar) para no escanear
    el historial completo de sesiones — importante en BD productivas donde
    BPASession puede tener millones de filas.

    Nota: usa ``GETUTCDATE()``. Si tu instalación guarda ``startdatetime``
    en hora local del server en vez de UTC, cambia esto por ``GETDATE()``
    (confírmalo comparando una fila reciente contra la hora real de esa
    corrida en Control Room).
    """
    sql = """
        WITH sesiones_recientes AS (
            SELECT
                s.sessionid,
                s.processid,
                p.name AS process_name,
                s.resourceid,
                r.name AS resource_name,
                s.statusid,
                s.startdatetime,
                s.enddatetime,
                s.terminationreason,
                ROW_NUMBER() OVER (
                    PARTITION BY s.processid, s.resourceid
                    ORDER BY s.startdatetime DESC
                ) AS rn
            FROM BPASession s
            INNER JOIN BPAProcess p ON p.processid = s.processid
            INNER JOIN BPAResource r ON r.resourceid = s.resourceid
            WHERE s.startdatetime >= DATEADD(HOUR, :lookback_negativo, GETUTCDATE())
        )
        SELECT
            sessionid,
            processid,
            process_name,
            resourceid,
            resource_name,
            statusid,
            startdatetime,
            enddatetime,
            terminationreason
        FROM sesiones_recientes
        WHERE rn = 1
        ORDER BY process_name, resource_name
    """
    return _select(connection, sql, lookback_negativo=-abs(lookback_hours))


@db_retry
def _execute_stmt(
    connection: Connection, stmt: TextClause, params: Mapping[str, object]
) -> Sequence[Row]:
    """Como :func:`_execute`, pero recibe un ``TextClause`` ya construido en
    vez de armarlo desde un string. Necesario para consultas con
    ``bindparam(..., expanding=True)`` (listas variables en un ``IN``), que
    no se pueden pasar como el ``sql: str`` que espera :func:`_select`.
    """
    result = connection.execute(stmt, params)
    return result.mappings().all()


def get_last_session_time_for_resources(
    connection: Connection, resource_ids: Sequence[int]
) -> Sequence[Row]:
    """Última actividad histórica (sin ventana de tiempo) de un subconjunto
    puntual de resources.

    Se usa solo para los resources que :func:`get_latest_sessions` ya
    marcó sin actividad reciente (ver services/process_monitor.py, paso 5),
    para poder reportar "sin corridas desde tal hora" en vez de un
    booleano plano. Deliberadamente NO se corre sin filtro para el
    universo completo de resources en cada corrida: sería un full scan de
    BPASession, que puede tener millones de filas en un ambiente
    productivo con este volumen (12 runtimes trabajando 24/7).
    """
    if not resource_ids:
        return []
    stmt = text(
        """
        SELECT
            resourceid,
            MAX(startdatetime) AS last_start_time
        FROM BPASession
        WHERE resourceid IN :resource_ids
        GROUP BY resourceid
        """
    ).bindparams(bindparam("resource_ids", expanding=True))
    return _execute_stmt(connection, stmt, {"resource_ids": tuple(resource_ids)})
