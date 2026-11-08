import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from uuid import uuid4

import httpx
import pytest

from server.tests_wechat.conftest import personal
from server.tests_wechat.test_deepseek_cloud_preview import analyzed
from server.cloud_store import ProcessLock
from server.wechat_collect.deepseek_content_gate import leased_grant
from server.cloud_sync.deepseek_preview import build_deepseek_preview
from server.cloud_sync.deepseek_dispatch import ReviewedCloudGrant,ReviewedDeepSeekDispatch
from server.cloud_sync.wechat_dispatch_authority import DispatchAuthority,Grant,locked_source
from server.cloud_sync.wechat_dispatch_transport import DispatchTransport
from server.cloud_sync.wechat_incremental_journal import WeChatIncrementalJournal,reconciliation_proof
from server.cloud_sync.settings import ENDPOINT


class Backend:
    def __init__(self):
        self.prepare_hook=lambda:None
        self.accept_hook=lambda:None
        self.sent=[]
        self.lost=False
        self.status=200
        self.value={'state':'published','revision':1}

    async def prepare(self,url):
        self.prepare_hook()
        return self

    def accept(self,request):
        self.accept_hook()
        self.sent.append(json.loads(request.content))

    async def response(self):
        if self.lost:raise httpx.ReadError('synthetic lost response')
        return httpx.Response(self.status,json=self.value)

    async def aclose(self):pass


@pytest.fixture
def setup(personal,tmp_path):
    _,job=analyzed(personal)
    clock=[datetime(2026,11,8,10,0,tzinfo=timezone.utc)]
    config={'endpoint':ENDPOINT,'enabled':True,'workspace_id':str(uuid4()),
        'processor_id':str(uuid4()),'worker_key':'ework_'+'a'*43}
    @contextmanager
    def protected(approval):
        with leased_grant(personal) as consent:
            with locked_source(personal.path,lambda c:build_deepseek_preview(c,consent,job)) as preview:
                yield preview,dict(config)
    with protected(None) as (preview,_):
        grant=ReviewedCloudGrant(str(uuid4()),job,preview['analysis_receipt_sha256'],preview['bundle']['bundle_hash'],
            config['workspace_id'],config['processor_id'],str(uuid4()),1,clock[0].isoformat(),
            (clock[0]+timedelta(hours=4)).isoformat(),'a'*64,'b'*64)
    cache=tmp_path/'isolated-sync-owner';cache.mkdir()
    lock=ProcessLock(cache);lock.acquire();assert lock.file is not None
    authority=DispatchAuthority(cache/'authority.sqlite3',clock=lambda:clock[0].timestamp())
    authority.initialize()
    descriptor=Grant(grant.approval_id,grant.binding(),datetime.fromisoformat(grant.expires_at).timestamp(),False)
    # This is independently granted synthetic evidence, never a production grant.
    authority.import_verified(descriptor,lambda candidate:candidate==descriptor)
    journal=WeChatIncrementalJournal(cache/'journal.json',lock)
    backend=Backend();transport=DispatchTransport(backend);client=httpx.AsyncClient(transport=transport)
    proof=[True]
    sender=ReviewedDeepSeekDispatch(journal=journal,authority=authority,transport=transport,client=client,
        protected_preview=protected,current=lambda candidate:proof[0] and candidate==grant,clock=lambda:clock[0])
    yield {'sender':sender,'grant':grant,'backend':backend,'authority':authority,'clock':clock,
        'journal':journal,'personal':personal,'config':config,'proof':proof,'job':job,'lock':lock}
    asyncio.run(client.aclose());lock.close()


def step(s):return asyncio.run(s['sender'].step(s['grant']))['state']


def test_one_reviewed_notice_acknowledges_and_does_not_resend(setup):
    s=setup
    assert step(s)=='acknowledged'
    assert step(s)=='acknowledged'
    assert len(s['backend'].sent)==1
    assert len(s['backend'].sent[0]['records'])==4
    contents=s['journal'].path.read_text(encoding='utf8')
    assert '请全体同学' not in contents and 'worker_key' not in contents


