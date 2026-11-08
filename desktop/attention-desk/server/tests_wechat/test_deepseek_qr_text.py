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
