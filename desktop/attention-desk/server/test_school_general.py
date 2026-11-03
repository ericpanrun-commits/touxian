"""General-course reviews use actual sources without weakening legacy Math gates."""
import asyncio
import copy
import io
import json
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import pytest
from PIL import Image
from pydantic import SecretStr

from server.extraction_errors import ExtractionFailure
from server.extraction_provider import ProviderResult
from server.personal_store import PersonalError, encode
from server.inbox_store import InboxStore
from server.school.export import context_pack
from server.school.models import BookMetadataInput, LectureInput, SectionInput, SourceCorrectionInput
from server.school.profiles import GENERAL, MATH, profile_for
from server.school.recovery import saved_composition
from server.school.source_correction import save_correction
from server.school.protocol import ProtocolFailure, catalog, compose_review, extract_evidence
from server.school.provider import SchoolProvider
from server.school.pdf_pages import catalog as pdf_catalog
from server.school.validation import validate_review
from server.test_course_school import english_course, intake
from server.test_school import evidence_selection, finish, generate_input, prepared, rig
from server.test_documents import text_pdf


def frame(text='Conditional sentences express a possible situation.', quality='text_extracted'):
    return {'lecture': {'id':'test-lecture', 'course_id':'test-english', 'title':'条件句'},
        'mistakes': {'available':False, 'items':[]},
        'sources':[{'source_id':'src-note', 'source_kind':'notes', 'kind':'text', 'text':text, 'quality':quality}]}


def draft(evidence):
    note=next(f for f in evidence['facts'] if f['source_kind']=='notes')
    claim={'text':'核对这段笔记：[['+note['fact_id']+']]',
        'provenance':'uncertain' if note['uncertain'] else 'notes','fact_ids':[note['fact_id']]}
    return {'core_concepts':[{'name':'笔记要点','explanation':copy.deepcopy(claim)}],
        'notes_taught':[claim], 'must_master':[], 'method_cards':[], 'textbook_supplement':[],
        'understanding_gaps':[], 'connections':[], 'self_tests':[], 'suggested_actions':[]}


class GeneralProvider:
    def __init__(self): self.calls=[]; self.bad=False; self.repair_passes=True

    async def extract(self, context, config, key, gate):
        gate(); self.calls.append('evidence')
        return ProviderResult(encode(evidence_selection(context)), {'prompt_tokens':100,'completion_tokens':100}, config.model)

    async def compose(self, evidence, context, config, key, gate):
        gate(); self.calls.append('composition'); result=draft(evidence)
        if self.bad: result['core_concepts'][0]['explanation']['text']='老师没有讲这些内容。'
        return ProviderResult(encode(result), {'prompt_tokens':100,'completion_tokens':100}, config.model)

    async def repair(self, stage, raw, issues, evidence, context, config, key, gate):
        gate(); self.calls.append('repair')
        result=draft(evidence)['core_concepts'][0]
        if not self.repair_passes: result['explanation']['text']='老师确实讲过这些。'
        patch={'patches':[{'op':'replace','path':'/core_concepts/0','value':result}]}
        return ProviderResult(encode(patch), {'prompt_tokens':100,'completion_tokens':100}, config.model)


def test_sparse_general_review_has_explicit_notes_scope_and_no_invented_minimums():
    context=frame(); evidence=extract_evidence(encode(evidence_selection(context)),context)
    content,sources,_=compose_review(encode(draft(evidence)),evidence,context)
    assert content['schema_version']=='lecture-review-general-v1' and content['source_scope']=='notes_only'
    assert len(content['core_concepts'])==1 and content['must_master']==content['method_cards']==content['self_tests']==[]
    assert content['textbook_supplement']==[] and sources==context['sources']
    content['source_scope']='notes_with_textbook'
    with pytest.raises(ExtractionFailure) as failure: validate_review(encode(content),context)
    assert failure.value.code=='source_scope'
    # The model's schema label cannot downgrade the server-selected Math policy.
    context['lecture']['course_id']='course-math-ii'
    with pytest.raises(ProtocolFailure): compose_review(encode(draft(evidence)),evidence,context)
    assert profile_for(context)==MATH and profile_for({})==MATH


