"""Known blank PDF pages must not block or consume note vision requests."""
import asyncio
from uuid import uuid4
import pytest
from server.personal_store import PersonalError
from server.inbox_store import InboxStore
from server.test_documents import local_snapshot, text_pdf
from server.test_school import rig, finish
from server.school.models import LectureInput


@pytest.mark.parametrize('reuse_blank_proof', [True, False])
def test_note_index_and_vision_use_saved_blank_proof(tmp_path, monkeypatch, reuse_blank_proof):
    async def run():
        async with rig(tmp_path, fixture_mode=False) as r:
            raw=local_snapshot(r.store,text_pdf(tmp_path/'note.pdf',[
                'Synthetic note with derivative conditions and a trailing blank page.', '']))
            rid=raw['snapshot']['raw_item_id']
            InboxStore(r.store).link(rid,1,'course-math-ii','')
            doc=r.service.learning.register(raw['snapshot']['attachments'][0]['id'])
            assert r.service.learning.blank_pages(doc['id'])=={2}
            if not reuse_blank_proof:
                monkeypatch.setattr(r.service.learning,'blank_pages',lambda _:set())
            calls=[]
            def worker(*args):
                calls.append(args[1]);raise PersonalError('Unreadable page, not proven blank.','ocr_empty',409)
            monkeypatch.setattr(r.docs,'worker',worker)
            job=r.service.start_note_index(doc['id'],str(uuid4()),raw_item_id=rid,course_id='course-math-ii')
            await finish(r.service)
            saved=r.service.jobs.get(job['id'])
            assert saved['status']==('completed' if reuse_blank_proof else 'partial')
            assert saved['result']['blank_pages']==([2] if reuse_blank_proof else [])
            assert calls==([] if reuse_blank_proof else ['school-page:2'])
            lecture=r.service.store.create_lecture(LectureInput(title='Synthetic note',lecture_date='2026-11-09',notes_raw_item_id=rid),str(uuid4()))
            context=r.service.contexts.build(lecture['id'],allow_incomplete=True)
            assert any(ref['page']==2 for ref in context['needs_vision']) is (not reuse_blank_proof)
            assert any(s['page']==2 for s in context['sources']) is (not reuse_blank_proof)
            assert not r.provider.calls
            assert all(b['page_index']==1 for b in r.service.learning.blocks([doc['id']]))
    asyncio.run(run())
