"""Adversarial source, formula and bounded-repair checks on unrelated fixtures."""
import asyncio
import copy
import json
from types import SimpleNamespace
import pytest
from server.personal_store import PersonalError, encode
from server.extraction_provider import ProviderResult
from server.school.protocol import (ProtocolFailure, catalog, extract_evidence, compose_review, apply_targeted_repair)
from server.school.models import FeedbackInput
from server.test_school import rig, prepared, finish, generate_input, evidence_selection, composition_output


def sample():
    return {'lecture':{'id':'fixture-series','title':'等比级数与变量代换','lecture_date':'2026-10-26'},
      'mistakes':{'available':False,'items':[]},'context_id':'unrelated-fixture',
      'sources':[
        {'source_id':'src-note','source_kind':'notes','kind':'text','quality':'text_extracted',
         'text':'例：设 a≠0，t→0，lim sin(a·t)/t=a。需保留外部系数与变量趋向。'},
        {'source_id':'src-book','source_kind':'textbook','kind':'text','quality':'text_extracted',
         'text':'等比级数：若 |r|<1，则 ∑_{k=0}∞ r^k=1/(1−r)。使用前须检查收敛条件。'},
        {'source_id':'src-uncertain','source_kind':'notes','kind':'text','quality':'vision_uncertain',
         'text':'原页写 lim_{h→2}(h²−4)/(h−2)=?，末行不可辨认。'}]}


def valid(context=None):
    context=context or sample()
    evidence=extract_evidence(encode(evidence_selection(context)),context)
    draft=composition_output(evidence,context)
    return context,evidence,draft


def issues_for(draft,evidence,context):
    with pytest.raises(ProtocolFailure) as failure:compose_review(encode(draft),evidence,context)
    return failure.value


def test_fact_binding_copies_formula_conditions_and_builds_ten_public_sections():
    context,evidence,draft=valid()
    result,sources,report=compose_review(encode(draft),evidence,context)
    assert len(result)==11 and len(result['self_tests'])==3
    assert result['textbook_supplement'][0]['notes_presence']=='not_found_in_provided_evidence'
    assert result['mistakes']=={'history_available':False,'message':'当前没有足够历史错误数据','items':[]}
    lookup={s['source_id']:s for s in context['sources']}
    assert {s['source_id'] for s in sources}==set(lookup)
    for item in result['method_cards']:
        assert '|r|<1' in item['conditions'] and '1/(1−r)' in item['conditions']
        assert all(c['quote']==lookup[c['source_id']]['text'] for c in item['citations'])
    assert report['needs_review'] and report['uncertain_fact_ids']


@pytest.mark.parametrize('text,code',[
    ('这个极限等于二。','unbound_math'),('计算得到 x→3。','unbound_math'),
    ('把结果写成 a^2。','unbound_math'),('老师没有讲这项内容。','teacher_claim'),
    ('教材第十二页给出这个结论。','model_page')])
def test_generated_math_teacher_and_page_assertions_cannot_bypass_binding(text,code):
    context,evidence,draft=valid();draft['core_concepts'][0]['explanation']['text']=text
    failure=issues_for(draft,evidence,context)
    assert any(i['code']==code and i['path']=='/core_concepts/0/explanation/text' for i in failure.issues)


def test_unknown_fact_and_inline_reference_both_have_targetable_paths():
    context,evidence,draft=valid()
    draft['core_concepts'][0]['explanation'].update(text='核对原式：[[F99]]',fact_ids=['F99'])
    failure=issues_for(draft,evidence,context)
    assert {i['path'] for i in failure.issues}>={'/core_concepts/0/explanation/text','/core_concepts/0/explanation/fact_ids'}


def test_notes_and_textbook_roles_are_validated_independently_of_prose():
    context,evidence,draft=valid();draft['notes_taught'][0]['fact_ids']=['F02'];draft['notes_taught'][0]['text']='当前笔记记录了比较方法。'
    failure=issues_for(draft,evidence,context)
    assert {'notes_evidence','notes_only','source_role'} <= {i['code'] for i in failure.issues}


