"""Attention quality: source truth, no review noise, contextual confirmation and push gates."""
import asyncio
import copy
from datetime import datetime,timedelta
import json

import httpx
import pytest
from PIL import Image

from server.extraction_models import ConfirmationInput
from server.notifications.config import BarkConfig
from server.notifications.wechat import WeChatBarkDispatcher,candidate_level
from server.personal_store import PersonalError
from server.review_store import ReviewStore
from server.test_extraction_review import confirmation
from server.tests_wechat.test_pipeline import output_for,setup_service,scan
from server.tests_wechat.test_workbench import populated
from server.tests_wechat.test_multigroup import make_service
from server.wechat_collect.inbox import list_candidates,briefing,set_ignored
from server.wechat_collect.models import Observation,ObservedMessage,WeChatOutput
from server.wechat_collect.quality import assess,apply,personalize,reclassify_saved

NOW=datetime.fromisoformat('2026-10-23T12:00:00+08:00')
BASE={'id':'i','title':'通知','important':True,'relevant':True,'category':'task','action_required':True,'participation':'required','priority':'P1','attention_level':'action','information_complete':True,'await_more_context':False,'actions':[{'id':'a','title':'执行通知要求','kind':'explicit','evidence_ids':['q'],'deadline_ids':[]}],'evidence_message_ids':['m'],'deadlines':[],'uncertainties':[]}

@pytest.mark.parametrize('text,change,bucket',[
 ('请各位同学完成在线自学和考试，完成后提交承诺书与成绩单。',{'category':'exam'},'action'),
 ('各位老师，如果同学查不到成绩，请老师登录网址查询。',{'title':'成绩查询指引','action_required':False},'fyi'),
 ('练习需要校园网，考试必须使用校园网。',{'category':'notice'},'fyi'),
 ('优秀内务一等奖、二等奖、三等奖。',{'action_required':False,'relevant':None},'filtered'),
 ('有意参加的同学请报名校园健身活动。',{'category':'event','participation':'optional'},'filtered'),
 ('[图片]',{'category':'other','message_type':'image'},'media_pending'),
 ('[图片]',{'category':'other','message_type':'image','title':'另一张不可识别的图片'},'media_pending'),
 ('上课地点是不是改成二田？',{'category':'course_change'},'unresolved'),
])
def test_original_eight_semantic_regression(text,change,bucket):
    item={**BASE,**change}; kind=item.pop('message_type','text')
    decision=assess(item,[{'message_id':'m','raw_text':text,'message_type':kind}],legacy=True)
    assert decision.bucket==bucket
    actual=apply(item,decision)
    assert actual['needs_review']==(bucket=='action')
    if bucket!='action': assert candidate_level(actual,NOW) is None


@pytest.mark.parametrize('text',['收到','哈哈哈哈','吃什么好呢','作业怎么办？','听说考试地点改了','上课地点是不是改为二田？'])
def test_chatter_and_uncertainty_never_reach_review_even_if_model_claims_action(text):
    decision=assess(BASE,[{'message_id':'m','raw_text':text,'message_type':'text'}])
    assert decision.bucket!='action'
    assert candidate_level(apply(BASE,decision),NOW) is None


def test_model_contract_requires_attention_and_completeness_fields():
    required=WeChatOutput.model_json_schema()['$defs']['WeChatItem']['required']
    assert {'attention_level','action_required','relevant','information_complete','await_more_context','reason'}<=set(required)


def confirmed_output(payload):
    result=output_for(payload)
    item=result['items'][0]
    item.update(title='课程上课地点改为二田',category='course_change',action_required=False,action=None,actions=[])
    question=next(m for m in payload['messages'] if '是不是' in next(b['text'] for b in payload['blocks'] if b['block_id']==m['block_id']))
    block=next(b for b in payload['blocks'] if b['block_id']==question['block_id'])
    result['evidence'].append({'id':'q0','message_id':question['message_id'],'block_id':block['block_id'],'quote':block['text'],'start':0,'end':len(block['text'])})
    item['evidence_message_ids'].append(question['message_id'])
    item['evidence_mapping']=[{'field':'title','evidence_ids':['q0','q1']}]
    return result


