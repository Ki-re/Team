from __future__ import annotations


def test_readme_no_real_gateway_ip():
    with open('README.md', 'r', encoding='utf-8') as f:
        content = f.read()
    assert '203.0.113.10' not in content
    assert '<gateway-host>' in content


def test_readme_architecture_section():
    with open('README.md', 'r', encoding='utf-8') as f:
        content = f.read()
    assert '## Architecture' in content
    assert '### Components' in content
    assert 'team_feature` pipeline' in content
    assert 'docs/diagrams/architecture.svg' in content
    assert 'docs/diagrams/team_feature_pipeline.svg' in content


def test_readme_structure_order():
    with open('README.md', 'r', encoding='utf-8') as f:
        content = f.read()
    pos_arch = content.find('## Architecture')
    pos_tools = content.find('## The five tools')
    assert pos_arch != -1 and pos_tools != -1
    assert pos_arch < pos_tools
