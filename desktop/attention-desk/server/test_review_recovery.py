"""A known failed draft gets one source-bound repair, never a paid replay."""
import asyncio
import copy
import json
import pytest
from server.extraction_provider import ProviderResult
from server.personal_store import encode
from server.school.recovery import saved_composition
from server.test_school import rig, prepared, finish, generate_input, composition_output


async def legacy_failure(r, p, monkeypatch):
    """Simulate the earlier release that could not target an empty section."""
    import server.school.service as module
    normal = r.provider.compose
    async def compose(evidence, context, config, key, gate):
        response = await normal(evidence, context, config, key, gate)
        draft = json.loads(response.raw_output); draft['notes_taught'] = []
        draft['core_concepts'][0]['explanation']['text'] = '变量趋于三。'
        return ProviderResult(encode(draft), response.usage, response.response_model)
    r.provider.compose = compose
    with monkeypatch.context() as old:
        old.setattr(module, 'repair_roots', lambda *args: set())
        queued = await r.service.generate(p.lecture['id'], generate_input(p.lecture, use_vision=False))
        await finish(r.service)
    r.provider.compose = normal
    job = r.service.jobs.get(queued['job']['id'])
    artifact = r.service.artifacts.get(job['result']['artifact_id'])
    assert job['status'] == 'needs_review' and not artifact['generation']['repair_used']
    return job, artifact


def repair_provider(r, *, valid=True):
    async def repair(stage, raw, issues, evidence, context, config, key, gate):
        gate(); r.provider.calls.append('repair')
        good = composition_output(evidence, context)
        if not valid: good['core_concepts'][0]['explanation']['text'] = '老师没有讲这个内容。'
        return ProviderResult(encode({'patches':[
            {'op':'add','path':'/notes_taught/0','value':good['notes_taught'][0]},
            {'op':'replace','path':'/core_concepts/0','value':good['core_concepts'][0]}]}),
            {'prompt_tokens':100,'completion_tokens':100}, 'deepseek-flash')
    r.provider.repair = repair


def test_saved_failure_repairs_once_into_new_version_without_resending_evidence_or_images(tmp_path, monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path, notes_quality='ocr_unverified')
            old_job, old = await legacy_failure(r, p, monkeypatch)
            repair_provider(r)
            request = generate_input(p.lecture, use_vision=True)
            queued = await r.service.generate(p.lecture['id'], request); await finish(r.service)
            job = r.service.jobs.get(queued['job']['id'])
            artifact = r.service.artifacts.get(job['result']['artifact_id'])
            assert job['payload']['repair_from'] == old['id']
            assert job['status'] == 'completed' and artifact['content']
            assert r.provider.calls == ['evidence','composition','repair']
            assert artifact['version'] == old['version'] + 1 and artifact['generation']['recovered_from'] == old['id']
            assert artifact['generation']['repair_used'] and artifact['cost']['text_calls'] == 1
            assert artifact['cost']['image_calls'] == 0
            assert artifact['content']['notes_taught'][0]['provenance'] == 'uncertain'
            assert artifact['status'] == 'needs_review' and not artifact['adopted']
            summaries = {item['id']: item for item in r.service.store.lecture(p.lecture['id'])['artifacts']}
            assert summaries[old['id']]['has_content'] is False
            assert summaries[artifact['id']]['has_content'] is True
            assert r.service.artifacts.get(old['id']) == old
            assert r.service.jobs.get(old_job['id']) == old_job
            again = await r.service.generate(p.lecture['id'], request); await finish(r.service)
            assert again['job']['id'] == job['id'] and r.provider.calls == ['evidence','composition','repair']
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM task').fetchone()[0] == 0
                assert c.execute('SELECT accepted_artifact_id FROM lecture').fetchone()[0] is None
                assert not c.execute('PRAGMA foreign_key_check').fetchall()
    asyncio.run(run())


