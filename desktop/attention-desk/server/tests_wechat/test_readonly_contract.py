import ast
from pathlib import Path

import pytest

from server.wechat_collect.adapter import DENIED_CONTROL_ROLES,UnverifiedUIAAdapter
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy
from datetime import datetime
from server.personal_store import PersonalError


def test_adapter_has_no_sending_typing_clipboard_or_activation_surface():
    root=Path(__file__).parents[1]/'wechat_collect'
    prohibited={'SendKeys','SendInput','SetValue','SetFocus','SetForegroundWindow','SetCursorPos','Click','Invoke',
                'send_message','type_into_chat','click_send','paste_into_chat','reply_message','OpenClipboard','SetClipboardData','SwitchDesktop'}
    for path in root.glob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                assert node.name not in prohibited,path
            if isinstance(node,ast.Call):
                name=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else ''
                assert name not in prohibited,(path,name)
    assert 'EditControl' in DENIED_CONTROL_ROLES and 'ButtonControl' in DENIED_CONTROL_ROLES


def test_uncertified_adapter_remains_closed_even_with_forged_binding():
    policy=SchedulerPolicy(WeChatConfig(enabled=True),lambda:datetime.fromisoformat('2026-10-22T10:00:00+08:00'))
    with pytest.raises(PersonalError) as exc:
        UnverifiedUIAAdapter().read(None,{'capability':'GO'},None,policy=policy)
    assert exc.value.code=='adapter_not_certified'
