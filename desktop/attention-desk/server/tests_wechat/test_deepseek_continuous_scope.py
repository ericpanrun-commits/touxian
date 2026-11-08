import asyncio
from dataclasses import asdict,replace
from datetime import datetime,timedelta
import hashlib
import json
from uuid import uuid4

import pytest

from server.tests_wechat.test_deepseek_cloud_evidence import personal,setup,bound
from server.cloud_sync.deepseek_cloud_scope import CloudSource,ContinuousCloudGrant,ScopedCloudPublication
from server.cloud_sync.deepseek_cloud_continuous import ContinuousLocalEvidence,continuous_approval
from server.cloud_sync.settings import WorkerSettings
from server.cloud_sync.wechat_dispatch_authority import Grant,DispatchDenied
from server.wechat_collect.deepseek_content_gate import binding_digest


def scope_for(s,**changes):
    with s['personal'].connection() as c:
        row=c.execute('SELECT id,source_id,binding_json FROM wechat_chat').fetchone()
    source=CloudSource(row['id'],row['source_id'],binding_digest(row['source_id'],row['binding_json']),0)
    sources=(source,*(CloudSource('synthetic'+str(i),'wechat:synthetic'+str(i),'a'*64,0) for i in range(4)))
    old=s['grant']
    return ContinuousCloudGrant(approval_id=str(uuid4()),approved_at='2026-10-22T00:00:00+00:00',
        not_before='2026-10-22T01:00:00+00:00',workspace_id=old.workspace_id,processor_id=old.processor_id,
        epoch=old.epoch,generation=1,evidence_hash=old.evidence_hash,api_bundle_hash=old.api_bundle_hash,
        sources=sources,**changes)


def connect(s,scope):
    publication=ScopedCloudPublication(scope,s['job'],s['grant'].receipt_hash,s['grant'].bundle_hash)
    path=s['personal'].path.parent/'synthetic-continuous-cloud.json'
    path.write_text(json.dumps({'version':1,'mode':'continuous','enabled':True,
        'authorization_record':'synthetic future-scope fixture','scope':asdict(scope)}),encoding='utf8')
    local=ContinuousLocalEvidence(s['personal'].path.parent,path,approval_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        settings=WorkerSettings(s['personal'].path.parent/'cloud-worker-settings.json',protector=lambda value,**_:value))
    descriptor=Grant(scope.approval_id,scope.binding(),datetime.fromisoformat(scope.expires_at).timestamp(),False)
    s['authority'].import_verified(descriptor,lambda value:value==descriptor)
    s['sender'].protected_preview=local.snapshot
    s['sender'].current=lambda value:value.scope==scope
    return publication,path


def test_new_notice_uses_one_parent_grant_without_a_new_human_label(bound):
    s=bound;scope=scope_for(s);publication,_=connect(s,scope)
    assert asyncio.run(s['sender'].step(publication))['state']=='acknowledged'
    assert asyncio.run(s['sender'].step(publication))['state']=='acknowledged'
    assert len(s['backend'].sent)==1
    assert publication.binding()==scope.binding()
    assert publication.max_calls is None and publication.daily_limit==30 and publication.cycle_limit==4


@pytest.mark.parametrize('case',['watermark','publication_time','raw_creation_time','rebound_source','other_source'])
def test_future_permission_cannot_upload_old_or_rebound_content(bound,case):
    s=bound;scope=scope_for(s)
    if case=='watermark':scope=replace(scope,sources=(replace(scope.sources[0],after_seq=99999),*scope.sources[1:]))
    elif case=='publication_time':scope=replace(scope,not_before='2026-10-22T02:00:01+00:00')
    elif case=='raw_creation_time':
        with s['personal'].connection(write=True) as c:c.execute("UPDATE raw_item SET created_at='2026-10-21T00:00:00+00:00'")
    elif case=='rebound_source':scope=replace(scope,sources=(replace(scope.sources[0],binding_hash='0'*64),*scope.sources[1:]))
    else:scope=replace(scope,sources=(CloudSource('another','wechat:another','b'*64,0),*scope.sources[1:]))
    publication,_=connect(s,scope)
    assert asyncio.run(s['sender'].step(publication))['state'] in {'protected_preview_unavailable','reviewed_projection_changed'}
    assert s['backend'].sent==[]


def test_shared_quota_persists_and_unknown_delivery_uses_exponential_backoff(bound):
    s=bound;scope=scope_for(s,daily_limit=2,cycle_limit=2);publication,_=connect(s,scope)
    s['backend'].lost=True
    assert asyncio.run(s['sender'].step(publication))['state']=='needs_reconciliation'
    s['clock'][0]+=timedelta(seconds=31)
    assert asyncio.run(s['sender'].step(publication))['state']=='needs_reconciliation'
    s['clock'][0]+=timedelta(seconds=31)
    assert asyncio.run(s['sender'].step(publication))['state']=='backoff'
    s['clock'][0]+=timedelta(seconds=60)
    assert asyncio.run(s['sender'].step(publication))['state']=='daily_limit_reached'
    assert len(s['backend'].sent)==2
    assert all('payload' not in row for row in s['backend'].sent[1]['records'])


def test_parent_revocation_blocks_fresh_publication(bound):
    s=bound;scope=scope_for(s);publication,_=connect(s,scope)
    s['authority'].revoke(scope.approval_id)
    assert asyncio.run(s['sender'].step(publication))['state']=='grant_not_current'
    assert s['backend'].sent==[]


def test_one_notice_cloud_approval_is_not_a_continuous_grant(bound):
    with pytest.raises(DispatchDenied,match='separate_continuous_cloud_approval_required'):
        continuous_approval(bound['approval'].read_bytes())


@pytest.mark.parametrize('field,value',[('daily_limit',31),('cycle_limit',5),('destination','https://api.deepseek.com')])
def test_continuous_scope_rejects_expanded_budgets_or_wrong_destination(bound,field,value):
    with pytest.raises(ValueError):replace(scope_for(bound),**{field:value})


def test_exactly_five_distinct_sources_and_future_cutoff_are_required(bound):
    scope=scope_for(bound)
    for value in (scope.sources[:4],(scope.sources[0],)*5):
        with pytest.raises(ValueError):replace(scope,sources=value)
    with pytest.raises(ValueError):replace(scope,not_before='2026-10-21T00:00:00+00:00')
