"""Consent, egress, bounded billing, and conservative evidence integration."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from server.ai_settings import AIConfig
from server.ai_transport import DeepSeekTransport
from server.learning.analysis import LearningAnalysis,record_authorization,authorization,CALL_KIND,DAILY_REQUESTS
from server.learning.models import AnalysisAuthorizationInput,CapturedPage,MistakeSuggestion,QuizQuestion
from server.learning.service import LearningService
from server.learning.local_questions import retest_family
from server.personal_store import PersonalError,encode
from server.school.provider import SchoolProvider
from server.test_learning import store,capture,parse


def consent(store,enabled=True):
    return record_authorization(store,AnalysisAuthorizationInput(scope_version='official-deepseek-learning-text-v1',
        enabled=enabled,confirmation_source='explicit_user_chat',confirmation_text='允许上述范围自动处理' if enabled else '暂停后台处理'))


def runner(store,handler,config=None,provider=None):
    service=LearningService(store.personal,None,None)
    settings=SimpleNamespace(resolved=lambda:(config or AIConfig(),SecretStr('fixture-key-never-real')))
    service.analysis_runner=LearningAnalysis(service,settings,provider=provider or SchoolProvider(DeepSeekTransport(httpx.MockTransport(handler))))
    return service


def response(raw='{"questions":[],"preview_completed_quote":""}',usage=True):
    data={'model':'deepseek-flash','choices':[{'message':{'content':raw},'finish_reason':'stop'}]}
    if isinstance(usage,dict):data['usage']=usage
    elif usage:data['usage']={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}
    return httpx.Response(200,json=data)


def calls(store):
    with store.personal.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM learning_artifact WHERE kind=?',(CALL_KIND,))]


def test_no_consent_never_sends_and_user_grant_survives_service_restart(store):
    async def run():
        sent=[];service=runner(store,lambda req:sent.append(req) or response())
        art=store.reserve_artifact('capture_analysis','one')
        assert await service.call(art,CapturedPage,{'operation':'extract_submitted_records','text':'共享学习文字'}) is None
        assert not sent and not calls(store)
        consent(store);assert service.analysis_status()['enabled']
        service=runner(store,lambda req:sent.append(req) or response())
        art=service.reserve_work('capture_analysis','one')
        assert await service.call(art,CapturedPage,{'operation':'extract_submitted_records','text':'共享学习文字'}) is not None
        assert len(sent)==1
        assert calls(store)[0]['status']=='completed'
        await service.close()
    asyncio.run(run())


def test_actual_egress_is_only_official_text_and_credentials_are_redacted(store):
    async def run():
        sent=[];service=runner(store,lambda req:sent.append(req) or response());consent(store)
        art=store.reserve_artifact('diagnostic','safe-text')
        payload={'operation':'extract_submitted_records','text':'题目文字\n正文 password=hidden-password; Cookie: sid=fixture-session\nCookie: session=secret\nfixture-key-never-real\nC:\\private\\book.pdf\nhttps://school.test/results?token=secret#session\nhttps://fixture-user:fixture-link-password@school.test/result?token=fixture-session#secret\nHTTPS://fixture-user:fixture-link-password@school.test/result',
            'raw_file':'never-send-original','browser_session':'never-send-session','local_path':'never-send-path'}
        await service.call(art,CapturedPage,payload)
        assert len(sent)==1 and str(sent[0].url)=='https://api.deepseek.com/chat/completions'
        body=sent[0].content.decode();data=json.loads(json.loads(body)['messages'][1]['content'])
        assert set(data)=={'schema','operation','text'}
        assert all(s not in body for s in ['hidden-password','fixture-session','fixture-user','fixture-link-password','session=secret','fixture-key-never-real','private','token=secret','never-send'])
        assert '[credential omitted]' in body and '[本机路径已省略]' in body
        assert 'https://school.test/results' in data['text']
        assert json.loads(calls(store)[0]['cost_json'])['estimated_cost']>0
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('base',['https://proxy.test','https://api.deepseek.com.evil.test','https://api.deepseek.com:444','https://api.deepseek.com/upload'])
def test_non_official_destination_cannot_send(store,base):
    async def run():
        sent=[];service=runner(store,lambda req:sent.append(req) or response(),AIConfig(base_url=base));consent(store)
        art=store.reserve_artifact('capture_analysis',base)
        assert await service.call(art,CapturedPage,{'operation':'extract_submitted_records','text':'共享文字'}) is None
        assert not sent and not calls(store)
        await service.close()
    asyncio.run(run())


def test_pause_between_reservation_and_network_keeps_zero_cost(store):
    class PausingProvider(SchoolProvider):
        async def _json(self,*args):
            consent(store,False)
            return await super()._json(*args)
    async def run():
        sent=[];provider=PausingProvider(DeepSeekTransport(httpx.MockTransport(lambda req:sent.append(req) or response())))
        service=runner(store,None,provider=provider);consent(store)
        art=store.reserve_artifact('capture_analysis','pause')
        assert await service.call(art,CapturedPage,{'operation':'extract_submitted_records','text':'共享文字'}) is None
        ledger=calls(store)[0]
        assert not sent and ledger['model'] is None and json.loads(ledger['cost_json'])['estimated_cost']==0
        assert not service.analysis_status()['enabled']
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure',['invalid_json','timeout','429','missing_usage'])
def test_failure_is_recorded_once_and_never_replayed(store,failure):
    async def run():
        sent=[]
        def handler(req):
            sent.append(req)
            if failure=='timeout':raise httpx.ReadTimeout('fixture timeout',request=req)
            if failure=='429':return httpx.Response(429)
            return response('{"questions":[],"questions":[]}' if failure=='invalid_json' else '{"questions":[],"preview_completed_quote":""}',usage=failure!='missing_usage')
        service=runner(store,handler);consent(store)
        art=store.reserve_artifact('capture_analysis','failure')
        data={'operation':'extract_submitted_records','text':'共享文字'}
        await service.call(art,CapturedPage,data)
        ledger=calls(store)[0];before=ledger.copy()
        assert ledger['model'] and ledger['status']==('completed' if failure=='missing_usage' else 'needs_review')
        cost=json.loads(ledger['cost_json'])
        assert cost['estimated_cost'] is None if failure in {'timeout','429','missing_usage'} else cost['estimated_cost']>0
        await service.call(art,CapturedPage,data)
        resumed=service.reserve_work('capture_analysis','failure')
        assert resumed['status']!='running' and len(sent)==1 and calls(store)[0]==before
        await service.close()
    asyncio.run(run())


def test_daily_and_monthly_limits_stop_without_network(store):
    async def run():
        sent=[];service=runner(store,lambda req:sent.append(req) or response());consent(store)
        for i in range(DAILY_REQUESTS+1):
            art=store.reserve_artifact('capture_analysis',str(i))
            await service.call(art,CapturedPage,{'operation':'extract_submitted_records','text':'共享文字'})
        assert len(sent)==DAILY_REQUESTS and len(calls(store))==DAILY_REQUESTS
        with store.personal.connection(write=True) as c:
            c.execute('UPDATE learning_artifact SET cost_json=? WHERE kind=?',(encode({'estimated_cost':20}),CALL_KIND))
        with pytest.raises(PersonalError,match='额度'):
            service.analysis_runner.reserve_call(store.reserve_artifact('diagnostic','budget'),{'operation':'historical_diagnostic'},'deepseek-flash',.20)
        assert len(sent)==DAILY_REQUESTS
        await service.close()
    asyncio.run(run())


def test_unknown_usage_preserves_model_specific_reservation_and_near_limit_stops(store):
    async def run():
        sent=[];config=AIConfig(model='deepseek-v4-pro',max_output_tokens=4500)
        service=runner(store,lambda req:sent.append(req) or response(usage=False),config);consent(store)
        data={'operation':'extract_submitted_records','text':'学习文字'*7500}
        art=store.reserve_artifact('capture_analysis','large')
        reserve=LearningAnalysis.reservation(data,config,CapturedPage)
        assert reserve>.40
        # An already billed fixture call leaves less than this request's reservation.
        previous=store.reserve_artifact(CALL_KIND,'previous')
        store.finish_artifact(previous['id'],{'parent_id':'previous'},model=config.model,cost={'estimated_cost':20-reserve+.001})
        assert await service.call(art,CapturedPage,data) is None
        assert not sent
        with store.personal.connection(write=True) as c:c.execute('DELETE FROM learning_artifact WHERE id=?',(previous['id'],))
        art=service.reserve_work('capture_analysis','large')
        await service.call(art,CapturedPage,data)
        assert len(sent)==1
        value=json.loads(calls(store)[0]['cost_json'])
        assert value['estimated_cost'] is None and value['reserved_cost_cny']==reserve
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('usage',[{'prompt_tokens':100},{'completion_tokens':20}])
def test_partial_usage_does_not_release_unknown_cost_reservation(store,usage):
    async def run():
        service=runner(store,lambda req:response(usage=usage));consent(store)
        art=store.reserve_artifact('capture_analysis','partial')
        data={'operation':'extract_submitted_records','text':'共享文字'}
        expected=LearningAnalysis.reservation(data,AIConfig(),CapturedPage)
        await service.call(art,CapturedPage,data)
        cost=json.loads(calls(store)[0]['cost_json'])
        assert cost['estimated_cost'] is None and cost['reserved_cost_cny']==expected
        await service.close()
    asyncio.run(run())


def test_consent_text_must_be_explicit_and_pause_order_is_stable(store):
    with pytest.raises(PersonalError):record_authorization(store,AnalysisAuthorizationInput(scope_version='official-deepseek-learning-text-v1',
        enabled=True,confirmation_source='explicit_local_ui',confirmation_text='随便'))
    consent(store);consent(store,False);consent(store)
    with store.personal.connection() as c:assert authorization(c)['enabled']


def test_cause_quote_and_retest_family_cannot_overstate_learning(store):
    async def run():
        service=runner(store,lambda req:response());consent(store)
        text='高数\n题目：重要极限的条件是什么\nA. 无需条件\nB. 需要条件\n我的答案：A\n回答错误'
        aid=store.save_page(capture(store,text),parse(text),trusted=True)[0]
        service.sources_for=lambda _:[{'source_id':'algebra','quote':'lim_{x→1}(x²−1)/(x−1)=2'}]
        q=QuizQuestion(question='lim(x→3) (x²−9)/(x−3) 等于多少？',options={'A':'0','B':'3','C':'6','D':'9'},correct_key='C',explanation='先约去非零因子，再求极限。',source_ids=['algebra'])
        async def guess(*args):return MistakeSuggestion(category='粗心',reason='你只是粗心',evidence_quotes=['不存在的推导'],retest=q)
        service.analysis_runner=guess
        await service.prepare_mistake(aid)
        detail=store.topic(store.topics()[0]['id'])
        assert detail['followups'][0]['exercise_id'] is None
        art=next(a for a in detail['artifacts'] if a['kind']=='mistake_analysis')
        assert art['payload']['category']=='暂时无法判断' and art['payload']['uncertain'] is True
        assert detail['learning_state']['label']=='建议复习'
        assert retest_family(q.question)=='removable_rational'
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('with_options',[True,False])
def test_nullable_retest_saves_only_evidenced_suggestion(store,with_options):
    async def run():
        service=runner(store,lambda req:response());consent(store)
        choices='\nA.先核对适用条件\nB.任何情况都能直接代入\nC.只看点值\nD.只看结果' if with_options else ''
        text='高数\n题目：重要极限的条件是什么'+choices+'\n我的答案：B\n回答错误'
        aid=store.save_page(capture(store,text),parse(text),trusted=True)[0]
        service.sources_for=lambda _:[{'source_id':'source','quote':'本题有适用条件，需要逐项检查。'}]
        async def suggest(*args):return MistakeSuggestion(category='条件漏看',reason='可先检查适用条件',evidence_quotes=['重要极限的条件是什么'],retest=None)
        service.analysis_runner=suggest
        await service.prepare_mistake(aid)
        detail=store.topic(store.topics()[0]['id'])
        art=next(a for a in detail['artifacts'] if a['kind']=='mistake_analysis')
        assert art['payload']['category']==('条件漏看' if with_options else '暂时无法判断')
        assert art['payload']['origin']=='ai_suggestion'
        if not with_options:assert '尚缺完整选项' in art['payload']['reason']
        assert detail['followups'][0]['exercise_id'] is None and len(detail['attempts'])==1
        await service.close()
    asyncio.run(run())


def test_authorized_cause_analysis_keeps_verified_same_skill_local_retest(store):
    async def run():
        sent=[]
        def handler(req):
            sent.append(req)
            return response(encode({'category':'条件漏看','reason':'可检查去心邻域的条件','uncertain':True,
                'evidence_quotes':['lim(x→2) (x²−4)/(x−2) 的极限'],'retest':None}))
        service=runner(store,handler);consent(store)
        text='高数\n题目：lim(x→2) (x²−4)/(x−2) 的极限\n我的答案：B\n回答错误'
        aid=store.save_page(capture(store,text),parse(text),trusted=True)[0]
        service.sources_for=lambda _:[{'source_id':'source','quote':'lim_{x→1}(x²−1)/(x−1)=2'}]
        await service.prepare_mistake(aid)
        detail=store.topic(store.topics()[0]['id'])
        assert len(sent)==1 and len(calls(store))==1
        assert detail['followups'][0]['status']=='ready' and len(detail['attempts'])==1
        art=next(a for a in detail['artifacts'] if a['kind']=='mistake_analysis')
        assert art['payload']['origin']=='ai_suggestion' and art['payload']['uncertain']
        await service.close()
    asyncio.run(run())
