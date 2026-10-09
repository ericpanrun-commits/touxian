"""Phase 5.5 acceptance: real local parsers, deterministic model/network fixtures."""
import asyncio
from copy import deepcopy
import json
import re
from uuid import uuid4
import httpx
import pytest
from pydantic import SecretStr
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from server.ai_settings import AIConfig
from server.documents.parsers import parse_document
from server.documents.text_boundaries import semantic_ranges
from server.extraction_chunks import plan_chunks, MAX_CALLS, MAX_CHUNKS
from server.extraction_errors import ExtractionFailure
from server.extraction_merge import merge_candidates
from server.extraction_models import RawInput
from server.extraction_provider import DeepSeekProvider, ProviderResult
from server.extraction_service import ExtractionService
from server.extraction_validation import parse_output, validate_candidate
from server.personal_store import PersonalStore, PersonalError, encode
from server.review_store import ReviewStore


class Settings:
    def __init__(self): self.config = AIConfig()
    def _read(self): return {},self.config
    def resolved(self): return self.config,SecretStr('unit-test-key-not-a-real-credential')


@pytest.fixture
def store(tmp_path):
    result = PersonalStore(tmp_path/'test.sqlite3'); result.initialize(); return result


def output_for(block, page=1):
    text = block['text']
    match = re.search(r'2026-10-\d{2}',text)
    if not match or 'DEADLINE' not in text:
        return {'events':[],'evidence':[]}
    action = 'Submit materials'
    a = text.index(action)
    quote = {'id':'q1','block_id':block.get('block_id'),'quote':action,'start':a,'end':a+len(action)}
    date = {'id':'q2','block_id':block.get('block_id'),'quote':match.group(),'start':match.start(),'end':match.end()}
    event = {'id':'event1','title':'Course applications','summary':'','category':'报名','audience':None,'participation':'required','notice_kind':'new',
        'eligibility':[],'materials':[],'locations':[],'uncertainties':[],
        'actions':[{'id':'a1','title':f'Stage {page}','kind':'explicit','evidence_ids':['q1']}],
        'deadlines':[{'id':'d1','role':f'Stage {page}','original_text':match.group(),'normalized_date':match.group(),'precision':'date',
        'related_action_ids':['a1'],'evidence_ids':['q2'],'uncertain':False,'relation':'current'}]}
    return {'events':[event],'evidence':[quote,date]}


class Provider:
    name = 'deepseek'
    def __init__(self, bad=None, mode=None, pause=None):
        self.calls = []; self.bad = set(bad or []); self.mode = mode; self.pause = pause; self.entered = asyncio.Event()
    async def extract(self,snapshot,config,key):
        match = re.search(r'PAGE_(\d+)',snapshot.get('_chunk_context',{}).get('chunk_id',''))
        page = int(match[1]) if match else 1
        block = next((b for b in snapshot.get('blocks',[]) if b.get('page')==page),None) or (snapshot.get('blocks') or [{'text':snapshot['original_text']}])[0]
        self.calls.append(page)
        if page==self.pause:
            self.entered.set(); await asyncio.Event().wait()
        if page in self.bad:
            if self.mode=='timeout': raise ExtractionFailure('timeout','fixture timeout')
            if self.mode=='unavailable': raise ExtractionFailure('provider_error','fixture unavailable')
            if self.mode=='malformed': return ProviderResult('not JSON',{},'fixture')
            if self.mode=='truncated': return ProviderResult('{"events":[',{'completion_tokens':config.max_output_tokens},'fixture','length')
            if self.mode=='once' and self.calls.count(page)==1:
                return ProviderResult('{"events":[',{'completion_tokens':config.max_output_tokens},'fixture','length')
        return ProviderResult(encode(output_for(block,page)),{'total_tokens':200},'fixture')


