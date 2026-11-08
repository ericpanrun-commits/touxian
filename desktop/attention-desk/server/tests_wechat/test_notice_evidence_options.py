import json
import runpy
from pathlib import Path

import pytest

from server.tests_wechat.test_pipeline import output_for
from server.wechat_collect.analysis import evidence_options, validate_output
from server.extraction_errors import ExtractionFailure


def test_repeated_lines_and_emoji_keep_distinct_exact_positions():
    text='  校园通知 🏫\r\n\n  综合楼\n  综合楼  \n'
    options=evidence_options(text)
    assert [r['quote'] for r in options]==['校园通知 🏫','综合楼','综合楼']
    assert options[1]['start']!=options[2]['start']
    assert all(text[r['start']:r['end']]==r['quote'] for r in options)


def test_notice_identity_does_not_force_a_personal_action():
    text='请班长归档学习材料。'
    block={'block_id':'b1','text':text,'source_type':'text'}
    message={'message_id':'m1','block_id':'b1','raw_text':text,'message_type':'text'}
    output=output_for({'batch_id':'batch','blocks':[block],'messages':[message]})
    output['items'][0]['is_school_notice']=True
    parsed,_=validate_output(json.dumps(output,ensure_ascii=False),
        {'id':'batch','primary_ids':['m1']},
        {'blocks':[block],'wechat':{'messages':[message],'warnings':[]}},
        {'role_assertions':{'班长':{'confirmed':True,'value':'false'}}})
    item=parsed['items'][0]
    assert item['is_school_notice'] is True
    assert item['action_required'] is False and item['relevant'] is False


@pytest.mark.parametrize('change', ['text','source_time','id','original_sha256'])
def test_reviewed_content_or_metadata_changes_fail_before_send(tmp_path, change):
    module=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/deepseek-reviewed-evaluation.py'))
    samples=[{'id':'synthetic','text':'虚构通知','source_time':'2026-11-08','original_sha256':'fake'}]
    path=tmp_path/'approved.json'
    path.write_text(json.dumps(samples),encoding='utf8')
    module['verify_reviewed_inputs'](samples,path)
    samples[0][change]='changed'
    with pytest.raises(ValueError,match='approved_reviewed_inputs_changed'):
        module['verify_reviewed_inputs'](samples,path)


def test_new_response_requires_notice_flag_but_old_response_remains_readable():
    text='请全体同学提交报告'
    block={'block_id':'b1','text':text,'source_type':'text'}
    message={'message_id':'m1','block_id':'b1','raw_text':text,'message_type':'text'}
    output=output_for({'batch_id':'batch','blocks':[block],'messages':[message]})
    del output['items'][0]['is_school_notice']
    args=(json.dumps(output,ensure_ascii=False),{'id':'batch','primary_ids':['m1']},
          {'blocks':[block],'wechat':{'messages':[message],'warnings':[]}}, {})
    with pytest.raises(ExtractionFailure) as exc:validate_output(*args,require_notice=True)
    assert exc.value.code=='invalid_schema'
    assert validate_output(*args,legacy=True)[0]['items'][0]['is_school_notice'] is None


@pytest.mark.parametrize('confirmed,expected', [(True,False),(False,True)])
def test_explicit_other_year_cannot_override_confirmed_profile(confirmed,expected):
    text='各位同学，请2025级同学提交课程实验报告。'
    block={'block_id':'b1','text':text,'source_type':'text'}
    message={'message_id':'m1','block_id':'b1','raw_text':text,'message_type':'text'}
    output=output_for({'batch_id':'batch','blocks':[block],'messages':[message]})
    parsed,_=validate_output(json.dumps(output,ensure_ascii=False),{'id':'batch','primary_ids':['m1']},
        {'blocks':[block],'wechat':{'messages':[message],'warnings':[]}},
        {'entry_year':2026,'confirmed_fields':['entry_year'] if confirmed else []})
    assert parsed['items'][0]['relevant'] is expected
