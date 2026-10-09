"""WinRT adapter safety, blocked-delivery fallback and literal XML serialization."""
import os
import sys
from types import SimpleNamespace
from uuid import uuid4
from xml.etree import ElementTree

import pytest

from server.reminders.windows import APP_ID,WindowsNotifier


@pytest.mark.skipif(os.name!='nt',reason='Windows WinRT serialization')
def test_installed_toast_library_serializes_untrusted_text_as_literal_nodes():
    from windows_toasts import Toast,InteractableWindowsToaster
    from windows_toasts.toasters import _build_adaptable_data
    text = ['标题 </text><command>calc.exe</command> & "引用"','$(secret); 中文','第二行\n第三行']
    adapter = object.__new__(InteractableWindowsToaster)
    adapter.defaultAUMID = False
    toast = Toast(text_fields=text)
    document = adapter._setup_toast(toast,False)
    parsed = ElementTree.fromstring(document.xmlDocument.get_xml())
    assert [node.text for node in parsed.findall('.//text')]==text
    assert parsed.find('.//command') is None
    dynamic = _build_adaptable_data(toast)
    assert [dynamic.values[f'text{i+1}'] for i in range(3)]==text


@pytest.fixture
def adapter(monkeypatch):
    if os.name!='nt':
        pytest.skip('Windows adapter contract')
    from winrt.windows.ui.notifications import NotificationSetting
    state = SimpleNamespace(setting=NotificationSetting.ENABLED,shown=[],raise_error=False)
    class Toast:
        def __init__(self,**kwargs): self.__dict__.update(kwargs)
    class Toaster:
        def __init__(self,*args,**kwargs):
            assert kwargs['notifierAUMID']==APP_ID
            self.toastNotifier = SimpleNamespace(setting=state.setting)
        def show_toast(self,toast):
            if state.raise_error: raise RuntimeError('private source body must not leak')
            state.shown.append(toast)
    monkeypatch.setitem(sys.modules,'windows_toasts',SimpleNamespace(Toast=Toast,InteractableWindowsToaster=Toaster))
    monkeypatch.setattr(WindowsNotifier,'_register',staticmethod(lambda:None))
    return WindowsNotifier(),state


def test_adapter_blocks_os_disabled_without_show_or_data_leak(adapter):
    from winrt.windows.ui.notifications import NotificationSetting
    notifier,state = adapter
    state.setting = NotificationSetting.DISABLED_FOR_USER
    assert notifier.send(['private title'],delivery_id='fixture')=='os_blocked'
    assert notifier.last_setting=='DISABLED_FOR_USER' and not state.shown


def test_adapter_uses_bounded_literal_data_stable_tag_and_allowlisted_event_link(adapter,monkeypatch):
    notifier,state = adapter
    monkeypatch.setenv('ATTENTION_WEB_PORT','5173')
    event_id = str(uuid4())
    title = '</text><command>calc.exe</command>\x00'+'a'*300
    for _ in range(2):
        assert notifier.send([title],delivery_id='same-id',event_id=event_id)=='os_accepted'
    first,second = state.shown
    assert first.tag==second.tag and len(first.tag)==16
    assert first.launch_action==f'http://127.0.0.1:5173/?personal_event={event_id}'
    assert first.text_fields[0].startswith('</text><command>')
    assert '\x00' not in first.text_fields[0] and len(first.text_fields[0])==240
    assert notifier.send(['title'],delivery_id='bad',event_id='javascript:alert(1)')=='delivery_failed'
    assert len(state.shown)==2


def test_adapter_errors_are_fixed_results_and_test_is_independent(adapter):
    notifier,state = adapter
    state.raise_error = True
    assert notifier.test()=='delivery_failed'
    state.raise_error = False
    assert notifier.test()=='os_accepted'
    assert state.shown[0].text_fields[0:2]==['Attention Desk','测试提醒']