@pytest.mark.parametrize('quality',['ocr_unverified','vision_unverified','vision_uncertain','unverified'])
def test_general_unverified_notes_stay_uncertain_and_cannot_supply_answers(quality):
    context=frame('Unverified original wording remains visible.',quality)
    evidence=extract_evidence(encode(evidence_selection(context)),context)
    value=draft(evidence); value['notes_taught'][0]['provenance']='notes'
    content,_,report=compose_review(encode(value),evidence,context)
    assert content['notes_taught'][0]['provenance']=='uncertain' and report['uncertain_fact_ids']==['F01']
    assert content['understanding_gaps'][0]['provenance']=='uncertain'
    value['self_tests']=[{'question':'这段原文是什么？','answer':'原文：[[F01]]','fact_ids':['F01']}]
    with pytest.raises(ProtocolFailure) as failure: compose_review(encode(value),evidence,context)
    assert 'unreliable_answer' in {i['code'] for i in failure.value.issues}


def test_programming_xor_does_not_inherit_math_power_or_missing_domain_conditions():
    context=frame('The C operator ^ performs bitwise XOR on integer operands.')
    evidence=extract_evidence(encode(evidence_selection(context)),context)
    value=draft(evidence)
    value['method_cards']=[{'signal':'核对运算含义','method':'按原文核对：[[F01]]',
        'conditions':'原文没有提供额外前提。','common_misuse':'先对照原文，不套用其他运算含义。','fact_ids':['F01']}]
    content,_,_=compose_review(encode(value),evidence,context)
    assert '^' in content['method_cards'][0]['method'] and '最终为正' not in encode(content)
    assert content['must_master']==[]  # Already covered by the concept/method.
    uncovered=draft(evidence);uncovered['core_concepts']=[]
    retained,_,_=compose_review(encode(uncovered),evidence,context)
    assert retained['must_master'][0]['text'].startswith('核对原文中的关键表达')
    context=frame('Use the branch only when the condition is true.')
    evidence=extract_evidence(encode(evidence_selection(context)),context)
    assert catalog(context)[0]['condition_bearing']
    with pytest.raises(ProtocolFailure) as failure: compose_review(encode(value),evidence,context)
    assert 'conditions_required' in {i['code'] for i in failure.value.issues}


@pytest.mark.parametrize('text,code',[
    ('老师没有讲这段内容。','teacher_claim'),
    ('你经常把这个知识点弄错。','invented_history'),
    ('这段笔记记录 a = 7。','unbound_math'),
    ('教材第 8 页给出结论。','model_page'),
])
def test_general_does_not_bypass_fact_and_history_guards(text,code):
    context=frame(); evidence=extract_evidence(encode(evidence_selection(context)),context)
    value=draft(evidence);value['core_concepts'][0]['explanation']['text']=text
    with pytest.raises(ProtocolFailure) as failure: compose_review(encode(value),evidence,context)
    assert code in {i['code'] for i in failure.value.issues}


def test_notes_only_generation_reuses_receipt_and_ignores_unrelated_book_metadata(tmp_path):
    async def run():
        async with rig(tmp_path,fixture_mode=False) as r:
            course=english_course(r.service); provider=GeneralProvider();r.service.provider=provider
            raw,_=intake(r.service,tmp_path,'general-notes','Conditional sentences express a possible situation.',course)
            lecture=r.service.store.create_lecture(LectureInput(course_id=course,title='条件句',notes_raw_item_id=raw),str(uuid4()))
            before=r.service.contexts.build(lecture['id']); request=generate_input(lecture,use_vision=False)
            queued=await r.service.generate(lecture['id'],request);await finish(r.service)
            job=r.service.jobs.get(queued['job']['id']);assert job['status']=='completed',job
            artifact=r.service.artifacts.get(job['result']['artifact_id'])
            assert artifact['content']['source_scope']=='notes_only' and artifact['input_versions']['textbook'] is None
            assert artifact['status']=='needs_review' and not artifact['adopted'] and not artifact['lecture_version_changed']
            assert artifact['real_textbook_acceptance']=='not_used' and not artifact['is_fixture']
            assert artifact['generation']['stage_prompts']==GENERAL.stage_prompts
            assert artifact['prompt_version']==GENERAL.prompt and artifact['cost']['text_calls']==2
            with r.store.connection() as c:
                prompts={row[0] for row in c.execute('SELECT prompt_version FROM school_ai_attempt')}
                assert prompts=={GENERAL.evidence,GENERAL.composition}
                assert c.execute('SELECT count(*) FROM task').fetchone()[0]==0
            await r.service.generate(lecture['id'],request);await finish(r.service)
            unused=r.service.store.register_book(BookMetadataInput(course_id=course,title='Unused grammar reference'),str(uuid4()))
            book_raw,_=intake(r.service,tmp_path,'unused-reference','Isolated reference not linked to any classroom.',course)
            r.service.store.link_book(book_raw,unused['version'],'chapter_scan',fixture=True,textbook_id=unused['id'])
            after=r.service.contexts.build(lecture['id'])
            assert after['context_id']==before['context_id'] and after['input_versions']==before['input_versions']
            assert not r.service.artifacts.get(artifact['id'])['lecture_version_changed']
            reused=await r.service.generate(lecture['id'],generate_input(lecture,use_vision=False));await finish(r.service)
            assert reused['artifact_id']==artifact['id'] and provider.calls==['evidence','composition']
            with ZipFile(io.BytesIO(context_pack(r.service,lecture['id']))) as archive:
                schema=json.loads(archive.read('review-schema.json'))
                assert schema['properties']['schema_version']['const']=='lecture-review-general-v1'
                assert '资料不足的部分允许为空' in archive.read('context.md').decode()
    asyncio.run(run())