@pytest.mark.parametrize('quality',['ocr_unverified','vision_uncertain','vision_unverified','unverified'])
def test_source_confidence_controls_downgrade_even_when_model_selects_confirmed(quality):
    context=sample();context['sources'][0]['quality']=quality
    selection=evidence_selection(context);selection['notes_facts'].append('F01');selection['uncertain_items']=[]
    evidence=extract_evidence(encode(selection),context)
    assert 'F01' not in evidence['categories']['notes_facts']
    assert {'F01','F03'}<=set(evidence['uncertain_fact_ids'])
    draft=composition_output(evidence,context);draft['notes_taught'][0]['provenance']='notes'
    result,_,report=compose_review(encode(draft),evidence,context)
    assert result['notes_taught'][0]['provenance']=='uncertain' and report['needs_review']
    assert any(c['provenance']=='uncertain' for c in result['understanding_gaps'])


def test_uncertain_formula_cannot_be_a_method_or_reference_answer():
    context,evidence,draft=valid()
    draft['method_cards'][0]['fact_ids']=['F03']
    draft['method_cards'][0]['conditions']='需要核对原页：[[F03]]'
    draft['self_tests'][0]['fact_ids']=['F03'];draft['self_tests'][0]['answer']='[[F03]]'
    failure=issues_for(draft,evidence,context)
    assert {'unreliable_method','unreliable_answer'} <= {i['code'] for i in failure.issues}


def test_extracted_semantic_uncertainty_survives_a_reliable_transcription_label():
    context=sample();selection=evidence_selection(context)
    selection['uncertain_items'].append('F01')
    evidence=extract_evidence(encode(selection),context)
    assert 'F01' in evidence['uncertain_fact_ids'] and 'F01' not in evidence['protected_facts']
    assert 'F01' not in evidence['categories']['notes_facts']
    draft=composition_output(evidence,context)
    result,_,_=compose_review(encode(draft),evidence,context)
    assert result['notes_taught'][0]['provenance']=='uncertain'


def test_known_uncertainty_and_omitted_formula_are_preserved_without_new_math():
    context,evidence,draft=valid();draft['understanding_gaps']=[]
    for item in draft['core_concepts']:item['explanation']['fact_ids']=['F02']
    draft['notes_taught'][0]['text']='当前笔记提供了一则例题。'
    result,_,report=compose_review(encode(draft),evidence,context)
    assert any('sin(a·t)/t=a' in c['text'] for c in result['must_master'])
    assert any('末行不可辨认' in c['text'] and c['provenance']=='uncertain' for c in result['understanding_gaps'])
    assert {n['rule'] for n in report['safety_notes']}>={'source_formula_condition_coverage_preserved','uncertainty_warning_preserved'}


def test_conditions_cannot_be_dropped_and_scoped_patch_preserves_other_items():
    context,evidence,draft=valid();draft['method_cards'][0]['conditions']='直接使用就可以。';raw=encode(draft)
    failure=issues_for(draft,evidence,context)
    patch=encode({'patches':[{'op':'replace','path':'/method_cards/0/conditions','value':'核对原文的全部前提：[[F02]]'}]})
    repaired=apply_targeted_repair(patch,raw,failure);result,_,_=compose_review(repaired,evidence,context)
    assert json.loads(repaired)['core_concepts']==draft['core_concepts']
    assert '|r|<1' in result['method_cards'][0]['conditions']
    assert json.loads(raw)['method_cards'][0]['conditions']=='直接使用就可以。'


def test_repair_cannot_regenerate_whole_section_or_change_unrelated_fields():
    context,evidence,draft=valid();draft['method_cards'][0]['conditions']='直接使用即可。'
    failure=issues_for(draft,evidence,context)
    patch=encode({'patches':[{'op':'replace','path':'/core_concepts','value':[]}]})
    with pytest.raises(ProtocolFailure,match='具体项目'):apply_targeted_repair(patch,encode(draft),failure)


def test_missing_note_item_does_not_hide_math_failures_and_both_fit_one_scoped_repair():
    context,evidence,draft=valid();draft['notes_taught']=[]
    draft['core_concepts'][0]['explanation']['text']='变量趋于三。'
    raw=encode(draft);failure=issues_for(draft,evidence,context)
    assert {issue['code'] for issue in failure.issues}>={'missing_note_item','unbound_math'}
    note=next(f for f in evidence['facts'] if f['source_kind']=='notes' and f['uncertain'])
    corrected={'text':'对照原页核对这段笔记：[['+note['fact_id']+']]','provenance':'uncertain','fact_ids':[note['fact_id']]}
    concept=composition_output(evidence,context)['core_concepts'][0]
    patched=apply_targeted_repair(encode({'patches':[
        {'op':'add','path':'/notes_taught/0','value':corrected},
        {'op':'replace','path':'/core_concepts/0','value':concept}]}),raw,failure)
    result,_,report=compose_review(patched,evidence,context)
    assert result['notes_taught'][0]['provenance']=='uncertain' and report['needs_review']
    assert json.loads(patched)['method_cards']==draft['method_cards']
    assert json.loads(raw)['notes_taught']==[]


