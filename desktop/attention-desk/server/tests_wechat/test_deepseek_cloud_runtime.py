import asyncio
from datetime import timedelta
import importlib.util
import json
from pathlib import Path

import pytest

from server.tests_wechat.test_deepseek_cloud_evidence import personal,setup,bound
from server.cloud_sync.deepseek_cloud_evidence import ReviewedLocalEvidence
from server.cloud_sync.settings import WorkerSettings
from server.cloud_sync.wechat_dispatch_authority import DispatchDenied
from server.cloud_sync.wechat_incremental_journal import reconciliation_proof


@pytest.fixture
def runtime(bound,monkeypatch,tmp_path):
    path=Path(__file__).resolve().parents[2]/'scripts/deepseek-reviewed-cloud-test.py'
    spec=importlib.util.spec_from_file_location('reviewed_cloud_runtime',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    s=bound;grant=s['grant'];directory=s['personal'].path.parent
    for key,value in {'JOB':grant.job_id,'BUNDLE':grant.bundle_hash,'RECEIPT':grant.receipt_hash,
        'WORKSPACE':grant.workspace_id,'PROCESSOR':grant.processor_id,'EPOCH':grant.epoch}.items():
        monkeypatch.setattr(module,key,value)
    monkeypatch.setattr(module,'now',lambda:s['clock'][0])
    monkeypatch.setattr(module,'PreparedBackend',lambda origin:s['backend'])
    monkeypatch.setattr(module,'ReviewedLocalEvidence',lambda directory,path,**kwargs:ReviewedLocalEvidence(
        directory,path,**kwargs,settings=WorkerSettings(directory/'cloud-worker-settings.json',protector=lambda value,**_:value)))
    owner=tmp_path/'owner-receipt.json'
    owner.write_text(json.dumps({'workspace_id':grant.workspace_id,'processor_id':grant.processor_id,
        'epoch':grant.epoch,'outcome':'published_and_replayed'}),encoding='utf8')
    monkeypatch.setattr(module,'OWNER_RECEIPT',owner)
    manifest=json.loads(module.MANIFEST.read_bytes())
    proof={'checked_at':module.now().isoformat(),'endpoint':module.ENDPOINT,'api_version':24,
        'api_files':{**manifest['files'],'deno.json':manifest['baseline_files']['deno.json']},
        'implementation_hash':module.implementation_hash(),'workspace_id':grant.workspace_id,
        'processor_id':grant.processor_id,'processor_revoked':False,'publisher_epoch':grant.epoch,'generation':1,
        'owner_receipt_sha256':module.digest(owner.read_bytes()),'api_bundle_hash':'c'*64}
    proof_path=tmp_path/'deployment-proof.json';proof_path.write_text(json.dumps(proof),encoding='utf8')
    monkeypatch.setattr(module,'PROOF',proof_path)
    return module,s,directory,proof


def test_check_mode_writes_nothing_and_never_connects(runtime):
    module,s,directory,_=runtime
    assert asyncio.run(module.run(directory))['state']=='ready_for_authorized_test'
    assert not (directory/'cloud-sync/deepseek-reviewed-test').exists()
    assert s['backend'].sent==[]


def test_default_cli_does_not_start_an_asyncio_event_loop(runtime,monkeypatch,capsys):
    module,s,directory,_=runtime
    monkeypatch.setattr(module.sys,'argv',['deepseek-reviewed-cloud-test.py','--directory',str(directory)])
    def forbidden(*args,**kwargs):raise AssertionError('No event loop for read-only CLI')
    with monkeypatch.context() as isolated:
        isolated.setattr(module.asyncio,'run',forbidden)
        assert module.main()==0
    assert json.loads(capsys.readouterr().out)['network_requests']==0
    assert not (directory/'cloud-sync/deepseek-reviewed-test').exists()


def test_explicit_activation_sends_once_and_restart_keeps_dedup_and_revocation(runtime):
    module,s,directory,_=runtime
    args={'execute':True,'approval_record':module.APPROVAL_RECORD}
    assert asyncio.run(module.run(directory,**args))['state']=='acknowledged'
    assert asyncio.run(module.run(directory,**args))['state']=='acknowledged'
    assert len(s['backend'].sent)==1
    assert asyncio.run(module.run(directory,revoke=True))['state']=='revoked'
    # Already acknowledged is still a no-op after revocation, never a new POST.
    assert asyncio.run(module.run(directory,**args))['state']=='acknowledged'
    assert len(s['backend'].sent)==1


def test_controlled_disconnect_recovers_with_hashes_after_backoff(runtime):
    module,s,directory,_=runtime
    args={'execute':True,'approval_record':module.APPROVAL_RECORD}
    assert asyncio.run(module.run(directory,disconnect_after_send=True,**args))['state']=='needs_reconciliation'
    assert len(s['backend'].sent)==1
    assert asyncio.run(module.run(directory,**args))['state']=='backoff'
    s['clock'][0]+=timedelta(seconds=31)
    value=json.loads((directory/'cloud-sync/deepseek-reviewed-test/journal.json').read_bytes())
    entry=next(iter(value['entries'].values()))
    s['backend'].value={'state':'already_published','proof':reconciliation_proof(entry),'generation':1}
    assert asyncio.run(module.run(directory,**args))['state']=='acknowledged'
    assert len(s['backend'].sent)==2 and all('payload' not in r for r in s['backend'].sent[1]['records'])
    with pytest.raises(DispatchDenied,match='disconnect_test_already_attempted'):
        asyncio.run(module.run(directory,disconnect_after_send=True,**args))


@pytest.mark.parametrize('change',['wrong_api','wrong_epoch','revoked','old_proof','changed_code'])
def test_failed_remote_preflight_never_activates_or_sends(runtime,change):
    module,s,directory,proof=runtime
    if change=='wrong_api':proof['api_files']['guarded_wechat_publish.ts']='0'*64
    elif change=='wrong_epoch':proof['publisher_epoch']='00000000-0000-4000-8000-000000000000'
    elif change=='revoked':proof['processor_revoked']=True
    elif change=='old_proof':proof['checked_at']=(module.now()-timedelta(minutes=16)).isoformat()
    else:proof['implementation_hash']='0'*64
    module.PROOF.write_text(json.dumps(proof),encoding='utf8')
    with pytest.raises(DispatchDenied):
        asyncio.run(module.run(directory,execute=True,approval_record=module.APPROVAL_RECORD))
    assert not (directory/'cloud-sync/deepseek-reviewed-test').exists() and not s['backend'].sent


def test_missing_explicit_approval_and_partial_activation_cannot_reset_limits(runtime):
    module,s,directory,_=runtime
    with pytest.raises(DispatchDenied,match='explicit_approved_test_required'):
        asyncio.run(module.run(directory,execute=True))
    cache=directory/'cloud-sync/deepseek-reviewed-test';cache.mkdir(parents=True)
    (cache/'journal.json').write_text('{}',encoding='utf8')
    with pytest.raises(DispatchDenied,match='partial_activation_requires_review'):
        asyncio.run(module.run(directory,execute=True,approval_record=module.APPROVAL_RECORD))
    assert not s['backend'].sent
