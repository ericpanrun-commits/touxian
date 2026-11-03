"""Transaction fault injection and restart recovery for prepared media without a job."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from server.notifications.config import BarkConfig
from server.notifications.wechat import WeChatBarkDispatcher
from server.tests_wechat.test_media_pipeline import NOW,observe_images,ocr_answer
from server.tests_wechat.test_pipeline import Settings,setup_service,scan
from server.wechat_collect.adapter import FakeAdapter
from server.wechat_collect.models import Observation
from server.wechat_collect.service import WeChatService
from server.wechat_collect.quality import register_media


def stage(personal,response=None):
    service,clock,calls=setup_service(personal,[Observation('test','unverified',tuple(observe_images(personal,1)),True,True)],
        time=NOW.isoformat(),response=response or (lambda p:ocr_answer(p,'action')))
    service.document.wechat.context_followup.enabled=False
    service.media_engine=lambda *a:([{'text':'请完成实验室安全学习，并提交承诺书。'}],{})
    asyncio.run(scan(service))
    # Register the captured image through the same saved-media intake used by DB recovery.
    with personal.connection(write=True) as c:
        batch=c.execute('SELECT id,snapshot_id FROM wechat_batch ORDER BY created_at DESC LIMIT 1').fetchone()
        image=c.execute("SELECT id FROM wechat_message WHERE message_type='image'").fetchone()[0]
        register_media(c,batch['id'],batch['snapshot_id'],image)
    return service,clock,calls


def legacy_orphan(personal,monkeypatch,response=None):
    service,clock,calls=stage(personal,response)
    # Reproduce the pre-fix committed state, stopping at its former enqueue boundary.
    with monkeypatch.context() as patch:
        patch.setattr(service.analysis,'enqueue',lambda *args,**kwargs:None)
        asyncio.run(service.media_pipeline.tick(force=True))
    with personal.connection() as c:
        row=dict(c.execute('SELECT * FROM wechat_media_work').fetchone())
        assert row['status']=='recovery_needed' and row['last_error']=='media_job_scope_conflict'
        assert c.execute('SELECT count(*) FROM wechat_media_analysis').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==0
    # Historical versions committed this status before calling enqueue.
    # Restore only that legacy fixture state, never a production record.
    with personal.connection(write=True) as c:
        c.execute("UPDATE wechat_media_work SET status='analyzing',last_error=NULL")
        c.execute("UPDATE wechat_batch SET status='pending' WHERE id=?",(row['batch_id'],))
    assert calls==[]
    return service,clock,calls,row


def ids(personal,table):
    with personal.connection() as c:return {r[0] for r in c.execute('SELECT id FROM '+table)}


def test_prepare_and_enqueue_are_one_transaction(personal,monkeypatch):
    service,_,calls=stage(personal)
    enqueue=service.analysis.enqueue
    def fail_after_insertion(*args,**kwargs):
        enqueue(*args,**kwargs)
        raise OSError('fault after job/extraction insertion, before commit')
    with monkeypatch.context() as patch:
        patch.setattr(service.analysis,'enqueue',fail_after_insertion)
        with pytest.raises(OSError):asyncio.run(service.media_pipeline.tick(force=True))
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM ai_extraction').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM wechat_media_analysis').fetchone()[0]==0
        assert not c.execute("SELECT 1 FROM wechat_media_work WHERE status='analyzing'").fetchone()
    assert calls==[]
    asyncio.run(service.media_pipeline.tick(force=True))
    assert len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==1
        assert c.execute("SELECT status FROM wechat_media_work").fetchone()[0]=='completed'


def test_orphan_recovery_three_times_one_job_result_and_push(personal,monkeypatch):
    service,_,calls,row=legacy_orphan(personal,monkeypatch)
    original_ids={t:ids(personal,t) for t in ('raw_snapshot','wechat_candidate','event','task')}
    recovered=[service.media_pipeline.recover_missing_jobs()['created'] for _ in range(3)]
    assert recovered==[1,0,0] and calls==[]
    for table,value in original_ids.items():assert ids(personal,table)==value
    pushes=[]
    def push(request):pushes.append(request);return httpx.Response(200,json={'code':200})
    dispatcher=WeChatBarkDispatcher(personal,config=BarkConfig(True,'https://bark.invalid','media-recovery-test-only'),
        transport=httpx.MockTransport(push),clock=lambda:NOW)
    with personal.connection(write=True) as c:c.execute("UPDATE wechat_bark_settings SET enabled=1,activated_at='2020-01-01'")
    for _ in range(3):
        asyncio.run(service.media_pipeline.tick(force=True));dispatcher.tick()
        assert service.media_pipeline.recover_missing_jobs()['created']==0
    assert len(calls)==1 and len(pushes)==1
    dispatcher.tick()
    assert len(pushes)==1
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM wechat_candidate WHERE model_item_id!='_pending'").fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM wechat_bark_notification WHERE state='sent'").fetchone()[0]==1
        assert c.execute('SELECT status FROM wechat_media_work WHERE message_id=?',(row['message_id'],)).fetchone()[0]=='completed'
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==c.execute('SELECT count(*) FROM task').fetchone()[0]==0


@pytest.mark.parametrize('initial_status',['pending','analyzing'])
def test_startup_recovers_missing_job_and_three_restarts_are_idempotent(personal,monkeypatch,initial_status):
    _,_,calls,row=legacy_orphan(personal,monkeypatch)
    with personal.connection(write=True) as c:c.execute('UPDATE wechat_media_work SET status=?',(initial_status,))
    async def idle(self):await self.stop.wait()
    monkeypatch.setattr(WeChatService,'_run',idle)
    async def restart():
        service=WeChatService(personal,Settings(),adapter=FakeAdapter([]),clock=lambda:NOW)
        await service.start();await service.close()
    for _ in range(3):asyncio.run(restart())
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job WHERE batch_id=?',(row['batch_id'],)).fetchone()[0]==1
        assert c.execute('SELECT status FROM wechat_media_work').fetchone()[0]=='analyzing'
        assert c.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==0
    assert calls==[]


def test_failed_job_is_not_recreated_or_paid_again(personal,monkeypatch):
    service,_,calls,row=legacy_orphan(personal,monkeypatch,response='timeout')
    service.media_pipeline.recover_missing_jobs()
    for _ in range(3):asyncio.run(service.media_pipeline.tick(force=True))
    assert len(calls)==1
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==1
        assert c.execute('SELECT status FROM wechat_analysis_job').fetchone()[0]=='outcome_unknown'
        state=c.execute('SELECT status,last_error FROM wechat_media_work').fetchone()
        assert tuple(state)==('unreadable','timeout')
    assert service.media_pipeline.recover_missing_jobs()['created']==0


def test_recovery_enqueue_failure_rolls_back_partial_job_then_retries(personal,monkeypatch):
    service,_,calls,row=legacy_orphan(personal,monkeypatch)
    enqueue=service.analysis.enqueue
    def fail(*args,**kwargs):enqueue(*args,**kwargs);raise OSError('injected after insert')
    with monkeypatch.context() as patch:
        patch.setattr(service.analysis,'enqueue',fail)
        for _ in range(3):assert service.media_pipeline.recover_missing_jobs()['recovery_needed']==1
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM ai_extraction').fetchone()[0]==0
        assert tuple(c.execute('SELECT status,last_error FROM wechat_media_work').fetchone())==('recovery_needed','media_job_enqueue_failed')
    assert service.media_pipeline.recover_missing_jobs()['created']==1
    assert service.media_pipeline.recover_missing_jobs()['created']==0 and calls==[]


def test_saved_human_draft_requires_existing_explicit_retry(personal,monkeypatch):
    service,_,calls,row=legacy_orphan(personal,monkeypatch)
    with personal.connection(write=True) as c:c.execute("UPDATE raw_review SET draft_json=? WHERE raw_snapshot_id=(SELECT snapshot_id FROM wechat_batch WHERE id=?)",('{"events":[]}',row['batch_id']))
    assert service.media_pipeline.recover_missing_jobs()['recovery_needed']==1
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==0
        snapshot=c.execute('SELECT snapshot_id FROM wechat_batch WHERE id=?',(row['batch_id'],)).fetchone()[0]
    asyncio.run(service.reanalyze(snapshot))
    assert service.media_pipeline.recover_missing_jobs()['linked']==1
    with personal.connection() as c:
        assert c.execute('SELECT draft_json FROM raw_review WHERE raw_snapshot_id=?',(snapshot,)).fetchone()[0]=='{"events":[]}'
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==1
    assert calls==[]


@pytest.mark.parametrize('block',['disabled','superseded','existing_result'])
def test_unsafe_orphan_is_visible_without_paid_replay(personal,monkeypatch,block):
    service,_,calls,row=legacy_orphan(personal,monkeypatch)
    if block=='disabled':service.document.wechat.enabled=False
    with personal.connection(write=True) as c:
        if block=='superseded':c.execute('UPDATE wechat_batch SET superseded_by=id WHERE id=?',(row['batch_id'],))
        if block=='existing_result':c.execute("UPDATE wechat_candidate SET model_item_id='already-reviewed' WHERE batch_id=?",(row['batch_id'],))
    for _ in range(3):assert service.media_pipeline.recover_missing_jobs()['recovery_needed']==1
    # A later ordinary queue cycle must not bypass the explicit recovery boundary.
    asyncio.run(service.process_queue())
    with personal.connection() as c:
        assert c.execute('SELECT status FROM wechat_media_work').fetchone()[0]=='recovery_needed'
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==0
    assert calls==[]


def test_concurrent_recovery_and_changed_settings_still_create_one_job(personal,monkeypatch):
    service,_,calls,row=legacy_orphan(personal,monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:service.media_pipeline.recover_missing_jobs(),range(2)))
    assert sum(r['created'] for r in results)==1
    monkeypatch.setattr(service.context,'read',lambda:pytest.fail('An existing job must be reused even if context changed'))
    assert service.media_pipeline.recover_missing_jobs()['created']==0
    with personal.connection() as c:assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==1
    assert calls==[]


def test_missing_prepared_context_is_recoverable_not_waiting_forever(personal,monkeypatch):
    service,_,calls,row=legacy_orphan(personal,monkeypatch)
    with personal.connection(write=True) as c:c.execute('UPDATE wechat_media_work SET batch_id=NULL')
    assert service.media_pipeline.recover_missing_jobs()['recovery_needed']==1
    with personal.connection() as c:assert tuple(c.execute('SELECT status,last_error FROM wechat_media_work').fetchone())==('recovery_needed','media_recovery_context')
    assert calls==[]


def test_legacy_cross_batch_input_dedup_never_repeats_paid_input(personal,monkeypatch):
    service,_,calls,row=legacy_orphan(personal,monkeypatch,response=lambda p:ocr_answer(p,'ignore'))
    with personal.connection(write=True) as c:
        prior=c.execute('SELECT id,input_hash FROM wechat_batch WHERE id!=? ORDER BY created_at LIMIT 1',(row['batch_id'],)).fetchone()
        c.execute("UPDATE wechat_batch SET superseded_by=NULL,status='pending' WHERE id=?",(prior['id'],))
        c.execute('UPDATE wechat_batch SET input_hash=? WHERE id=?',(prior['input_hash'],row['batch_id']))
    prior_job=service.analysis.enqueue(prior['id'],service.context.read())
    asyncio.run(service.analysis.run_one(prior_job))
    assert len(calls)==1
    # This is the exact old cross-batch return that left work waiting for a nonexistent job.
    assert service.analysis.enqueue(row['batch_id'],service.context.read())==prior_job
    for _ in range(3):assert service.media_pipeline.recover_missing_jobs()['recovery_needed']==1
    asyncio.run(service.process_queue())
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_analysis_job').fetchone()[0]==1
        assert tuple(c.execute('SELECT status,last_error FROM wechat_media_work').fetchone())==('recovery_needed','media_job_scope_conflict')
        assert c.execute('SELECT status FROM wechat_batch WHERE id=?',(row['batch_id'],)).fetchone()[0]=='manual_review'
    assert len(calls)==1
    # Reuse the existing explicit force-reanalysis path if the user elects to rerun.
    with personal.connection() as c:snapshot=c.execute('SELECT snapshot_id FROM wechat_batch WHERE id=?',(row['batch_id'],)).fetchone()[0]
    asyncio.run(service.reanalyze(snapshot,force=True))
    assert service.media_pipeline.recover_missing_jobs()['linked']==1
    for _ in range(3):assert service.media_pipeline.recover_missing_jobs()['created']==0
    with personal.connection() as c:assert c.execute('SELECT count(*) FROM wechat_analysis_job WHERE batch_id=?',(row['batch_id'],)).fetchone()[0]==1
    assert len(calls)==1
