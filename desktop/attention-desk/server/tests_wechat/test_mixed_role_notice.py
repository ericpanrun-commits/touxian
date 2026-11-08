import json

import pytest

from server.tests_wechat.test_pipeline import output_for
from server.wechat_collect.analysis import validate_output
from server.wechat_collect.quality import assess


@pytest.mark.parametrize('text,action,allowed', [
    ('各位同学，请提交课程实验报告。请班长归档。', '提交课程实验报告', True),
    ('各位同学，请班长提交课程实验报告。', '提交课程实验报告', False),
    ('各位同学，请提交课程实验报告。仅限班长参加。', '提交课程实验报告', False),
    ('各位班长：各位同学，请提交课程实验报告。', '提交课程实验报告', False),
    ('请班长提交课程实验报告。其他同学无需操作。', '提交课程实验报告', False),
    ('各位同学，请提交课程实验报告。请班长归档。', '归档', False),
])
def test_role_restriction_attaches_to_its_action(text, action, allowed):
    block={'block_id':'b1','text':text,'source_type':'text'}
    message={'message_id':'m1','block_id':'b1','raw_text':text,'message_type':'text'}
    output=output_for({'batch_id':'batch','blocks':[block],'messages':[message]})
    item=output['items'][0]
    item['action']=action
    item['actions'][0]['title']=action
    parsed,_=validate_output(json.dumps(output,ensure_ascii=False),
        {'id':'batch','primary_ids':['m1']},
        {'blocks':[block],'wechat':{'messages':[message],'warnings':[]}},
        {'role_assertions':{'班长':{'value':'false','confirmed':True}}})
    assert parsed['items'][0]['relevant'] is allowed


def test_unseen_attachment_instructions_do_not_become_ready_actions():
    item={'evidence_message_ids':['m1'],'relevant':True,'category':'task',
          'attention_level':'action','information_complete':True,'action_required':True}
    decision=assess(item,[{'message_id':'m1','message_type':'text',
        'raw_text':'学校要求全体同学明天完成问卷，填写详见图片。'}])
    assert decision.bucket=='unresolved' and decision.await_more_context
    assert decision.action_required is False