@pytest.mark.parametrize('change', ['source','lecture_version','model','unknown_attempt','prepared_attempt','spent_repair','spent_repair_ledger','tampered_evidence'])
def test_recovery_requires_exact_current_sources_model_and_known_unspent_attempts(tmp_path, monkeypatch, change):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path); old_job, old = await legacy_failure(r, p, monkeypatch)
            if change == 'source':
                r.service.learning.store_page(p.note['id'], 1, {'extracted_text':'Different newly supplied evidence.', 'metadata':{}}, method='pypdf', quality='text_extracted')
            elif change == 'lecture_version':
                with r.store.connection(write=True) as c: c.execute('UPDATE lecture SET version=version+1 WHERE id=?', (p.lecture['id'],))
            elif change in {'unknown_attempt','prepared_attempt'}:
                with r.store.connection(write=True) as c:
                    c.execute('UPDATE school_ai_attempt SET status=? WHERE id=?',
                        ('unknown' if change == 'unknown_attempt' else 'prepared', old['generation']['attempts'][-1]['id']))
            elif change == 'spent_repair_ledger':
                with r.store.connection(write=True) as c:
                    c.execute("UPDATE school_ai_attempt SET prompt_version='lecture-targeted-repair-v3' WHERE id=?", (old['generation']['attempts'][-1]['id'],))
            elif change in {'spent_repair','tampered_evidence'}:
                generation = copy.deepcopy(old['generation'])
                if change == 'spent_repair': generation['repair_used'] = True
                else: generation['evidence_extraction']['facts'][0]['text'] = 'Changed outside the saved evidence request.'
                r.service.artifacts.record_generation(old['id'], generation)
            context = r.service.contexts.build(p.lecture['id'])
            assert saved_composition(r.store, context, 'changed-model' if change == 'model' else old['model']) is None
            assert r.provider.calls == ['evidence','composition']
    asyncio.run(run())


def test_failed_repair_stops_and_consumes_original_allowance_without_whole_generation(tmp_path, monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path); _, old = await legacy_failure(r, p, monkeypatch)
            repair_provider(r, valid=False)
            queued = await r.service.generate(p.lecture['id'], generate_input(p.lecture, use_vision=False)); await finish(r.service)
            job = r.service.jobs.get(queued['job']['id']); artifact = r.service.artifacts.get(job['result']['artifact_id'])
            assert job['status'] == 'needs_review' and artifact['content'] is None
            assert artifact['generation']['recovered_from'] == old['id']
            assert artifact['generation']['attempts'][0]['validation'] == 'failed'
            assert artifact['cost']['text_calls'] == 1
            assert saved_composition(r.store, r.service.contexts.build(p.lecture['id']), old['model']) is None
            await r.service.generate(p.lecture['id'], generate_input(p.lecture, use_vision=False)); await finish(r.service)
            assert r.provider.calls == ['evidence','composition','repair']
    asyncio.run(run())


def test_source_change_after_repair_reservation_sends_nothing(tmp_path, monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path); _, old = await legacy_failure(r, p, monkeypatch)
            repair_provider(r)
            with monkeypatch.context() as held:
                held.setattr(r.service, '_schedule_ai', lambda *args: None)
                queued = await r.service.generate(p.lecture['id'], generate_input(p.lecture, use_vision=False))
            assert queued['job']['payload']['repair_from'] == old['id']
            r.service.learning.store_page(p.note['id'], 1, {'extracted_text':'Source changed before the reserved repair started.', 'metadata':{}}, method='pypdf', quality='text_extracted')
            await r.service._review(queued['job']['id'])
            job = r.service.jobs.get(queued['job']['id'])
            assert job['status'] == 'failed' and job['errors'][0]['code'] == 'conflict'
            assert r.provider.calls == ['evidence','composition']
    asyncio.run(run())


def test_explicit_repair_never_falls_back_to_paid_full_generation(tmp_path, monkeypatch):
    from uuid import uuid4
    from server.personal_store import PersonalError
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path); _, old = await legacy_failure(r, p, monkeypatch)
            with pytest.raises(PersonalError, match='来源或修复额度'):
                await r.service.generate(p.lecture['id'], generate_input(p.lecture, use_vision=False, repair_from=str(uuid4())))
            with pytest.raises(PersonalError, match='不能同时'):
                await r.service.generate(p.lecture['id'], generate_input(p.lecture, use_vision=False, repair_from=old['id'], force=True))
            assert r.provider.calls == ['evidence','composition']
            repair_provider(r)
            request = generate_input(p.lecture, use_vision=False, repair_from=old['id'])
            queued = await r.service.generate(p.lecture['id'], request); await finish(r.service)
            assert r.service.jobs.get(queued['job']['id'])['status'] == 'completed'
            again = await r.service.generate(p.lecture['id'], request); await finish(r.service)
            assert again['job']['id'] == queued['job']['id']
            assert r.provider.calls == ['evidence','composition','repair']
    asyncio.run(run())


