import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from server.tests_wechat.test_deepseek_cloud_consumer import personal,setup,bound,consumer
from server.tests_wechat.test_deepseek_cloud_preview import analyzed,preview
from server.tests_wechat.test_pipeline import output_for
from server.wechat_collect.qr_text_policy import EFFECTIVE_FROM,HINT,scan_notice
from server.wechat_collect.deepseek_content_gate import minimize_notice
from server.extraction_errors import ExtractionFailure
from server.cloud_sync.wechat_outbound_validation import validate_outbound

TEXT='请全体同学于11月10日中午12点前{scan}完成志愿活动登记，11月11日到教学楼一楼集合。二维码见群内图片。'
CASES=[dict(text=TEXT.format(scan=scan),time='2026-11-08T22:00:00+08:00',media=True)
       for scan in ('扫码','扫描二维码','扫码登记','扫码填写')]


@pytest.mark.parametrize('setup',CASES,indirect=True)
def test_qr_text_crosses_protected_dispatch_without_media_and_only_once(bound):
    s=bound;runtime,state,gets,_=consumer(s)
    before=preview(s['personal'],s['job'])
    text=before['samples']['samples'][0]['payload']['text']
    calls=s['analysis_calls']
    assert len(calls)==1 and '扫码正文规则' in calls[0]['messages'][0]['content']
    wire=json.loads(calls[0]['messages'][-1]['content'].split('仅处理以下数据:\n')[1])
    assert len(wire['messages'])==len(wire['blocks'])==1
    assert wire['blocks'][0]['text']==text
    assert 'captured_at' not in wire['messages'][0]
    assert 'QR-ATTACHMENT-SECRET' not in json.dumps(calls)
    assert asyncio.run(runtime.tick())['results']==['acknowledged']
    assert asyncio.run(runtime.tick())['state']=='idle'
    assert len(gets)==len(s['backend'].sent)==len(calls)==1
    body=s['backend'].sent[0]
    rows={r['kind']:r['payload'] for r in body['records']}
    snapshot=rows['raw_review']['snapshot']
    assert snapshot['original_text']==text
    assert snapshot['document_warnings'][-1]=={'code':'qr_in_original_chat','message':HINT}
    assert snapshot['attachments']==snapshot['attachment_jobs']==rows['inbox']['attachments']==[]
    assert before['bundle']['resources']==[]
    assert all(m['message_type']=='text' for m in snapshot['wechat']['messages'])
    assert all(b['source_type']=='text' and b['attachment_id'] is None for b in snapshot['blocks'])
    assert 'QR-ATTACHMENT-SECRET' not in json.dumps(body)
    with s['personal'].connection() as db:
        assert db.execute("SELECT count(*) FROM wechat_message m JOIN wechat_message_state s ON s.message_id=m.id WHERE m.message_type IN ('image','file') AND s.status='parsed'").fetchone()[0]==0
    # Synthetic fixture used by the independent TypeScript and browser checks.
    out=Path(__file__).resolve().parents[2]/'artifacts/deepseek-qr-20261108/synthetic-qr-bundle.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf8')


@pytest.mark.parametrize('time',['2026-11-08T21:51:59+08:00','2026-10-22T10:00:00+08:00'])
def test_old_scan_text_does_not_get_new_policy_or_trigger_reanalysis(personal,time):
    calls,job=analyzed(personal,text=TEXT.format(scan='扫码'),time=time,media=True)
    with pytest.raises(ValueError,match='no_complete_school_notice'):preview(personal,job)
    assert len(calls)==1
    assert '扫码正文规则' not in calls[0]['messages'][0]['content']


@pytest.mark.parametrize('change',[{'captured_at':'2026-11-08T13:51:59+00:00'},
    {'source_time':'2026-11-08T13:51:59+00:00'},{'time_reliability':'unverified'},
    {'message_type':'image'},{'source_time':None}])
def test_scan_policy_requires_new_reliable_text(change):
    message={'raw_text':'请扫码登记','source_time':EFFECTIVE_FROM,'captured_at':EFFECTIVE_FROM,
             'message_type':'text','time_reliability':'reliable',**change}
    assert not scan_notice([message])


@pytest.mark.parametrize('text',['请扫码，姓名：张三','请扫码，学号：2026001234',
    '请扫码，手机号13800000000','请扫码：\n张三、李四','请扫码查看处分名单',
    '请扫码 data:image/png;base64,AAAA'])
