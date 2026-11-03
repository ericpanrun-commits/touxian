"""Launcher ownership, cold/warm readiness and errors; no real browser or Collector."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import desktop_launcher as launcher
from server.desktop_runtime import APP_ID, bundle_ready, health_identity, workspace_id


def bundle(root):
    dist=root/'dist';(dist/'assets').mkdir(parents=True);(dist/'.vite').mkdir()
    (dist/'index.html').write_text('<html><div id="root"></div><script src="/assets/app.js"></script></html>',encoding='utf-8')
    (dist/'assets/app.js').write_text('/* synthetic bundle */',encoding='utf-8')
    (dist/'assets/app.css').write_text('/* synthetic css */',encoding='utf-8')
    (dist/'.vite/manifest.json').write_text(json.dumps({'index.html':{'file':'assets/app.js','css':['assets/app.css']}}),encoding='utf-8')
    return root


@pytest.fixture
def project(tmp_path,monkeypatch):
    root=bundle(tmp_path)
    monkeypatch.setattr(launcher,'local_get',lambda *a,**k:(root/'dist/index.html').read_bytes())
    return root


def test_readiness_requires_entry_and_all_assets(tmp_path):
    assert not bundle_ready(tmp_path)
    root=bundle(tmp_path);assert bundle_ready(root)
    (root/'dist/assets/app.css').unlink()
    assert not bundle_ready(root)


def test_manifest_cannot_claim_an_asset_outside_bundle(tmp_path):
    root=bundle(tmp_path);(root/'outside.js').write_text('private',encoding='utf-8')
    (root/'dist/.vite/manifest.json').write_text(json.dumps({'index.html':{'file':'../outside.js'}}),encoding='utf-8')
    assert not bundle_ready(root)


def test_probe_requires_this_workspace_and_initialized_storage(project,monkeypatch):
    data={'application':APP_ID,'workspace_id':workspace_id(project),'process_id':123,'frontend_ready':True,'personal_storage':{'initialized':True}}
    monkeypatch.setattr(launcher,'local_get',lambda *a:json.dumps(data).encode())
    monkeypatch.setattr(launcher.socket,'create_connection',lambda *a,**k:__import__('contextlib').nullcontext())
    assert launcher.probe(project)['state']=='ready'
    data['personal_storage']['initialized']=False
    assert launcher.probe(project)['state']=='starting'
    data['workspace_id']='other-project'
    assert launcher.probe(project)['state']=='other'


def test_closed_windows_loopback_timeout_is_stopped_but_slow_http_is_starting(monkeypatch):
    def timeout(*a,**k):raise TimeoutError('synthetic connection timeout')
    monkeypatch.setattr(launcher.socket,'create_connection',timeout)
    assert launcher.probe()['state']=='stopped'
    monkeypatch.setattr(launcher.socket,'create_connection',lambda *a,**k:__import__('contextlib').nullcontext())
    monkeypatch.setattr(launcher,'local_get',timeout)
    assert launcher.probe()['state']=='starting'


def test_running_service_is_reused_and_opens_today(project):
    result=launcher.ensure_ready(project,check=lambda *a:{'state':'ready','process_id':123},start=lambda *a:pytest.fail('must reuse the running Collector'))
    assert result=={'url':'http://127.0.0.1:8765/?personal_view=today','mode':'reused','process_id':123}


def test_cold_start_waits_for_readiness_without_duplicate_starts(project):
    states=iter([{'state':'stopped'},{'state':'starting'},{'state':'ready','process_id':456}]);started=[]
    def start(*a):started.append(a);return SimpleNamespace(poll=lambda:None)
    result=launcher.ensure_ready(project,check=lambda *a:next(states),start=start,pause=lambda _:None)
    assert result['mode']=='started' and result['process_id']==456 and len(started)==1


def test_existing_slow_service_is_waited_on_not_restarted(project):
    states=iter([{'state':'starting'},{'state':'ready','process_id':321}])
    result=launcher.ensure_ready(project,check=lambda *a:next(states),start=lambda *a:pytest.fail('no competing backend'),pause=lambda _:None)
    assert result['mode']=='reused'


def test_port_conflict_does_not_start_or_stop_any_process(project):
    with pytest.raises(launcher.LaunchError) as e:
        launcher.ensure_ready(project,check=lambda *a:{'state':'other'},start=lambda *a:pytest.fail('occupied port'))
    assert e.value.code=='address_in_use'


def test_failed_child_has_readable_error_and_no_respawn(project):
    started=[]
    def start(*a):started.append(1);return SimpleNamespace(poll=lambda:1)
    with pytest.raises(launcher.LaunchError) as e:launcher.ensure_ready(project,check=lambda *a:{'state':'stopped'},start=start)
    assert e.value.code=='startup_failed' and len(started)==1


def test_timeout_is_bounded_and_never_kills_existing_collector(project):
    clock=iter([0,1,3])
    with pytest.raises(launcher.LaunchError) as e:
        launcher.ensure_ready(project,timeout=2,check=lambda *a:{'state':'starting'},clock=lambda:next(clock),pause=lambda _:None)
    assert e.value.code=='startup_timeout'


def test_frontend_is_prepared_before_starting_backend(project):
    (project/'dist/assets/app.css').unlink();order=[]
    def prepare(root):order.append('build');(root/'dist/assets/app.css').write_text('/* rebuilt */')
    states=iter([{'state':'stopped'},{'state':'ready','process_id':789}])
    def start(*a):order.append('backend');return SimpleNamespace(poll=lambda:None)
    launcher.ensure_ready(project,check=lambda *a:next(states),prepare=prepare,start=start,pause=lambda _:None)
    assert order==['build','backend']


def test_api_ready_without_own_frontend_does_not_open_browser(project,monkeypatch):
    monkeypatch.setattr(launcher,'local_get',lambda *a:b'<html>unrelated service</html>')
    with pytest.raises(launcher.LaunchError) as e:launcher.ensure_ready(project,check=lambda *a:{'state':'ready'})
    assert e.value.code=='frontend_failed'


@pytest.mark.skipif(os.name!='nt',reason='Native Windows mutex')
def test_native_launcher_mutex_coalesces_parallel_opens_and_releases_after_exit(tmp_path):
    with launcher.LauncherMutex(tmp_path) as first:
        assert first
        with launcher.LauncherMutex(tmp_path) as second:assert not second
        with launcher.LauncherMutex(tmp_path) as third:assert not third
    with launcher.LauncherMutex(tmp_path) as reopened:assert reopened


def test_fast_health_identifies_background_without_optional_integration_probes(monkeypatch):
    from server import main
    monkeypatch.setenv('ATTENTION_DESKTOP_BACKGROUND','1')
    monkeypatch.setattr(main.mail_digest,'mail_status',lambda:pytest.fail('readiness must not run mail CLI'))
    result=main.health(check_integrations=False)
    assert result['application']==APP_ID and result['background'] is True
    assert result['workspace_id']==workspace_id(main.APP_ROOT)


def test_frontend_built_after_collector_start_is_served_without_restart(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import main
    monkeypatch.setattr(main,'DIST_ROOT',tmp_path/'dist')
    app=FastAPI();app.add_api_route('/{path:path}',main.frontend,methods=['GET'])
    with TestClient(app) as client:
        assert client.get('/?personal_view=today').status_code==503
        bundle(tmp_path)
        assert client.get('/?personal_view=today').status_code==200
        assert client.get('/assets/app.js').text=='/* synthetic bundle */'


def test_frontend_never_serves_a_file_outside_its_build(tmp_path,monkeypatch):
    from server import main
    from fastapi import HTTPException
    bundle(tmp_path);(tmp_path/'dist-private').mkdir();(tmp_path/'dist-private/private.txt').write_text('private')
    monkeypatch.setattr(main,'DIST_ROOT',tmp_path/'dist')
    with pytest.raises(HTTPException) as e:main.frontend('../dist-private/private.txt')
    assert e.value.status_code==404


def test_failed_launcher_shows_readable_prompt_and_never_opens_browser(project,monkeypatch):
    from contextlib import nullcontext
    prompts=[];old_handlers=set(launcher.LOG.handlers)
    monkeypatch.setattr(launcher,'APP_ROOT',project)
    monkeypatch.setattr(launcher,'LauncherMutex',lambda:nullcontext(True))
    def blocked(**kwargs):raise launcher.LaunchError('address_in_use')
    monkeypatch.setattr(launcher,'ensure_ready',blocked)
    monkeypatch.setattr(launcher,'show_error',prompts.append)
    monkeypatch.setattr(launcher,'open_home',lambda *a:pytest.fail('must not open an unready service'))
    try:
        assert launcher.main([])==1
        assert prompts==['address_in_use']
        assert '被其他程序占用' in launcher.MESSAGES[prompts[0]]
    finally:
        for handler in set(launcher.LOG.handlers)-old_handlers:
            launcher.LOG.removeHandler(handler);handler.close()
