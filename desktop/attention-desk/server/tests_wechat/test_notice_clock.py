import pytest
import json

from server.wechat_collect.analysis import resolve_clock, validate_output, verified_calendar
from server.tests_wechat.test_pipeline import output_for


@pytest.mark.parametrize('phrase,expected', [
    ('明天下午2.35到体育馆集合', '14:35'),
    ('明天下午2:35到体育馆集合', '14:35'),
    ('上午9点30分交作业', '09:30'),
    ('晚上8点开会', '20:00'),
    ('凌晨12点到场', '00:00'),
    ('中午12:30集合', '12:30'),
    ('14:35集合', '14:35'),
    ('11.08体测', None),
    ('下午2.99集合', None),
    ('中午1点集合', None),
])
def test_explicit_period_clock(phrase, expected):
    assert resolve_clock(phrase) == expected


@pytest.mark.parametrize('clock,retained', [('14:35', True), ('02:35', False)])
def test_source_clock_validation_preserves_afternoon_and_rejects_morning(clock, retained):
    text = '明天下午2.35到体育馆门口集合'
    batch = {'id':'batch', 'primary_ids':['m1']}
    block = {'block_id':'b1', 'text':text, 'source_type':'text'}
    message = {'message_id':'m1', 'block_id':'b1', 'raw_text':text,
               'source_time':'2026-11-07T10:00:00+08:00', 'time_reliability':'reliable', 'message_type':'text'}
    output = output_for({'batch_id':'batch', 'blocks':[block], 'messages':[message]})
    output['items'][0]['start_time'] = dict(value='2026-11-08T'+clock, end_value=None,
        precision='minute', range_precision=None, timezone='Asia/Shanghai',
        original_text=text, anchor_message_id='m1', uncertain=False, evidence_ids=['q1'])
    parsed, warnings = validate_output(json.dumps(output, ensure_ascii=False), batch,
        {'blocks':[block], 'wechat':{'messages':[message], 'warnings':[]}}, {})
    value = parsed['items'][0]['start_time']
    assert value['value'] == ('2026-11-08T14:35' if retained else None)
    assert ('uncertain_time' in warnings) is not retained


def test_declared_unknown_time_cannot_smuggle_a_date():
    text='请明天下午参加课程考试'
    block={'block_id':'b1','text':text,'source_type':'text'}
    message={'message_id':'m1','block_id':'b1','raw_text':text,'message_type':'text',
             'source_time':'2026-11-08T10:00:00+08:00','time_reliability':'reliable'}
    output=output_for({'batch_id':'batch','blocks':[block],'messages':[message]})
    output['items'][0]['start_time']=dict(value='2026-11-09',end_value=None,precision='unknown',
        range_precision=None,timezone='Asia/Shanghai',original_text='明天下午',
        anchor_message_id='m1',uncertain=True,evidence_ids=['q1'])
    parsed,warnings=validate_output(json.dumps(output,ensure_ascii=False),
        {'id':'batch','primary_ids':['m1']},
        {'blocks':[block],'wechat':{'messages':[message],'warnings':[]}},{})
    assert parsed['items'][0]['start_time']['value'] is None
    assert parsed['items'][0]['start_time']['precision']=='unknown'
    assert 'unknown_time_value_cleared' in warnings


def test_calendar_uses_source_week_and_shanghai_not_system_today():
    calendar=verified_calendar({'source_time':'2026-11-08T10:00:00+08:00','time_reliability':'reliable'})
    assert calendar['下周二']=='2026-11-10'
    assert calendar['本周日']=='2026-11-08'
    assert calendar['明天']=='2026-11-09'
    shifted=verified_calendar({'source_time':'2026-11-08T23:00:00-04:00','time_reliability':'reliable'})
    assert shifted['今天']=='2026-11-09'
    assert shifted['下周二']=='2026-11-17'
    assert verified_calendar({'source_time':'2026-11-08T10:00:00+08:00','time_reliability':'unverified'})=={}
    assert verified_calendar({'source_time':'broken','time_reliability':'reliable'})=={}