def pages(store,count=10,important=None,revision=None,parsed_blocks=None):
    texts = []
    for i in range(1,count+1):
        body = f'Page {i}.\n'+('Ordinary explanatory paragraph.\n'*20)
        if important is None or i in important:
            body += f'DEADLINE: Submit materials by 2026-10-{i%28+1:02d}.\n'
        if revision==i: body += 'Changed eligibility paragraph.\n'
        texts.append(body)
    if parsed_blocks:
        texts = [b['text'] for b in parsed_blocks]
    raw = ReviewStore(store).collect(RawInput(original_text='\n'.join(texts),title_hint='Test document'))
    with store.connection(write=True) as c:
        for i,text in enumerate(texts,1):
            c.execute('INSERT INTO raw_source_block (raw_snapshot_id,block_id,source_type,ordinal,text,page,section,words_json) VALUES (?,?,?,?,?,?,?,?)',
                (raw['snapshot']['id'],f'PDF_{uuid4().hex}_PAGE_{i}','pdf',i,text,i,f'Page {i}','[]'))
    return ReviewStore(store).get(raw['snapshot']['id'])


async def execute(store,raw,provider,*,force=False,scope='auto',settings=None):
    service = ExtractionService(store,settings or Settings(),provider)
    await service.start(raw['snapshot']['id'],force,confirm_budget=True,scope=scope)
    await asyncio.gather(*list(service.tasks.values()))
    return ReviewStore(store).get(raw['snapshot']['id']),service


def test_short_notice_retains_single_call_and_cache(store):
    raw = ReviewStore(store).collect(RawInput(original_text='DEADLINE: Submit materials by 2026-10-12.'))
    provider = Provider()
    async def run():
        result,service = await execute(store,raw,provider)
        await service.start(raw['snapshot']['id'])
        return result
    result = asyncio.run(run())
    assert len(provider.calls)==1 and result['extraction']['plan'] is None
    assert result['extraction']['analysis_state']=='validated'


