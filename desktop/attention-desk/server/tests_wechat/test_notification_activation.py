"""New release binding and real scoped-consumer integration, with fake I/O."""
import asyncio
from datetime import timedelta
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from server.personal_store import encode
from server.user_context import UserContextStore
from server.tests_wechat.test_queue_starvation_regression import rig
from server.tests_wechat.test_notification_automation import fresh,counts
from server.tests_wechat.test_deepseek_content_gate import grant,write_grant
from server.tests_wechat.test_deepseek_dispatch import Backend
from server.wechat_collect.config import ChatConfig,ConfigStore
from server.wechat_collect.deepseek_content_gate import binding_digest
from server.cloud_sync import notification_runtime as runtime
from server.cloud_sync.deepseek_cloud_consumer import ConsumerState,DeepSeekCloudConsumer
from server.cloud_sync.deepseek_dispatch import ReviewedDeepSeekDispatch
from server.cloud_sync.settings import WorkerSettings,ENDPOINT
from server.cloud_store import ProcessLock
from server.cloud_sync.wechat_dispatch_authority import DispatchAuthority
from server.cloud_sync.wechat_incremental_journal import WeChatIncrementalJournal,JournalError
from server.cloud_sync.wechat_dispatch_transport import DispatchTransport


@pytest.fixture
def prepared(rig,monkeypatch):
    directory=rig.personal.path.parent
    (directory/'recovery-controls.json').write_text(encode({'version':1,'pause_paid_ai':False,'hold_cloud_uploads':False}),'utf-8')
    config=ConfigStore(directory/'wechat.yaml');doc=config.read()
    doc.wechat.priority_chats=[*doc.wechat.priority_chats,*(ChatConfig(id='fake'+str(i),name='合成附加群'+str(i)) for i in range(4))]
    doc.wechat.allowed_chats=[c.name for c in doc.wechat.priority_chats]
    config.save(doc,doc.revision);rig.service.repository.configure(doc)
    with rig.personal.connection() as c:
        chats=c.execute('SELECT * FROM wechat_chat ORDER BY id').fetchall()
    bindings={ch['id']:binding_digest(ch['source_id'],ch['binding_json']) for ch in chats}
    write_grant(rig.personal,grant(chat_watermarks={key:0 for key in bindings},source_bindings=bindings))
    factory=lambda path:WorkerSettings(path,protector=lambda value,**_:value)
    monkeypatch.setattr(runtime,'WorkerSettings',factory)
    monkeypatch.setattr('server.cloud_sync.deepseek_cloud_evidence.WorkerSettings',factory)
    settings=factory(directory/'cloud-worker-settings.json')
    settings._write({'endpoint':ENDPOINT,'processor_id':str(uuid4()),'workspace_id':str(uuid4()),
        'notes_installation_id':str(uuid4()),'protected_worker_key':'ework_'+'a'*43,'enabled':True})
    old=rig.add('请全体同学提交原有积压材料。')
    spec={'authorization_record':'synthetic separately approved release','approved_at':rig.clock[0].isoformat(),
        'profile':{'class_name':'2026级车辆工程2班','entry_year':2026,'confirmed_fields':['class_name','entry_year']},
        'audiences':{ch['id']:{'kind':'class' if ch['id']=='test' else 'review','value':'2026级车辆工程2班' if ch['id']=='test' else ''} for ch in chats}}
    api={'baseline_version':33,'files':{'guarded_wechat_actions.ts':'a'*64}}
    plan=runtime.prepare(directory,spec,api,at=rig.clock[0])
    proof={'checked_at':rig.clock[0].isoformat(),'files':api['files'],'api_version':34,
        'api_bundle_hash':'b'*64,'api_scope':plan['api_scope'],'processor_revoked':False,
        'workspace_id':plan['api_scope']['workspace_id'],'processor_id':plan['api_scope']['processor_id'],
        'publisher_epoch':str(uuid4()),'generation':1}
    return rig,plan,proof,old


def test_readonly_plan_does_not_activate_or_change_profile_and_activation_excludes_prior_backlog(prepared):
    rig,plan,proof,old=prepared;directory=rig.personal.path.parent
    assert not (directory/'notification-automation.json').exists()
    assert UserContextStore(directory).read().class_name==''
    before=(directory/'recovery-controls.json').read_bytes()
    assert runtime.activate(directory,plan,proof,at=rig.clock[0])['history_backfill'] is False
    assert runtime.load(directory)[1].policy=='wechat-personal-actions-v2'
    assert before==(directory/'recovery-controls.json').read_bytes()
    rig.worker.automatic_mode=True;rig.worker.queue.automation_directory=directory;rig.worker.queue.exclusive_worker=True
    fresh(rig)
    assert asyncio.run(rig.worker.cycle())['created']==1
    with rig.personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job WHERE batch_id=?',(old['id'],)).fetchone()[0]==0
    with pytest.raises(ValueError,match='profile_changed_since_review|existing_automation'):
        runtime.activate(directory,plan,proof,at=rig.clock[0])


