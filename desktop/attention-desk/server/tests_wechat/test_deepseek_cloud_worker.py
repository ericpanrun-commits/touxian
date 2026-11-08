import asyncio
from datetime import timedelta
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from server.tests_wechat.test_deepseek_cloud_evidence import personal,setup,bound
from server.cloud_sync.deepseek_cloud_continuous import ContinuousLocalEvidence,continuous_approval
from server.cloud_sync.settings import WorkerSettings
from server.cloud_sync.wechat_dispatch_authority import DispatchDenied
from server.wechat_collect.deepseek_content_gate import binding_digest,FILENAME


@pytest.fixture
def worker(bound,monkeypatch,tmp_path):
    path=Path(__file__).resolve().parents[2]/'scripts/deepseek-cloud-worker.py'
    spec=importlib.util.spec_from_file_location('continuous_cloud_runtime',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    s=bound;directory=s['personal'].path.parent;grant=s['grant']
    monkeypatch.setattr(module,'now',lambda:s['clock'][0])
    monkeypatch.setattr(module,'WorkerSettings',lambda path:WorkerSettings(path,protector=lambda value,**_:value))
    monkeypatch.setattr(module,'ContinuousLocalEvidence',lambda directory,path,**kwargs:ContinuousLocalEvidence(
        directory,path,**kwargs,settings=WorkerSettings(directory/'cloud-worker-settings.json',protector=lambda value,**_:value)))
    document=yaml.safe_load((directory/'wechat.yaml').read_text(encoding='utf8'))
    with s['personal'].connection(write=True) as db:
        chat=dict(db.execute('SELECT * FROM wechat_chat').fetchone())
        source=dict(db.execute('SELECT * FROM source WHERE id=?',(chat['source_id'],)).fetchone())
        for i in range(4):
            extra={**source,'id':'wechat:fixture'+str(i)}
            db.execute('INSERT INTO source ('+','.join(extra)+') VALUES ('+','.join('?' for _ in extra)+')',list(extra.values()))
            extra={**chat,'id':'fixture'+str(i),'source_id':'wechat:fixture'+str(i),'name':'Fixture '+str(i)}
            db.execute('INSERT INTO wechat_chat ('+','.join(extra)+') VALUES ('+','.join('?' for _ in extra)+')',list(extra.values()))
            document['wechat']['priority_chats'].append({**document['wechat']['priority_chats'][0],
                'id':extra['id'],'name':extra['name']})
        rows=db.execute('SELECT * FROM wechat_chat').fetchall()
    document['wechat']['allowed_chats']=[c['name'] for c in document['wechat']['priority_chats']]
    (directory/'wechat.yaml').write_text(yaml.safe_dump(document,allow_unicode=True),encoding='utf8')
    consent=json.loads((directory/FILENAME).read_bytes())
    consent['chat_watermarks']={row['id']:0 for row in rows}
    consent['source_bindings']={row['id']:binding_digest(row['source_id'],row['binding_json']) for row in rows}
    (directory/FILENAME).write_text(json.dumps(consent),encoding='utf8')
    owner=tmp_path/'owner-receipt.json'
    owner.write_text(json.dumps({'workspace_id':grant.workspace_id,'processor_id':grant.processor_id,
        'epoch':grant.epoch,'outcome':'published_and_replayed'}),encoding='utf8')
    monkeypatch.setattr(module,'OWNER_RECEIPT',owner)
    manifest=json.loads(module.MANIFEST.read_bytes())
    proof={'checked_at':module.now().isoformat(),'endpoint':module.ENDPOINT,'api_version':25,
        'api_files':manifest['files'],'implementation_hash':module.implementation_hash(),
        'workspace_id':grant.workspace_id,'processor_id':grant.processor_id,'processor_revoked':False,
        'publisher_epoch':grant.epoch,'generation':1,'owner_receipt_sha256':module.digest(owner.read_bytes()),
        'api_bundle_hash':'d'*64}
    proof_path=tmp_path/'deployment-proof.json';proof_path.write_text(json.dumps(proof),encoding='utf8')
    monkeypatch.setattr(module,'PROOF',proof_path)
    return module,s,directory,proof


def activate(module,directory):
    return module.activate(directory,'synthetic independent continuous cloud approval',module.now().isoformat())


def test_default_cli_refuses_missing_permission_without_event_loop_or_writes(worker,monkeypatch,capsys):
    module,s,directory,_=worker
    monkeypatch.setattr(module.sys,'argv',['deepseek-cloud-worker.py','--directory',str(directory)])
    def forbidden(*args,**kwargs):raise AssertionError('Read-only check must not start event loop')
    with monkeypatch.context() as isolated:
        isolated.setattr(module.asyncio,'run',forbidden)
        assert module.main()==2
    assert json.loads(capsys.readouterr().out)['state']=='continuous_cloud_authorization_required'
    assert not module.cache_for(directory).exists() and s['backend'].sent==[]


@pytest.mark.parametrize('record',[None,'','call_352b35dc67624000ad6e1472ec5cdd82','call_8185e266de77402aadb066fd8ac9d958'])
def test_previous_content_permissions_cannot_activate_continuous_upload(worker,record):
    module,s,directory,_=worker
    with pytest.raises(DispatchDenied,match='separate_continuous_cloud_approval_required'):
        module.activate(directory,record,module.now().isoformat())
    assert not module.cache_for(directory).exists()


def test_activation_captures_current_watermarks_and_does_not_backfill(worker,monkeypatch):
    module,s,directory,_=worker
    assert activate(module,directory)['state']=='activated_not_started'
    scope=continuous_approval((module.cache_for(directory)/'approval.json').read_bytes())
    assert len(scope.sources)==5 and scope.not_before==module.now().isoformat()
    assert next(source for source in scope.sources if source.chat_id=='test').after_seq==1
    assert module.check(directory)['network_requests']==0
    # Idle run must not even prepare a TLS connection, much less post content.
    def forbidden(*args,**kwargs):raise AssertionError('Historical job must not connect')
    monkeypatch.setattr(module.PreparedLocalProxyBackend,'prepare',forbidden)
    assert asyncio.run(module.run(directory,once=True))['state']=='idle'
    with pytest.raises(DispatchDenied,match='activation_already_present'):activate(module,directory)
    assert module.revoke(directory)['state']=='revoked'
    with pytest.raises(DispatchDenied,match='grant_not_current'):module.check(directory)


@pytest.mark.parametrize('change',['stale','old_api','wrong_epoch','revoked','wrong_code','wrong_files'])
def test_invalid_deployment_proof_does_not_create_activation(worker,change):
    module,s,directory,proof=worker
    if change=='stale':proof['checked_at']=(module.now()-timedelta(minutes=16)).isoformat()
    elif change=='old_api':proof['api_version']=24
    elif change=='wrong_epoch':proof['publisher_epoch']='00000000-0000-4000-8000-000000000000'
    elif change=='revoked':proof['processor_revoked']=True
    elif change=='wrong_code':proof['implementation_hash']='0'*64
    else:proof['api_files']['handler.ts']='0'*64
    module.PROOF.write_text(json.dumps(proof),encoding='utf8')
    with pytest.raises(DispatchDenied):activate(module,directory)
    assert not module.cache_for(directory).exists()


def test_partial_activation_never_resets_journal_and_revoke_works_after_code_change(worker,monkeypatch):
    module,s,directory,_=worker
    activate(module,directory)
    cache=module.cache_for(directory);(cache/'runtime.sqlite3').unlink()
    with pytest.raises(DispatchDenied,match='continuous_cloud_authorization_required'):module.check(directory)
    with pytest.raises(DispatchDenied,match='activation_already_present'):activate(module,directory)
    monkeypatch.setattr(module,'implementation_hash',lambda:'0'*64)
    assert module.revoke(directory)['state']=='revoked'


def test_changed_source_binding_prevents_activation(worker):
    module,s,directory,_=worker
    with s['personal'].connection(write=True) as db:
        db.execute("UPDATE wechat_chat SET binding_json=? WHERE id='test'",(json.dumps({'internal_chat_id':'changed'}),))
    with pytest.raises(DispatchDenied,match='source_binding_changed'):activate(module,directory)
    assert not module.cache_for(directory).exists()


def test_startup_hook_is_inert_without_permission_and_after_revocation(worker,monkeypatch):
    from server.cloud_sync import deepseek_cloud_launch as launch
    module,s,directory,_=worker;starts=[]
    monkeypatch.setattr(launch.subprocess,'Popen',lambda *args,**kwargs:starts.append((args,kwargs)) or 'synthetic-process')
    assert launch.launch_if_authorized(directory) is None and starts==[]
    activate(module,directory)
    assert launch.launch_if_authorized(directory)=='synthetic-process'
    command=starts[0][0][0]
    assert command[-1]=='--run' and command[1].endswith('deepseek-cloud-worker.py')
    assert starts[0][1]['creationflags'] & launch.subprocess.CREATE_NO_WINDOW
    module.revoke(directory)
    assert launch.launch_if_authorized(directory) is None and len(starts)==1
