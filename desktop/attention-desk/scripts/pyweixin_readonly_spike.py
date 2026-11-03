"""Audited, isolated pyweixin API feasibility worker. Never a production Collector.

Run with the separate spike venv. All keyboard/text/button input is blocked in
the initial compatibility test. A blocked action is reported, never worked around.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import sys
import time
import traceback

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))

READ_APIS={'pull_messages','check_new_messages','listen_on_newMessages','listen_on_chat','save_media'}


class ReadOnlyBlocked(RuntimeError):
    def __init__(self,action):
        super().__init__(action);self.action=action


def blocker(action,events):
    def denied(*args,**kwargs):
        events.append({'blocked_action':action})
        raise ReadOnlyBlocked(action)
    return denied


def install_input_guard(events):
    import pyautogui
    import pywinauto.keyboard
    import pywinauto.mouse
    from pywinauto.base_wrapper import BaseWrapper
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.controls.uia_controls import EditWrapper,ButtonWrapper
    for name in ('write','typewrite','press','hotkey','keyDown','keyUp','click','doubleClick',
                 'tripleClick','rightClick','mouseDown','mouseUp','moveTo','moveRel','scroll','hscroll','vscroll'):
        if hasattr(pyautogui,name):setattr(pyautogui,name,blocker('pyautogui.'+name,events))
    pywinauto.keyboard.send_keys=blocker('keyboard.send_keys',events)
    for name in ('click','double_click','right_click','press','release','move','wheel','_perform_click_input'):
        if hasattr(pywinauto.mouse,name):setattr(pywinauto.mouse,name,blocker('mouse.'+name,events))
    for cls,names in [(BaseWrapper,('type_keys','click_input','double_click_input','right_click_input')),
                      (UIAWrapper,('type_keys','click_input','double_click_input','right_click_input')),
                      (EditWrapper,('set_text','set_edit_text','type_keys')),
                      (ButtonWrapper,('click','invoke'))]:
        for name in names:
            if hasattr(cls,name):setattr(cls,name,blocker(cls.__name__+'.'+name,events))
    pyautogui.FAILSAFE=True


def summarize_result(value,chat_name,allowed):
    if isinstance(value,list):rows=value
    elif isinstance(value,dict):
        rows=[]
        for key,items in value.items():
            if allowed(key) and isinstance(items,list):rows.extend(items)
        if not rows and '新消息总数' in value:
            return {'message_count':value.get('新消息总数'),'returned_keys':sorted(value),
                    'content_count':len(value.get('文本内容',[])),'sender_count':len(value.get('消息发送人',[]))}
    else:return {'return_type':type(value).__name__}
    records=[row for row in rows if isinstance(row,dict)]
    return {'chat_name':chat_name,'message_count':len(rows),
            'returned_fields':sorted({key for row in records for key in row}),
            'sender_present':sum(bool(row.get('消息发送人')) for row in records),
            'content_present':sum(bool(row.get('消息内容')) for row in records),
            'type_present':sum(bool(row.get('消息类型')) for row in records),
            'send_time_present':sum(bool(row.get('消息发送时间')) for row in records),
            'ordered_record_hashes':[hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest() for row in records]}


def run(method,chat_name,hwnd,output,policy=None):
    from server.wechat_collect.config import WeChatConfig
    from server.wechat_collect.scheduler import SchedulerPolicy
    from server.wechat_collect.visual_spike import exact_group_header
    from server.wechat_collect.windows import desktop_state
    policy=policy or SchedulerPolicy(WeChatConfig(enabled=True))
    report={'method':method,'status':'NOT_TESTED','actual_api_invoked':False,'messages_sent':0,
            'drafts_modified':0,'group_settings_modified':False,'deepseek_calls':0,'vision_calls':0,
            'guards':[],'system_actions_suppressed':[]}
    if method not in READ_APIS:raise ValueError('Only the five audited read APIs are exposed')
    root=(PROJECT/'artifacts/pyweixin-spike').resolve();output=Path(output).resolve()
    if not output.is_relative_to(root):raise ValueError('Invalid diagnostic output path')
    try:
        policy.require(operation='uia',maximum_seconds=45)
        if not desktop_state(policy).unlocked:raise ReadOnlyBlocked('desktop_locked')
        # Import and input interception happen only after the shared time gate.
        install_input_guard(report['guards'])
        from pyweixin import Messages,Monitor,Navigator,Tools,SystemSettings
        import pyautogui
        from pywinauto import Desktop
        pyautogui.FAILSAFE=True  # Upstream resets this during its imports.
        report['package_version']=importlib.metadata.version('pywechat127')
        report['pywinauto_version']=importlib.metadata.version('pywinauto')
        allowed=lambda name:isinstance(name,str) and exact_group_header(name,chat_name)
        original_pull=Messages.pull_messages
        def allowed_pull(*args,**kwargs):
            friend=kwargs.get('friend',args[0] if args else None)
            if not allowed(friend):
                report['guards'].append({'outside_allowlist_read_dropped':True})
                return []
            policy.require(operation='uia',maximum_seconds=20)
            return original_pull(*args,**kwargs)
        Messages.pull_messages=staticmethod(allowed_pull)
        for name in ('open_listening_mode','close_listening_mode','set_system_volume'):
            def suppressed(*args,_name=name,**kwargs):report['system_actions_suppressed'].append(_name)
            setattr(SystemSettings,name,staticmethod(suppressed))
        captured=io.StringIO()
        with contextlib.redirect_stdout(captured),contextlib.redirect_stderr(captured):
            report['connected_hwnd']=int(Tools.get_weixin_hwnd() or 0)
            report['connected']=bool(report['connected_hwnd'])
            import win32process,psutil
            owner=win32process.GetWindowThreadProcessId(hwnd)[1]
            if psutil.Process(owner).name().lower() not in {'weixin.exe','wechat.exe'}:
                raise ReadOnlyBlocked('window_owner_mismatch')
            native=Desktop(backend='uia').window(handle=hwnd).wrapper_object()
            pending=[(native,0)];structure=[]
            while pending and len(structure)<60:
                item,depth=pending.pop(0)
                structure.append({'class_name':item.class_name(),'control_type':item.element_info.control_type,
                                  'framework_id':item.element_info.framework_id})
                if depth<4:pending.extend((child,depth+1) for child in item.children())
            report['window_structure']=structure
            policy.require(operation='uia',maximum_seconds=30)
            output.mkdir(parents=True,exist_ok=True)
            start=time.monotonic();report['actual_api_invoked']=True
            if method=='pull_messages':
                value=Messages.pull_messages(friend=chat_name,number=20,search_pages=1,is_maximize=False,close_weixin=False)
            elif method=='check_new_messages':
                value=Messages.check_new_messages(is_maximize=False,search_pages=1,close_weixin=False)
            elif method=='listen_on_newMessages':
                value=Monitor.listen_on_newMessages(duration='2s',maxPages=0,is_maximize=False,close_weixin=False)
            elif method=='listen_on_chat':
                # Main-window form is explicitly supported in this API's docstring.
                dialog=Desktop(backend='uia').window(handle=hwnd)
                value=Monitor.listen_on_chat(dialog_window=dialog,duration='2s',save_file=False,
                                              save_media=False,close_dialog_window=False)
            else:
                folder=output/'media';folder.mkdir(exist_ok=True)
                value=Messages.save_media(friend=chat_name,number=1,target_folder=str(folder),
                                          search_pages=1,is_maximize=False,close_weixin=False)
                report['media_files']=[{'suffix':path.suffix,'bytes':path.stat().st_size} for path in folder.iterdir() if path.is_file()]
            report['elapsed_seconds']=round(time.monotonic()-start,3)
        report['suppressed_output_chars']=len(captured.getvalue())
        report['result']=summarize_result(value,chat_name,allowed)
        report['status']='PASS' if report['result'].get('message_count',0) else 'PARTIAL'
        report['note']='A completed empty listener does not prove incoming, mute or phone-read behavior.'
    except ReadOnlyBlocked as exc:
        report['status']='NOT_TESTED';report['reason']='read_only_guard';report['blocked_action']=exc.action
    except Exception as exc:
        code=getattr(exc,'code',None)
        report['status']='NOT_TESTED' if code in {'quiet','quiet_boundary','disabled'} else 'FAIL'
        report['error_type']=type(exc).__name__;report['error_code']=code
        report['error_frames']=[{'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name}
                                for frame in traceback.extract_tb(exc.__traceback__)]
    output.mkdir(parents=True,exist_ok=True)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--method',choices=sorted(READ_APIS),required=True)
    parser.add_argument('--chat-name',required=True);parser.add_argument('--window-handle',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.method,args.chat_name,args.window_handle,args.output),ensure_ascii=False))


if __name__=='__main__':main()
