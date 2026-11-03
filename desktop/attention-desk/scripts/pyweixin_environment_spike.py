"""Temporary, restored current-user accessibility experiment. No HKLM writes."""
import argparse
from contextlib import contextmanager
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy


@contextmanager
def temporary_accessibility(backend,policy,record):
    policy.require(operation='uia',maximum_seconds=120)
    saved=backend.read();record['before']=saved
    screen_changed=narrator_changed=False
    try:
        if not saved['screen_reader']:
            backend.set_screen_reader(True);screen_changed=True
        if saved.get('narrator')==0:
            backend.set_narrator(1);narrator_changed=True
        record['active']=backend.read()
        yield
    finally:
        try:
            if narrator_changed:backend.set_narrator(saved['narrator'])
        finally:
            if screen_changed:backend.set_screen_reader(saved['screen_reader'])
        record['restored']=backend.read()
        record['restoration_matches']=record['restored']==saved


class CurrentUserAccessibility:
    narrator_path=r'SOFTWARE\Microsoft\Narrator\NoRoam'

    def __init__(self):
        import winreg
        self.reg=winreg;self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.user.SystemParametersInfoW.argtypes=[ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint]
        self.user.SystemParametersInfoW.restype=ctypes.c_int

    def read(self):
        reader=ctypes.c_int()
        if not self.user.SystemParametersInfoW(0x46,0,ctypes.byref(reader),0):raise ctypes.WinError(ctypes.get_last_error())
        value=None
        try:
            with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER,self.narrator_path,0,self.reg.KEY_READ) as key:
                raw,kind=self.reg.QueryValueEx(key,'RunningState')
                if kind==self.reg.REG_DWORD:value=raw
        except FileNotFoundError:pass
        return {'screen_reader':bool(reader.value),'narrator':value}

    def set_screen_reader(self,value):
        # SPIF_SENDCHANGE only. Do not persist a user profile setting.
        if not self.user.SystemParametersInfoW(0x47,int(value),None,2):raise ctypes.WinError(ctypes.get_last_error())

    def set_narrator(self,value):
        # Only the pre-existing current-user value; never create/delete a key.
        with self.reg.OpenKey(self.reg.HKEY_CURRENT_USER,self.narrator_path,0,self.reg.KEY_SET_VALUE) as key:
            self.reg.SetValueEx(key,'RunningState',0,self.reg.REG_DWORD,value)


