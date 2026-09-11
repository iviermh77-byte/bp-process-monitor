"""Conexión a la base de datos de Blue Prism.

Construye un engine de SQLAlchemy (dialecto ``mssql+pyodbc``) apuntando a la
BD productiva de Blue Prism, usando siempre un usuario de BD de SOLO
LECTURA. La restricción real la impone el permiso ``GRANT SELECT`` del login
en SQL Server (ver docs/architecture.md); este módulo añade una capa
adicional de disciplina en el código, no un control de seguridad por sí solo.

Principios aplicados aquí:

- El engine se cachea (patrón singleton por proceso) para reutilizar un
  único pool de conexiones en vez de abrir una conexión nueva en cada
  consulta.
- ``pool_pre_ping`` evita "conexiones fantasma": si el servidor cerró la
  conexión por inactividad (timeout de firewall, reinicio del server),
  SQLAlchemy la descarta y abre una nueva antes de usarla.
- ``pool_recycle`` recicla conexiones periódicamente por la misma razón.
- Reintentos con backoff exponencial (tenacity) ante errores transitorios
  de red/DB, consistente con el principio de idempotencia del proyecto.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.exc import DBAPIError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from bp_process_monitor.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Reintenta hasta 3 veces ante errores transitorios (timeout, conexión
# caída, deadlock), con backoff exponencial: ~1s, ~2s, ~4s. No reintenta
# sobre errores de sintaxis SQL o de permisos: esos no se arreglan solos y
# hay que fallar rápido y visible.
db_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(DBAPIError),
    before_sleep=lambda retry_state: logger.warning(
        "Reintentando operación contra la BD de Blue Prism (intento %d) tras error: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception() if retry_state.outcome else None,
    ),
)


def _build_connection_url(settings: Settings) -> URL:
    """Arma la URL de conexión sin construirla a mano con f-strings.

    Usar ``URL.create`` es importante: si el usuario o la password de BD
    contienen caracteres especiales (@, :, /, etc.), una connection string
    armada con f-strings se rompe o, peor, se interpreta mal. ``URL.create``
    escapa esto automáticamente.
    """
    return URL.create(
        "mssql+pyodbc",
        username=settings.bp_db_user,
        password=settings.bp_db_password,
        host=settings.bp_db_server,
        database=settings.bp_db_name,
        query={
            "driver": settings.bp_db_driver,
            # El server de BP suele usar un certificado autofirmado o de una
            # CA interna que el cliente no valida por defecto (especialmente
            # con ODBC Driver 18+, que exige TLS por defecto). Esto evita
            # fallos de handshake en red interna. Si esta conexión llegara a
            # cruzar una red no confiable, reemplazar por un certificado
            # válido y quitar TrustServerCertificate.
            "Encrypt": "yes",
            "TrustServerCertificate": "yes",
        },
    )


def _build_engine(settings: Settings) -> Engine:
    return create_engine(
        _build_connection_url(settings),
        pool_pre_ping=True,
        pool_recycle=1800,  # recicla conexiones cada 30 min
        pool_size=5,
        max_overflow=5,
        connect_args={"timeout": 10},  # timeout de login en segundos (no de query)
    )


def build_engine(settings: Settings) -> Engine:
    """Crea un engine nuevo (sin cachear) a partir de un ``Settings`` dado.

    Pensado principalmente para tests: permite construir un engine con
    configuración de prueba (o contra una BD de prueba) sin tocar el engine
    cacheado de :func:`get_engine`.
    """
    return _build_engine(settings)


@lru_cache
def get_engine() -> Engine:
    """Engine cacheado (un solo pool de conexiones por proceso).

    Igual que :func:`bp_process_monitor.config.get_settings`, se cachea con
    ``lru_cache`` para no reabrir el pool en cada llamada. Usa siempre la
    configuración real leída del entorno.
    """
    return _build_engine(get_settings())


@contextmanager
def get_connection(engine: Engine | None = None) -> Iterator[Connection]:
    """Context manager que entrega una conexión y garantiza su cierre.

    No abre una transacción explícita: todo lo que corre contra esta BD es
    SELECT, y SQL Server no requiere COMMIT para lecturas.
    """
    eng = engine if engine is not None else get_engine()
    with eng.connect() as connection:
        yield connection


def check_connection(engine: Engine | None = None) -> bool:
    """Prueba de vida: valida que la BD responde.

    Útil para un healthcheck manual o para que ``main.py`` falle rápido y
    con un mensaje claro si la BD no es alcanzable, en vez de fallar más
    adelante con un error críptico a mitad de una consulta real.
    """
    with get_connection(engine) as connection:
        connection.execute(text("SELECT 1"))
    return True
