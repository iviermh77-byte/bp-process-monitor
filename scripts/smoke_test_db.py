r"""Script de prueba manual (smoke test) - NO es parte de la suite de pytest.

Objetivo: validar "de punta a punta" que las piezas ya construidas del
proyecto (pasos 3 y 4: config/secretos y capa de BD) funcionan contra la
BD PRODUCTIVA real de Blue Prism, mostrando explicitamente de que archivo
viene cada funcion, que necesita para funcionar y que entrega.

No inventa nada nuevo: solo importa y llama, en orden, las funciones que ya
existen en el proyecto. El siguiente paso (fuera de este script) es tomar
estas mismas filas y pasarlas a services/process_monitor.py + 
services/smartsheet_sync.py para sincronizarlas a Smartsheet.

Es de solo lectura (el usuario de BD configurado en .env es solo-lectura, y
db/queries.py ademas rechaza en codigo cualquier sentencia que no sea
SELECT/WITH), por lo que es seguro correrlo contra el ambiente productivo.

Como correrlo (IMPORTANTE: desde la raiz del repo, para que Settings
encuentre el archivo .env con env_file=".env" relativo):

    cd C:\bp-process-monitor
    .venv\Scripts\python.exe scripts\smoke_test_db.py

Parametro opcional: ventana de horas hacia atras para "sesiones recientes"
(por defecto usa DEFAULT_LOOKBACK_HOURS de db/queries.py):

    .venv\Scripts\python.exe scripts\smoke_test_db.py --lookback-hours 48
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path
from pprint import pformat

# Este script puede invocarse desde cualquier directorio (por ejemplo parado
# dentro de "scripts\" con el venv activado -- fue justo lo que fallo en la
# primera prueba). Settings (config.py) lee ".env" de forma RELATIVA al
# directorio de trabajo actual (CWD), asi que nos posicionamos en la raiz
# del repo -- un nivel arriba de este archivo -- antes de llamar nada de
# bp_process_monitor, para que .env se resuelva igual sin importar desde
# donde se invoque el script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_REPO_ROOT)

# ---------------------------------------------------------------------------
# PASO 1 - Credenciales / configuracion
# ---------------------------------------------------------------------------
# Funcion:  get_settings()
# Archivo:  src/bp_process_monitor/config.py
# Solicita: nada como argumento -- internamente lee las variables de entorno
#           declaradas en la clase Settings (BP_DB_SERVER, BP_DB_NAME,
#           BP_DB_USER, BP_DB_PASSWORD, BP_DB_DRIVER, SMARTSHEET_API_KEY,
#           SMARTSHEET_SHEET_ID, RUN_INTERVAL_MINUTES, LOG_*), tomandolas del
#           archivo ".env" en la raiz del repo (o del entorno del proceso).
#           Si falta una variable requerida, pydantic lanza un error de
#           validacion inmediatamente (fail fast), antes de intentar nada mas.
# Entrega:  una instancia de Settings (pydantic BaseSettings) con los valores
#           ya parseados y tipados. Esta cacheada con @lru_cache: la primera
#           llamada lee/valida el .env, las siguientes devuelven la misma
#           instancia sin releerlo.
from bp_process_monitor.config import get_settings

# ---------------------------------------------------------------------------
# PASO 2 - Conexion a la BD
# ---------------------------------------------------------------------------
# Funcion:  check_connection(engine=None)
# Archivo:  src/bp_process_monitor/db/connection.py
# Solicita: opcionalmente un Engine ya construido; si no se le pasa nada usa
#           get_engine() (tambien de este archivo), que arma el engine de
#           SQLAlchemy (mssql+pyodbc) a partir de get_settings().
# Entrega:  True si la BD respondio a un "SELECT 1"; lanza la excepcion de
#           SQLAlchemy/pyodbc correspondiente si no pudo conectar (credenciales
#           invalidas, server inalcanzable, driver ODBC no instalado, etc).
#
# Funcion:  get_connection(engine=None)
# Archivo:  src/bp_process_monitor/db/connection.py
# Solicita: igual que check_connection -- opcionalmente un Engine.
# Entrega:  un context manager que entrega una Connection de SQLAlchemy ya
#           abierta y se encarga de cerrarla al salir del "with".
from bp_process_monitor.db.connection import check_connection, get_connection

# ---------------------------------------------------------------------------
# PASO 3 - Consultas (capa de BD, datos crudos sin interpretar)
# ---------------------------------------------------------------------------
# Funcion:  get_processes(connection)
# Archivo:  src/bp_process_monitor/db/queries.py
# Solicita: una Connection de SQLAlchemy ya abierta (la que entrega
#           get_connection()).
# Entrega:  Sequence[RowMapping] (lista de filas dict-like) con TODOS los
#           procesos/objetos de BPAProcess: processid, name, attributeid
#           (bitmask crudo, sin traducir a "Proceso"/"Objeto").
#
# Funcion:  get_resources(connection)
# Archivo:  src/bp_process_monitor/db/queries.py
# Solicita: una Connection de SQLAlchemy ya abierta.
# Entrega:  Sequence[RowMapping] con TODOS los resources/runtimes de
#           BPAResource: resourceid, name, attributeid (crudo). No incluye
#           estado online/offline (ver docstring de la funcion en el
#           archivo original: ese estado no es confiable desde BD).
#
# Funcion:  get_latest_sessions(connection, lookback_hours=24)
# Archivo:  src/bp_process_monitor/db/queries.py
# Solicita: una Connection de SQLAlchemy ya abierta, y opcionalmente
#           lookback_hours (int) para limitar la ventana de tiempo que se
#           escanea antes de particionar por (processid, resourceid).
# Entrega:  Sequence[RowMapping] con la sesion MAS RECIENTE de cada
#           combinacion (proceso, resource) dentro de la ventana: sessionid,
#           processid, process_name, resourceid, resource_name, statusid
#           (crudo: 1=Running/2=Terminated/3=Stopped/4=Completed/5=Warning,
#           segun lo ya validado contra esta BD), startdatetime,
#           enddatetime, terminationreason. Esta es la consulta principal
#           que alimentara al monitor (paso 5, no incluido en este script).
from bp_process_monitor.db.queries import (
    DEFAULT_LOOKBACK_HOURS,
    get_latest_sessions,
    get_processes,
    get_resources,
)


def _print_header(titulo: str) -> None:
    print()
    print("=" * 78)
    print(titulo)
    print("=" * 78)


def _print_rows(rows: Sequence[object], max_preview: int = 5) -> None:
    """Imprime el total de filas y una muestra de las primeras `max_preview`."""
    print(f"Total de filas: {len(rows)}")
    if not rows:
        return
    print(f"Muestra (primeras {min(max_preview, len(rows))} de {len(rows)}):")
    for row in rows[:max_preview]:
        # RowMapping es dict-like; dict(row) lo vuelve un dict normal legible.
        print(f"  - {pformat(dict(row), sort_dicts=False)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=DEFAULT_LOOKBACK_HOURS,
        help=f"Horas hacia atras para 'sesiones recientes' (default: {DEFAULT_LOOKBACK_HOURS}, "
        "definido en db/queries.py).",
    )
    args = parser.parse_args()

    # --- PASO 1: obtener credenciales / configuracion -----------------------
    _print_header("PASO 1: Credenciales (config.get_settings)")
    print(f"Directorio de trabajo (raiz del repo): {_REPO_ROOT}")
    settings = get_settings()
    print(f"BP_DB_SERVER: {settings.bp_db_server}")
    print(f"BP_DB_NAME:   {settings.bp_db_name}")
    print(f"BP_DB_USER:   {settings.bp_db_user}")
    # La password NUNCA se imprime en claro, ni en un script de prueba.
    print(f"BP_DB_PASSWORD: {'*' * 8 if settings.bp_db_password else '(vacia)'}")
    print(f"BP_DB_DRIVER: {settings.bp_db_driver}")

    # --- PASO 2: validar conectividad y abrir conexion -----------------------
    _print_header("PASO 2: Conexion (db.connection.check_connection / get_connection)")
    check_connection()
    print("check_connection() OK -- la BD respondio 'SELECT 1'.")

    with get_connection() as connection:
        # --- PASO 3a: BPAProcess ---------------------------------------------
        _print_header("PASO 3a: Procesos/objetos (db.queries.get_processes)")
        processes = get_processes(connection)
        _print_rows(processes)

        # --- PASO 3b: BPAResource --------------------------------------------
        _print_header("PASO 3b: Resources/runtimes (db.queries.get_resources)")
        resources = get_resources(connection)
        _print_rows(resources)

        # --- PASO 3c: BPASession (sesion mas reciente por proceso+resource) --
        _print_header(
            f"PASO 3c: Sesiones recientes, lookback_hours={args.lookback_hours} "
            "(db.queries.get_latest_sessions)"
        )
        sessions = get_latest_sessions(connection, lookback_hours=args.lookback_hours)
        _print_rows(sessions)

    _print_header("Fin del smoke test -- conexion cerrada")
    print(
        "Siguiente paso (fuera de este script): pasar estas filas por "
        "services/process_monitor.build_report() para interpretar statusid y "
        "armar el reporte, y luego por services/smartsheet_sync.sync_report() "
        "para escribirlo en Smartsheet."
    )


if __name__ == "__main__":
    main()
