from __future__ import annotations

from pathlib import Path

from team_mcp.engine.frontmatter import (
    check_dangling_links,
    check_stale,
    find_local_links,
    list_kb_entries,
    split_frontmatter,
)


def test_split_frontmatter_parses_valid_block():
    text = "---\nname: foo\ndescription: bar\n---\n\nCuerpo del documento.\n"
    fm, body = split_frontmatter(text)
    assert fm == {"name": "foo", "description": "bar"}
    assert body == "\nCuerpo del documento.\n"


def test_split_frontmatter_returns_none_without_leading_dashes():
    fm, body = split_frontmatter("Solo texto normal.\n")
    assert fm is None
    assert body == "Solo texto normal.\n"


def test_split_frontmatter_returns_none_on_invalid_yaml():
    text = "---\nname: [unclosed\n---\ncuerpo\n"
    fm, body = split_frontmatter(text)
    assert fm is None
    assert body == text


def test_split_frontmatter_returns_none_when_not_a_mapping():
    text = "---\n- a\n- b\n---\ncuerpo\n"
    fm, _ = split_frontmatter(text)
    assert fm is None


def test_list_kb_entries_skips_index_and_files_without_frontmatter(tmp_path: Path):
    (tmp_path / "INDEX.md").write_text("- [a](a.md) — desc\n")
    (tmp_path / "a.md").write_text("---\nname: a\ndescription: sobre a\n---\ncontenido\n")
    (tmp_path / "sin_frontmatter.md").write_text("solo texto\n")

    entries = list_kb_entries(tmp_path)

    assert len(entries) == 1
    assert entries[0]["path"] == "a.md"
    assert entries[0]["name"] == "a"
    assert entries[0]["description"] == "sobre a"


def test_list_kb_entries_defaults_name_to_stem_when_missing(tmp_path: Path):
    (tmp_path / "sin_name.md").write_text("---\ndescription: algo\n---\ncontenido\n")
    entries = list_kb_entries(tmp_path)
    assert entries[0]["name"] == "sin_name"


def test_find_local_links_ignores_urls_and_anchors_and_mailto():
    text = (
        "[externo](https://example.com/x) "
        "[ancla](#seccion) "
        "[correo](mailto:a@b.com) "
        "[interno](otros/tema.md) "
        "[con ancla](otros/tema.md#seccion)"
    )
    assert find_local_links(text) == ["otros/tema.md", "otros/tema.md"]


def test_check_dangling_links_detects_broken_relative_target(tmp_path: Path):
    (tmp_path / "a.md").write_text("ver [b](b.md) y [roto](no_existe.md)\n")
    (tmp_path / "b.md").write_text("contenido de b\n")

    broken = check_dangling_links(tmp_path)

    assert len(broken) == 1
    assert "no_existe.md" in broken[0]


def test_check_dangling_links_clean_when_all_targets_exist(tmp_path: Path):
    (tmp_path / "a.md").write_text("ver [b](b.md)\n")
    (tmp_path / "b.md").write_text("contenido\n")
    assert check_dangling_links(tmp_path) == []


def test_check_stale_flags_missing_and_old_last_verified():
    entries = [
        {"path": "sin_fecha.md"},
        {"path": "vieja.md", "last_verified": "2020-01-01"},
        {"path": "reciente.md", "last_verified": "2026-08-01"},
        {"path": "invalida.md", "last_verified": "no-es-una-fecha"},
    ]
    stale = check_stale(entries, max_age_days=180)
    stale_paths = {s.split(":")[0] for s in stale}
    assert stale_paths == {"sin_fecha.md", "vieja.md", "invalida.md"}
