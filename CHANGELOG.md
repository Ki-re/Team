# Changelog

Todos los cambios notables de este proyecto se documentan aquí. El formato
sigue [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) y el proyecto
usa [SemVer](https://semver.org/) para las versiones etiquetadas en git.

## [Unreleased]

### Added
- `update_docs`/`kb_path` opcionales en `team_feature`/`team_epic`: tras un
  cambio de código exitoso, un subagente de documentación (`docs_sync`)
  decide qué archivos de un knowledge-base en markdown (frontmatter +
  `INDEX.md`, misma convención que la memoria de Claude — ver
  `docs/KB_CONVENTION.md`) quedaron desactualizados y los actualiza. Dos
  pasadas (selección barata sobre el índice, luego edición con contenido
  real por archivo, con reintento) — una primera versión de una sola
  pasada falló en la primera prueba en vivo porque el modelo no tenía
  texto real que copiar para el bloque `search`. Solo actualiza entradas
  existentes, nunca crea nuevas en esta versión.
- `team_validate`: cuando `scope` es un directorio con `INDEX.md`, añade
  chequeos deterministas y gratuitos de frontmatter YAML inválido
  (bloqueante), links relativos rotos y entradas con `last_verified`
  vencido (ambos informativos).
- `engine/frontmatter.py`: parseo de frontmatter y utilidades de KB
  (índice barato, detección de links rotos, staleness), reutilizado por
  `docs_sync` y `team_validate`.

## [1.0.0] - 2026-08-27

Primera versión pública. Las 5 tools MCP (`team_task`, `team_feature`,
`team_epic`, `team_ask`, `team_validate`) están completas, sin stubs, y
verificadas en vivo contra el gateway real — ver `.claude/plans` (no
versionado, es estado de desarrollo local) para el detalle fase a fase.

### Added
- Núcleo MCP (`server.py`, `router.py`, `sandbox.py`, `ledger.py`,
  `schemas.py`) con jaula de rutas, telemetría en SQLite y resolución de
  `agy` (Antigravity CLI) para el tier premium.
- Primitivas de calidad: verificación determinista (`verify.py`), consenso
  por validación cruzada N×N (`consensus.py`), crítica adversarial con
  filtro anti-falso-positivo (`critic.py`), bucle de reparación acotado con
  detección de estancamiento y escalada a `agy` (`repair.py`).
- `team_task`: cambio de un archivo sin ambigüedad, con auto-escalada a
  `team_feature` si el gate determinista falla dos veces.
- `team_feature`: fan-out + consenso + crítica + reparación, con
  `kind ∈ {new, refactor, fix, review}`. `refactor` preserva comportamiento
  vía tests de caracterización; `fix` exige un `repro_command` real
  (rojo→verde, no un test que el modelo se inventa).
- `team_ask`: preguntas sobre código/logs vía map-reduce en `tier-context`,
  con verificación de citas `ruta:línea` y búsqueda web opcional (Tavily,
  vía el MCP Gateway de LiteLLM) para contexto externo actual.
- `team_epic`: orquestación de un DAG de nodos por oleadas topológicas en
  paralelo, con presupuesto de tokens real y parada limpia al agotarse.
- `team_validate`: veredicto GO/NO-GO (sintaxis, tests, secretos, git,
  lint, trazabilidad de requisitos, revisión de arquitectura) y modo
  `selftest` que audita la salud de los 4 tiers.
- Registro global del MCP (`claude mcp add --scope user`) y skill global
  (`~/.claude/skills/team/`) para que cualquier sesión/proyecto lo descubra.
- Suite de tests unitarios (`tests/unit/`, pytest) sobre toda la lógica
  determinista/local; los caminos con modelos en vivo se verifican
  manualmente contra el gateway real, no en la suite.
- Chequeo de salud programado semanalmente (`selftest`) vía scheduled task.

### Fixed
- `extract_json`: regex greedy que podía cruzar un array suelto por encima
  de un objeto JSON real; y falta de defensa contra bloques `<think>` de
  modelos razonadores corrompiendo el parseo.
- Fan-out de `team_feature`: los fallos de cada worker (timeout, 429, JSON
  roto) quedaban indistinguibles bajo un mismo mensaje genérico; ahora se
  propaga y reporta el error real de cada uno.
- Ledger: la columna `note` nunca se rellenaba en los fallos ni en las
  degradaciones silenciosas de `agy` a su fallback; ahora sí, verificado
  en vivo forzando un error real contra el gateway.
- `budget=0` en `team_epic` se trataba como "no especificado" (falsy) en
  vez de forzar el corte inmediato pedido.
- `Sandbox.workdir_copy`: código muerto, mal indentado y sin ningún caller,
  eliminado en vez de resucitado.
- Roster de proveedores mantenido al día con la realidad de las cuotas
  gratuitas reales (Cerebras dado de baja tras confirmar 402 en toda la
  cuenta; roster de Gemini reconstruido a partir del panel de cuotas real
  del usuario, no de suposiciones).

[Unreleased]: https://github.com/Ki-re/Team/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Ki-re/Team/releases/tag/v1.0.0
