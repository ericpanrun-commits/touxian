"""Synthetic files only; fixtures are outside the repository and never uploaded."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.engineering_portfolio import Portfolio, encode, router, safe_root


@pytest.fixture
def portfolio(tmp_path, monkeypatch):
    monkeypatch.delenv('ERIC_PORTFOLIO_ROOT', raising=False)
    root = tmp_path / 'Engineering-Portfolio'
    root.mkdir()
    service = Portfolio(tmp_path / 'settings' / 'portfolio.json', tmp_path / 'repo')
    service.configure(str(root))
    return service


def make_project(root, name='转向节（合成测试）'):
    project = root / name
    project.mkdir()
    for name in ['cover.PNG', '零件.SLDPRT', '装配.SLDASM', '工程图.SLDDRW', '图纸.DWG', '交换.DXF', '模型.STEP', '模型.STP', '工程图.pdf', '另一张.jpg', '照片.jpeg', '忽略.txt']:
        (project / name).write_bytes(('synthetic: ' + name).encode())
    (project / '子目录').mkdir()
    (project / '子目录' / '手绘.png').write_bytes(b'synthetic image')
    return project


def client_for(service, peer='127.0.0.1', base='http://127.0.0.1'):
    app = FastAPI()
    app.state.engineering_portfolio = service
    app.include_router(router)
    return TestClient(app, base_url=base, client=(peer, 51000))


def fingerprints(root):
    return {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


def test_refresh_restart_and_read_only_originals(portfolio):
    root = portfolio.root()
    assert portfolio.scan()['items'] == []
    project = make_project(root)
    before = fingerprints(root)
    client = client_for(portfolio)
    result = client.get('/api/personal/portfolio')
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    item = result.json()['items'][0]
    assert item['title'] == project.name
    assert len(item['files']) == 12
    assert item['cover'] == encode('cover.PNG')
    assert set(item['types']) == {'PNG', 'JPG', 'JPEG', 'PDF', 'SLDPRT', 'SLDASM', 'SLDDRW', 'DWG', 'DXF', 'STEP', 'STP'}
    restarted = Portfolio(portfolio.settings, portfolio.repository)
    assert restarted.scan() == portfolio.scan()
    assert fingerprints(root) == before
    assert not list(root.rglob('*.json'))
    (project / '新增.dwg').write_bytes(b'new')
    assert len(client.get('/api/personal/portfolio').json()['items'][0]['files']) == 13
    (project / '新增.dwg').unlink()
    project.rename(root / '改名后的作品')
    assert client.get('/api/personal/portfolio').json()['items'][0]['title'] == '改名后的作品'
    assert client.post(f'/api/personal/portfolio/projects/{item["id"]}/open').status_code == 404
    (root / '改名后的作品').rename(root.parent / '移出作品集')
    assert client.get('/api/personal/portfolio').json()['items'] == []


def test_preview_serves_original_bytes_and_never_cad(portfolio):
    project = make_project(portfolio.root())
    before = fingerprints(portfolio.root())
    client = client_for(portfolio)
    prefix = f'/api/personal/portfolio/projects/{encode(project.name)}/preview/'
    for name, media in [('cover.PNG', 'image/png'), ('工程图.pdf', 'application/pdf'), ('子目录/手绘.png', 'image/png')]:
        response = client.get(prefix + encode(name))
        assert response.status_code == 200
        assert response.content == (project / name).read_bytes()
        assert response.headers['content-type'] == media
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
    assert client.get(prefix + encode('零件.SLDPRT')).status_code == 415
    assert client.get(prefix + encode('图纸.DWG')).status_code == 415
    assert fingerprints(portfolio.root()) == before


def test_cover_priority_fallback_and_empty_project(portfolio):
    project = make_project(portfolio.root())
    (project / 'cover.PNG').unlink()
    (project / 'cover.jpg').write_bytes(b'cover')
    assert portfolio.scan()['items'][0]['cover'] == encode('cover.jpg')
    (project / 'cover.jpg').unlink()
    assert portfolio.scan()['items'][0]['cover'] is not None
    for path in project.rglob('*'):
        if path.suffix.lower() in {'.png', '.jpg', '.jpeg'}:
            path.unlink()
    assert portfolio.scan()['items'][0]['cover'] is None
    (portfolio.root() / '空作品').mkdir()
    assert next(item for item in portfolio.scan()['items'] if item['title'] == '空作品')['files'] == []


@pytest.mark.parametrize('name', ['../secret.png', '..\\secret.png', '/secret.png', 'C:\\secret.png', 'sub/../../secret.png', 'secret.png:stream', '\x00'])
def test_path_traversal_is_rejected(portfolio, name):
    project = make_project(portfolio.root())
    client = client_for(portfolio)
    response = client.get(f'/api/personal/portfolio/projects/{encode(project.name)}/preview/{encode(name)}')
    assert response.status_code == 400
    assert client.post(f'/api/personal/portfolio/projects/{encode(name)}/open').status_code == 400


def test_repository_roots_and_nested_git_are_rejected(portfolio, tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').write_text('gitdir: elsewhere')
    for path in [repo, repo / 'works']:
        with pytest.raises(HTTPException) as error:
            safe_root(str(path), repo)
        assert error.value.detail['code'] == 'repository_root'
    project = make_project(portfolio.root())
    (project / '.git').mkdir()
    assert portfolio.scan()['items'] == []


def test_linked_files_and_directory_junctions_are_not_followed(portfolio, tmp_path):
    project = make_project(portfolio.root())
    outside = tmp_path / 'private'
    outside.mkdir()
    (outside / 'secret.png').write_bytes(b'private')
    junction = project / 'junction'
    if os.name == 'nt':
        import subprocess
        subprocess.run(['pwsh', '-NoProfile', '-Command', 'New-Item -ItemType Junction -Path $env:ERIC_TEST_LINK -Target $env:ERIC_TEST_TARGET | Out-Null'],
                       env={**os.environ, 'ERIC_TEST_LINK': str(junction), 'ERIC_TEST_TARGET': str(outside)}, check=True)
    else:
        junction.symlink_to(outside, target_is_directory=True)
    result = portfolio.scan()['items'][0]
    assert not any('secret' in item['name'] for item in result['files'])
    assert result['warnings']
    client = client_for(portfolio)
    assert client.get(f'/api/personal/portfolio/projects/{encode(project.name)}/preview/{encode("junction/secret.png")}').status_code == 404
    with pytest.raises(HTTPException):
        portfolio.configure(str(junction))


def test_root_setting_persistence_missing_directory_and_bad_input(portfolio, tmp_path):
    second = tmp_path / 'other-works'
    second.mkdir()
    client = client_for(portfolio)
    assert client.put('/api/personal/portfolio/root', json={'root': str(second)}).status_code == 200
    assert Portfolio(portfolio.settings).root() == second
    assert json.loads(portfolio.settings.read_text()) == {'root': str(second)}
    for root in ['relative', str(second / 'missing'), str(Path(second.anchor))]:
        assert client.put('/api/personal/portfolio/root', json={'root': root}).status_code == 400
    assert client.get('/api/personal/portfolio/projects/!/preview/!').status_code == 400
    assert client.put('/api/personal/portfolio/root', json={'root': ''}).status_code == 422


@pytest.mark.parametrize('peer,base,headers', [
    ('192.168.1.2', 'http://127.0.0.1', {}), ('127.0.0.1', 'http://evil.example', {}),
    ('127.0.0.1', 'http://127.0.0.1', {'origin': 'https://evil.example'}),
    ('127.0.0.1', 'http://127.0.0.1', {'origin': 'null'}),
    ('127.0.0.1', 'http://127.0.0.1', {'sec-fetch-site': 'cross-site'}),
])
def test_loopback_only_for_reads_settings_preview_and_open(portfolio, peer, base, headers):
    client = client_for(portfolio, peer, base)
    for method, path in [('GET', ''), ('PUT', '/root'), ('GET', '/projects/a/preview/b'), ('POST', '/projects/a/open')]:
        assert client.request(method, '/api/personal/portfolio' + path, headers=headers, json={'root': str(portfolio.root())}).status_code == 403


def test_cloud_runtime_refuses_even_loopback(portfolio, monkeypatch):
    monkeypatch.setenv('APP_RUNTIME', 'cloud')
    assert client_for(portfolio).get('/api/personal/portfolio').status_code == 403


def test_open_explorer_uses_exact_project_and_no_shell(portfolio, monkeypatch):
    project = make_project(portfolio.root())
    calls = []
    monkeypatch.setattr(os, 'startfile', lambda *args: calls.append(args), raising=False)
    if os.name != 'nt':
        pytest.skip('Windows Explorer contract')
    response = client_for(portfolio).post(f'/api/personal/portfolio/projects/{encode(project.name)}/open')
    assert response.status_code == 200
    assert calls == [(str(project), 'explore')]


def test_missing_root_and_environment_override(portfolio, tmp_path, monkeypatch):
    monkeypatch.setenv('ERIC_PORTFOLIO_ROOT', str(tmp_path / 'not-created'))
    result = portfolio.scan()
    assert not result['exists'] and not result['items'] and result['managed']
    assert not (tmp_path / 'not-created').exists()
    assert client_for(portfolio).put('/api/personal/portfolio/root', json={'root': str(portfolio.root())}).status_code == 409
