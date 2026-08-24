"""Extracción/parseo de JSON de respuestas de modelo, con cadena de rescate.

Los modelos pequeños producen JSON roto con frecuencia (primitiva #5 del
plan): parse directo -> si falla, una reparación barata con tier-fast -> si
sigue fallando, error duro. Nunca se reintenta más de una vez: si tier-fast
tampoco puede arreglarlo, el problema es real.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class JsonExtractionError(ValueError):
    pass


def extract_json(raw: str) -> dict | list:
    obj_match = re.search(r"\{.*\}", raw, re.DOTALL)
    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    candidates = [m for m in (obj_match, arr_match) if m]
    if not candidates:
        raise JsonExtractionError(f"sin JSON en la respuesta del modelo: {raw[:200]}")
    # el más largo suele ser el objeto/array real, no un fragmento anidado suelto
    best = max(candidates, key=lambda m: len(m.group(0)))
    return json.loads(best.group(0))


async def parse_or_repair(raw: str, schema: type[T], router, workflow: str) -> T:
    """router: providers.router.Router — se evita el import directo para no
    crear un ciclo (router no depende de engine, pero se mantiene laxo)."""
    try:
        return schema.model_validate(extract_json(raw))
    except (JsonExtractionError, ValidationError, json.JSONDecodeError) as exc:
        repair_prompt = (
            "El siguiente texto debería ser un JSON válido que cumpla este "
            f"esquema (JSON Schema): {schema.model_json_schema()}\n\n"
            f"Texto a corregir:\n{raw}\n\n"
            f"Error de validación: {exc}\n\n"
            "Devuelve ÚNICAMENTE el JSON corregido, sin texto adicional ni markdown."
        )
        fixed_raw = await router.fast(workflow, repair_prompt, temperature=0.0)
        return schema.model_validate(extract_json(fixed_raw))
