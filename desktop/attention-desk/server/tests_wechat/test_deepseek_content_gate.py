import asyncio
import json

import pytest

from server.extraction_errors import ExtractionFailure
from server.tests_wechat.conftest import personal
from server.tests_wechat.test_pipeline import setup_service, scan
from server.wechat_collect.deepseek_content_gate import (
    FILENAME, POLICY, leased_grant, minimize_notice, read_grant, binding_digest,
)
from server.wechat_collect.models import Observation, ObservedMessage


def grant(**updates):
    value={'version':1, 'enabled':True, 'authorization_id':'synthetic-ongoing-grant',
        'destination':'https://api.deepseek.com', 'policy':POLICY,
        'not_before':'2026-10-22T09:00:00+08:00', 'chat_watermarks':{'test':0}, **updates}
    value.setdefault('source_bindings',{key:'0'*64 for key in value['chat_watermarks']})
    return value


def write_grant(personal, value):
    with personal.connection() as connection:
        for key in value['chat_watermarks']:
            row=connection.execute('SELECT source_id,binding_json FROM wechat_chat WHERE id=?',(key,)).fetchone()
            if row:
                value['source_bindings'][key]=binding_digest(row['source_id'],row['binding_json'])
    (personal.path.parent/FILENAME).write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')


def queued(personal, text='请全体同学明天提交实验报告。', *, reliable=True):
    observed=ObservedMessage(text, sender='虚构联系人',
        source_time='2026-10-22T10:00:00+08:00' if reliable else None,
        time_reliability='reliable' if reliable else 'unverified')
    service, clock, calls=setup_service(personal,[Observation('test','unverified',(observed,),True,True)])
    service.synthetic_content_grant=False
    service.document.wechat.context_followup.enabled=False
    asyncio.run(scan(service))
    with personal.connection() as connection:
        batch=connection.execute('SELECT id FROM wechat_batch').fetchone()[0]
    job=service.analysis.enqueue(batch,service.context.read())
    return service, clock, calls, job


@pytest.mark.parametrize('text', [
    '@所有人，请明天下午2:35到体育馆集合，携带身份证。',
    '请2026级同学在11月10日提交高等数学作业。',
    '成绩查询需要使用校园网，操作方式另行通知。',
    '全体同学，请明天提交报告。',
])
def test_safe_notice_is_not_rewritten(text):
    assert minimize_notice(text)==text


def test_only_complete_contact_line_is_removed():
    original='请全体同学明天提交实验报告。\n联系人：虚构甲 13800000000'
    assert minimize_notice(original,['虚构甲'])=='请全体同学明天提交实验报告。'


@pytest.mark.parametrize('header',['活动联系人：','十、活动联系人：','9. 活动联系人：','   [太阳]活动联系人'])
def test_contact_header_requires_a_concrete_non_action_contact_line(header):
    original='请全体同学明天提交实验报告。\n'+header+'\n虚构甲 13800000000\n虚构乙 13900000000'
    assert minimize_notice(original,['虚构甲','虚构乙'])=='请全体同学明天提交实验报告。'


@pytest.mark.parametrize('text', [
    '请提交报告，联系13800000000。', '请提交报告，联系１３８００００００００。',
    '请提交报告，联系138 0000 0000。', '请提交报告，联系138\u200b00000000。',
    '请提交报告，联系138\t0000\t0000。', '请提交报告，@ 张三',
    '请提交报告。\n活动联系人：\n张三', '联系人：请张三明天提交报告。',
    '张三同学：请提交报告。', '成绩：张三85分。', '请提交报告，@张三',
    '请提交报告 https://example.test/private?token=secret',
    '请提交报告，foo%40example%2Etest', '请提交报告，\\u0031\\u0033\\u0038',
    '请提交报告。\n家庭住址：某路', '请提交报告，学号：20260000001',
    '请提交报告。\n1. 张三', '请提交报告。\n张三、李四',
    '请提交报告 YWxpY2VAcHJpdmF0ZS1leGFtcGxlLmNvbQ==',
])
def test_private_ambiguous_or_encoded_content_is_held(text):
    with pytest.raises(ExtractionFailure): minimize_notice(text)


def test_known_sender_name_in_task_is_held_instead_of_removing_audience():
    with pytest.raises(ExtractionFailure): minimize_notice('请虚构甲明天提交报告。',['虚构甲'])


