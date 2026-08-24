"""Crítica adversarial (primitiva #3 del plan).

Rúbrica fija, no "revisa esto". El crítico corre en tier-premium (agy o su
fallback), de familia de modelo distinta a tier-coder, para no correlacionar
errores. El filtro anti-falso-positivo real vive en el schema: CriticFinding
exige `failure_scenario`, así que un hallazgo sin escenario concreto ni
siquiera pasa la validación pydantic — se descarta en el parseo, no después.
"""

from __future__ import annotations

from team_mcp.engine.jsonio import parse_or_repair
from team_mcp.engine.schemas import CriticReport, FileEdit

_RUBRIC = """\
Eres un revisor de código adversarial. Tu trabajo es encontrar problemas
reales, no elogiar el código. Evalúa ÚNICAMENTE estos criterios:

1. correctness: ¿hace lo que la spec pide, en todos los casos, no solo el feliz?
2. edge_cases: ¿entradas vacías, nulas, límites, tipos inesperados?
3. security: ¿inyección, path traversal, deserialización insegura, secretos?
4. contract_adherence: ¿respeta las APIs/convenciones del código circundante?
5. simplicity: ¿hay complejidad injustificada para lo que pide la spec?

Spec original:
{spec}

Código a revisar:
{code}

Para CADA hallazgo, debes poder describir un `failure_scenario` CONCRETO:
entradas específicas que producen una salida incorrecta o un crash. Si no
puedes construir ese escenario, NO reportes el hallazgo — una sospecha vaga
no cuenta.

Responde ÚNICAMENTE con JSON con esta forma exacta (lista vacía si no hay
hallazgos reales):
{{"findings": [
  {{"severity": "low|medium|high|critical",
    "file": "<ruta>",
    "line": <número o null>,
    "claim": "<qué está mal, en una frase>",
    "failure_scenario": "<entrada concreta -> salida/comportamiento incorrecto>",
    "suggested_fix": "<opcional>"}}
]}}
"""


def _render_code(edits: list[FileEdit]) -> str:
    return "\n\n".join(f"--- {e.path} ---\n{e.replace}" for e in edits)


async def review(router, workflow: str, spec: str, edits: list[FileEdit]) -> CriticReport:
    prompt = _RUBRIC.format(spec=spec, code=_render_code(edits))
    raw = await router.premium_review(workflow, prompt)
    return await parse_or_repair(raw, CriticReport, router, workflow)