@pytest.mark.parametrize('repair_passes',[True,False])
def test_general_paid_stage_ledger_retains_one_repair_limit(tmp_path,repair_passes):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service);provider=GeneralProvider();provider.bad=True;provider.repair_passes=repair_passes
            r.service.provider=provider
            raw,_=intake(r.service,tmp_path,'general-repair','Original classroom wording for a conditional sentence.',course)
            lecture=r.service.store.create_lecture(LectureInput(course_id=course,title='条件句',notes_raw_item_id=raw),str(uuid4()))
            queued=await r.service.generate(lecture['id'],generate_input(lecture,use_vision=False));await finish(r.service)
            job=r.service.jobs.get(queued['job']['id']); artifact=r.service.artifacts.get(job['result']['artifact_id'])
            assert job['status']==('completed' if repair_passes else 'needs_review')
            assert bool(artifact['content'])==repair_passes and artifact['generation']['repair_used']
            assert provider.calls==['evidence','composition','repair'] and artifact['cost']['text_calls']==3
            assert artifact['cost']['stages']['repair']['calls']==1
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM school_ai_attempt WHERE prompt_version=?',(GENERAL.repair,)).fetchone()[0]==1
    asyncio.run(run())


def test_general_saved_composition_recovers_once_with_original_profile_and_sources(tmp_path,monkeypatch):
    async def run():
        import server.school.service as service_module
        async with rig(tmp_path) as r:
            course=english_course(r.service);provider=GeneralProvider();provider.bad=True;r.service.provider=provider
            raw,_=intake(r.service,tmp_path,'saved-general','A possible situation is described in the notes.',course)
            lecture=r.service.store.create_lecture(LectureInput(course_id=course,title='条件句',notes_raw_item_id=raw),str(uuid4()))
            with monkeypatch.context() as earlier:
                earlier.setattr(service_module,'repair_roots',lambda *args:set())
                queued=await r.service.generate(lecture['id'],generate_input(lecture,use_vision=False));await finish(r.service)
            old=r.service.artifacts.get(r.service.jobs.get(queued['job']['id'])['result']['artifact_id'])
            context=r.service.contexts.build(lecture['id'])
            assert saved_composition(r.store,context,old['model'])['artifact_id']==old['id']
            queued=await r.service.generate(lecture['id'],generate_input(lecture,use_vision=False,repair_from=old['id']));await finish(r.service)
            job=r.service.jobs.get(queued['job']['id']);new=r.service.artifacts.get(job['result']['artifact_id'])
            assert job['status']=='completed' and new['content']['source_scope']=='notes_only'
            assert new['cost']['text_calls']==1 and new['generation']['recovered_from']==old['id']
            assert new['input_versions']['review_profile']==GENERAL.key and provider.calls==['evidence','composition','repair']
            assert r.service.artifacts.get(old['id'])==old
            assert saved_composition(r.store,context,old['model']) is None
    asyncio.run(run())


