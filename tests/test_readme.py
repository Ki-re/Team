from __future__ import annotations


def test_readme_anonymization():
    with open('README.md', 'r', encoding='utf-8') as f:
        content = f.read()
    assert '203.0.113.10' not in content
    assert '<ip-del-servidor>' in content
    assert '<usuario>@' in content

def test_readme_architecture_section():
    with open('README.md', 'r', encoding='utf-8') as f:
        content = f.read()
    assert '## Arquitectura' in content
    assert '### Componentes' in content
    assert '### Pipeline de team_feature' in content
    assert '```mermaid' in content

def test_readme_structure_order():
    with open('README.md', 'r', encoding='utf-8') as f:
        content = f.read()
    pos_arch = content.find('## Arquitectura')
    pos_estado = content.find('## Estado')
    assert pos_arch < pos_estado
    assert pos_arch != -1 and pos_estado != -1