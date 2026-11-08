import pytest
import json

from server.wechat_collect.analysis import resolve_clock, validate_output
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