@pytest.mark.parametrize('change',['stale','files','scope','profile'])
def test_activation_rejects_stale_mismatched_proof_before_production_changes(prepared,change):
    rig,plan,proof,_=prepared
    if change=='stale':proof['checked_at']=(rig.clock[0]-timedelta(hours=1)).isoformat()
    if change=='files':proof['files']={}
    if change=='scope':proof['api_scope']={**proof['api_scope'],'activation_hash':'c'*64}
    if change=='profile':plan['activation']['profile_hash']='d'*64
    with pytest.raises(ValueError):runtime.activate(rig.personal.path.parent,plan,proof,at=rig.clock[0])
    assert not (rig.personal.path.parent/'notification-automation.json').exists()
    assert UserContextStore(rig.personal.path.parent).read().class_name==''


def test_burst_of_eight_new_notices_analyzes_and_publishes_in_two_bounded_minute_cycles(prepared):
    rig,plan,proof,old=prepared;directory=rig.personal.path.parent
    runtime.activate(directory,plan,proof,at=rig.clock[0]);cache,scope,local=runtime.load(directory)
    rig.worker.automatic_mode=True;rig.worker.queue.automation_directory=directory;rig.worker.queue.exclusive_worker=True
    rig.clock[0]+=timedelta(minutes=15)
    for i in range(8):rig.add('请全体同学提交突发实验报告'+str(i)+'。')
    assert asyncio.run(rig.worker.cycle())['calls']==4
    rig.clock[0]+=timedelta(seconds=60)
    assert asyncio.run(rig.worker.cycle())['calls']==4
    lock=ProcessLock(cache);lock.acquire()
    backend=Backend();transport=DispatchTransport(backend);client=httpx.AsyncClient(transport=transport)
    authority=DispatchAuthority(cache/'authority.sqlite3')
    journal=WeChatIncrementalJournal(cache/'journal.json',lock)
    async def status(_):
        return {'protocol':scope.policy,'workspace_id':scope.workspace_id,'processor_id':scope.processor_id,
                'epoch':scope.epoch,'generation':scope.generation,'activation_hash':plan['api_scope']['activation_hash'],
                'not_before':scope.not_before,'source_ids':plan['api_scope']['source_ids']}
    consumer=DeepSeekCloudConsumer(scope=scope,state=ConsumerState(cache/'runtime.sqlite3',scope),local=local,
        authority=authority,journal=journal,status_read=status,
        sender_factory=lambda current:ReviewedDeepSeekDispatch(journal=journal,authority=authority,transport=transport,
            client=client,protected_preview=local.snapshot,current=current,clock=lambda:rig.clock[0]),
        clock=lambda:rig.clock[0],release_current=lambda:True)
    try:
        first=asyncio.run(consumer.tick());assert first['results']==['acknowledged']*4
        with pytest.raises(JournalError,match='cycle_limit_reached'):asyncio.run(consumer.tick())
        rig.clock[0]+=timedelta(seconds=60)
        assert asyncio.run(consumer.tick())['results']==['acknowledged']*4
        assert asyncio.run(consumer.tick())['state']=='idle'
        assert len(backend.sent)==8
        assert all(any(r['kind']=='event' for r in body['records']) for body in backend.sent)
        assert counts(rig)=={'event':8,'task':8,'wechat_llm_attempt':8}
    finally:asyncio.run(client.aclose());lock.close()


def test_collector_restart_under_hold_does_not_reconcile_old_ai_records(rig):
    old=rig.add('请全体同学提交旧报告。');job=rig.worker.queue.enqueue(old['id'],rig.service.context.read())
    with rig.personal.connection(write=True) as c:
        c.execute("UPDATE wechat_analysis_job SET status='running' WHERE id=?",(job,))
    rig.service.repository.recover()
    with rig.personal.connection() as c:assert c.execute('SELECT status FROM wechat_analysis_job WHERE id=?',(job,)).fetchone()[0]=='running'


def test_discovery_defers_unpromoted_receipt_until_review_revision_changes(prepared):
    from unittest.mock import patch
    rig,plan,proof,_=prepared;directory=rig.personal.path.parent
    runtime.activate(directory,plan,proof,at=rig.clock[0]);cache,scope,local=runtime.load(directory)
    rig.worker.automatic_mode=True;rig.worker.queue.automation_directory=directory;rig.worker.queue.exclusive_worker=True
    fresh(rig)
    with patch('server.wechat_collect.automation.promote',side_effect=OSError('crash after successful analysis')):
        with pytest.raises(OSError):asyncio.run(rig.worker.cycle())
    lock=ProcessLock(cache);lock.acquire();journal=WeChatIncrementalJournal(cache/'journal.json',lock)
    authority=DispatchAuthority(cache/'authority.sqlite3')
    consumer=DeepSeekCloudConsumer(scope=scope,state=ConsumerState(cache/'runtime.sqlite3',scope),local=local,
        authority=authority,journal=journal,status_read=None,sender_factory=None,clock=lambda:rig.clock[0],release_current=lambda:True)
    original=local.build_preview;built=[]
    def build(*args):built.append(True);return original(*args)
    local.build_preview=build
    try:
        assert consumer.discover()==0
        assert consumer.discover()==0 and len(built)==1
        assert asyncio.run(rig.worker.cycle())['created']==1
        assert consumer.discover()==1 and len(built)==2
        assert consumer.discover()==0 and len(built)==2
        assert counts(rig)['wechat_llm_attempt']==1
    finally:lock.close()


