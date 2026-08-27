# Changelog

Todos los cambios notables de este proyecto se documentan aquí. El formato
sigue [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) y el proyecto
usa [SemVer](https://semver.org/) para las versiones etiquetadas en git.

## [Unreleased]

## [1.1.0] - 2026-08-28

Segunda pasada de preparación para publicación: el repo pasa de "privado
y usable por el autor" a "genéricamente instalable y entendible por
cualquiera". README y documentación de cara al usuario (`docs/DIAGRAMS.md`,
`docs/KB_CONVENTION.md`, los 7 diagramas, los `.env.example`, `deploy/
litellm.config.yaml`) reescritos en inglés — el resto del proyecto
(comentarios de código, este CHANGELOG, el resto de docs internos) sigue
en español, decisión explícita del usuario para no traducir cientos de
comentarios sin necesidad real.

### Added
- `LICENSE` (MIT) y metadatos de empaquetado en `pyproject.toml`
  (`license`, `readme`, `[project.urls]`) — el repo no tenía licencia.
- `docs/logo.svg`: logo propio (nodo central + 4 nodos conectados,
  representando el orquestador y la granja de modelos). Se intentó
  primero generarlo con Gemini (`gemini-3.1-flash-image` y variantes) vía
  su API directa — bloqueado con cuota 0 en el free tier de esta cuenta
  para todo modelo de generación de imágenes (mismo patrón que Gemini Pro/
  embeddings/TTS ya documentado); tampoco había ningún modelo de imagen
  gratuito en OpenRouter. SVG propio como alternativa razonable: escala
  sin pérdida, no depende de infraestructura externa.
- `skill/SKILL.md` y `skill/CLAUDE.md`: versión portable (inglés, sin rutas
  ni IPs específicas del autor) de la skill global y la guía de uso — antes
  solo existían localmente en `~/.claude/`, fuera del repo, inútiles para
  cualquier otra persona que clonara el proyecto.
- Sección "Set up with an AI coding agent" en el README: un prompt
  autocontenido que cualquiera puede pegarle a su propio agente para que
  clone, instale, despliegue el gateway, registre el MCP e instale la
  skill — sin pedirle credenciales, solo pidiéndoselas al humano.
- `.github/workflows/tests.yml`: CI que corre la suite de pytest en cada
  push/PR — el repo no tenía ninguna comprobación automática.
- Sección "Architecture" en el README con dos diagramas (componentes del
  sistema, pipeline de `team_feature`), y `docs/DIAGRAMS.md` con los otros
  5 (`team_task`, `team_epic`, `team_ask`, `team_validate`, `docs_sync`).
  SVG propio hecho a mano, no Mermaid — los diagramas Mermaid iniciales
  resultaron poco fiables/feos en varios visores.
- `tests/test_readme.py`, `tests/test_diagrams.py`, `tests/test_diagrams_md.py`:
  comprueban que el README no tiene la IP real, que los 7 SVG existen y son
  XML válido con `viewBox`/`role`/`aria-label`, y que `docs/DIAGRAMS.md`
  referencia las 5 tools y las 5 rutas de imagen correctas.
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

### Changed
- README reescrito de cero para audiencia general: quickstart real
  (prerequisitos, clonar, desplegar el propio gateway, configurar,
  registrar el MCP, verificar), tabla de las 5 tools, estructura del
  proyecto, cómo correr los tests — ya no asume que quien lo lee es el
  propio autor con su despliegue ya hecho.
- `deploy/litellm.config.yaml` recortado a una plantilla genérica más
  corta (2 modelos por tier en vez de hasta 8) — la lista completa y
  ajustada de verdad a las cuotas de la cuenta del autor sigue viva en el
  propio servidor (añadida en caliente vía la API de admin de LiteLLM en
  la Fase 13), este archivo es ahora el punto de partida razonable para un
  despliegue nuevo, no un espejo exacto de esa instancia concreta.
- `tier-coder`: 4 modelos gratuitos más de OpenRouter (`poolside/laguna-s-2.1:free`,
  `poolside/laguna-xs-2.1:free`, `minimax/minimax-m3:free`, y el router
  propio de OpenRouter `openrouter/free`, que reparte al azar sobre ~23
  modelos gratis de su catálogo). Motivado por un fallo real diagnosticado
  en vivo: ~2h de llamadas colgándose el timeout completo (120s, cero
  output) porque el pool de solo 4 modelos tenía demasiada concentración
  de riesgo en cualquier backend individual caído. Añadido en caliente vía
  la API de admin de LiteLLM (sin redeploy) y verificado en vivo antes y
  después: 10/10 llamadas reales sanas tras el cambio.

### Fixed
- Anonimizadas todas las apariciones de la IP privada real del gateway
  (`203.0.113.10`) en `README.md`, `.env.example`, `deploy/.env.example`.
  El default de `TEAM_GATEWAY_URL` en `config.py` pasa de esa IP a
  `http://localhost:4000`, un default genérico razonable para código
  público en vez del valor específico del autor original.
- **Bug real encontrado usando el propio `team` sobre este mismo repo**:
  `engine/consensus.py::run_consensus` no capturaba `EditConflict` al
  materializar los edits de cada candidato en la matriz N×N — un solo
  candidato cuyo `search` no encajara limpio (ambiguo o inexistente)
  abortaba TODO `team_feature` sin manifiesto, en vez de descartar solo esa
  celda. Reproducido en vivo contra un README real con frases repetidas
  (`el bloque "search" no aparece exactamente una vez (apariciones=5)`).

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

[Unreleased]: https://github.com/Ki-re/Team/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/Ki-re/Team/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Ki-re/Team/releases/tag/v1.0.0
