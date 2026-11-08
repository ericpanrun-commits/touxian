import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

from server.tests_wechat.test_deepseek_continuous_scope import personal,setup,bound,scope_for,connect
from server.cloud_sync.deepseek_cloud_consumer import ConsumerState,DeepSeekCloudConsumer,verify_status
from server.cloud_sync.wechat_dispatch_authority import DispatchDenied


def consumer(s,scope=None):
    scope=scope or scope_for(s)
    publication,path=connect(s,scope)
    local=s['sender'].protected_preview.__self__
    state=ConsumerState(path.with_name('consumer.sqlite3'),scope);state.initialize()
    gets=[]
    status={'protocol':'wechat-receipt-fence-v1','workspace_id':scope.workspace_id,
        'processor_id':scope.processor_id,'epoch':scope.epoch,'generation':scope.generation}
    async def read(config):
        gets.append(config['endpoint'])
        return dict(status)
    def factory(current):
        s['sender'].current=current
        return s['sender']
    runtime=DeepSeekCloudConsumer(scope=scope,state=state,local=local,authority=s['authority'],
        journal=s['journal'],status_read=read,sender_factory=factory,clock=lambda:s['clock'][0],release_current=lambda:True)
    return runtime,state,gets,status


def test_completed_job_is_discovered_published_and_restart_does_not_resend(bound):
    s=bound;runtime,state,gets,_=consumer(s)
    assert asyncio.run(runtime.tick())['results']==['acknowledged']
    assert len(gets)==len(s['backend'].sent)==1
    runtime.status_at=None  # restart loses status cache, not delivery proof
    assert asyncio.run(runtime.tick())['state']=='idle'
    assert len(gets)==len(s['backend'].sent)==1
    with state.connection() as db:
        assert db.execute('SELECT state FROM jobs').fetchone()[0]=='acknowledged'
    assert b'ework_' not in state.path.read_bytes()


def test_pending_job_completing_after_scan_is_not_lost(bound):
    s=bound;runtime,state,gets,_=consumer(s)
    with s['personal'].connection(write=True) as db:db.execute("UPDATE wechat_analysis_job SET status='pending'")
    assert asyncio.run(runtime.tick())['state']=='idle' and gets==[]
    with s['personal'].connection(write=True) as db:db.execute("UPDATE wechat_analysis_job SET status='parsed'")
    assert asyncio.run(runtime.tick())['results']==['acknowledged']


def test_historical_source_is_held_without_any_metadata_or_content_request(bound):
    s=bound;scope=scope_for(s)
    scope=replace(scope,sources=(replace(scope.sources[0],after_seq=99999),*scope.sources[1:]))
    runtime,state,gets,_=consumer(s,scope)
    assert asyncio.run(runtime.tick())['state']=='idle'
    assert gets==s['backend'].sent==[]


def test_revocation_and_missing_runtime_do_not_recreate_state_or_make_requests(bound):
    s=bound;runtime,state,gets,_=consumer(s)
    s['authority'].revoke(runtime.scope.approval_id)
    with pytest.raises(DispatchDenied,match='grant_not_current'):asyncio.run(runtime.tick())
    assert gets==s['backend'].sent==[]
    state.path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        with state.connection():pass
    assert not state.path.exists()


@pytest.mark.parametrize('field,value',[('epoch','other'),('workspace_id','other'),('processor_id','other'),
                                     ('generation',True),('protocol','other')])
def test_changed_remote_identity_stops_before_content_handoff(bound,field,value):
    s=bound;runtime,state,gets,status=consumer(s);status[field]=value
    with pytest.raises(DispatchDenied,match='publisher_identity_changed'):asyncio.run(runtime.tick())
    assert len(gets)==1 and s['backend'].sent==[]
    assert asyncio.run(runtime.tick())['state']=='status_backoff'


def test_failed_status_get_is_counted_and_restart_waits_without_retry_storm(bound):
    s=bound;runtime,state,gets,_=consumer(s)
    async def failed(config):gets.append(True);raise OSError('synthetic disconnect')
    runtime.status_read=failed
    with pytest.raises(OSError):asyncio.run(runtime.tick())
    runtime.status_at=None
    assert asyncio.run(runtime.tick())['state']=='status_backoff'
    assert len(gets)==1 and s['backend'].sent==[]
    s['clock'][0]+=timedelta(minutes=15)
    with pytest.raises(OSError):asyncio.run(runtime.tick())
    assert len(gets)==2


def test_unknown_delivery_recovers_with_metadata_only_then_stays_deduplicated(bound):
    from server.cloud_sync.wechat_incremental_journal import reconciliation_proof
    s=bound;runtime,state,gets,_=consumer(s);s['backend'].lost=True
    assert asyncio.run(runtime.tick())['results']==['needs_reconciliation']
    assert asyncio.run(runtime.tick())['state']=='status_backoff'
    s['clock'][0]+=timedelta(minutes=15)
    entry=s['journal'].pending_entries()[0]
    s['backend'].lost=False
    s['backend'].value={'state':'already_published','proof':reconciliation_proof(entry),'generation':1}
    assert asyncio.run(runtime.tick())['results']==['acknowledged']
    assert all('payload' not in record for record in s['backend'].sent[1]['records'])
    assert asyncio.run(runtime.tick())['state']=='idle'


def test_repeated_unknown_results_open_persistent_circuit(bound):
    s=bound;runtime,state,gets,_=consumer(s);s['backend'].lost=True
    asyncio.run(runtime.tick());s['clock'][0]+=timedelta(minutes=15)
    assert asyncio.run(runtime.tick())['results']==['needs_reconciliation']
    runtime.status_at=None
    assert asyncio.run(runtime.tick())['state']=='circuit_open'
    assert len(s['backend'].sent)==2


def test_release_change_during_preparation_stops_content(bound):
    s=bound;runtime,state,gets,_=consumer(s)
    s['backend'].prepare_hook=lambda:setattr(runtime,'release_current',lambda:False)
    assert asyncio.run(runtime.tick())['results']==['cloud_scope_or_session_changed']
    assert s['backend'].sent==[]


def test_state_initialization_cannot_reset_existing_runtime(bound):
    _,state,_,_=consumer(bound)
    with pytest.raises(FileExistsError):state.initialize()
    changed=ConsumerState(state.path,replace(state.scope,daily_limit=1,cycle_limit=1))
    with pytest.raises(DispatchDenied,match='runtime_scope_changed'):
        with changed.connection():pass