@pytest.mark.parametrize('patches',[
    [{'op':'replace','path':'/notes_taught','value':[]}],
    [{'op':'replace','path':'/notes_taught/0','value':{}}],
    [{'op':'add','path':'/notes_taught/0/text','value':'没有来源的描述'}],
    [{'op':'add','path':'/notes_taught/1','value':{}}],
    [{'op':'add','path':'/notes_taught/0','value':{}},{'op':'add','path':'/notes_taught/0','value':{}}],
])
def test_missing_note_repair_cannot_replace_section_or_add_multiple_items(patches):
    context,evidence,draft=valid();draft['notes_taught']=[]
    failure=issues_for(draft,evidence,context)
    with pytest.raises(ProtocolFailure):apply_targeted_repair(encode({'patches':patches}),encode(draft),failure)


def test_missing_note_repair_cannot_turn_textbook_fact_into_classroom_note():
    context,evidence,draft=valid();draft['notes_taught']=[]
    failure=issues_for(draft,evidence,context)
    item={'text':'对照笔记：[[F02]]','provenance':'uncertain','fact_ids':['F02']}
    patched=apply_targeted_repair(encode({'patches':[{'op':'add','path':'/notes_taught/0','value':item}]}),encode(draft),failure)
    assert any(issue['code']=='notes_only' for issue in issues_for(json.loads(patched),evidence,context).issues)


def test_missing_note_provider_exposes_only_one_add_and_other_failed_item():
    from server.school.provider import SchoolProvider
    context,evidence,draft=valid();draft['notes_taught']=[]
    draft['core_concepts'][0]['explanation']['text']='变量趋于三。'
    failure=issues_for(draft,evidence,context);captured={};provider=SchoolProvider()
    async def capture(instruction,payload,*args):captured.update(payload=payload)
    provider._json=capture
    asyncio.run(provider.repair('composition',encode(draft),failure.issues,evidence,context,None,None,None))
    items={item['path']:item for item in captured['payload']['target_items']}
    assert set(items)=={'/notes_taught/0','/core_concepts/0'}
    assert items['/notes_taught/0']['operation']=='add' and items['/notes_taught/0']['current_value'] is None
    assert items['/core_concepts/0']['operation']=='replace'


def test_atomic_action_repair_can_fix_linked_fields_without_touching_other_items():
    context,evidence,draft=valid()
    action={'text':'核对这个表达：[[F01]]','duration_minutes':10,
            'rationale':{'text':'查看原文。','provenance':'notes','fact_ids':['F01']}}
    draft['suggested_actions']=[copy.deepcopy(action),copy.deepcopy(action)]
    draft['suggested_actions'][1]['text']='另行整理笔记。'
    failure=issues_for(draft,evidence,context)
    corrected=copy.deepcopy(action);corrected['text']='核对原式的完整条件。'
    corrected['rationale']['text']='对照已登记的笔记原文：[[F01]]'
    repaired=apply_targeted_repair(encode({'patches':[{'op':'replace','path':'/suggested_actions/0','value':corrected}]}),encode(draft),failure)
    compose_review(repaired,evidence,context)
    assert json.loads(repaired)['suggested_actions'][1]==draft['suggested_actions'][1]
    assert json.loads(repaired)['core_concepts']==draft['core_concepts']
    for path in ('/suggested_actions','/suggested_actions/1/rationale/text'):
        with pytest.raises(ProtocolFailure):
            apply_targeted_repair(encode({'patches':[{'op':'replace','path':path,'value':'无关改动。'}]}),encode(draft),failure)


def test_repair_receives_only_failed_atomic_items_and_same_binding_rules():
    from server.school.provider import SchoolProvider, REPAIR_INSTRUCTION
    context,evidence,draft=valid();draft['method_cards'][0]['conditions']='直接使用即可。'
    failure=issues_for(draft,evidence,context);captured={}
    provider=SchoolProvider()
    async def capture(instruction,payload,*args):
        captured.update(instruction=instruction,payload=payload)
    provider._json=capture
    asyncio.run(provider.repair('composition',encode(draft),failure.issues,evidence,context,None,None,None))
    payload=captured['payload']
    assert payload['allowed_paths']==['/method_cards/0']
    assert len(payload['target_items'])==1 and payload['target_items'][0]['current_value']==draft['method_cards'][0]
    assert 'core_concepts' not in payload and 'original_raw' not in payload
    assert payload['schema']['$defs']['RepairPatch']['properties']['path']['enum']==['/method_cards/0']
    assert '不附写任何英文字母' in REPAIR_INSTRUCTION and '建议 text' in REPAIR_INSTRUCTION