def qt_runtime_matrix(policy,args,root,record):
    """Hold the same Qt wake reference as wechat-notify; never read cell Names."""
    from wechat_phase0_probe import Phase0Window
    sys.coinit_flags=0
    import uiautomation as auto
    from uiautomation import uiautomation as core
    window=Phase0Window(args.window_handle,args.chat_name,policy=policy)
    held=[];record['runtime_matrix']=[]
    try:
        with auto.UIAutomationInitializerInThread():
            for state in ['foreground','background','minimized']:
                policy.require(operation='uia',maximum_seconds=30)
                activation_error=None
                if state=='foreground':
                    try:
                        window.activate()
                    except Exception as exc:
                        activation_error=getattr(exc,'code',type(exc).__name__)
                        # SetFocus targets only the verified top-level Window,
                        # never an Edit, message bubble, button or input region.
                        root_control=auto.ControlFromHandle(window.hwnd)
                        window.user.ShowWindow(window.hwnd,9)
                        root_control.SetFocus()
                        time.sleep(.25)
                        window.activated=window.user.GetForegroundWindow()==window.hwnd
                elif state=='background':
                    if window.before.foreground==window.hwnd:
                        record['runtime_matrix'].append({'state':state,'status':'NOT_TESTED','reason':'no_prior_background_window'})
                        continue
                    window.user.SetForegroundWindow(window.before.foreground)
                else:window.user.ShowWindow(window.hwnd,6)
                time.sleep(.25)
                control=auto.ControlFromHandle(window.hwnd);held.append(control)
                client=core._AutomationClient.instance().IUIAutomation
                counts={}
                for kind,property_id,value in [('session_list',auto.PropertyId.AutomationIdProperty,'session_list'),
                                                ('chat_session_cell',auto.PropertyId.ClassNameProperty,'mmui::ChatSessionCell'),
                                                ('session_cell',auto.PropertyId.ClassNameProperty,'mmui::SessionCell')]:
                    condition=client.CreatePropertyCondition(property_id,value)
                    counts[kind]=control.Element.FindAll(7,condition).Length
                row={'state':state,'class_name':control.ClassName,'framework_id':control.FrameworkId,
                     'is_offscreen':control.IsOffscreen,'qt_wake_reference_held':True,'expected_controls':counts,
                     'actual_foreground':window.user.GetForegroundWindow()==window.hwnd,
                     'actual_minimized':bool(window.user.IsIconic(window.hwnd))}
                if activation_error:row['initial_activation_error']=activation_error
                row['requested_state_verified']=(row['actual_minimized'] if state=='minimized' else
                    row['actual_foreground'] if state=='foreground' else
                    not row['actual_foreground'] and not row['actual_minimized'])
                python=PROJECT/'artifacts/pyweixin-spike/venv/Scripts/python.exe'
                destination=root/('qt-'+state)
                result=subprocess.run([str(python),'-X','utf8',str(PROJECT/'scripts/pyweixin_readonly_spike.py'),
                                       '--method','pull_messages','--chat-name',args.chat_name,
                                       '--window-handle',str(window.hwnd),'--output',str(destination)],
                                      stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      timeout=22,creationflags=subprocess.CREATE_NO_WINDOW)
                path=destination/'result.json'
                if not result.returncode and path.exists():
                    tested=json.loads(path.read_text(encoding='utf-8'));row['pull_status']=tested['status'];row['error_type']=tested.get('error_type')
                else:row['pull_status']='worker_failed'
                record['runtime_matrix'].append(row)
                print(json.dumps(row,ensure_ascii=False),flush=True)
    finally:
        # This experiment explicitly changes window state; restore its prior state.
        if not window.original_minimized:window.user.ShowWindow(window.hwnd,9)
        window.restore()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chat-name',required=True);parser.add_argument('--window-handle',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--qt-matrix',action='store_true')
    args=parser.parse_args();root=args.output.resolve()
    if not root.is_relative_to((PROJECT/'artifacts/pyweixin-spike').resolve()):raise ValueError('Invalid output directory')
    policy=SchedulerPolicy(WeChatConfig(enabled=True));policy.require(operation='uia',maximum_seconds=150)
    root.mkdir(parents=True,exist_ok=True)
    backend=CurrentUserAccessibility()
    saved=backend.read()
    (root/'restore-snapshot.json').write_text(json.dumps(saved,indent=2),encoding='utf-8')
    report={'hklm_writes':0,'methods':[]}
    try:
        with temporary_accessibility(backend,policy,report):
            time.sleep(.5)
            if args.qt_matrix:
                qt_runtime_matrix(policy,args,root,report)
                return
            python=PROJECT/'artifacts/pyweixin-spike/venv/Scripts/python.exe'
            for method in ['pull_messages','check_new_messages','listen_on_newMessages','listen_on_chat','save_media']:
                policy.require(operation='uia',maximum_seconds=30)
                destination=root/method
                command=[str(python),'-X','utf8',str(PROJECT/'scripts/pyweixin_readonly_spike.py'),
                         '--method',method,'--chat-name',args.chat_name,'--window-handle',str(args.window_handle),'--output',str(destination)]
                try:
                    result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                          timeout=22,creationflags=subprocess.CREATE_NO_WINDOW)
                    path=destination/'result.json'
                    if result.returncode or not path.exists():entry={'method':method,'status':'worker_failed','exit_code':result.returncode}
                    else:entry=json.loads(path.read_text(encoding='utf-8'))
                except subprocess.TimeoutExpired:entry={'method':method,'status':'bounded_timeout'}
                report['methods'].append(entry)
                print(json.dumps({'method':method,'status':entry.get('status'),'connected':entry.get('connected'),
                                  'error_type':entry.get('error_type'),'guards':entry.get('guards')},ensure_ascii=False),flush=True)
    except Exception as exc:
        report['diagnostic_error_type']=type(exc).__name__
        report['diagnostic_error_code']=getattr(exc,'code',None)
        raise
    finally:
        (root/'environment-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'before':report.get('before'),'active':report.get('active'),'restored':report.get('restored'),
                      'restoration_matches':report.get('restoration_matches'),'hklm_writes':0}),flush=True)


if __name__=='__main__':main()
