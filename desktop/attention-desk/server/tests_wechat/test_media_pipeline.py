"""Saved-media continuation, costs and source preservation, with mock providers."""
import asyncio,json
from datetime import datetime,timedelta
from pathlib import Path
import httpx
import pytest
from PIL import Image

from server.tests_wechat.test_pipeline import setup_service,scan,output_for
from server.wechat_collect.models import Observation,ObservedMessage
from server.wechat_collect.inbox import list_candidates,briefing
from server.wechat_collect.batching import BatchPlan
from server.wechat_collect.ingestion import prepare_batch
from server.review_store import ReviewStore

NOW=datetime.fromisoformat('2026-10-23T12:00:00+08:00')

def observe_images(personal,count=3,caption=True):
    spool=personal.path.parent/'wechat-spool';spool.mkdir(exist_ok=True)
    result=[]
    def text(value,index):return ObservedMessage(value,sender='班长',source_time=(NOW+timedelta(seconds=index)).isoformat(),time_reliability='reliable',sender_reliability='reliable')
    if caption:result.append(text('重要通知见图',0))
    for index in range(count):
        path=spool/f'notice-{index}.png';Image.new('RGB',(200,300),(245,index,250)).save(path)
        result.append(ObservedMessage('[图片]',sender='班长',message_type='image',attachment_identity=f'image-{index}',media_path=str(path),source_time=(NOW+timedelta(seconds=index+1)).isoformat(),time_reliability='reliable',sender_reliability='reliable'))
    if caption:result.append(text('大家记得周五前完成',count+1))
    return result

