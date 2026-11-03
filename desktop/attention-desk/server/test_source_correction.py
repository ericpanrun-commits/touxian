"""Explicit text correction cannot spend a repair, change facts or overwrite history."""
import asyncio
import copy
from uuid import uuid4
import pytest
from server.personal_store import PersonalError
from server.school.models import SourceCorrectionInput
from server.school.source_correction import save_correction
from server.test_school import rig,prepared,generate_input,finish
from server.test_review_recovery import legacy_failure,repair_provider


async def candidate(r, root, monkeypatch):
    p=prepared(r.service,root)
    _,original=await legacy_failure(r,p,monkeypatch)
    repair_provider(r,valid=False)
    queued=await r.service.generate(p.lecture['id'],generate_input(p.lecture,use_vision=False))
    await finish(r.service)
    result=r.service.artifacts.get(r.service.jobs.get(queued['job']['id'])['result']['artifact_id'])
    assert result['source_correction'] and not result['content']
    fields=result['source_correction']['fields']
    body={'request_id':str(uuid4()),'expected_version':p.lecture['version'],
        'draft_hash':result['source_correction']['draft_hash'],
        'changes':[{'key':f['key'],'segments':['请对照来源核对这段说明。']+f['segments'][1:]} for f in fields]}
    return p,original,result,body


def test_explicit_correction_is_append_only_idempotent_and_free(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            p,original,parent,body=await candidate(r,tmp_path,monkeypatch)
            with r.store.connection() as c:
                before={row['id']:dict(row) for row in c.execute('SELECT * FROM review_artifact')}
                attempts=c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]
            saved=save_correction(r.store,parent['id'],SourceCorrectionInput.model_validate(body))
            assert save_correction(r.store,parent['id'],SourceCorrectionInput.model_validate(body))==saved
            repeated={**body,'request_id':str(uuid4())}
            assert save_correction(r.store,parent['id'],SourceCorrectionInput.model_validate(repeated))==saved
            corrected=r.service.artifacts.get(saved['artifact_id'])
            assert corrected['content'] and corrected['version']==parent['version']+1
            assert corrected['status']=='needs_review' and not corrected['adopted']
            assert corrected['provider']=='source_checked' and corrected['cost']['text_calls']==0
            assert corrected['generation']['validation_status']=='passed'
            assert corrected['generation']['recovered_from']==parent['id']
            assert corrected['input_versions']['source_checked_revision']['changes']
            assert corrected['sources'] and r.provider.calls==['evidence','composition','repair']
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==attempts
                assert all(dict(c.execute('SELECT * FROM review_artifact WHERE id=?',(identity,)).fetchone())==row for identity,row in before.items())
                assert not c.execute('PRAGMA foreign_key_check').fetchall()
                job=c.execute('SELECT * FROM attachment_job WHERE id=?',(corrected['job_id'],)).fetchone()
                assert job['operation']=='school_source_correction' and job['status']=='completed'
            body['changes'][0]['segments'][0]='同一请求更改内容。'
            with pytest.raises(PersonalError,match='同一发送标识'):save_correction(r.store,parent['id'],SourceCorrectionInput.model_validate(body))
    asyncio.run(run())


@pytest.mark.parametrize('change',['unlisted_field','changed_slots','forged_slot','still_invalid','stale_hash','stale_lecture','source_changed','unknown_attempt'])
def test_unsafe_or_stale_correction_preserves_everything(tmp_path,monkeypatch,change):
    async def run():
        async with rig(tmp_path) as r:
            p,_,parent,body=await candidate(r,tmp_path,monkeypatch)
            if change=='unlisted_field':body['changes'][0]['key']='/must_master/0/text'
            elif change=='changed_slots':body['changes'][0]['segments'].append('额外文字')
            elif change=='forged_slot':body['changes'][0]['segments'][0]='[[F99]]'
            elif change=='still_invalid':body['changes'][0]['segments'][0]='变量趋于五。'
            elif change=='stale_hash':body['draft_hash']='0'*64
            elif change=='stale_lecture':body['expected_version']+=1
            elif change=='source_changed':
                with r.store.connection(write=True) as c:c.execute('UPDATE document SET version=version+1 WHERE id=?',(p.note['id'],))
            elif change=='unknown_attempt':
                with r.store.connection(write=True) as c:c.execute("UPDATE school_ai_attempt SET status='unknown' WHERE job_id=?",(parent['job_id'],))
            with r.store.connection() as c:count=c.execute('SELECT count(*) FROM review_artifact').fetchone()[0]
            with pytest.raises(PersonalError):save_correction(r.store,parent['id'],SourceCorrectionInput.model_validate(body))
            with r.store.connection() as c:assert c.execute('SELECT count(*) FROM review_artifact').fetchone()[0]==count
            assert r.provider.calls==['evidence','composition','repair']
    asyncio.run(run())


def test_durable_command_uses_same_atomic_receipt_and_source_guards(tmp_path,monkeypatch):
    from server.cloud_sync.commands import CommandExecutor
    async def run():
        async with rig(tmp_path) as r:
            _,_,parent,body=await candidate(r,tmp_path,monkeypatch)
            executor=CommandExecutor(r.store,r.service,r.extract,None)
            command={'id':str(uuid4()),'method':'POST','path':'/school/reviews/'+parent['id']+'/source-correction','body':body}
            first=executor.pure(command);assert executor.pure(command)==first
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM cloud_command_receipt WHERE id=?',(command['id'],)).fetchone()[0]==1
                assert c.execute("SELECT count(*) FROM review_artifact WHERE provider='source_checked'").fetchone()[0]==1
    asyncio.run(run())


@pytest.mark.parametrize('proof',['valid','unrelated_root','unfinished_job'])
def test_capture_follows_only_a_validated_source_revision_of_its_exact_failed_review(tmp_path,monkeypatch,proof):
    from server.learning.service import LearningService
    from server.cloud_sync.projection import CloudProjection
    async def run():
        async with rig(tmp_path) as r:
            p,original,parent,body=await candidate(r,tmp_path,monkeypatch)
            learning=LearningService(r.store,r.service,r.extract)
            try:
                with r.store.connection() as c:sid=c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=? ORDER BY created_at LIMIT 1',(p.raw_id,)).fetchone()[0]
                context={'note_share':True,'course_id':'course-math-ii','lecture_id':p.lecture['id'],
                    'note_automation':{'status':'needs_review','document_id':p.note['id'],'review_id':original['id'] if proof!='unrelated_root' else 'unrelated-review',
                    'review_job_id':original['job_id'],'message':'The original review failed.'}}
                capture=learning.store.record_capture(p.raw_id,sid,'Isolated source check',context)
                learning.store.capture_status(capture['id'],'needs_context',error={'message':'Review stopped'})
                result=save_correction(r.store,parent['id'],SourceCorrectionInput.model_validate(body))
                corrected=r.service.artifacts.get(result['artifact_id'])
                if proof=='unfinished_job':r.service.jobs.update(corrected['job_id'],'running','Not committed')
                local=learning.capture_detail(capture['id'])
                assert local['status']==('completed' if proof=='valid' else 'needs_context')
                if proof=='valid':assert local['context']['note_automation']['review_id']==corrected['id'] and local['error'] is None
                projection=CloudProjection(r.store,tmp_path/'projection').records()
                cloud=next(item['payload'] for item in projection if item['kind']=='learning_capture' and item['id']==capture['id'])
                assert cloud['status']==local['status']
                assert r.provider.calls==['evidence','composition','repair'] and not corrected['adopted']
            finally:await learning.close()
    asyncio.run(run())