def test_unknown_delivery_uses_only_hashes_on_reconciliation_and_no_duplicate(setup):
    s=setup;s['backend'].lost=True
    assert step(s)=='needs_reconciliation'
    assert step(s)=='backoff'
    s['clock'][0]+=timedelta(minutes=15)
    entry=s['journal'].pending_entries()[0]
    s['backend'].lost=False
    s['backend'].value={'state':'already_published','proof':reconciliation_proof(entry),'generation':1}
    assert step(s)=='acknowledged'
    assert 'records' in s['backend'].sent[1]
    assert all(set(r)=={'kind','id','source_hash'} for r in s['backend'].sent[1]['records'])
    assert step(s)=='acknowledged' and len(s['backend'].sent)==2


def test_absence_fence_allows_one_rebuilt_successor_and_preserves_quota_history(setup):
    s=setup;s['backend'].lost=True
    assert step(s)=='needs_reconciliation'
    s['clock'][0]+=timedelta(minutes=15)
    entry=s['journal'].pending_entries()[0]
    s['backend'].lost=False
    s['backend'].value={'state':'fenced_absent','proof':reconciliation_proof(entry),'generation':2}
    assert step(s)=='fenced_absent'
    s['clock'][0]+=timedelta(minutes=15)
    s['backend'].value={'state':'published','revision':2}
    assert step(s)=='acknowledged'
    assert [body['generation'] for body in s['backend'].sent]==[1,1,2]
    entry=s['journal']._read()['entries'].popitem()[1]
    assert len(entry['history'])==1


@pytest.mark.parametrize('change',['revoke','source','review','config','proof'])
def test_change_during_connection_preparation_prevents_any_handoff(setup,change):
    s=setup
    def mutate():
        if change=='revoke':s['authority'].revoke(s['grant'].approval_id)
        elif change=='config':s['config']['enabled']=False
        elif change=='proof':s['proof'][0]=False
        else:
            with s['personal'].connection(write=True) as c:
                if change=='source':c.execute("UPDATE wechat_message SET raw_text='原文改变'")
                else:c.execute("UPDATE raw_review SET status='ignored'")
    s['backend'].prepare_hook=mutate
    result=step(s)
    assert result!='acknowledged' and s['backend'].sent==[]


def test_nonparticipating_writer_is_blocked_at_actual_handoff(setup):
    s=setup;blocked=[]
    def edit():
        with sqlite3.connect(s['personal'].path,timeout=0) as other:
            with pytest.raises(sqlite3.OperationalError,match='locked'):
                other.execute("UPDATE raw_review SET status='ignored'")
            blocked.append(True)
    s['backend'].accept_hook=edit
    assert step(s)=='acknowledged' and blocked==[True]


def test_invalid_reconciliation_proof_does_not_resend_and_has_total_call_bound(setup):
    s=setup;s['backend'].lost=True
    assert step(s)=='needs_reconciliation'
    s['backend'].lost=False;s['backend'].value={'state':'fenced_absent','proof':'0'*64,'generation':2}
    for _ in range(2):
        s['clock'][0]+=timedelta(minutes=15)
        assert step(s)=='needs_reconciliation'
    s['clock'][0]+=timedelta(minutes=15)
    assert step(s)=='test_call_limit_reached'
    assert len(s['backend'].sent)==3
    assert all('payload' not in r for body in s['backend'].sent[1:] for r in body['records'])


def test_revoked_credential_stops_reconciliation(setup):
    s=setup;s['backend'].status=403
    assert step(s)=='credential_revoked'
    s['clock'][0]+=timedelta(minutes=15)
    assert step(s)=='blocked' and len(s['backend'].sent)==1


def test_no_permission_expiry_and_missing_lock_fail_before_network(setup):
    s=setup
    assert asyncio.run(s['sender'].step(None))=={'state':'cloud_authorization_required'}
    s['clock'][0]+=timedelta(days=1)
    assert step(s)=='cloud_authorization_inactive'
    s['clock'][0]-=timedelta(days=1);s['lock'].close()
    assert step(s)=='existing_sync_lock_required'
    assert s['backend'].sent==[]


def test_four_hour_approval_cannot_be_reused_for_a_different_notice(setup):
    s=setup;changed=replace(s['grant'],job_id=str(uuid4()))
    assert asyncio.run(s['sender'].step(changed))['state']=='cloud_scope_or_session_changed'
    assert s['backend'].sent==[]
