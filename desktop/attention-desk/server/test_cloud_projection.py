"""Read-only export regressions; synthetic files never become learner evidence."""
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from server.cloud_sync.projection import CloudProjection, public_value, record
from server.inbox_store import InboxStore
from server.test_collector import rig
from server.test_documents import text_pdf
from server.documents.storage import AttachmentStore
from server.personal_store import PersonalStore,now


def test_note_pause_survives_cloud_backup_directory(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            # Exercise the real on-disk control lookup, not the test env override.
            monkeypatch.delenv('ATTENTION_RECOVERY_CONTROL_PATH')
            controls=tmp_path/'recovery-controls.json'
            controls.write_text(json.dumps({'version':1,'hold_cloud_uploads':True,'pause_paid_ai':True}))
            monkeypatch.setattr(r.service,'_schedule',lambda raw:None)
            path=text_pdf(tmp_path/'note.pdf',['Synthetic note; no model request.'])
            receipt=await r.service.submit('desktop','file',{'submission_id':str(uuid4()),'purpose':'lecture_note'},[(path.name,'application/pdf',path)])
            with r.store.connection(write=True) as c:
                raw=c.execute('SELECT * FROM raw_item WHERE id=?',(receipt['submission_id'],)).fetchone()
                c.execute('INSERT INTO learning_capture VALUES (?,?,?,?,?,?,NULL,NULL,?)',
                    ('capture-paused',raw['id'],raw['inbox_original_snapshot_id'],'fixture-hash','received',
                     json.dumps({'note_share':True,'note_automation':{'status':'local_ready'}}),now()))
            records=CloudProjection(r.store,tmp_path/'cloud-cache').records()
            for kind in ('inbox_item','inbox'):
                value=next(row['payload'] for row in records if row['kind']==kind and row['id']==raw['id'])
                assert value['note_processing']['status']=='paused'
                assert value['processing_status']=='needs_review'
            assert json.loads(controls.read_text())['pause_paid_ai'] is True
    asyncio.run(run())


def test_projection_removes_machine_credentials_but_preserves_real_evidence_paths():
    value = public_value({'id':'existing-id','local_path':'C:/private/original.pdf',
        'nested':{'api_key':'never publish','protected_sync_key':'never publish','field_path':'tasks.existing.title'},
        'source':{'document_id':'doc-stable','page':12,'quote':'Original evidence'}})
    assert value == {'id':'existing-id','nested':{'field_path':'tasks.existing.title'},
        'source':{'document_id':'doc-stable','page':12,'quote':'Original evidence'}}
    assert record('course','course-math-ii',value) == record('course','course-math-ii',value)


def test_projection_publishes_only_small_reading_metadata_and_preserves_document_identity(tmp_path,monkeypatch):
    store=PersonalStore(tmp_path/'personal.sqlite3');store.initialize()
    path=text_pdf(tmp_path/'book.pdf',['Synthetic page '+str(index) for index in range(70)])
    raw=AttachmentStore(store).collect([(path.name,'application/pdf',path)],'','Synthetic book')
    attachment=raw['snapshot']['attachments'][0];identity='doc-'+attachment['sha256'][:32]
    with store.connection(write=True) as c:
        c.execute('INSERT INTO document(id,attachment_id,sha256,title,page_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                  (identity,attachment['id'],attachment['sha256'],'Synthetic book',70,now(),now()))
    projection=CloudProjection(store,tmp_path/'cloud-cache')
    resources,documents=projection.resources()
    assert len(resources)==1 and len(documents)==1
    records=projection.records();document=next(row for row in records if row['kind']=='document')['payload']
    assert records[-1]['kind']=='facade' and records[-1]['id']=='/review-queue'
    assert records[-1]['payload']=={'schema_version':1,'members':[]}
    view=next(row for row in records if row['kind']=='pdf_reading')['payload']
    assert document['id']==identity and document['sha256']==attachment['sha256']
    assert document['reading_resource_id']=='pdf-view:'+view['id']
    assert view['source_resource_id']==resources[0]['id'] and view['tail_bytes']<1_000_000
    assert not any('path' in row['payload'] for row in records if row['kind'] in ('document','pdf_reading'))
    def unavailable(*args):raise ValueError('Synthetic unsupported PDF')
    monkeypatch.setattr('server.cloud_sync.projection.reading_view',unavailable)
    assert projection.resources()[0][0]['sha256']==attachment['sha256']
    records=projection.records()
    assert not any(row['kind']=='pdf_reading' for row in records)
    assert 'reading_resource_id' not in next(row for row in records if row['kind']=='document')['payload']


def test_consistent_projection_closes_backup_handles_and_preserves_original_bytes(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            monkeypatch.setattr(r.service,'_schedule',lambda raw:None)
            path=text_pdf(tmp_path/'source.pdf',['Synthetic exported note.'])
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            saved=await r.service.submit('desktop','file',{'submission_id':str(uuid4()),'source_name':'Synthetic'},[('source.pdf','application/pdf',path)])
            projection=CloudProjection(r.store,tmp_path/'cloud-cache')
            one=projection.snapshot()
            two=projection.snapshot()
            assert one.path==two.path and one.path.exists()
            with r.store.connection() as c:
                count=c.execute('SELECT count(*) FROM inbox_receipt').fetchone()[0]
            with two.connection() as c:
                assert c.execute('SELECT count(*) FROM inbox_receipt').fetchone()[0]==count
            resources,_=projection.resources()
            assert resources and resources[0]['sha256']==before
            assert resources[0]['path'].read_bytes()==path.read_bytes()
            assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    asyncio.run(run())


def test_legacy_original_without_a_candidate_review_remains_readable(tmp_path,monkeypatch):
    async def run():
        async with rig(tmp_path) as r:
            monkeypatch.setattr(r.service,'_schedule',lambda raw:None)
            saved=await r.service.submit('desktop','text',{'submission_id':str(uuid4()),'text':'Synthetic standalone original.'})
            with r.store.connection(write=True) as c:
                raw=c.execute('SELECT raw_item_id FROM inbox_receipt WHERE id=?',(saved['id'],)).fetchone()[0]
                sid=c.execute('SELECT inbox_original_snapshot_id FROM raw_item WHERE id=?',(raw,)).fetchone()[0]
                c.execute('DELETE FROM raw_review WHERE raw_snapshot_id=?',(sid,))
            detail=InboxStore(r.store).detail(raw)
            assert detail['original']['original_text']=='Synthetic standalone original.'
            assert detail['review_available'] is False and detail['review']['extraction'] is None
            with r.store.connection() as c:
                assert not c.execute('SELECT 1 FROM raw_review WHERE raw_snapshot_id=?',(sid,)).fetchone()
    asyncio.run(run())