def test_question_waits_without_model_then_confirmation_uses_held_context_once(personal):
    question=ObservedMessage('上课地点是不是改成二田？',sender='同学',source_time='2026-10-22T10:00:00+08:00',time_reliability='reliable')
    service,current,calls=setup_service(personal,[Observation('test','unverified',(question,),True,True)],response=confirmed_output)
    async def run():
        await scan(service); await service.process_queue()
        assert not calls and list_candidates(personal)['total']==0
        assert briefing(personal,at=current[0])['unresolved']['total']==1
        # The question falls outside the ordinary 8-message context tail.
        noise=tuple(ObservedMessage('收到',sender='同学'+str(i)) for i in range(50))
        current[0]+=timedelta(hours=1)
        confirmation_message=ObservedMessage('老师确认了，改到二田。',sender='同学',source_time=current[0].isoformat(),time_reliability='reliable')
        service.adapter.observations=[Observation('test','unverified',(question,*noise,confirmation_message),True,True)]
        await scan(service); await service.process_queue()
        assert len(calls)==1
    asyncio.run(run())
    item=list_candidates(personal)['items'][0]
    assert item['analysis']['title']=='课程上课地点改为二田'
    assert len(item['analysis']['evidence_message_ids'])==2
    assert candidate_level(item['analysis'],NOW)=='timeSensitive'
    with personal.connection() as c:
        assert c.execute('SELECT status FROM wechat_context_buffer').fetchone()[0]=='resolved'
        assert c.execute('SELECT count(*) FROM event').fetchone()[0]==0
    assert not reclassify_saved(personal,service.context.read().model_dump()) or list_candidates(personal)['total']==1
    assert briefing(personal)['unresolved']['total']==0


def test_exact_reposted_notice_is_filtered_before_paid_analysis(personal,tmp_path):
    service,sources,calls=make_service(personal,tmp_path,2)
    text='作业通知：请提交完整实验报告'
    async def run():
        sources['g0'].add(text);await scan(service);await service.process_queue()
        assert len(calls)==1
        sources['g0'].add(text);await scan(service);await service.process_queue()
        assert len(calls)==1 and list_candidates(personal)['total']==1
        sources['g1'].add(text);await scan(service);await service.process_queue()
        assert len(calls)==2 and list_candidates(personal)['total']==2
    asyncio.run(run())
    with personal.connection() as c:
        assert c.execute("SELECT count(*) FROM wechat_message_state WHERE filter_reason='duplicate_notice'").fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM wechat_message').fetchone()[0]==3


def test_continuous_unreadable_images_are_one_attachment_group_not_candidates(personal):
    images=(ObservedMessage('[图片]',message_type='image',attachment_identity='one'),ObservedMessage('[图片]',message_type='image',attachment_identity='two'))
    service,_,calls=setup_service(personal,[Observation('test','unverified',images,True,True)])
    async def run(): await scan(service); await service.process_queue()
    asyncio.run(run())
    assert not calls and list_candidates(personal)['total']==0
    held=briefing(personal)['media']
    assert held['total']==2 and len(held['items'])==1
    assert held['items'][0]['attachment_count']==2


def test_readable_image_reaches_attention_only_after_ocr(personal):
    spool=personal.path.parent/'wechat-spool';spool.mkdir()
    path=spool/'notice.png';Image.new('RGB',(150,100),'white').save(path)
    message=ObservedMessage('[图片]',message_type='image',attachment_identity='readable',media_path=str(path))
    service,_,calls=setup_service(personal,[Observation('test','unverified',(message,),True,True)])
    service.document.wechat.context_followup.enabled=False
    service.media_engine=lambda *a:([{'text':'请在周五前提交实验报告'}],{})
    async def run(): await scan(service); await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1 and list_candidates(personal)['total']==1
    assert briefing(personal)['media']['total']==0


def test_fyi_archives_and_cannot_be_confirmed_or_restored_into_pending(personal):
    populated(personal)
    fyi=briefing(personal)['fyi']['items'][0]
    assert fyi['auto_archived_at'] and not fyi['analysis']['needs_review']
    bundle=ReviewStore(personal).get(fyi['snapshot_id'])
    request=confirmation(bundle,bundle['extraction']['parsed_output'])
    request.events[0].id=fyi['model_item_id']
    with pytest.raises(PersonalError) as error: ReviewStore(personal).confirm(fyi['snapshot_id'],request)
    assert error.value.code=='attention_not_action'
    with pytest.raises(PersonalError): set_ignored(personal,fyi['id'],False)
    assert list_candidates(personal)['total']==1


