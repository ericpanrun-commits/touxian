"""Native WinRT toast adapter. Event text never enters a command or XML markup."""
from __future__ import annotations

import hashlib
import os
import threading
from collections import deque
from uuid import UUID

APP_ID = 'AttentionDesk.Desktop'


def content(item,privacy='title'):
    if privacy=='hidden':
        return ['Attention Desk','你有一条事项提醒','请在工作台提醒中心核对。']
    title = item['event_title'][:120]
    value = item['value'].replace('T',' ')
    if item.get('end_value'):
        value += ' – '+item['end_value'].replace('T',' ')
    timing = f"{item['label']} · {value}"
    if item['state']=='overdue':
        timing += ' · 已逾期'
    elif item.get('days_remaining') == 0:
        timing += ' · 今天'
    elif item.get('days_remaining') == 1:
        timing += ' · 明天'
    detail = '请在工作台核对并处理。'
    if privacy=='full':
        count = item['remaining_task_count']
        names = '；'.join(task['title'][:60] for task in item['tasks'][:2])
        detail = f"还有 {count} 项任务未完成"+(f'：{names}' if names else '')
    return [title,timing[:180],detail[:200]]


class WindowsNotifier:
    def __init__(self):
        self._recent = deque(maxlen=32)
        self.last_setting = None

    @staticmethod
    def _register():
        # Per-user display identity, following Windows-Toasts' documented
        # register_hkey_aumid helper. No startup, privileges or executable data.
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,rf'SOFTWARE\Classes\AppUserModelId\{APP_ID}') as key:
            winreg.SetValueEx(key,'DisplayName',0,winreg.REG_SZ,'Attention Desk')

    def send(self,text,*,delivery_id,event_id=None,on_failed=None):
        self.last_setting = None
        if os.name != 'nt':
            return 'unsupported'
        try:
            from windows_toasts import Toast, InteractableWindowsToaster
            from winrt.windows.ui.notifications import NotificationSetting
            self._register()
            toaster = InteractableWindowsToaster('Attention Desk',notifierAUMID=APP_ID)
            try:
                setting = toaster.toastNotifier.setting
            except OSError as error:
                # An unpackaged app's Setting lookup can return ERROR_NOT_FOUND
                # even when native Show works. Let Show enforce Windows policy
                # for this specific lookup error; all other errors still fail.
                if getattr(error,'winerror',None) not in (-2147023728,0x80070490):
                    raise
            else:
                self.last_setting = setting.name
                if setting != NotificationSetting.ENABLED:
                    return 'os_blocked'
            failed = threading.Event()

            def failure(_):
                failed.set()
                if on_failed:
                    on_failed('delivery_failed')

            toast = Toast(text_fields=[self._text(value) for value in text],group='reminders',on_failed=failure)
            toast.tag = hashlib.sha256(delivery_id.encode()).hexdigest()[:16]
            if event_id:
                event_uuid = str(UUID(event_id))
                port = int(os.environ.get('ATTENTION_WEB_PORT','5173'))
                if not 1 <= port <= 65535:
                    raise ValueError('invalid desktop port')
                toast.launch_action = f'http://127.0.0.1:{port}/?personal_event={event_uuid}'
            # Library uses XmlDocument.create_text_node/NotificationData values.
            # A title such as </text><command> stays literal text.
            toaster.show_toast(toast)
            self._recent.append((toaster,toast))
            return 'delivery_failed' if failed.wait(0.3) else 'os_accepted'
        except (ImportError,ModuleNotFoundError):
            return 'backend_unavailable'
        except Exception:
            # Exception strings may contain notification data; never expose them.
            return 'delivery_failed'

    @staticmethod
    def _text(value):
        return ''.join(char for char in str(value) if ord(char)>=32 or char in '\n\t')[:240]

    def test(self):
        from uuid import uuid4
        return self.send(['Attention Desk','测试提醒','请回到工作台核对通知结果。'],delivery_id=str(uuid4()))
