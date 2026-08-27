# Diagramas de los workflows

Estos son los pipelines internos de las tools graduadas del servidor MCP `Team` (cada una con su subagente, manifiesto y presupuesto de tokens aislado): `team_task`, `team_epic`, `team_ask`, `team_validate` y `docs_sync`. Los componentes generales del proyecto y el pipeline de `team_feature` están en la sección Arquitectura del README principal.

## team_task

`team_task` delega una edición puntual al modelo `tier-coder`, que propone exactamente UN edit en JSON estricto. Antes de aplicarlo, el edit se prueba en un scratch dir: si falla, se reintenta una sola vez pasándole al modelo el error literal; si tras esos 2 intentos sigue fallando, el nodo se marca con `escalated_from=task` en el manifiesto en lugar de escribir algo peor de lo prometido (nunca escala solo silenciosamente).

<img src="diagrams/team_task.svg" alt="Pipeline de team_task" width="620">

## team_epic

`team_epic` recibe un DAG de nodos (p. ej. A y B independientes, C dependiente de ambos) y lo ejecuta por oleadas topológicas con un algoritmo de Kahn: los nodos de cada oleada corren en paralelo vía `team_feature.run()`. Antes de cada oleada se consulta el presupuesto de tokens y, si está agotado, los nodos pendientes se marcan como `skipped` con un corte limpio (sin excepción). Termina emitiendo un manifiesto con `completed` / `failed` / `skipped` y el gasto total acumulado.

<img src="diagrams/team_epic.svg" alt="Pipeline de team_epic" width="660">

## team_ask

`team_ask` trocea la pregunta junto con las rutas proporcionadas, siempre con solape y pasando únicamente rutas (jamás contenido completo) al modelo `tier-context`, que resume cada trozo en paralelo (fase `map`); luego se sintetiza una respuesta final (fase `reduce`). Cada cita `ruta:línea` se verifica de forma determinista contra el repositorio antes de devolver el manifiesto, y lo que no puede verificarse se marca explícitamente como `[no verificado]` en vez de descartarse en silencio. Hay una rama opcional: si `allow_web_search=True`, se habilita Tavily a través del MCP Gateway de LiteLLM (es el único de los 5 tools que hace tool-use en vivo).

<img src="diagrams/team_ask.svg" alt="Pipeline de team_ask" width="660">

## team_validate

`team_validate` corre primero, en paralelo, todas las comprobaciones deterministas y gratuitas (sintaxis, tests, secretos, git, lint, y, si el scope es un directorio de knowledge-base con `INDEX.md`, también frontmatter, links y staleness). Cualquier bloqueante determinista fuerza un veredicto `NO-GO` inmediato **sin** consultar a ningún modelo. Solo si todas pasan y se proporcionó `spec_original` se añaden la trazabilidad de requisitos y la revisión de arquitectura, que aportan avisos (warnings) pero nunca llegan a bloquear el veredicto.

<img src="diagrams/team_validate.svg" alt="Pipeline de team_validate" width="660">

## docs_sync

`docs_sync` es el subagente de documentación, activado por `update_docs=True` y `kb_path` en `team_feature` / `team_epic`, y opera en dos pasadas. La primera es una selección barata sobre el índice del knowledge-base: solo `INDEX.md` + frontmatter de cada tema (sin leer el cuerpo) decide qué archivos podrían estar desactualizados a raíz del cambio. En la segunda, únicamente para esos archivos seleccionados, se lee su contenido real completo y se propone una edición puntual, con un reintento si la edición no encaja. Cada edición se valida de forma determinista (la ruta está dentro de `kb_path`, el frontmatter YAML sigue siendo válido y ningún link relativo queda roto) antes de aplicarse a través del mismo Sandbox que cualquier otro edit. Solo actualiza entradas ya existentes del índice, nunca inventa archivos nuevos, y nunca lanza una excepción aunque todo falle.

<img src="diagrams/docs_sync.svg" alt="Pipeline de docs_sync" width="660">

Ver también la sección Arquitectura en el README principal para los componentes generales y el pipeline de `team_feature`.