def test_three_images_and_followup_are_one_automatic_batch_with_immutable_evidence(personal):
    messages=observe_images(personal)
    service,clock,calls=setup_service(personal,[Observation('test','unverified',tuple(messages),True,True)],time=NOW.isoformat())
    service.document.wechat.context_followup.enabled=False
    service.media_engine=lambda *a:([{'text':'回 回'}],{})
    payloads=[]
    def provider(request):
        body=json.loads(request.content);payloads.append(body)
        if isinstance(body['messages'][-1]['content'],list):
            rows=[json.loads(p['text']) for p in body['messages'][-1]['content'] if p['type']=='text' and p['text'].startswith('{')]
            result={'images':[{'media_id':r['media_id'],'blocks':[{'text':'请完成实验室安全学习，并提交成绩单。','bounding_box':{'x':0,'y':0,'width':180,'height':100}}],'uncertainty':''} for r in rows]}
        else:
            payload=json.loads(body['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
            primary=next(m for m in payload['messages'] if not m['context_only'] and any(b['block_id']==m['block_id'] and b['source_type']=='image_vision' for b in payload['blocks']))
            block=next(b for b in payload['blocks'] if b['block_id']==primary['block_id'])
            result=output_for({**payload,'messages':[primary],'blocks':[block]})
            assert any('重要通知见图' in b['text'] for b in payload['blocks'])
            assert any('周五前完成' in b['text'] for b in payload['blocks'])
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(result,ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    from server.ai_transport import DeepSeekTransport
    service.analysis.transport=DeepSeekTransport(httpx.MockTransport(provider))
    async def run():
        await scan(service);await service.process_queue()
        assert payloads==[] and list_candidates(personal)['total']==0
        await service.media_pipeline.tick(force=True)
        await service.media_pipeline.tick(force=True)
        await service.process_queue()
    asyncio.run(run())
    assert len(payloads)==2 and list_candidates(personal)['total']==1
    assert briefing(personal,at=NOW)['media']['total']==0
    candidate=list_candidates(personal)['items'][0]
    snapshot=ReviewStore(personal).get(candidate['snapshot_id'])['snapshot']
    assert len(snapshot['attachments'])==3
    assert len([b for b in snapshot['blocks'] if b['source_type']=='image_vision'])==3
    with personal.connection() as c:
        assert c.execute('SELECT count(*) FROM wechat_media_analysis').fetchone()[0]==1
        assert c.execute("SELECT count(*) FROM wechat_media_work WHERE status='completed'").fetchone()[0]==3
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0

def test_unrelated_unreadable_image_stays_out_of_review_without_vision(personal):
    messages=observe_images(personal,1,False)
    service,clock,calls=setup_service(personal,[Observation('test','unverified',tuple(messages),True,True)],time=NOW.isoformat())
    service.media_engine=lambda *a:([{'text':'回 回'}],{})
    async def run():await scan(service);await service.media_pipeline.tick(force=True);await service.media_pipeline.tick(force=True)
    asyncio.run(run())
    assert calls==[] and list_candidates(personal)['total']==0
    assert briefing(personal,at=NOW)['media']['total']==1
    with personal.connection() as c:assert c.execute('SELECT last_error FROM wechat_media_work').fetchone()[0]=='ocr_insufficient_no_notice_context'


def ocr_answer(payload,bucket):
    block=next(b for b in payload['blocks'] if b['source_type']=='image_ocr')
    message=next(m for m in payload['messages'] if m['block_id']==block['block_id'])
    result=output_for({**payload,'messages':[message],'blocks':[block]})
    if bucket=='ignore':return {**result,'items':[],'evidence':[]}
    if bucket=='fyi':result['items'][0].update(attention_level='fyi',category='notice',action_required=False,actions=[])
    return result


@pytest.mark.parametrize('bucket',['action','fyi','ignore'])
def test_sufficient_local_ocr_reclassifies_automatically_without_vision(personal,bucket):
    service,clock,calls=setup_service(personal,[Observation('test','unverified',tuple(observe_images(personal,1)),True,True)],time=NOW.isoformat(),response=lambda p:ocr_answer(p,bucket))
    ocr=[]
    def engine(*args):ocr.append(1);return [{'text':'请完成实验室安全学习，并提交承诺书。'}],{}
    service.media_engine=engine
    service.document.wechat.context_followup.enabled=False
    async def run():
        await scan(service);await service.process_queue();await service.media_pipeline.tick(force=True)
        await service.media_pipeline.tick(force=True)
    asyncio.run(run())
    assert len(calls)==len(ocr)==1
    assert list_candidates(personal)['total']==int(bucket=='action')
    assert briefing(personal,at=NOW)['fyi']['total']==int(bucket=='fyi')
    assert briefing(personal,at=NOW)['media']['total']==0
    with personal.connection() as c:
        assert c.execute("SELECT count(*) FROM wechat_media_work WHERE status!='completed'").fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM wechat_llm_attempt WHERE kind='vision'").fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0


@pytest.mark.parametrize('guard',['quiet','disabled','revoked'])
def test_media_guard_prevents_lookup_and_ocr(personal,guard):
    service,clock,calls=setup_service(personal,[Observation('test','unverified',tuple(observe_images(personal,1)),True,True)],time=NOW.isoformat())
    service.media_engine=lambda *a:([{'text':'回 回'}],{})
    asyncio.run(scan(service))
    service.media_engine=lambda *a:pytest.fail('no OCR beyond permission boundary')
    service.media_pipeline.locator=lambda *a:pytest.fail('no media lookup beyond permission boundary')
    if guard=='quiet':clock[0]=clock[0].replace(hour=1)
    elif guard=='disabled':service.document.wechat.enabled=False
    else:
        service.document.wechat.priority_chats[0].enabled=False
        with personal.connection(write=True) as c:c.execute('UPDATE wechat_chat SET enabled=0')
    asyncio.run(service.media_pipeline.tick(force=True))
    assert calls==[]


def test_cached_vision_hash_reused_without_another_ocr_or_vision(personal):
    from server.personal_store import encode,now
    service,clock,calls=setup_service(personal,[Observation('test','unverified',tuple(observe_images(personal,1)),True,True)],time=NOW.isoformat(),response=lambda p:ocr_answer(p,'action'))
    service.media_engine=lambda *a:([{'text':'回 回'}],{})
    asyncio.run(scan(service))
    with personal.connection(write=True) as c:
        digest=c.execute('SELECT sha256 FROM wechat_media').fetchone()[0]
        c.execute("INSERT INTO wechat_media_parse_cache VALUES (?,'vision','vision-v2','parsed',?,'{}',?)",(digest,encode([{'text':'请完成实验室安全学习，并提交承诺书。'}]),now()))
    service.media_engine=lambda *a:pytest.fail('reusable Vision must avoid local OCR')
    # Text provider selects the reused Vision block. The original cache remains
    # image-only; this message's context is still classified afresh.
    service.analysis.transport=__import__('server.ai_transport',fromlist=['DeepSeekTransport']).DeepSeekTransport(httpx.MockTransport(lambda request: _cached_answer(request,calls)))
    async def run():await service.media_pipeline.tick(force=True);await service.media_pipeline.tick(force=True)
    asyncio.run(run())
    assert len(calls)==1 and list_candidates(personal)['total']==1
    with personal.connection() as c:assert c.execute("SELECT count(*) FROM wechat_llm_attempt WHERE kind='vision'").fetchone()[0]==0


def _cached_answer(request,calls):
    body=json.loads(request.content);calls.append(body)
    assert isinstance(body['messages'][-1]['content'],str)
    payload=json.loads(body['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
    block=next(b for b in payload['blocks'] if b['source_type']=='image_vision')
    message=next(m for m in payload['messages'] if m['block_id']==block['block_id'])
    return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(output_for({**payload,'messages':[message],'blocks':[block]}),ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'total_tokens':100}})


def test_existing_action_gains_files_without_new_candidate_or_model_call(personal):
    from server.personal_store import encode
    from server.documents.storage import AttachmentStore
    from server.wechat_collect.file_evidence import enrich_existing
    names=['安全活动方案.docx','平台操作指引.doc','安全承诺书.xlsx','活动说明.pdf']
    notice=ObservedMessage('请完成安全学习并提交安全承诺书，附件供参考。',sender='老师')
    files=[ObservedMessage(n,sender='老师',message_type='file',attachment_identity=encode({'kind':'file','title':n})) for n in names]
    # One existing notice owns the files as primary evidence. Its human draft is
    # deliberately preserved while local cache availability changes.
    service,clock,calls=setup_service(personal,[Observation('test','unverified',tuple([notice,*files]),True,True)],time=NOW.isoformat())
    asyncio.run(scan(service))
    with personal.connection(write=True) as c:
        c.execute("UPDATE wechat_candidate SET status='needs_review',attention_level='action',information_complete=1,await_more_context=0,model_item_id='existing' WHERE model_item_id='_pending'")
        original=c.execute('SELECT snapshot_id FROM wechat_batch').fetchone()[0]
        c.execute('UPDATE raw_review SET draft_json=? WHERE raw_snapshot_id=?',(encode({'events':[{'id':'existing','title':'我的修改'}]}),original))
        old_blocks=[dict(r) for r in c.execute('SELECT * FROM raw_source_block WHERE raw_snapshot_id=?',(original,))]
    service.media_pipeline._seed()
    bound=enrich_existing(personal)
    assert len(bound)==4
    first=list_candidates(personal)['items'][0]
    assert first['attachment_count']==4
    metadata=ReviewStore(personal).get(first['snapshot_id'])
    assert [a['filename'] for a in metadata['snapshot']['attachments']]==names
    assert metadata['draft']=={'events':[{'id':'existing','title':'我的修改'}]}
    assert all(a['parse_status']=='not_downloaded' for a in metadata['snapshot']['attachments'])
    with personal.connection(write=True) as c:
        for row in c.execute('SELECT * FROM wechat_media_work').fetchall():
            path=personal.path.parent/('local-'+row['message_id']+'.bin');path.write_bytes(b'local file '+row['message_id'].encode())
            saved,digest,_=AttachmentStore(personal).preserve(path,'bin')
            ref=json.loads(row['reference_json']);ref.update(evidence_path=saved,evidence_sha256=digest)
            c.execute('UPDATE wechat_media_work SET reference_json=? WHERE message_id=?',(encode(ref),row['message_id']))
    enrich_existing(personal);enrich_existing(personal)
    candidate=list_candidates(personal)['items'][0];detail=ReviewStore(personal).get(candidate['snapshot_id'])
    assert candidate['id']==first['id'] and candidate['attachment_count']==4
    assert detail['draft']==metadata['draft']
    assert all(a['downloaded_at'] for a in detail['snapshot']['attachments'])
    assert calls==[]
    with personal.connection() as c:
        assert [dict(r) for r in c.execute('SELECT * FROM raw_source_block WHERE raw_snapshot_id=?',(original,))]==old_blocks
        assert c.execute("SELECT count(*) FROM wechat_media_work WHERE status='completed'").fetchone()[0]==4
        assert c.execute('SELECT count(*) FROM raw_snapshot').fetchone()[0]==3
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_missing_cache_retry_keeps_due_time_then_recovers_automatically(personal):
    from server.personal_store import encode
    message=ObservedMessage('[图片]',sender='老师',message_type='image',attachment_identity=encode({'kind':'image'}))
    service,clock,calls=setup_service(personal,[Observation('test','unverified',(ObservedMessage('重要通知见图'),message),True,True)],time=NOW.isoformat(),response=lambda p:ocr_answer(p,'action'))
    lookups=[];source=personal.path.parent/'files'/'later.png';source.parent.mkdir(exist_ok=True)
    def locate(rows,target):
        lookups.append(clock[0]);return [{'reference':{'kind':'image','cache_status':'missing',**({'evidence_path':str(source)} if source.exists() else {})}}]
    service.media_pipeline.locator=locate;service.media_engine=lambda *a:([{'text':'请完成安全学习，并提交承诺书。'}],{})
    async def run():
        await scan(service);await service.media_pipeline.tick(force=True)
        with personal.connection() as c:due=c.execute('SELECT next_attempt_at FROM wechat_media_work').fetchone()[0]
        clock[0]+=timedelta(minutes=1);await service.media_pipeline.tick(force=True)
        with personal.connection() as c:assert c.execute('SELECT next_attempt_at FROM wechat_media_work').fetchone()[0]==due
        Image.new('RGB',(100,100),'white').save(source)
        clock[0]+=timedelta(minutes=15);await service.media_pipeline.tick(force=True)
    asyncio.run(run())
    assert len(lookups)==2 and len(calls)==1
    assert list_candidates(personal)['total']==1 and briefing(personal,at=clock[0])['media']['total']==0


def test_legacy_image_evidence_reads_thumbnail_label_only_for_matching_bytes(personal):
    from server.personal_store import encode
    service,clock,calls=setup_service(personal,[Observation('test','unverified',tuple(observe_images(personal,1)),True,True)],time=NOW.isoformat())
    service.media_engine=lambda *a:([{'text':'回 回'}],{})
    asyncio.run(scan(service));service.media_pipeline._seed()
    with personal.connection(write=True) as c:
        snapshot=c.execute('SELECT snapshot_id FROM wechat_batch').fetchone()[0]
        media=c.execute('SELECT message_id,sha256 FROM wechat_media').fetchone()
        manifests=[r[0] for r in c.execute('SELECT parse_manifest_json FROM attachment')]
        c.execute('UPDATE wechat_media_work SET reference_json=? WHERE message_id=?',(encode({'kind':'image','evidence_tier':'thumb','evidence_sha256':media['sha256']}),media['message_id']))
    first=ReviewStore(personal).get(snapshot)['snapshot']['attachments'][0]
    assert first['parse_manifest']['metadata']['evidence_tier']=='thumb'
    with personal.connection(write=True) as c:
        assert [r[0] for r in c.execute('SELECT parse_manifest_json FROM attachment')]==manifests
        c.execute('UPDATE wechat_media_work SET reference_json=? WHERE message_id=?',(encode({'kind':'image','evidence_tier':'thumb','evidence_sha256':'different'}),media['message_id']))
    second=ReviewStore(personal).get(snapshot)['snapshot']['attachments'][0]
    assert not second['parse_manifest']['metadata'].get('evidence_tier')


def test_recovered_attachment_keeps_confirmation_valid_and_does_not_reanalyze(personal):
    from server.personal_store import encode
    from server.tests_wechat.test_workbench import confirm_one
    def answer(payload):
        block=next(b for b in payload['blocks'] if '请完成' in b['text'])
        message=next(m for m in payload['messages'] if m['block_id']==block['block_id'])
        return output_for({**payload,'messages':[message],'blocks':[block]})
    notice=ObservedMessage('请完成安全学习并提交安全承诺书，附件供参考。',sender='老师')
    file=ObservedMessage('安全承诺书.docx',message_type='file',sender='老师',attachment_identity=encode({'kind':'file','title':'安全承诺书.docx'}))
    service,clock,calls=setup_service(personal,[Observation('test','unverified',(notice,file),True,True)],time=NOW.isoformat(),response=answer)
    service.document.wechat.context_followup.enabled=False
    path=personal.path.parent/'files'/'safety.docx';path.parent.mkdir(exist_ok=True);path.write_bytes(b'synthetic local document')
    service.media_pipeline.locator=lambda *a:[{'reference':{'kind':'file','title':'安全承诺书.docx','evidence_path':str(path)}}]
    async def run():
        await scan(service);await service.process_queue()
        before=list_candidates(personal)['items'][0]
        await service.media_pipeline.tick(force=True)
        return before
    before=asyncio.run(run());candidate=list_candidates(personal)['items'][0]
    assert candidate['id']==before['id'] and candidate['snapshot_id']!=before['snapshot_id'] and candidate['attachment_count']==1
    assert len(calls)==1 and briefing(personal,at=NOW)['media']['total']==0
    with personal.connection() as c:assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
    saved,request=confirm_one(personal,candidate)
    replay=ReviewStore(personal).confirm(candidate['snapshot_id'],request)
    assert saved['events'][0]['id']==replay['events'][0]['id']
    assert len(saved['events'][0]['snapshot']['attachments'])==1
    assert list_candidates(personal)['total']==0