def test_scan_words_do_not_exempt_personal_or_encoded_data(text):
    with pytest.raises(ExtractionFailure):minimize_notice(text)


@pytest.mark.parametrize('text',['有意参加的同学请扫码，张三同学负责。','有意参加的张三同学请扫码。'])
def test_generic_audience_never_exempts_a_named_person(text):
    from server.cloud_sync.wechat_outbound_validation import _safe_text
    assert not _safe_text(text,scan_audience=True)


def test_generic_audience_exception_is_not_enabled_for_legacy_text():
    from server.cloud_sync.wechat_outbound_validation import _safe_text
    assert not _safe_text('有意参加的同学请扫码。')
    assert _safe_text('有意参加的同学请扫码。',scan_audience=True)


@pytest.mark.parametrize('reference',['二维码见群内图片','二维码详见附件','请参照附件中的二维码','请按照图片中的二维码'])
def test_only_qr_reference_is_removed_from_dependency_check(reference):
    from server.wechat_collect.qr_text_policy import dependency_text
    text='请全体同学扫码登记。'+reference+'。截止时间见附件。'
    messages=[dict(raw_text=text,source_time=EFFECTIVE_FROM,captured_at=EFFECTIVE_FROM,
                   message_type='text',time_reliability='reliable')]
    result=dependency_text(text,messages,dict(information_complete=True,await_more_context=False))
    assert reference not in result and '截止时间见附件' in result
    assert messages[0]['raw_text']==text


def test_attachment_dependent_incomplete_notice_remains_held(personal):
    def incomplete(payload):
        result=output_for(payload)
        result['items'][0].update(information_complete=False,await_more_context=True)
        return result
    _,job=analyzed(personal,text='请扫码，具体事项见图片。',time='2026-11-08T22:00:00+08:00',media=True,response=incomplete)
    with pytest.raises(ValueError,match='no_complete_school_notice'):preview(personal,job)


@pytest.mark.parametrize('change',['image','binary','warning','phone','roster'])
def test_cloud_qr_hint_never_exempts_any_other_validation(personal,change):
    _,job=analyzed(personal,**CASES[0]);result=preview(personal,job)
    rows={r['kind']:deepcopy(r) for r in result['bundle']['records']}
    snap=rows['raw_review']['payload']['snapshot']
    if change=='image':snap['attachments']=[{'data':'QR-ATTACHMENT-SECRET'}]
    elif change=='binary':snap['attachment_jobs']=[{'binary':'AAAA'}]
    elif change=='warning':snap['document_warnings'][-1]['message']='extra private text'
    else:snap['original_text']+='手机号13800000000' if change=='phone' else '名单：张三、李四'
    raw=rows['raw_item']['payload']
    with pytest.raises(ValueError):validate_outbound(rows,raw['id'],snap['id'],raw['source_id'])


@pytest.mark.parametrize('index',range(3))
def test_actual_fictional_deepseek_response_saves_and_projects_without_another_call(personal,index):
    fixture=Path(__file__).resolve().parents[1]/'fixtures/wechat/qr-text-live-replay.json'
    case=json.loads(fixture.read_text(encoding='utf8'))['cases'][index]
    sample=case['sample']
    def response(payload):
        result=deepcopy(case['response']);result['batch_id']=payload['batch_id']
        return result
    calls,job=analyzed(personal,text=sample['text'],time=sample['source_time'],media=True,response=response)
    if sample['id']=='qr-incomplete':
        with pytest.raises(ValueError,match='no_complete_school_notice'):preview(personal,job)
    else:
        result=preview(personal,job)
        assert result==preview(personal,job)
        assert result['samples']['samples'][0]['payload']['text']==sample['text']
        with personal.connection() as db:
            item=json.loads(db.execute("SELECT data_json FROM wechat_candidate WHERE model_item_id!='_pending'").fetchone()[0])
        assert item['deadline']['value']==('2026-11-10T12:00' if sample['id']=='qr-required' else '2026-11-12T18:00')
        assert item['location']==('教学楼一楼' if sample['id']=='qr-required' else '图书馆报告厅')
        assert item['action_required']==(sample['id']=='qr-required')
        assert '扫码' in item['action'] or '扫描二维码' in item['action']
        assert result['bundle']['resources']==[]
    assert len(calls)==1