def test_targeted_repair_is_revalidated_for_the_original_math_failure():
    context,evidence,draft=valid()
    draft['core_concepts'][0]['explanation']['text']='参数 a 的公式是：[[F02]]'
    failure=issues_for(draft,evidence,context)
    replacement=copy.deepcopy(draft['core_concepts'][0])
    bad=apply_targeted_repair(encode({'patches':[{'op':'replace','path':'/core_concepts/0','value':replacement}]}),encode(draft),failure)
    assert any(i['code']=='unbound_math' for i in issues_for(json.loads(bad),evidence,context).issues)
    replacement['explanation']['text']='参数取值与趋向按原文核对：[[F02]]'
    good=apply_targeted_repair(encode({'patches':[{'op':'replace','path':'/core_concepts/0','value':replacement}]}),encode(draft),failure)
    compose_review(good,evidence,context)


def test_validation_reports_the_exact_free_prose_fragment_including_chinese_numbers():
    context,evidence,draft=valid()
    draft['core_concepts'][0]['explanation']['text']='当变量趋于三时，核对原文：[[F02]]'
    failure=issues_for(draft,evidence,context)
    issue=next(i for i in failure.issues if i['code']=='unbound_math')
    assert issue['unbound_fragments']==['趋于三']


def test_generic_binding_has_no_lesson_specific_numeric_answer():
    context=sample();context['sources'][0]['text']='例：当 x→7，常函数 f(x)=13，故极限为13。'
    context,evidence,draft=valid(context);result,_,_=compose_review(encode(draft),evidence,context)
    assert '极限为13' in result['notes_taught'][0]['text']
    assert 'sin(3x)' not in encode(result) and 'e^k' not in encode(result)


def test_source_passages_are_not_recursively_interpreted_as_model_placeholders():
    context=sample();context['sources'][0]['text']='原文字符串[[F99]]：当 x→7，常函数 f(x)=13。'
    context,evidence,draft=valid(context)
    result,_,_=compose_review(encode(draft),evidence,context)
    assert '[[F99]]' in result['notes_taught'][0]['text']
    assert any('[[F99]]' in c['text'] for c in result['must_master'])


def test_evidence_unknown_id_and_wrong_source_role_are_not_silently_relabelled():
    context=sample();selection=evidence_selection(context);selection['notes_facts']=['F02','F99']
    with pytest.raises(ProtocolFailure) as failure:extract_evidence(encode(selection),context)
    assert {'source_role','unknown_fact','evidence_coverage'} <= {i['code'] for i in failure.value.issues}


def test_pipeline_records_two_stages_and_preserves_user_adoption_boundary(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path);request=generate_input(p.lecture)
            queued=await r.service.generate(p.lecture['id'],request);await finish(r.service)
            job=r.service.jobs.get(queued['job']['id']);assert job['status']=='completed',job
            artifact=r.service.artifacts.get(job['result']['artifact_id'])
            assert r.provider.calls==['evidence','composition']
            assert artifact['status']=='needs_review' and not artifact['adopted']
            assert artifact['generation']['validation_status']=='passed'
            assert set(artifact['cost']['stages'])=={'evidence','composition'}
            with r.store.connection() as c:
                assert c.execute('SELECT count(*) FROM task').fetchone()[0]==0
                assert c.execute('SELECT accepted_artifact_id FROM lecture').fetchone()[0] is None
            again=await r.service.generate(p.lecture['id'],request);await finish(r.service)
            assert again['job']['id']==job['id'] and r.provider.calls==['evidence','composition']
    asyncio.run(run())