@pytest.mark.parametrize('updates', [
    {'enabled':False}, {'enabled':'true'}, {'version':2}, {'destination':'https://elsewhere.test'},
    {'policy':'different'}, {'not_before':'2026-10-22'}, {'chat_watermarks':{'test':-1}},
    {'mode':'reviewed'}, {'unexpected_field':True},
])
def test_invalid_grants_fail_closed(updates):
    with pytest.raises(ExtractionFailure): read_grant(json.dumps(grant(**updates)).encode())


def test_duplicate_permission_fields_are_rejected():
    raw=json.dumps(grant()).replace('"enabled": true','"enabled": false, "enabled": true')
    with pytest.raises(ExtractionFailure): read_grant(raw.encode())


def test_inflight_lease_prevents_false_revocation_success(personal):
    write_grant(personal,grant())
    with leased_grant(personal):
        with pytest.raises(OSError): write_grant(personal,grant(enabled=False))
    write_grant(personal,grant(enabled=False))
    with pytest.raises(ExtractionFailure):
        with leased_grant(personal): pytest.fail('revoked grant accepted')


def test_missing_grant_blocks_before_key_and_cost(personal,monkeypatch):
    service,_,calls,job=queued(personal)
    monkeypatch.setattr(service.analysis.settings,'resolved',lambda:pytest.fail('must not read key'))
    assert asyncio.run(service.analysis.run_one(job)) is False
    assert calls==[]
    with personal.connection() as connection:
        assert connection.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==0
        assert connection.execute('SELECT error_code FROM wechat_analysis_job').fetchone()[0]=='content_authorization_unavailable'


def test_continuous_projection_saved_once_without_contact_or_identifiers(personal):
    text='请全体同学明天提交实验报告。\n联系人：虚构甲 13800000000'
    service,_,calls,job=queued(personal,text)
    write_grant(personal,grant())
    assert asyncio.run(service.analysis.run_one(job)) is True
    assert asyncio.run(service.analysis.run_one(job)) is False
    assert len(calls)==1
    wire=json.dumps(calls,ensure_ascii=False)
    assert '13800000000' not in wire and '虚构' not in wire
    payload=json.loads(calls[0]['messages'][-1]['content'].split('仅处理以下数据:\n',1)[1])
    assert payload['primary_message_ids']==['m1'] and payload['batch_id']=='batch'
    assert payload['messages'][0]['sender'] is None and payload['user_context']['notes']==''
    with personal.connection() as connection:
        assert connection.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==1
        assert connection.execute('SELECT status FROM wechat_analysis_job').fetchone()[0]=='parsed'
        parsed=json.loads(connection.execute('SELECT parsed_output FROM ai_extraction').fetchone()[0])
        assert parsed['evidence'][0]['block_id'].startswith('WX_')
        assert connection.execute('SELECT count(*) FROM event').fetchone()[0]==0


@pytest.mark.parametrize('boundary', ['source_time','watermark','chat','revoked','unreliable'])
def test_continuous_scope_blocks_history_other_chat_and_unreliable_time(personal,boundary):
    service,_,calls,job=queued(personal,reliable=boundary!='unreliable')
    options={}
    if boundary=='source_time': options['not_before']='2026-10-23T00:00:00+08:00'
    if boundary=='watermark': options['chat_watermarks']={'test':999999}
    if boundary=='chat': options['chat_watermarks']={'other':0}
    if boundary=='revoked': options['enabled']=False
    write_grant(personal,grant(**options))
    assert asyncio.run(service.analysis.run_one(job)) is False
    assert calls==[]
    with personal.connection() as connection:
        assert connection.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==0


def test_source_change_between_preparation_and_send_blocks_without_reservation(personal,monkeypatch):
    service,_,calls,job=queued(personal)
    write_grant(personal,grant())
    resolved=service.analysis.settings.resolved
    def change_source():
        with personal.connection(write=True) as connection:
            connection.execute("UPDATE wechat_message SET raw_text='改变后的文字，不在已准备请求中'")
        return resolved()
    monkeypatch.setattr(service.analysis.settings,'resolved',change_source)
    assert asyncio.run(service.analysis.run_one(job)) is False
    assert calls==[]
    with personal.connection() as connection:
        assert connection.execute('SELECT count(*) FROM wechat_llm_attempt').fetchone()[0]==0


def test_rebinding_a_logical_chat_cannot_reuse_an_old_content_grant(personal):
    service,_,calls,job=queued(personal)
    write_grant(personal,grant())
    with personal.connection(write=True) as connection:
        connection.execute('UPDATE wechat_chat SET binding_json=?',(json.dumps({'account_scope':'another','internal_chat_id':'another@chatroom'}),))
    assert asyncio.run(service.analysis.run_one(job)) is False
    assert calls==[]
