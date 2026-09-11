# bp-process-monitor

Monitorea los procesos de Blue Prism en la base de datos productiva (qué procesos existen,
en qué server corren y su estado) y reporta ese estado a Smartsheet de forma periódica.

## Estado del proyecto

En construcción — ver `docs/architecture.md` para el diseño y el plan de trabajo por pasos.

## Estructura

```
src/bp_process_monitor/
├── config.py           # Carga y valida configuración (pydantic-settings)
├── logging_config.py   # Logging estructurado con rotación
├── db/
│   ├── connection.py   # Engine SQLAlchemy de solo lectura hacia la BD de BP
│   └── queries.py      # Consultas SQL parametrizadas
├── models.py            # Modelos de datos (ProcessStatus, ServerInfo, ...)
├── services/
│   ├── process_monitor.py  # Orquesta: BD -> reporte
│   └── smartsheet_sync.py  # Reporte -> Smartsheet (con reintentos)
└── main.py               # Punto de entrada: una corrida completa
```

## Requisitos

- Python 3.11+
- Driver ODBC de SQL Server instalado en el servidor (`ODBC Driver 17` o superior)
- Un usuario de base de datos con permisos de **solo lectura** sobre las tablas de Blue Prism
- Una API key de Smartsheet con permiso de escritura sobre el sheet de destino

## Instalación (desarrollo local)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
copy .env.example .env          # y completa los valores reales
```

## Ejecución

```bash
bp-process-monitor
```

## Pruebas

```bash
pytest
```

La suite excluye por defecto las pruebas marcadas `integration` (las que
necesitan una BD de Blue Prism real). Para correrlas explícitamente:

```bash
pytest -m integration
```

Cada corrida imprime el reporte de cobertura de `bp_process_monitor` en
consola (`--cov-report=term-missing`, configurado en `pyproject.toml`).

## Logging

Cada corrida escribe en `logs/bp_process_monitor.log` (carpeta configurable
con `LOG_DIR`). La rotación es semanal -- los domingos a medianoche -- y se
conservan `LOG_BACKUP_WEEKS` semanas de historial (por defecto 4, ~1 mes).
El nivel se controla con `LOG_LEVEL` (por defecto `INFO`).

## Seguridad

- Ninguna credencial vive en el código ni en git — todo llega por variables de entorno.
- El acceso a la BD de Blue Prism es exclusivamente de lectura.
- Ver `.env.example` para la lista de variables requeridas.