@pytest.mark.parametrize('repair_passes',[True,False])
def test_only_one_item_repair_is_issued_and_failure_never_becomes_usable_content(tmp_path,repair_passes):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            async def bad(evidence,context,config,key,gate):
                gate();r.provider.calls.append('composition')
                draft=composition_output(evidence,context);draft['method_cards'][0]['conditions']='老师没有讲这些条件。'
                return ProviderResult(encode(draft),{'prompt_tokens':100,'completion_tokens':100},'deepseek-flash')
            async def repair(stage,raw,issues,evidence,context,config,key,gate):
                gate();r.provider.calls.append('repair')
                fid=next(f['fact_id'] for f in evidence['facts'] if f['source_kind']=='textbook')
                text='核对原文条件：[['+fid+']]' if repair_passes else '老师确实讲过这些。'
                return ProviderResult(encode({'patches':[{'op':'replace','path':'/method_cards/0/conditions','value':text}]}),
                    {'prompt_tokens':100,'completion_tokens':100},'deepseek-flash')
            r.provider.compose=bad;r.provider.repair=repair
            queued=await r.service.generate(p.lecture['id'],generate_input(p.lecture));await finish(r.service)
            job=r.service.jobs.get(queued['job']['id']);artifact=r.service.artifacts.get(job['result']['artifact_id'])
            assert r.provider.calls==['evidence','composition','repair']
            assert job['status']==('completed' if repair_passes else 'needs_review')
            assert bool(artifact['content'])==repair_passes
            assert artifact['status']=='needs_review' and artifact['cost']['text_calls']==3
            assert artifact['generation']['repair_used']
            assert artifact['cost']['stages']['repair']['calls']==1
            if not repair_passes:
                with r.store.connection() as c:
                    raw=c.execute("SELECT raw_output FROM school_ai_attempt WHERE job_id=? AND prompt_version LIKE 'lecture-targeted-repair-%'",(job['id'],)).fetchone()[0]
                assert list(json.loads(raw))==['patches']
                with pytest.raises(PersonalError):r.service.artifacts.feedback(artifact['id'],FeedbackInput(action='accept',expected_lecture_version=p.lecture['version']))
                await asyncio.sleep(0)
                assert r.provider.calls==['evidence','composition','repair']
    asyncio.run(run())


def test_source_edit_during_composition_prevents_stale_content_from_being_saved(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path);normal=r.provider.compose
            async def edit(evidence,context,config,key,gate):
                response=await normal(evidence,context,config,key,gate)
                r.service.learning.store_page(p.note['id'],1,{'extracted_text':'This source changed while generation was running.'},method='pypdf',quality='text_extracted')
                return response
            r.provider.compose=edit
            queued=await r.service.generate(p.lecture['id'],generate_input(p.lecture));await finish(r.service)
            job=r.service.jobs.get(queued['job']['id']);artifact=r.service.artifacts.get(job['result']['artifact_id'])
            assert job['status']=='failed' and artifact['content'] is None
            assert artifact['error']['code']=='conflict'
            assert r.provider.calls==['evidence','composition']
    asyncio.run(run())


def test_repair_budget_is_shared_between_extraction_and_composition(tmp_path):
    async def run():
        async with rig(tmp_path) as r:
            p=prepared(r.service,tmp_path)
            async def bad_extract(context,config,key,gate):
                gate();r.provider.calls.append('evidence')
                selected=evidence_selection(context);selected['notes_facts']=['F99']
                return ProviderResult(encode(selected),{'prompt_tokens':100,'completion_tokens':100},'deepseek-flash')
            async def fix_extract(stage,raw,issues,evidence,context,config,key,gate):
                gate();r.provider.calls.append('repair')
                # Both bad element and category coverage are targetable; patch the
                # listed whole category rather than touching another stage.
                correct=evidence_selection(context)['notes_facts']
                return ProviderResult(encode({'patches':[{'op':'replace','path':'/notes_facts','value':correct}]}),
                    {'prompt_tokens':100,'completion_tokens':100},'deepseek-flash')
            async def bad_compose(evidence,context,config,key,gate):
                gate();r.provider.calls.append('composition')
                draft=composition_output(evidence,context);draft['core_concepts'][0]['name']='老师没讲这项内容'
                return ProviderResult(encode(draft),{'prompt_tokens':100,'completion_tokens':100},'deepseek-flash')
            r.provider.extract=bad_extract;r.provider.repair=fix_extract;r.provider.compose=bad_compose
            queued=await r.service.generate(p.lecture['id'],generate_input(p.lecture));await finish(r.service)
            job=r.service.jobs.get(queued['job']['id']);artifact=r.service.artifacts.get(job['result']['artifact_id'])
            assert r.provider.calls==['evidence','repair','composition']
            assert job['status']=='needs_review' and artifact['content'] is None
            assert artifact['cost']['text_calls']==3 and artifact['generation']['repair_used']
    asyncio.run(run())
