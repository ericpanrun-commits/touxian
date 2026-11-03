from datetime import datetime
import importlib.util
from pathlib import Path

import pytest

from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy

spec=importlib.util.spec_from_file_location('pyweixin_readonly_spike',Path(__file__).resolve().parents[2]/'scripts/pyweixin_readonly_spike.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


def test_read_only_guard_cannot_execute_keyboard_side_effects():
    events=[]
    with pytest.raises(probe.ReadOnlyBlocked):probe.blocker('typing',events)('private text')
    assert events==[{'blocked_action':'typing'}]
    assert 'private text' not in str(events)


def test_result_summary_does_not_expose_message_content():
    result=probe.summarize_result([{'消息发送人':'测试姓名','消息内容':'不能进入诊断输出的原文','消息类型':'文本'}],'合成群',lambda name:name=='合成群')
    assert result['sender_present']==result['content_present']==1
    assert '不能进入诊断输出' not in str(result) and '测试姓名' not in str(result)


def test_pyweixin_imports_are_behind_shared_quiet_gate(monkeypatch):
    policy=SchedulerPolicy(WeChatConfig(enabled=True),lambda:datetime.fromisoformat('2026-10-22T03:00:00+08:00'))
    monkeypatch.setattr(probe,'install_input_guard',lambda *args:pytest.fail('No upstream imports at night'))
    output=probe.PROJECT/'artifacts/pyweixin-spike/offline-quiet-test'
    result=probe.run('pull_messages','合成群',123,output,policy=policy)
    assert result['status']=='NOT_TESTED' and result['error_code']=='quiet'
    assert not result['actual_api_invoked'] and result['messages_sent']==0
