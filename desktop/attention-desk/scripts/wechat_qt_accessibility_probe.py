"""Fresh-process Qt accessibility inspection. No Name/Value/Text or input calls."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))

from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy


EXPECTED_CLASSES=('mmui::MainWindow','mmui::ChatSessionCell','mmui::SessionCell',
                  'mmui::ChatListView','mmui::ChatMasterView','mmui::ChatInputField')
EXPECTED_IDS=('session_list','chat_input_field')


def desktop_binding(attach):
    """Run before importing any UI/COM package; never switches the visible desktop."""
    user=ctypes.WinDLL('user32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    user.GetThreadDesktop.argtypes=[w.DWORD];user.GetThreadDesktop.restype=w.HANDLE
    user.GetProcessWindowStation.restype=w.HANDLE
    user.GetUserObjectInformationW.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
    user.OpenDesktopW.argtypes=[w.LPCWSTR,w.DWORD,w.BOOL,w.DWORD];user.OpenDesktopW.restype=w.HANDLE
    user.SetThreadDesktop.argtypes=[w.HANDLE];user.SetThreadDesktop.restype=w.BOOL
    original=user.GetThreadDesktop(kernel.GetCurrentThreadId())
    def name(handle):
        buffer=ctypes.create_unicode_buffer(256);needed=w.DWORD()
        if not user.GetUserObjectInformationW(handle,2,buffer,ctypes.sizeof(buffer),ctypes.byref(needed)):
            return None
        return buffer.value
    record={'original_desktop':name(original),'window_station':name(user.GetProcessWindowStation()),
            'attach_requested':attach}
    held=None
    if attach:
        # Same current-user Default desktop scope as the reference project.
        held=user.OpenDesktopW('Default',0,False,0x1ff)
        record['open_succeeded']=bool(held)
        record['attach_succeeded']=bool(held and user.SetThreadDesktop(held))
        if not record['attach_succeeded']:record['error']=ctypes.get_last_error()
    record['current_desktop']=name(user.GetThreadDesktop(kernel.GetCurrentThreadId()))
    # The opened desktop stays alive for this diagnostic process only.
    return record,held


def wechat_windows(process_ids):
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    user.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    user.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    user.IsWindowVisible.argtypes=[w.HWND]
    rows=[];callback_type=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    @callback_type
    def visit(hwnd,_):
        pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value not in process_ids:return True
        rect=w.RECT();user.GetWindowRect(hwnd,ctypes.byref(rect))
        cls=ctypes.create_unicode_buffer(256);user.GetClassNameW(hwnd,cls,len(cls))
        if not ((cls.value.startswith('Qt') and cls.value.endswith('QWindowIcon')) or
                cls.value=='WeChatMainWndForPC'):return True
        rows.append({'hwnd':hwnd,'pid':pid.value,'native_class':cls.value,
                     'native_visible':bool(user.IsWindowVisible(hwnd)),
                     'width':rect.right-rect.left,'height':rect.bottom-rect.top})
        return True
    if not user.EnumWindows(visit,0):raise ctypes.WinError(ctypes.get_last_error())
    return rows


def inspect_root(root,client,auto,policy):
    def found(property_id,value,scope=7):
        policy.require(operation='uia',maximum_seconds=8)
        return root.Element.FindAll(scope,client.CreatePropertyCondition(property_id,value)).Length
    direct=root.Element.FindAll(2,client.CreateTrueCondition())
    descendants=root.Element.FindAll(4,client.CreateTrueCondition())
    result={'class_name':root.ClassName,'framework_id':root.FrameworkId,
            'direct_child_count':direct.Length,'descendant_count':descendants.Length,
            'class_counts':{value:found(auto.PropertyId.ClassNameProperty,value) for value in EXPECTED_CLASSES},
            'automation_id_counts':{value:found(auto.PropertyId.AutomationIdProperty,value) for value in EXPECTED_IDS},
            'mmui_count':int(root.ClassName.startswith('mmui::')),'structure':[],
            'enumeration_complete':descendants.Length<=2000}
    for index in range(min(descendants.Length,2000)):
        policy.require(operation='uia',maximum_seconds=2)
        control=auto.Control.CreateControlFromElement(descendants.GetElement(index))
        cls=control.ClassName
        result['mmui_count']+=int(cls.startswith('mmui::'))
        if index<60:
            result['structure'].append({'class_name':cls,'framework_id':control.FrameworkId,
                                        'control_type':control.ControlTypeName})
    return result


def run(output,*,attach=False,show=False,policy=None):
    policy=policy or SchedulerPolicy(WeChatConfig(enabled=True))
    report={'at':policy.clock().isoformat(),'messages_sent':0,'hklm_writes':0,'windows':[],
            'screenshots':0,'ocr_calls':0,'deepseek_calls':0,'vision_calls':0}
    try:
        policy.require(operation='uia',maximum_seconds=40)
        report['desktop_binding'],held=desktop_binding(attach)
        from server.wechat_collect.windows import desktop_state
        if not desktop_state(policy).unlocked:raise RuntimeError('Interactive desktop is locked')
        from server.wechat_collect.spike import _process_metadata
        report['processes']=_process_metadata()
        process_ids={row['pid'] for row in report['processes']}
        handles=wechat_windows(process_ids)
        # Each invocation starts a new COM/UIA client after desktop attachment.
        sys.coinit_flags=0
        import uiautomation as auto
        from uiautomation import uiautomation as core
        with auto.UIAutomationInitializerInThread():
            client=core._AutomationClient.instance().IUIAutomation
            held_roots=[]
            for window in handles:
                policy.require(operation='uia',maximum_seconds=15)
                root=auto.ControlFromHandle(window['hwnd']);held_roots.append(root)
                if show and window['width']>200 and window['height']>200:
                    # Only the verified process's top-level window, never Edit.
                    user=ctypes.WinDLL('user32',use_last_error=True)
                    user.ShowWindow.argtypes=[w.HWND,ctypes.c_int]
                    user.IsWindowVisible.argtypes=[w.HWND]
                    if root.ClassName=='mmui::LoginWindow':
                        # Login is manual. Repeated probes must preserve the
                        # user's current login control and keyboard focus.
                        if not user.IsWindowVisible(window['hwnd']):
                            user.ShowWindow(window['hwnd'],4)  # Show without activation.
                        window['login_focus_preserved']=True
                    else:
                        user.ShowWindow(window['hwnd'],9)
                        window['focus_request_succeeded']=root.SetFocus()
                        time.sleep(.7)
                    window['visible_after_show']=bool(user.IsWindowVisible(window['hwnd']))
                    root=auto.ControlFromHandle(window['hwnd']);held_roots.append(root)
                result=inspect_root(root,client,auto,policy)
                report['windows'].append(window|result)
        report['status']='complete'
    except Exception as exc:
        report['status']='not_completed';report['error_type']=type(exc).__name__
        report['error_code']=getattr(exc,'code',None)
    output=Path(output).resolve()
    if not output.is_relative_to((PROJECT/'artifacts/pyweixin-spike').resolve()):
        raise ValueError('Output must stay in the spike artifacts directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attach-default-desktop',action='store_true')
    parser.add_argument('--show-main-window',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=run(args.output,attach=args.attach_default_desktop,show=args.show_main_window)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