@pytest.mark.parametrize('count',[10,50])
def test_real_pdf_parser_and_bounded_rounds(tmp_path,store,count):
    writer=PdfWriter()
    for i in range(count):
        page=writer.add_blank_page(612,792)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject(); stream.set_data(('BT /F1 12 Tf 40 700 Td ('+('Explanatory paragraph. '*28)+f' DEADLINE: Submit materials by 2026-10-{i%28+1:02d}.) Tj ET').encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    path=tmp_path/'document.pdf'; writer.write(path)
    extracted=parse_document(path,'pdf',tmp_path)['blocks']
    assert {b['page'] for b in extracted}==set(range(1,count+1))
    raw=pages(store,count,parsed_blocks=extracted); provider=Provider()
    async def run():
        first,_=await execute(store,raw,provider)
        if count>MAX_CHUNKS:
            assert first['extraction']['analysis_state']=='partial'
            assert first['extraction']['plan']['omitted']
            second,_=await execute(store,raw,provider,force=True,scope='remaining')
            return second
        return first
    result=asyncio.run(run())
    assert result['extraction']['analysis_state']=='validated'
    assert len(result['extraction']['parsed_output']['events'][0]['deadlines'])==count
    assert len(provider.calls)==count
    assert all(q['valid'] and q['page'] for q in result['extraction']['validation']['evidence'])


def test_irrelevant_pages_filter_and_explicit_remaining_scope(store):
    raw=pages(store,50,important={25}); plan=plan_chunks(raw['snapshot'])
    assert len(plan['selected'])<8 and plan['omitted']
    assert any('PAGE_025' in ch['id'] for ch in plan['selected'])
    provider=Provider(); result,_=asyncio.run(execute(store,raw,provider))
    assert result['extraction']['analysis_state']=='partial'
    assert result['extraction']['parsed_output']['events'][0]['deadlines'][0]['role']=='Stage 25'
    assert len(result['snapshot']['blocks'])==50


def test_multi_deadline_action_and_cross_chunk_merge_evidence(store):
    raw=pages(store,10); result,_=asyncio.run(execute(store,raw,Provider()))
    event=result['extraction']['parsed_output']['events'][0]
    assert len(event['deadlines'])==len(event['actions'])==10
    assert all(d['related_action_ids'] for d in event['deadlines'])
    assert len({q['page'] for q in result['extraction']['validation']['evidence']})==10
    for q in result['extraction']['validation']['evidence']:
        b=next(b for b in result['snapshot']['blocks'] if b['block_id']==q['block_id'])
        assert b['text'][q['start']:q['end']]==q['quote']


@pytest.mark.parametrize('mode',['once','timeout','malformed','truncated'])
def test_recovery_and_partial_results_never_create_events(store,mode):
    raw=pages(store,10); provider=Provider(bad={3},mode=mode)
    result,_=asyncio.run(execute(store,raw,provider)); ai=result['extraction']
    assert ai['parsed_output'] is not None
    assert ai['analysis_state']==('validated' if mode=='once' else 'partial')
    assert ai['progress']['calls']<=MAX_CALLS
    assert any(ch['error'] or ch['retry_reason']!='initial' for ch in ai['chunks'])
    if mode=='truncated':
        assert ai['token_usage']['completion_tokens']>=Settings().config.max_output_tokens
    with store.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM event').fetchone()[0]==0
        assert c.execute('SELECT original_text FROM raw_snapshot WHERE id=?',(raw['snapshot']['id'],)).fetchone()[0]==raw['snapshot']['original_text']


def test_eighteen_success_two_failures_preserved(store):
    raw=pages(store,20); result,_=asyncio.run(execute(store,raw,Provider(bad={3,10},mode='timeout')))
    assert result['extraction']['analysis_state']=='partial'
    assert result['extraction']['progress']['failed_regions']==2
    assert sum(ch['status']=='validated' for ch in result['extraction']['chunks'])==18
    assert len(result['extraction']['parsed_output']['events'][0]['deadlines'])==18


def test_cache_and_changed_page_rebind_current_source_ids(store):
    raw=pages(store,10); provider=Provider()
    async def run():
        first,service=await execute(store,raw,provider)
        initial=len(provider.calls)
        await service.start(raw['snapshot']['id'],True,confirm_budget=True)
        await asyncio.gather(*list(service.tasks.values()))
        assert len(provider.calls)==initial
        changed=pages(store,10,revision=5)
        updated,_=await execute(store,changed,provider)
        assert 0<len(provider.calls)-initial<=3
        return first,updated,changed
    _,result,changed=asyncio.run(run())
    assert result['extraction']['progress']['cached']>=7
    ids={b['block_id'] for b in changed['snapshot']['blocks']}
    assert all(q['block_id'] in ids and q['valid'] for q in result['extraction']['validation']['evidence'])


def test_cancel_preserves_completed_regions_and_resumes_from_cache(store):
    raw=pages(store,10); provider=Provider(pause=2)
    async def run():
        service=ExtractionService(store,Settings(),provider)
        await service.start(raw['snapshot']['id'],confirm_budget=True)
        await provider.entered.wait()
        cancelled=await service.long.cancel(raw['snapshot']['id'])
        assert cancelled['extraction']['analysis_state']=='cancelled'
        assert cancelled['extraction']['parsed_output']['events']
        provider.pause=None
        await service.start(raw['snapshot']['id'],True,confirm_budget=True)
        await asyncio.gather(*list(service.tasks.values()))
        return service.reviews.get(raw['snapshot']['id'])
    result=asyncio.run(run())
    assert provider.calls.count(1)==1
    assert result['extraction']['analysis_state']=='validated'


def test_provider_unavailable_preserves_raw(store):
    raw=pages(store,10); provider=Provider(bad={1},mode='unavailable')
    result,_=asyncio.run(execute(store,raw,provider))
    assert len(provider.calls)==1 and result['extraction']['analysis_state']=='failed'
    assert result['snapshot']['original_text']==raw['snapshot']['original_text']


def test_restart_recovers_successful_regions_and_database_migration_is_additive(tmp_path,monkeypatch):
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION',4)
    old=PersonalStore(tmp_path/'upgrade.sqlite3'); old.initialize()
    raw=ReviewStore(old).collect(RawInput(original_text='Original kept before migration.'))
    monkeypatch.setattr('server.personal_store.SCHEMA_VERSION',5)
    old.initialize()
    assert ReviewStore(old).get(raw['snapshot']['id'])['snapshot']['original_text']=='Original kept before migration.'
    raw=pages(old,10); result,_=asyncio.run(execute(old,raw,Provider(bad={3},mode='timeout')))
    with old.connection(write=True) as c:
        c.execute("UPDATE ai_extraction SET status='running',analysis_state='running' WHERE id=?",(result['extraction_id'],))
    restarted=ExtractionService(old,Settings(),Provider()); restarted.recover_interrupted()
    recovered=restarted.reviews.get(raw['snapshot']['id'])
    assert recovered['extraction']['analysis_state']=='partial'
    assert recovered['extraction']['parsed_output']['events']
    with old.connection() as c:
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_cross_chunk_deadline_finds_matching_action_without_inventing_other_links(store):
    raw=pages(store,10); snapshot=raw['snapshot']; first=output_for(snapshot['blocks'][0],1); second=deepcopy(first)
    first['events'][0]['deadlines'][0]['related_action_ids']=[]
    first['events'][0]['actions']=[]
    second['events'][0]['deadlines']=[]
    merged,_=merge_candidates([('date',first),('action',second)],snapshot)
    event=merged['events'][0]
    assert event['deadlines'][0]['related_action_ids']==[event['actions'][0]['id']]
    assert all(q['valid'] for q in validate_candidate(deepcopy(merged),snapshot)['evidence'])


def test_concurrent_identical_chunks_share_paid_call_and_idempotency_survives_cache_changes(store):
    first=pages(store,10); second=pages(store,10)
    class SlowProvider(Provider):
        async def extract(self,*args):
            await asyncio.sleep(.01)
            return await super().extract(*args)
    provider=SlowProvider(); key=str(uuid4())
    async def run():
        service=ExtractionService(store,Settings(),provider)
        one=await service.start(first['snapshot']['id'],request_id=key,confirm_budget=True)
        again=await service.start(first['snapshot']['id'],True,confirm_budget=True,scope='remaining')
        assert one['extraction_id']==again['extraction_id']
        await service.start(second['snapshot']['id'],confirm_budget=True)
        await asyncio.gather(*list(service.tasks.values()))
        retry=await service.start(first['snapshot']['id'],request_id=key,confirm_budget=True)
        assert retry['extraction_id']==one['extraction_id']
        assert not service.long.locks
    asyncio.run(run())
    assert len(provider.calls)==10


@pytest.mark.parametrize('raw,code',[
    ('{"events":[','truncated'), ('{"events":[]','truncated'), ('not JSON','invalid_json'),
    ('{"events":[]}','invalid_schema'), ('{"events":[],"events":[],"evidence":[]}','invalid_json')])
def test_output_incompleteness_classification(raw,code):
    with pytest.raises(ExtractionFailure) as error: parse_output(raw)
    assert error.value.code==code


def test_budget_confirmation_prevents_any_calls_and_http_limit_is_real(store,monkeypatch):
    raw=pages(store,10); fake=Provider()
    async def blocked():
        service=ExtractionService(store,Settings(),fake)
        with pytest.raises(PersonalError) as error: await service.start(raw['snapshot']['id'])
        assert error.value.code=='budget_confirmation_required' and not fake.calls
    asyncio.run(blocked())
    monkeypatch.setattr('server.extraction_long.MAX_CALLS',3)
    requests=[]
    def handler(request):
        requests.append(request)
        if len(requests)%2: return httpx.Response(503)
        content=json.loads(request.content)['messages'][-1]['content']
        context=json.loads(content.split('只分析以下 JSON 中的 original_text 或 source_blocks 数据：\n')[1])
        b=context['source_blocks'][0]
        return httpx.Response(200,json={'choices':[{'message':{'content':encode(output_for(b,1))},'finish_reason':'stop'}],'usage':{'total_tokens':100}})
    provider=DeepSeekProvider(transport=httpx.MockTransport(handler))
    result,_=asyncio.run(execute(store,raw,provider))
    assert len(requests)==3 and result['extraction']['analysis_state']=='partial'
    assert result['extraction']['progress']['calls']==3


def test_semantic_boundaries_are_lossless_and_identity_is_stable(store):
    text=('标题\n这是一个完整句子。第二句！\n\n下一段有表格行。\n'*90)
    ranges=list(semantic_ranges(text,180))
    assert ''.join(text[a:z] for a,z in ranges)==text
    assert all(z-a<=180 for a,z in ranges)
    assert all(text[z-1] in '\n。！' for a,z in ranges[:-1])
    raw=pages(store,10)
    assert [c['id'] for c in plan_chunks(raw['snapshot'])['all']]==[c['id'] for c in plan_chunks(raw['snapshot'])['all']]


def test_extension_conflict_and_deadline_dedup_keep_both_evidence(store):
    text='原定10月12日截止，现延长至10月15日。'
    raw=ReviewStore(store).collect(RawInput(original_text=text,source_time='2026-10-01'))
    def dated(quote,value,index):
        a=text.index(quote)
        data=output_for({'text':'DEADLINE: Submit materials by 2026-10-12.'})
        data['evidence']=[{'id':'q','quote':quote,'block_id':None,'start':a,'end':a+len(quote)}]
        data['events'][0]['actions']=[]
        d=data['events'][0]['deadlines'][0]
        d.update(id=f'd{index}',role='报名',original_text=quote,normalized_date=value,related_action_ids=[],evidence_ids=['q'])
        return data
    old=dated('10月12日','2026-10-12',1); new=dated('10月15日','2026-10-15',2)
    merged,warnings=merge_candidates([('A',old),('B',new),('C',deepcopy(new))],raw['snapshot'])
    assert len(merged['events'][0]['deadlines'])==2
    assert {d['relation'] for d in merged['events'][0]['deadlines']}=={'previous','current'}
    assert len(merged['evidence'])==2
    assert any(w['code']=='superseded_date' for w in warnings)
    conflict_source={**raw['snapshot'],'original_text':'报名10月12日截止。附件写10月15日截止。'}
    _,warnings=merge_candidates([('A',old),('B',new)],conflict_source)
    assert any(w['code']=='date_conflict' for w in warnings)


def test_unrelated_extension_cannot_replace_dates_from_another_attachment(store):
    raw=pages(store,10); snapshot=raw['snapshot']
    blocks=deepcopy(snapshot['blocks'])
    blocks[0]['text']='原定10月12日截止，现延长至10月15日。'
    blocks[1]['text']='另一项申请10月12日截止。附件写10月15日截止。'
    snapshot={**snapshot,'blocks':blocks}
    outputs=[]
    for i,(phrase,value) in enumerate([('10月12日','2026-10-12'),('10月15日','2026-10-15')]):
        candidate=output_for(raw['snapshot']['blocks'][0])
        start=blocks[1]['text'].index(phrase)
        candidate['evidence']=[{'id':'q','block_id':blocks[1]['block_id'],'quote':phrase,'start':start,'end':start+len(phrase)}]
        event=candidate['events'][0];event['actions']=[]
        event['deadlines'][0].update(role='报名',original_text=phrase,normalized_date=value,evidence_ids=['q'],related_action_ids=[])
        outputs.append((str(i),candidate))
    merged,warnings=merge_candidates(outputs,snapshot)
    assert {d['relation'] for d in merged['events'][0]['deadlines']}=={'current'}
    assert any(w['code']=='date_conflict' for w in warnings)
    assert not any(w['code']=='superseded_date' for w in warnings)


@pytest.mark.parametrize('response_kind',['empty','nonstop','invalid_response','too_large'])
def test_provider_failure_retry_counts_every_wire_request(store,monkeypatch,response_kind):
    monkeypatch.setattr('server.extraction_long.MAX_CALLS',3)
    requests=[]
    def handler(request):
        requests.append(request)
        if len(requests)%2: return httpx.Response(503)
        if response_kind=='invalid_response': return httpx.Response(200,json={})
        if response_kind=='too_large': return httpx.Response(200,content=b'x'*2_000_001)
        return httpx.Response(200,json={'choices':[{'message':{'content':'' if response_kind=='empty' else '{"events":[],"evidence":[]}'},'finish_reason':'stop' if response_kind=='empty' else 'content_filter'}]})
    result,_=asyncio.run(execute(store,pages(store,10),DeepSeekProvider(transport=httpx.MockTransport(handler))))
    assert len(requests)==3
    assert result['extraction']['progress']['calls']==3
    assert result['extraction']['analysis_state']=='failed'


def test_cache_invalidates_model_output_setting_and_thinking(store):
    raw=pages(store,10); provider=Provider(); settings=Settings()
    async def run():
        await execute(store,raw,provider,settings=settings)
        for changes in ({'model':'deepseek-pro'},{'max_output_tokens':8192},{'thinking':True}):
            settings.config=settings.config.model_copy(update=changes)
            result,_=await execute(store,raw,provider,settings=settings)
            assert result['extraction']['progress']['cached']==0
    asyncio.run(run())
    assert len(provider.calls)==40


def test_unique_whitespace_restoration_does_not_rewrite_or_guess_evidence(store):
    raw=pages(store,10); service=ExtractionService(store,Settings(),Provider())
    block={**raw['snapshot']['blocks'][0],'text':'公布后\n5 个工作日申请复核。'}
    chunk={'views':[{'block':block,'start':0,'end':len(block['text'])}]}
    parsed=output_for(raw['snapshot']['blocks'][0])
    parsed['evidence']=[{'id':'q','block_id':block['block_id'],'quote':block['text'],'start':0,'end':len(block['text'])}]
    date=parsed['events'][0]['deadlines'][0]
    date.update(original_text='公布后5个工作日',normalized_date=None,evidence_ids=['q'])
    assert service.long.restore_phrases(parsed,chunk)==1
    assert date['original_text']=='公布后\n5 个工作日'
    assert date['normalized_date'] is None and parsed['evidence'][0]['quote']==block['text']
    date['original_text']='公布后6个工作日'
    assert service.long.restore_phrases(parsed,chunk)==0
    date['original_text']='公布后5个工作日'
    repeated={**block,'text':block['text']*2};chunk['views']=[{'block':repeated,'start':0,'end':len(repeated['text'])}]
    assert service.long.restore_phrases(parsed,chunk)==0


def test_merged_many_deadlines_confirm_through_existing_review_contract(store):
    from server.extraction_models import ConfirmationInput
    raw=pages(store,50);provider=Provider()
    async def run():
        await execute(store,raw,provider)
        result,_=await execute(store,raw,provider,force=True,scope='remaining')
        return result
    result=asyncio.run(run());parsed=result['extraction']['parsed_output'];event=parsed['events'][0]
    def evidence(ids):
        return [{'block_id':q['block_id'],'quote':q['quote'],'start_offset':q['start'],'end_offset':q['end']} for q in parsed['evidence'] if q['id'] in ids]
    value={**event,'relevance':'yes','participation':'yes'}
    value['deadlines']=[{'id':d['id'],'role':d['role'],'original_text':d['original_text'],'value':d['normalized_date'],'precision':d['precision'],'confirmed_by_user':True,'evidence':evidence(d['evidence_ids'])} for d in event['deadlines']]
    value['actions']=[{'id':a['id'],'title':a['title'],'kind':a['kind'],'deadline_id':next(d['id'] for d in event['deadlines'] if a['id'] in d['related_action_ids']),'evidence':evidence(a['evidence_ids'])} for a in event['actions']]
    payload=ConfirmationInput(expected_version=result['version'],extraction_id=result['extraction_id'],acknowledged_warnings=True,events=[value])
    saved=ReviewStore(store).confirm(raw['snapshot']['id'],payload)['events'][0]
    assert len(saved['deadlines'])==len(saved['tasks'])==50
    with store.connection() as c:
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert {r['page'] for r in c.execute('SELECT page FROM evidence')}==set(range(1,51))


def test_multi_attachment_merge_keeps_pdf_docx_and_ocr_source_locations(store):
    snapshot=deepcopy(pages(store,10)['snapshot'])
    outputs=[]
    for i,block in enumerate(snapshot['blocks']):
        kind='pdf' if i<4 else 'docx' if i<8 else 'image_ocr'
        block.update(source_type=kind,attachment_id=kind+'-fixture',filename=kind+'-fixture',page=i+1 if kind=='pdf' else None,
                     section=f'Table 1 row {i+1}' if kind=='docx' else 'OCR region' if kind=='image_ocr' else None)
        if kind=='image_ocr':
            box={'x':12,'y':34,'width':100,'height':20}
            block['words']=[{'start':0,'end':len(block['text']),'box':box}]
        outputs.append((str(i),output_for(block,i+1)))
    plan=plan_chunks(snapshot,'all')
    assert all(len({v['block']['attachment_id'] for v in chunk['views']})==1 for chunk in plan['all'])
    merged,_=merge_candidates(outputs,snapshot)
    checked=validate_candidate(deepcopy(merged),snapshot)['evidence']
    assert all(q['valid'] for q in checked)
    assert {q['source_type'] for q in checked}=={'pdf','docx','image_ocr'}
    assert all(q['page'] for q in checked if q['source_type']=='pdf')
    assert all(q['section'].startswith('Table 1 row') for q in checked if q['source_type']=='docx')
    assert all(q['bounding_box']==box for q in checked if q['source_type']=='image_ocr')


def test_prior_whitespace_failure_recovers_locally_without_another_paid_request(store,monkeypatch):
    from server.extraction_long import LongExtraction
    blocks=[{'text':f'Page {i}.\n'+('Paragraph.\n'*40)+f'DEADLINE: Submit materials by\n2026-10-{i:02d}.\n'} for i in range(1,11)]
    raw=pages(store,10,parsed_blocks=blocks)
    class WrappedProvider(Provider):
        async def extract(self,*args):
            value=await super().extract(*args)
            if self.calls[-1]==3:
                parsed=json.loads(value.raw_output)
                parsed['events'][0]['deadlines'][0]['original_text']='Submit materials by2026-10-03'
                return ProviderResult(encode(parsed),value.usage,'fixture')
            return value
    provider=WrappedProvider(); original=LongExtraction.restore_phrases
    async def run():
        monkeypatch.setattr(LongExtraction,'restore_phrases',lambda *args:0)
        first,_=await execute(store,raw,provider)
        assert first['extraction']['analysis_state']=='partial'
        calls=len(provider.calls)
        monkeypatch.setattr(LongExtraction,'restore_phrases',original)
        second,_=await execute(store,raw,provider,force=True)
        assert len(provider.calls)==calls
        return second
    result=asyncio.run(run())
    assert result['extraction']['analysis_state']=='validated'
    assert result['extraction']['progress']['calls']==0
    assert result['extraction']['progress']['locally_recovered']==1
    recovered=next(ch for ch in result['extraction']['chunks'] if ch['retry_reason']=='local_phrase_recovery')
    assert recovered['configuration']['recovered_from_record_id']
    assert all(q['valid'] for q in result['extraction']['validation']['evidence'])


def test_many_short_docx_rows_cannot_fall_back_to_lossy_eighty_block_input(store):
    raw=ReviewStore(store).collect(RawInput(original_text='A document with many short table rows.'))
    with store.connection(write=True) as c:
        for i in range(84):
            c.execute('INSERT INTO raw_source_block (raw_snapshot_id,block_id,source_type,ordinal,text,section,words_json) VALUES (?,?,?,?,?,?,?)',
                (raw['snapshot']['id'],f'DOCX_TABLE_01_ROW_{i:03d}','docx',i,f'Row {i}',f'Table 1 row {i}','[]'))
    provider=Provider();service=ExtractionService(store,Settings(),provider)
    async def run():
        with pytest.raises(PersonalError) as error:
            await service.start(raw['snapshot']['id'])
        assert error.value.code=='budget_confirmation_required'
    asyncio.run(run())
    assert not provider.calls
    assert len(ReviewStore(store).get(raw['snapshot']['id'])['snapshot']['blocks'])==84


def test_long_plain_text_persists_source_owner_and_reuses_current_exact_offsets(store):
    text=('📚背景资料。这是一段说明文字。\n'*450)+'DEADLINE: Submit materials by 2026-10-12.'
    raw=ReviewStore(store).collect(RawInput(original_text=text))
    provider=Provider()
    async def run():
        first,_=await execute(store,raw,provider)
        calls=len(provider.calls)
        second,_=await execute(store,raw,provider,force=True)
        assert len(provider.calls)==calls
        return first,second
    first,result=asyncio.run(run())
    assert result['snapshot']['original_text']==text
    assert len(result['snapshot']['blocks'])==1
    assert result['snapshot']['blocks'][0]['block_id']=='TEXT_BODY_001'
    assert result['extraction']['progress']['calls']==0
    assert result['extraction']['parsed_output']['events'][0]['deadlines']
    assert all(q['valid'] and text[q['start']:q['end']]==q['quote'] for q in result['extraction']['validation']['evidence'])
    assert first['extraction']['analysis_state']==result['extraction']['analysis_state']=='validated'
