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


# los modelos "razonadores" (algunos de tier-coder/tier-context vía
# OpenRouter) a veces filtran su cadena de pensamiento envuelta en estas
# etiquetas ANTES del JSON pedido. Si ese bloque contiene aunque sea una
# sola llave suelta (frecuente: el modelo "piensa en voz alta" sobre la
# forma del JSON que va a producir), el regex greedy de abajo la toma como
# inicio del match y cruza hasta el `}` real final, produciendo un slice
# inválido — visto fallar en producción (reportado como "backend leaking
# <think> tags"). Se recortan enteros antes de buscar JSON.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>|<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)


def extract_json(raw: str) -> dict | list:
    raw = _THINK_BLOCK_RE.sub("", raw)
    obj_match = re.search(r"\{.*\}", raw, re.DOTALL)
    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    candidates = [m.group(0) for m in (obj_match, arr_match) if m]
    if not candidates:
        raise JsonExtractionError(f"sin JSON en la respuesta del modelo: {raw[:200]}")

    # el regex es greedy: si hay un array suelto ANTES de un objeto real (o
    # viceversa), el match del array se extiende hasta el último `]` del
    # texto, cruzando por encima del objeto y produciendo un slice inválido
    # (visto fallar en tests). Por eso no basta con "el más largo" a ciegas:
    # se intenta parsear cada candidato y solo se compara longitud entre los
    # que de verdad son JSON válido.
    parsed: list[tuple[str, object]] = []
    last_error: Exception | None = None
    for text in candidates:
        try:
            parsed.append((text, json.loads(text)))
        except json.JSONDecodeError as exc:
            last_error = exc

    if not parsed:
        raise JsonExtractionError(
            f"JSON encontrado pero no parsea ({last_error}): {raw[:200]}"
        )

    _, best_value = max(parsed, key=lambda pair: len(pair[0]))
    return best_value


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
