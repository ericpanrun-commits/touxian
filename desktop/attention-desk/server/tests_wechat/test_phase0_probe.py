import ast
from datetime import datetime
import importlib.util
from pathlib import Path

import pytest
spec=importlib.util.spec_from_file_location('wechat_phase0_probe',Path(__file__).resolve().parents[2]/'scripts/wechat_phase0_probe.py')
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
Phase0Window,find_target_header=probe.Phase0Window,probe.find_target_header
sanitized_layout,redacted_image=probe.sanitized_layout,probe.redacted_image
from server.personal_store import PersonalError
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy


def line(text,x,y):
    return {'text':text,'words':[{'x':x,'y':y,'width':200,'height':24}]}


def test_only_header_location_can_verify_explicit_target():
    lines=[line('合成26班（公告群）',100,100),line('合成26班（公告群）',500,500),line('合成26班（公告群）',500,50)]
    assert find_target_header(lines,'合成26班（公告群）',(1200,1000))==[[500,50,700,74]]


def test_diagnostics_redact_other_chats_and_images():
    secret='不应出现在报告中的私人消息'
    layout=sanitized_layout([line(secret,100,200),line('合成26班（公告群）',500,50)],'合成26班（公告群）',(1200,1000))
    assert secret not in str(layout)
    assert layout['target_header_match']
    assert layout['lines'][0]['text'] is None
    image=redacted_image((1200,1000),layout)
    assert image.getpixel((110,210))==(245,245,245)


def test_active_phase0_has_no_keyboard_clipboard_or_sending_interface():
    path=Path(__file__).resolve().parents[2]/'scripts/wechat_phase0_probe.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    forbidden={'SendInput','SendKeys','SetValue','OpenClipboard','SetClipboardData','keybd_event',
               'send_message','reply_message','click_send','paste_into_chat'}
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):
            name=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else ''
            assert name not in forbidden,name
            if name=='SendMessageTimeoutW':
                assert isinstance(node.args[1],ast.Constant) and node.args[1].value==0x020A
    product=Path(__file__).resolve().parents[1]/'wechat_collect'
    assert not any('wechat_phase0_probe' in file.read_text(encoding='utf-8') for file in product.glob('*.py'))


def test_exploratory_authority_does_not_bypass_quiet_hours():
    policy=SchedulerPolicy(WeChatConfig(enabled=True),lambda:datetime.fromisoformat('2026-10-22T03:00:00+08:00'))
    with pytest.raises(PersonalError) as error:
        Phase0Window(123,'合成测试群',policy=policy)
    assert error.value.code=='quiet'
