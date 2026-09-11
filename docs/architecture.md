# Arquitectura

## Objetivo

Consultar la base de datos productiva de Blue Prism para obtener la lista de
procesos, en qué server corren y su estado, y reportar ese estado a
Smartsheet de forma periódica (cada N minutos).

## Principios de diseño

- **Solo lectura hacia Blue Prism.** El usuario de base de datos que use este
  proyecto tiene permisos `SELECT` únicamente. Nunca se escribe en la BD de BP.
- **Sin secretos en el código ni en git.** Toda credencial llega por variables
  de entorno, validadas al arrancar el proceso (falla rápido si falta algo).
- **Separación de capas**: configuración, acceso a datos, lógica de negocio,
  integración externa (Smartsheet) y orquestación (main.py) son módulos
  independientes y testeables por separado.
- **Idempotencia**: una corrida fallida debe poder reintentarse sin duplicar
  datos ni dejar el sheet de Smartsheet en un estado inconsistente.

## Componentes

| Módulo | Responsabilidad |
|---|---|
| `config.py` | Cargar y validar configuración desde variables de entorno |
| `db/connection.py` | Engine SQLAlchemy de solo lectura hacia la BD de BP |
| `db/queries.py` | Consultas SQL parametrizadas |
| `models.py` | Estructuras de datos del dominio (`RunStatus`, `ProcessStatusRecord`, `ResourceActivityRecord`, `StatusReport`) |
| `services/process_monitor.py` | BD → reporte de estado: traduce `statusid` con `STATUS_MAP`, detecta runtimes sin actividad reciente |
| `services/smartsheet_sync.py` | reporte → Smartsheet (con reintentos) |
| `main.py` | Orquesta una corrida completa |

## Alcance de datos

- [x] Columnas exactas necesarias de `BPAProcess` — `processid`, `name`, `attributeid` (ver `db/queries.py`)
- [x] Columnas exactas necesarias de `BPASession` — `sessionid`, `processid`, `resourceid`, `statusid`, `startdatetime`, `enddatetime`, `terminationreason`
- [x] Columnas exactas necesarias de `BPAResource` — `resourceid`, `name`, `attributeid`
- [ ] Estructura de columnas esperada en el sheet de Smartsheet (paso 6)

**Nota sobre interpretación de estados (cerrado en el paso 5):** `db/queries.py`
expone `statusid` (BPASession) *sin traducir*; la traducción vive en
`services/process_monitor.STATUS_MAP`. Se confirmó contra la BD productiva
de Telmex cruzando `SELECT DISTINCT statusid FROM BPASession` con el patrón
de `enddatetime`/`terminationreason` por statusid:

| statusid | Estado          | Evidencia |
|---|---|---|
| 1 | Running    | 100% sin `enddatetime` (sesión en curso); volumen = cantidad de runtimes con corrida activa al momento de la consulta |
| 2 | Terminated | 100% con `terminationreason` |
| 3 | Stopped    | volumen bajo, con `enddatetime` pero sin motivo (paro manual) |
| 4 | Completed  | el más común; con `enddatetime` y sin motivo (resultado esperado normal) |
| 5 | Warning    | mezcla de sesiones en curso y ya terminadas, sin motivo de terminación |

Confianza alta para 1, 2 y 4 por el patrón inequívoco de los datos; 3 y 5
se infirieron por patrón y quedan pendientes de una confirmación visual
puntual en Control Room (abrir una sesión de cada uno) antes de confiar el
mapeo en producción — es el estado 5 (Warning) el que dispara la alerta de
Smartsheet del paso 6, así que vale la pena esa verificación.

**Decisión de diseño (paso 5): "sin actividad" se evalúa por runtime, no por
proceso.** Los 12 runtimes de Telmex operan 24/7, así que un runtime sin
ninguna sesión en la ventana de lookback es en sí mismo la señal de alerta
(desconexión, scheduler caído), independientemente de qué proceso debería
correr ahí. Cruzar cada proceso contra cada runtime habría generado falsos
positivos (un proceso puede legítimamente no tener trabajo en cierta
ventana). Para los runtimes detectados sin actividad reciente,
`get_last_session_time_for_resources()` trae su última actividad histórica
(sin filtro de ventana), para poder reportar "sin corridas desde tal hora"
en vez de un booleano plano — evitando además un full scan de `BPASession`
en cada corrida para los runtimes que sí están activos.

## Plan de trabajo

1. Definir alcance y diseño ✅ (columnas confirmadas arriba; falta estructura del sheet de Smartsheet, se cierra en el paso 6)
2. Inicializar repo y flujo de Git ✅
3. Configuración y manejo de secretos ✅
4. Capa de acceso a datos (BD Blue Prism) ✅
5. Modelos y lógica de negocio ✅ (`models.py` + `services/process_monitor.py`; ver nota de interpretación de estados arriba)
6. Integración con Smartsheet
7. Logging y pruebas
8. Programar la ejecución y desplegar

## Ejecución en producción

Por decidir entre:
- **Windows Task Scheduler** ejecutando `bp-process-monitor` como una corrida
  única — más simple de auditar, recomendado como punto de partida.
- **Proceso persistente con APScheduler** (o como Windows Service) — útil si
  más adelante se necesita un healthcheck HTTP o estado en memoria.