def test_general_source_correction_is_free_append_only_and_keeps_notes_scope(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service);provider=GeneralProvider();provider.bad=True;provider.repair_passes=False;r.service.provider=provider
            raw,_=intake(r.service,tmp_path,'correct-general','An original sentence is retained for source checking.',course)
            lecture=r.service.store.create_lecture(LectureInput(course_id=course,title='原文核对',notes_raw_item_id=raw),str(uuid4()))
            queued=await r.service.generate(lecture['id'],generate_input(lecture,use_vision=False));await finish(r.service)
            old=r.service.artifacts.get(r.service.jobs.get(queued['job']['id'])['result']['artifact_id'])
            descriptor=old['source_correction'];assert descriptor
            body=SourceCorrectionInput.model_validate({'request_id':str(uuid4()),'expected_version':lecture['version'],
                'draft_hash':descriptor['draft_hash'],
                'changes':[{'key':f['key'],'segments':['请依据原文核对这段说明。']+f['segments'][1:]} for f in descriptor['fields']]})
            with r.store.connection() as c: before={row['id']:dict(row) for row in c.execute('SELECT * FROM review_artifact')}
            saved=save_correction(r.store,old['id'],body)
            assert save_correction(r.store,old['id'],body)==saved
            corrected=r.service.artifacts.get(saved['artifact_id'])
            assert corrected['content']['source_scope']=='notes_only' and corrected['provider']=='source_checked'
            assert corrected['cost']['text_calls']==0 and corrected['status']=='needs_review' and not corrected['adopted']
            assert corrected['input_versions']['source_checked_revision']['source_quality_upgraded'] is False
            assert provider.calls==['evidence','composition','repair']
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==3
                assert all(dict(c.execute('SELECT * FROM review_artifact WHERE id=?',(identity,)).fetchone())==row for identity,row in before.items())
    asyncio.run(run())


def test_unreadable_general_note_does_not_start_paid_text_stages(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            course=english_course(r.service);provider=GeneralProvider();r.service.provider=provider
            file=text_pdf(tmp_path/'unreadable-general.pdf',['Raw document before registered text extraction.'])
            saved=r.service.documents.files.collect([(file.name,'application/pdf',file)],'',file.stem,str(uuid4()))
            raw=saved['snapshot']['raw_item_id'];InboxStore(r.service.personal).link(raw,1,course,'')
            doc=r.service.learning.register(saved['snapshot']['attachments'][0]['id'])
            r.service.learning.catalog(doc['id'],pdf_catalog(file))
            lecture=r.service.store.create_lecture(LectureInput(course_id=course,title='待核对原页',notes_raw_item_id=raw),str(uuid4()))
            with pytest.raises(PersonalError) as failure:
                await r.service.generate(lecture['id'],generate_input(lecture,use_vision=False))
            assert failure.value.code=='notes_text_required' and provider.calls==[]
            with r.store.connection() as c: assert c.execute('SELECT count(*) FROM school_ai_attempt').fetchone()[0]==0
    asyncio.run(run())


def test_generic_provider_sends_profile_schema_and_never_math_domain_instructions(tmp_path):
    async def run():
        context=frame('The C operator ^ performs bitwise XOR on integer operands.')
        evidence=extract_evidence(encode(evidence_selection(context)),context)
        captured=[]; provider=SchoolProvider()
        async def capture(instruction,payload,*args): captured.append((instruction,payload))
        provider._json=capture
        await provider.extract(context,None,None,None)
        await provider.compose(evidence,context,None,SecretStr('test-only'),None)
        assert '不套用高数公式' in captured[0][0]
        assert '底数最终为正' not in captured[1][0]
        assert captured[1][1]['schema']['properties']['self_tests'].get('minItems',0)==0
        # Capture the real vision body without contacting any model service.
        class Transport:
            async def request(self,body,*args,**kwargs): self.body=body;return 'captured'
        transport=Transport();provider=SchoolProvider(transport)
        image=tmp_path/'page.png';Image.new('RGB',(8,8),'white').save(image)
        page={'source_id':'src-image','document_id':'doc','page':1,'block_id':'block','path':str(image),'review_profile':GENERAL.key}
        config=SimpleNamespace(base_url='https://api.deepseek.com',model='deepseek-flash',max_output_tokens=4096)
        await provider.vision([page],config,None,None)
        instruction=transport.body['messages'][0]['content'][0]['text']
        assert '保留原有英语、代码' in instruction and '高数笔记' not in instruction
    asyncio.run(run())
