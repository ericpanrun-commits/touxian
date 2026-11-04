"""The manual reviewed batch cannot turn into a general sync or implicit retry."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import wechat_limited_publish as limited
from server.personal_store import encode


@pytest.fixture
def rig(tmp_path, monkeypatch):
    records=[{'kind':kind,'id':f'fixture-{i}','payload':{'redacted_excerpt':True,'text':'必要通知'},'source_hash':'test-hash'}
             for i,kind in enumerate(['raw_item','inbox_item','inbox','raw_review']*2)]
    bundle={'preview_only':True,'upload_authorized':False,'records':records,'resources':[],
            'raw_item_ids':['first','second'],'bundle_hash':hashlib.sha256(encode(records).encode()).hexdigest()}
    monkeypatch.setattr(limited,'HASH',bundle['bundle_hash'])
    monkeypatch.setattr(limited,'minimized_projection_preview',lambda *args:deepcopy(bundle))
    flags={'hold_cloud_uploads':True,'pause_paid_ai':True,'state':'configured'}
    monkeypatch.setattr(limited,'controls',lambda _:dict(flags))
    now=datetime.now(timezone.utc)
    preflight={'at':now.isoformat(),'workspace':{'id':limited.WORKSPACE,'owner_id':limited.OWNER},
               'processors':[{'id':limited.PROCESSOR,'workspace_id':limited.WORKSPACE,'revoked_at':None}],
               'publisher':{'last_seen_at':(now-timedelta(days=1)).isoformat()},'target_records':[]}
    config={'enabled':True,'endpoint':limited.ENDPOINT,'workspace_id':limited.WORKSPACE,
            'processor_id':limited.PROCESSOR,'worker_key':'synthetic-credential-never-logged'}
    state={'published':False,'fail':False,'existing':False,'pause_after_publish':False}
    calls=[]
    def handler(request):
        calls.append({'method':request.method,'path':request.url.path,'body':json.loads(request.content) if request.content else None})
        assert str(request.url).startswith(limited.ENDPOINT+'/worker/')
        if request.method=='GET':
            rows=[{k:r[k] for k in ('kind','id','source_hash')} for r in records] if state['published'] or state['existing'] else []
            return httpx.Response(200,json={'items':rows,'limit':500})
        if request.url.path.endswith('/hello'):
            return httpx.Response(200,json={'workspace_id':limited.WORKSPACE,'record_catalogue':True})
        assert request.url.path.endswith('/publish')
        assert json.loads(request.content)['records']==records
        if state['fail']:raise httpx.ReadTimeout('unknown outcome',request=request)
        state['published']=True
        if state['pause_after_publish']:flags['hold_cloud_uploads']=False
        return httpx.Response(200,json={'revision':17})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    personal=SimpleNamespace(path=tmp_path/'unused.sqlite3')
    journal=tmp_path/'receipt.json'
    def run():return limited.execute(personal,{},deepcopy(bundle),preflight,config,client,journal)
    yield SimpleNamespace(**locals())
    client.close()


def test_exact_eight_records_and_same_id_replay_leave_global_holds_and_credentials_private(rig):
    result=rig.run()
    assert result['outcome']=='published_and_replayed'
    assert result['idempotent_highwater_unchanged']
    assert [c['path'].split('/')[-1] for c in rig.calls if c['method']=='POST']==['hello','publish','publish']
    assert rig.flags=={'hold_cloud_uploads':True,'pause_paid_ai':True,'state':'configured'}
    assert rig.config['worker_key'] not in rig.journal.read_text()
    assert result['attachment_count']==result['model_requests']==0
    before=len(rig.calls)
    with pytest.raises(limited.LimitedError,match='already_attempted'):rig.run()
    assert len(rig.calls)==before


def test_foreign_workspace_stops_before_any_network(rig):
    rig.config['workspace_id']='foreign'
    with pytest.raises(limited.LimitedError,match='identity_changed'):rig.run()
    assert rig.calls==[]


def test_edited_or_expanded_bundle_cannot_reach_network(rig,monkeypatch):
    original=deepcopy(rig.bundle)
    rig.bundle['records'][0]['payload']['text']='unreviewed material'
    monkeypatch.setattr(limited,'minimized_projection_preview',lambda *args:deepcopy(original))
    with pytest.raises(limited.LimitedError,match='bundle_changed'):rig.run()
    assert rig.calls==[]


def test_changed_original_evidence_cannot_reach_network(rig,monkeypatch):
    def changed(*args):raise ValueError('original_evidence_changed')
    monkeypatch.setattr(limited,'minimized_projection_preview',changed)
    with pytest.raises(ValueError,match='original_evidence_changed'):rig.run()
    assert rig.calls==[]


def test_existing_target_aborts_before_hello_or_publish(rig):
    rig.state['existing']=True
    with pytest.raises(limited.LimitedError,match='target_appeared'):rig.run()
    assert all(c['method']=='GET' for c in rig.calls)


def test_unknown_commit_outcome_is_not_retried_or_rolled_back(rig):
    rig.state['fail']=True
    with pytest.raises(limited.LimitedError,match='no_automatic_retry'):rig.run()
    assert [c['path'].split('/')[-1] for c in rig.calls if c['method']=='POST']==['hello','publish']
    receipt=json.loads(rig.journal.read_text())
    assert receipt['outcome']=='inspect_before_retry'
    assert receipt['requests'][-1]['state']=='unconfirmed'


def test_hold_changes_stop_further_requests(rig):
    rig.state['pause_after_publish']=True
    with pytest.raises(limited.LimitedError,match='holds_must_remain'):rig.run()
    assert [c['path'].split('/')[-1] for c in rig.calls if c['method']=='POST']==['hello','publish']


def test_active_remote_publisher_stops_before_any_network(rig):
    rig.preflight['publisher']['last_seen_at']=datetime.now(timezone.utc).isoformat()
    with pytest.raises(limited.LimitedError,match='publisher_may_be_active'):rig.run()
    assert rig.calls==[]