def test_fyi_only_source_is_archived_not_pending_raw_review(personal):
    def response(payload):
        result=output_for(payload); result['items'][0].update(attention_level='fyi',category='notice',action_required=False,actions=[])
        return result
    service,_,calls=setup_service(personal,[Observation('test','unverified',(ObservedMessage('考试必须使用校园网。'),),True,True)],response=response)
    async def run(): await scan(service); await service.process_queue()
    asyncio.run(run())
    assert len(calls)==1 and list_candidates(personal)['total']==0
    assert briefing(personal)['fyi']['total']==1
    with personal.connection() as c: assert c.execute('SELECT status FROM raw_review').fetchone()[0]=='archived'
    assert ReviewStore(personal).list()['total']==0


@pytest.mark.parametrize('change',[{'attention_level':'fyi'},{'information_complete':False},{'await_more_context':True},{'relevant':None},{'analysis_origin':'local_pending'}])
def test_bark_rejects_fyi_unresolved_and_media_even_when_urgent(change):
    assert candidate_level({**BASE,'priority':'P0','deadline':{'value':'2026-10-23'},**change},NOW) is None


def test_conditional_relay_is_fyi_without_assuming_user_has_login_problem():
    decision=assess(BASE,[{'message_id':'m','raw_text':'用校园网登录后显示密码错误、登录不进去的同学，请接龙姓名学号班级。','message_type':'text'}])
    assert decision.bucket=='fyi' and decision.relevant is None and not decision.action_required


def test_other_role_deadline_is_not_assigned_to_student():
    deadline={'value':'2026-11-20'}
    item={**BASE,'actions':[{'id':'student','title':'将成绩单交班级宣传委员','deadline_ids':[]},{'id':'officer','title':'班级宣传委员统一收齐并发送','deadline_ids':['d']}],
          'deadline':deadline,'deadlines':[{'id':'d','role':'班级宣传委员汇总提交','time':deadline}]}
    result=personalize(item,{})
    assert [a['id'] for a in result['actions']]==['student']
    assert result['deadline'] is None and result['deadlines']==[]

def test_rolling_relay_participation_is_not_a_new_task_or_model_request(personal,tmp_path):
    service,sources,calls=make_service(personal,tmp_path,1)
    for size in range(1,20):
        text='#接龙\n显示密码错误\n\n'+'\n'.join(f'{i}. 合成同学{i} - 合成班级' for i in range(1,size+1))
        sources['g0'].add(text)
    async def run(): await scan(service); await service.process_queue()
    asyncio.run(run())
    assert calls==[] and list_candidates(personal)['total']==0
    with personal.connection() as c:
        assert c.execute("SELECT count(*) FROM wechat_message_state WHERE filter_reason='relay_participation_update'").fetchone()[0]==19
        assert c.execute('SELECT count(*) FROM wechat_message').fetchone()[0]==19


def test_url_query_mark_is_not_a_question():
    from server.wechat_collect.quality import waiting_text
    assert not waiting_text('作业请在周五前提交到 https://example.invalid/form?id=1')


def test_old_queued_relay_is_pruned_without_model_or_source_loss(personal,tmp_path,monkeypatch):
    from server.wechat_collect.filtering import FilterDecision
    from server.wechat_collect.quality import prune_processing
    service,sources,calls=make_service(personal,tmp_path,1)
    sources['g0'].add('#接龙\n显示密码错误\n1. 合成同学 - 合成班级')
    # Reproduce a durable job created under the previous, broader local filter.
    with monkeypatch.context() as old:
        old.setattr('server.wechat_collect.service.classify',lambda *a,**kw:FilterDecision('candidate','legacy_filter'))
        asyncio.run(scan(service))
    with personal.connection() as c:
        batch=c.execute('SELECT id FROM wechat_batch').fetchone()[0]
    service.analysis.enqueue(batch,service.context.read())
    with personal.connection() as c:
        before=[tuple(r) for r in c.execute('SELECT id,raw_text FROM wechat_message')]
        assert c.execute("SELECT count(*) FROM wechat_analysis_job WHERE status='pending'").fetchone()[0]==1
    assert prune_processing(personal,service.context.read().model_dump(),NOW)==1
    asyncio.run(service.process_queue())
    assert calls==[] and list_candidates(personal)['total']==0
    with personal.connection() as c:
        assert [tuple(r) for r in c.execute('SELECT id,raw_text FROM wechat_message')]==before
        assert c.execute('SELECT status FROM wechat_analysis_job').fetchone()[0]=='filtered'
        assert c.execute('SELECT status FROM raw_review').fetchone()[0]=='archived'
