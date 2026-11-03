"""Store-first, durable receipts, failures, human confirmation and OCR semantics."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from fastapi import FastAPI
from server.test_collector import rig, done, payload, count, TEXT
from server.test_extraction_review import candidate, confirmation, finish
from server.extraction_errors import ExtractionFailure
from server.extraction_provider import ProviderResult
from server.extraction_models import RawInput
from server.review_store import ReviewStore
from server.personal_store import PersonalError
from server.inbox_store import InboxStore, extraction_quality
from server.inbox_cost import estimate
from server.collector.submissions import SubmissionService
from server.documents.policy import DocumentError
from server.test_documents import text_pdf, screenshot
from server.inbox_api import router as inbox_router
from server.review_api import router as review_router
from server.documents.api import router as documents_router


async def settled(r, receipt):
    if r.service.tasks: await asyncio.gather(*list(r.service.tasks.values()))
    return InboxStore(r.store).detail(receipt['submission_id'])


def test_store_first_ack_before_processing_and_durable_restart(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            gate = asyncio.Event()
            original = r.service._finish
            async def held(raw): await gate.wait(); await original(raw)
            r.service._finish = held
            p = payload(); receipt = await r.service.submit('desktop', 'text', p)
            d = InboxStore(r.store).detail(receipt['submission_id'])
            assert receipt['saved'] and receipt['state'] == 'received'
            assert d['original']['original_text'] == TEXT and count(r.store, 'ai_extraction') == 0
            await r.service.close()
            restart = SubmissionService(r.store, r.docs, r.extraction)
            assert (await restart.status('desktop', receipt['id']))['saved']
            assert InboxStore(r.store).detail(d['id'])['processing_status'] == 'recovery_needed'
            same = await restart.submit('desktop', 'text', p)
            assert same['duplicate'] and same['submission_id'] == d['id'] and not restart.tasks
            r.service = restart
            await restart.retry(d['id'], InboxStore(r.store).detail(d['id'])['version'])
            ready = await settled(r, receipt)
            assert ready['processing_status'] == 'needs_review' and r.provider.calls == 0
    asyncio.run(run())


def test_shared_desktop_routes_ack_file_and_text(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            app = FastAPI(); app.state.personal_store = r.store; app.state.inbox_service = r.service
            app.state.document_service = r.docs; app.state.extraction_service = r.extraction
            for router in (inbox_router, review_router, documents_router): app.include_router(router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                key = str(uuid4())
                text = await client.post('/api/personal/raw?inbox_kind=text', json={'original_text': 'IELTS speaking study note'}, headers={'Idempotency-Key': key})
                assert text.status_code == 201 and text.json()['saved']
                same = await client.post('/api/personal/raw?inbox_kind=text', json={'original_text': 'IELTS speaking study note'}, headers={'Idempotency-Key': key})
                assert same.json()['duplicate']
                unsupported = await client.post('/api/personal/collect-files?inbox=1', files={'files': ('design.cad', b'safe inert CAD sample', 'application/octet-stream')}, headers={'Idempotency-Key': str(uuid4())})
                assert unsupported.status_code == 201 and unsupported.json()['saved']
                d = await settled(r, unsupported.json())
                assert d['processing_status'] == 'failed' or d['error']['code'] == 'unsupported'
                assert (await client.get('/api/personal/inbox')).json()['total'] == 2
                file = d['original']['attachments'][0]
                assert (await client.get(f"/api/personal/attachments/{file['id']}/original")).content == b'safe inert CAD sample'
                legacy = await client.post('/api/personal/raw', json={'original_text': 'Legacy entry unaffected'})
                assert legacy.status_code == 201 and 'snapshot' in legacy.json()
    asyncio.run(run())


def test_file_hash_dedup_across_name_extension_owner_and_restart(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            path = tmp_path/'note.txt'; path.write_text('IELTS speaking study note', encoding='utf-8')
            first = await r.service.submit('desktop', 'file', payload(text=''), [('note.txt', 'text/plain', path)])
            d = await settled(r, first)
            with r.store.connection() as c: before = c.execute('SELECT count(*) FROM attachment_job').fetchone()[0]
            second = await r.service.submit('phone', 'file', payload(text=''), [('renamed.bin', 'application/octet-stream', path)])
            assert second['duplicate'] and first['submission_id'] == second['submission_id']
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM attachment_job').fetchone()[0] == before
                assert c.execute('SELECT count(*) FROM inbox_receipt').fetchone()[0] == 2
            assert count(r.store, 'raw_item') == count(r.store, 'attachment') == 1
            assert len(list(r.docs.files.root.rglob('*.*'))) == 1 and r.provider.calls == 0
            assert d['classification']['domain'] == 'ielts'
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['pdf', 'ocr', 'missing_job', 'worker'])
def test_failures_keep_original_and_retry_without_paid_replay(tmp_path, mode):
    async def run():
        async with rig(tmp_path) as r:
            path = text_pdf(tmp_path/'notice.pdf', ['ordinary readable document'])
            if mode == 'pdf': path.write_bytes(b'%PDF-1.7\nbroken')
            if mode == 'ocr':
                r.docs.worker = lambda *a, **k: (_ for _ in ()).throw(DocumentError('ocr_unavailable', 'OCR unavailable'))
            if mode in {'missing_job', 'worker'}:
                r.docs.start = lambda *a, **k: {'id': 'no-job'} if mode == 'missing_job' else (_ for _ in ()).throw(RuntimeError('worker stopped'))
            receipt = await r.service.submit('desktop', 'file', payload(text=''), [(path.name, 'application/pdf', path)])
            d = await settled(r, receipt)
            assert receipt['saved'] and d['processing_status'] in {'failed', 'needs_review'} and d['needs_manual']
            a = d['original']['attachments'][0]
            assert r.docs.files.path(r.docs.files.get(a['id'])).read_bytes() == path.read_bytes()
            assert d['error'] and r.provider.calls == 0 and count(r.store, 'task') == 0
    asyncio.run(run())


@pytest.mark.parametrize('quality', ['low', 'unverified'])
def test_successful_pdf_structure_and_ocr_never_mean_understood(tmp_path, quality):
    async def run():
        async with rig(tmp_path) as r:
            path = text_pdf(tmp_path/'notes.pdf', ['native text only as mock worker input'])
            def worker(_path, _ext, destination, **_):
                Path(destination).write_text(json.dumps({'extracted_text': 'fragment', 'metadata': {'extractor': 'pypdf+windows.media.ocr', 'extractor_version': 'test', 'page_count': 3, 'needs_ocr': True, 'needs_review': True, 'ocr': {'quality': quality, 'confidence': None}}, 'blocks': [{'text': 'fragment', 'source_type': 'image_ocr', 'page': 1, 'section': 'OCR', 'needs_review': True}]}), encoding='utf-8')
            r.docs.worker = worker
            receipt = await r.service.submit('desktop', 'file', payload(text=''), [(path.name, 'application/pdf', path)])
            d = await settled(r, receipt)
            assert d['processing_status'] == 'needs_review' and d['error']['code'] == 'extraction_warning'
            f = d['extraction_quality']['files'][0]
            assert f['saved'] and f['pdf_structure'] == 'readable' and f['content_quality'] == 'needs_review'
            assert f['confidence'] is None and d['extraction_quality']['reasons'] and r.provider.calls == 0
    asyncio.run(run())


def test_course_uncertainty_manual_change_clear_project_and_conflict(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            receipt = await r.service.submit('desktop', 'text', payload(text='某课程课堂笔记，不确定具体课程'))
            d = await settled(r, receipt); store = InboxStore(r.store)
            assert not d['classification']['possible_course'] and not d['course']
            linked = store.link(d['id'], d['version'], 'course-math-ii', 'Math notes')
            assert linked['course']['name'] == '高等数学 II'
            assert linked['review']['item']['inbox_course_id'] == 'course-math-ii'
            with pytest.raises(PersonalError): store.link(d['id'], d['version'], None, '')
            cleared = store.link(d['id'], linked['version'], None, '')
            assert cleared['course'] is None and cleared['project_ref'] is None
            assert cleared['original']['original_text'] == d['original']['original_text']
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['confirm', 'reject', 'ai_fail'])
def test_explicit_proposal_reuses_existing_confirmation_and_cost(tmp_path, outcome):
    async def run():
        async with rig(tmp_path, failure=ExtractionFailure('provider_unavailable', 'unavailable') if outcome == 'ai_fail' else None) as r:
            receipt = await r.service.submit('desktop', 'text', payload())
            d = await settled(r, receipt); assert r.provider.calls == count(r.store, 'task') == 0
            await r.extraction.start(d['snapshot_id']); await finish(r.extraction)
            d = InboxStore(r.store).detail(d['id'])
            assert r.provider.calls == 1 and d['review']['extraction']['cost'] is not None
            if outcome == 'confirm':
                assert d['proposal_count'] == 1
                approved = confirmation(d['review'], candidate(TEXT))
                event = ReviewStore(r.store).confirm(d['snapshot_id'], approved)['events'][0]
                again = ReviewStore(r.store).confirm(d['snapshot_id'], approved)['events'][0]
                assert event['id'] == again['id'] and count(r.store, 'task') == 1
            elif outcome == 'reject':
                ReviewStore(r.store).set_status(d['snapshot_id'], d['review']['version'], 'cancelled')
                assert InboxStore(r.store).detail(d['id'])['processing_status'] == 'completed' and count(r.store, 'task') == 0
            else:
                assert d['processing_stage'] == 'analysis' and d['processing_status'] == 'failed'
                assert d['original']['original_text'] == TEXT
            again = await r.service.submit('desktop', 'text', payload())
            assert again['duplicate'] and r.provider.calls == 1
    asyncio.run(run())


def test_old_attention_read_without_backfill_or_duplicate_sources(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            old = ReviewStore(r.store).collect(RawInput(original_text='Existing official body'))
            store = InboxStore(r.store); d = store.list(scope='all')
            assert d['total'] == 1 and d['items'][0]['id'] == old['snapshot']['raw_item_id']
            assert store.list(scope='submitted')['total'] == 0 and count(r.store, 'raw_item') == 1
    asyncio.run(run())


def test_cost_known_unknown_cached_and_cache_hits():
    info = estimate('deepseek', 'deepseek-flash', {'prompt_tokens': 1000, 'completion_tokens': 100, 'prompt_cache_hit_tokens': 200}, 'https://api.deepseek.com')
    assert info['estimated_cost'] == pytest.approx(.002408)
    assert estimate('deepseek', 'deepseek-flash', {'total_tokens': 1}, 'https://api.deepseek.com')['estimated_cost'] is None
    assert estimate('deepseek', 'other-model', {'prompt_tokens': 1}, 'https://api.deepseek.com')['estimated_cost'] is None
    assert estimate('deepseek', 'deepseek-flash', None, 'https://api.deepseek.com', True)['estimated_cost'] == 0


@pytest.mark.parametrize('kind', ['image', 'pdf'])
def test_original_preview_available_even_when_ocr_or_extraction_failed(tmp_path, kind):
    async def run():
        async with rig(tmp_path) as r:
            file = screenshot(tmp_path/'source.png', TEXT) if kind == 'image' else text_pdf(tmp_path/'source.pdf', ['readable PDF original'])
            worker = r.docs.worker
            r.docs.worker = lambda *a, **k: (_ for _ in ()).throw(DocumentError('ocr_unavailable', 'OCR failed'))
            receipt = await r.service.submit('desktop', 'file', payload(text=''), [(file.name, 'image/png' if kind == 'image' else 'application/pdf', file)])
            d = await settled(r, receipt)
            assert d['processing_status'] == 'failed' and d['original']['attachments'][0]['parse_status'] == 'failed'
            r.docs.worker = worker
            app = FastAPI(); app.state.document_service = r.docs; app.include_router(documents_router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                aid = d['attachments'][0]['id']
                preview = await client.get(f'/api/personal/attachments/{aid}/image' + ('?page=1' if kind == 'pdf' else ''))
                assert preview.status_code == 200 and preview.content.startswith(b'\x89PNG')
            assert r.provider.calls == count(r.store, 'task') == 0
    asyncio.run(run())


def test_real_apple_notes_pdf_local_parse_course_and_quality(tmp_path):
    sample = Path(r'D:\Eric OS\test-data\apple-notes\极限存在准则与两个重要极限.pdf')
    if not sample.is_file(): pytest.skip('Real user-provided sample is external to the repository')
    original = hashlib.sha256(sample.read_bytes()).hexdigest()
    async def run():
        async with rig(tmp_path) as r:
            receipt = await r.service.submit('desktop', 'file', payload(text=''), [(sample.name, 'application/pdf', sample)])
            d = await settled(r, receipt)
            assert d['processing_status'] == 'needs_review' and d['extraction_quality']['needs_review']
            f = d['extraction_quality']['files'][0]
            assert f['saved'] and f['pdf_structure'] == 'readable' and f['page_count'] == 2
            assert d['review']['snapshot']['blocks'] and r.provider.calls == 0
            linked = InboxStore(r.store).link(d['id'], d['version'], 'course-math-ii', '')
            assert linked['course']['name'] == '高等数学 II' and count(r.store, 'task') == 0
    asyncio.run(run())
    assert hashlib.sha256(sample.read_bytes()).hexdigest() == original