@pytest.mark.parametrize('proof', ['valid','unrelated_review','missing_note_source','unfinished_job'])
def test_capture_and_cloud_close_only_for_validated_repair_of_this_exact_note(tmp_path, monkeypatch, proof):
    from server.cloud_sync.projection import CloudProjection
    from server.cloud_sync.service import capture_states
    from server.inbox_store import InboxStore
    from server.learning.service import LearningService
    async def run():
        async with rig(tmp_path) as r:
            p = prepared(r.service, tmp_path); old_job, old = await legacy_failure(r, p, monkeypatch)
            learning = LearningService(r.store, r.service, r.extract)
            with r.store.connection() as c:
                sid = c.execute('SELECT id FROM raw_snapshot WHERE raw_item_id=? ORDER BY created_at LIMIT 1', (p.raw_id,)).fetchone()[0]
            context = {'note_share':True, 'course_id':'course-math-ii', 'lecture_id':p.lecture['id'],
                'note_automation':{'status':'needs_review','document_id':p.note['id'],
                    'review_id':old['id'],'review_job_id':old_job['id'],'message':'Review validation stopped.'}}
            capture = learning.store.record_capture(p.raw_id, sid, 'Isolated recovery receipt', context)
            learning.store.capture_status(capture['id'], 'needs_context', error={'message':'Review validation stopped.'})
            repair_provider(r)
            # Build negative provenance fixtures before the append-only content
            # is finalized. Never disable or bypass the immutability trigger.
            finalize = r.service.artifacts.finish
            def finish_fixture(artifact_id, content, evidence, cost, **kwargs):
                if content and proof == 'unrelated_review':
                    with r.store.connection() as c:
                        versions = json.loads(c.execute('SELECT input_versions_json FROM review_artifact WHERE id=?',(artifact_id,)).fetchone()[0])
                    generation = versions['generation']; generation.pop('recovered_from')
                    r.service.artifacts.record_generation(artifact_id, generation)
                if content and proof == 'missing_note_source':
                    evidence = [source for source in evidence if source['source_kind'] != 'notes']
                return finalize(artifact_id, content, evidence, cost, **kwargs)
            r.service.artifacts.finish = finish_fixture
            queued = await r.service.generate(p.lecture['id'], generate_input(p.lecture, use_vision=False)); await finish(r.service)
            job = r.service.jobs.get(queued['job']['id']); artifact = r.service.artifacts.get(job['result']['artifact_id'])
            assert job['status'] == 'completed'
            if proof == 'unfinished_job':
                r.service.jobs.update(job['id'], 'running', 'Not committed yet')
            expected = 'completed' if proof == 'valid' else 'needs_context'
            local = learning.capture_detail(capture['id']); assert local['status'] == expected
            records = CloudProjection(r.store,tmp_path/'cloud-cache').records()
            cloud = next(item['payload'] for item in records if item['kind']=='learning_capture')
            assert cloud == {key:value for key,value in local.items() if key!='lecture_choices'}
            assert InboxStore(r.store).detail(p.raw_id)['learning_capture']['status'] == expected
            receipt = {'kind':'inbox_item','payload':{'id':p.raw_id,'version':1,'needs_manual':False,'processing_status':'completed'}}
            state = capture_states([receipt,{'kind':'learning_capture','payload':cloud}])[0]
            assert state['state'] == ('completed' if proof=='valid' else 'needs_confirmation')
            if proof == 'valid':
                assert local['error'] is None and state['error'] is None
                assert local['context']['note_automation']['review_id'] == artifact['id']
            with r.store.connection() as c:
                assert json.loads(c.execute('SELECT context_json FROM learning_capture WHERE id=?',(capture['id'],)).fetchone()[0]) == context
            assert r.provider.calls == ['evidence','composition','repair']
            await learning.close()
    asyncio.run(run())
