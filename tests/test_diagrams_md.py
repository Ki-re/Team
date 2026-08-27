"""Tests for docs/DIAGRAMS.md.

These tests verify that docs/DIAGRAMS.md exists, is non-empty, references
 each of the 5 graduated tools by name, and embeds the 5 workflow SVG
diagrams that live under docs/diagrams/ using the relative path
`diagrams/<name>.svg` (because DIAGRAMS.md itself lives in the docs/
folder)."""

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DIAGRAMS_MD = REPO_ROOT / "docs" / "DIAGRAMS.md"


TOOL_NAMES = [
    "team_task",
    "team_epic",
    "team_ask",
    "team_validate",
    "docs_sync",
]

# (diagram filename used in the <img src=...> tag, width attribute)
DIAGRAM_REFS = [
    ("diagrams/team_task.svg", "620"),
    ("diagrams/team_epic.svg", "660"),
    ("diagrams/team_ask.svg", "660"),
    ("diagrams/team_validate.svg", "660"),
    ("diagrams/docs_sync.svg", "660"),
]


@pytest.fixture(scope="module")
def diagrams_text() -> str:
    assert DIAGRAMS_MD.is_file(), f"Expected file at {DIAGRAMS_MD}"
    return DIAGRAMS_MD.read_text(encoding="utf-8")


def test_diagrams_md_exists_and_is_non_empty(diagrams_text: str) -> None:
    assert diagrams_text.strip(), "docs/DIAGRAMS.md must not be empty"


@pytest.mark.parametrize("tool_name", TOOL_NAMES)
def test_diagrams_md_mentions_tool(diagrams_text: str, tool_name: str) -> None:
    assert tool_name in diagrams_text, (
        f"docs/DIAGRAMS.md should mention the tool {tool_name!r}"
    )


@pytest.mark.parametrize("src,width", DIAGRAM_REFS)
def test_diagrams_md_embeds_each_svg(diagrams_text: str, src: str, width: str) -> None:
    expected_tag = f'<img src="{src}"'
    assert expected_tag in diagrams_text, (
        f"docs/DIAGRAMS.md should contain an <img> tag with src={src!r}"
    )
    assert f'width="{width}"' in diagrams_text, (
        f"docs/DIAGRAMS.md should declare width=\"{width}\" for {src}"
    )


def test_diagrams_md_uses_relative_diagrams_path(diagrams_text: str) -> None:
    # DIAGRAMS.md lives in docs/, so the relative path to the SVGs is
    # "diagrams/...", NOT "docs/diagrams/..."
    assert "docs/diagrams/" not in diagrams_text, (
        "docs/DIAGRAMS.md must use the relative path 'diagrams/...', "
        "not 'docs/diagrams/...' (the file already lives inside docs/)."
    )
    assert "diagrams/team_task.svg" in diagrams_text
    assert "diagrams/docs_sync.svg" in diagrams_text


def test_diagrams_md_has_title_and_back_link(diagrams_text: str) -> None:
    assert "# Diagramas" in diagrams_text, (
        "docs/DIAGRAMS.md should start with a top-level title starting "
        "with '# Diagramas'"
    )
    assert "README" in diagrams_text, (
        "docs/DIAGRAMS.md should include a back-reference to the README."
    )