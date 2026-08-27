from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_DIAGRAMS_DIR = Path(__file__).resolve().parent.parent / "docs" / "diagrams"
_EXPECTED = [
    "architecture.svg",
    "team_feature_pipeline.svg",
    "team_task.svg",
    "team_epic.svg",
    "team_ask.svg",
    "team_validate.svg",
    "docs_sync.svg",
]


@pytest.mark.parametrize("name", _EXPECTED)
def test_diagram_exists_and_is_valid_svg(name):
    path = _DIAGRAMS_DIR / name
    assert path.exists(), f"falta el diagrama {name}"

    root = ET.parse(path).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox"), f"{name} no tiene viewBox"
    assert root.get("role") == "img", f"{name} deberia tener role=img"
    assert root.get("aria-label"), f"{name} no tiene aria-label"