def test_real_global_pauses_block_analysis_creation_and_publication_despite_scoped_approval(prepared):
    from server.wechat_collect.automation import activation
    from server.wechat_collect.deepseek_worker import run
    rig,plan,proof,_=prepared;directory=rig.personal.path.parent
    runtime.activate(directory,plan,proof,at=rig.clock[0])
    rig.worker.automatic_mode=True;rig.worker.queue.automation_directory=directory
    fresh(rig)
    (directory/'recovery-controls.json').write_text(encode({'version':1,'pause_paid_ai':True,'hold_cloud_uploads':True}),'utf-8')
    assert asyncio.run(run(rig.worker,once=True))['state']=='worker_controls_required'
    for permission in ('analyze','create_tasks','publish_tasks'):
        with pytest.raises(ValueError,match='global_automation_paused'):
            with activation(directory,permission=permission):pytest.fail('Global pause was bypassed')
    assert counts(rig)=={'event':0,'task':0,'wechat_llm_attempt':0}


def test_scoped_release_keeps_legacy_full_sync_and_historical_analysis_held(prepared,monkeypatch):
    from server.recovery_controls import controls,paid_ai_paused,cloud_uploads_held
    rig,plan,proof,_=prepared;directory=rig.personal.path.parent
    runtime.activate(directory,plan,proof,at=rig.clock[0])
    monkeypatch.delenv('ATTENTION_RECOVERY_CONTROL_PATH',raising=False)
    assert controls(rig.personal.path)['state']=='scoped_automation_legacy_held'
    assert paid_ai_paused(rig.personal.path) and cloud_uploads_held(rig.personal.path)
    assert json.loads((directory/'recovery-controls.json').read_bytes())['pause_paid_ai'] is False


def test_activation_never_releases_a_global_pause_even_with_matching_api_proof(prepared):
    rig,plan,proof,_=prepared;directory=rig.personal.path.parent
    before=encode({'version':1,'pause_paid_ai':True,'hold_cloud_uploads':True})
    (directory/'recovery-controls.json').write_text(before,'utf-8')
    with pytest.raises(ValueError,match='global_automation_paused'):runtime.activate(directory,plan,proof,at=rig.clock[0])
    assert not (directory/'notification-automation.json').exists()
    assert (directory/'recovery-controls.json').read_text('utf-8')==before


def test_persistent_delivery_failure_stops_after_twelve_total_publish_and_reconcile_requests(prepared):
    from server.cloud_sync.deepseek_cloud_scope import ScopedCloudPublication
    rig,plan,proof,_=prepared;directory=rig.personal.path.parent
    runtime.activate(directory,plan,proof,at=rig.clock[0]);cache,scope,local=runtime.load(directory)
    rig.worker.automatic_mode=True;rig.worker.queue.automation_directory=directory;rig.worker.queue.exclusive_worker=True
    fresh(rig);assert asyncio.run(rig.worker.cycle())['created']==1
    with local.files(scope) as (deepseek,_,__):
        with rig.personal.connection() as c:
            job=c.execute("SELECT id FROM wechat_analysis_job WHERE status='parsed'").fetchone()[0]
            preview=local.build_preview(c,deepseek,job,scope)
    publication=ScopedCloudPublication(scope,job,preview['analysis_receipt_sha256'],preview['bundle']['bundle_hash'])
    lock=ProcessLock(cache);lock.acquire();journal=WeChatIncrementalJournal(cache/'journal.json',lock)
    authority=DispatchAuthority(cache/'authority.sqlite3');backend=Backend();backend.status=503
    transport=DispatchTransport(backend);client=httpx.AsyncClient(transport=transport)
    sender=ReviewedDeepSeekDispatch(journal=journal,authority=authority,transport=transport,client=client,
        protected_preview=local.snapshot,current=lambda _:True,clock=lambda:rig.clock[0])
    try:
        for _ in range(12):
            assert asyncio.run(sender.step(publication))['state']=='needs_reconciliation'
            rig.clock[0]+=timedelta(minutes=16)
        for _ in range(2):assert asyncio.run(sender.step(publication))['state']=='delivery_attempt_limit_reached'
        assert len(backend.sent)==12
        assert counts(rig)['wechat_llm_attempt']==1
    finally:asyncio.run(client.aclose());lock.close()
