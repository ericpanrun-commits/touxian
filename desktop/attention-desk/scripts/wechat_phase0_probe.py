"""Explicit Phase 0 exploration. Local OCR only; never imported by the Collector.

The user permits window activation and full client captures for this spike.
There is deliberately no keyboard, clipboard, chat-input or sending interface.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from server.personal_store import PersonalError
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy
from server.wechat_collect.spike import _process_metadata
from server.wechat_collect.visual_spike import _output_directory,exact_group_header
from server.wechat_collect.windows import desktop_state


def line_box(line):
    words=line.get('words',[])
    if not words:return None
    left=min(word['x'] for word in words);top=min(word['y'] for word in words)
    right=max(word['x']+word['width'] for word in words)
    bottom=max(word['y']+word['height'] for word in words)
    return [round(left),round(top),round(right),round(bottom)]


def find_target_header(lines,chat_name,size):
    width,height=size
    return [line_box(line) for line in lines if line_box(line) and
            line_box(line)[0]>=width*.27 and line_box(line)[1]<height*.25 and
            exact_group_header(line['text'],chat_name)]


def sanitized_layout(lines,chat_name,size):
    headers=find_target_header(lines,chat_name,size)
    rows=[]
    for index,line in enumerate(lines):
        box=line_box(line)
        if not box:continue
        text=line.get('text','')
        rows.append({'index':index,'box':box,'chars':len(text),
                     'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                     'kind':'target_header' if box in headers else 'redacted',
                     'text':chat_name if box in headers else None})
    return {'target_header_boxes':headers,'target_header_match':len(headers)==1,'lines':rows}


def redacted_image(size,layout):
    # Preserve coordinates without retaining private previews, pictures or avatars.
    image=Image.new('RGB',size,'#f5f5f5');draw=ImageDraw.Draw(image)
    for row in layout['lines']:
        color='#148348' if row['kind']=='target_header' else '#9b9b9b'
        draw.rectangle(row['box'],outline=color,width=2)
        draw.text((row['box'][0],max(0,row['box'][1]-10)),str(row['index']),fill=color)
    return image


class Phase0Window:
    def __init__(self,hwnd,chat_name,policy=None):
        self.policy=policy or SchedulerPolicy(WeChatConfig(enabled=True))
        self.policy.require(operation='uia',maximum_seconds=60)
        self.before=desktop_state(self.policy)
        if not self.before.unlocked:raise PersonalError('桌面锁定。','locked',409)
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.gdi=ctypes.WinDLL('gdi32',use_last_error=True)
        self.hwnd=hwnd
        signatures={
            'GetWindowThreadProcessId':([w.HWND,ctypes.POINTER(w.DWORD)],w.DWORD),
            'GetWindowTextW':([w.HWND,w.LPWSTR,ctypes.c_int],ctypes.c_int),
            'GetClientRect':([w.HWND,ctypes.POINTER(w.RECT)],w.BOOL),
            'ClientToScreen':([w.HWND,ctypes.POINTER(w.POINT)],w.BOOL),
            'SetForegroundWindow':([w.HWND],w.BOOL), 'ShowWindow':([w.HWND,ctypes.c_int],w.BOOL),
            'IsIconic':([w.HWND],w.BOOL), 'GetDC':([w.HWND],w.HDC),
            'ReleaseDC':([w.HWND,w.HDC],ctypes.c_int),
            'GetForegroundWindow':([],w.HWND),
            'GetDpiForWindow':([w.HWND],w.UINT),
            'GetWindow':([w.HWND,w.UINT],w.HWND),
            'GetClassNameW':([w.HWND,w.LPWSTR,ctypes.c_int],ctypes.c_int),
            'GetAncestor':([w.HWND,w.UINT],w.HWND),
            'SendMessageTimeoutW':([w.HWND,w.UINT,w.WPARAM,w.LPARAM,w.UINT,w.UINT,ctypes.POINTER(ctypes.c_size_t)],ctypes.c_size_t),
        }
        for name,(args,result) in signatures.items():
            function=getattr(self.user,name);function.argtypes=args;function.restype=result
        processes=_process_metadata()
        pid=w.DWORD();self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value not in {row['pid'] for row in processes}:
            raise PersonalError('不是微信窗口。','window_identity_mismatch',409)
        self.pid=pid.value;self.processes=processes
        title=ctypes.create_unicode_buffer(1024);self.user.GetWindowTextW(hwnd,title,len(title))
        if title.value not in {'微信','Weixin','WeChat'} and not exact_group_header(title.value,chat_name):
            raise PersonalError('不是主窗口或指定测试群。','window_title_not_allowed',409)
        self.original_minimized=bool(self.user.IsIconic(hwnd))
        self.activated=False
        self.initial_input_tick=self.input_tick()
        self.dpi=self.user.GetDpiForWindow(hwnd)

    def input_tick(self):
        class LastInput(ctypes.Structure):_fields_=[('size',w.UINT),('tick',w.DWORD)]
        value=LastInput(ctypes.sizeof(LastInput),0)
        if not self.user.GetLastInputInfo(ctypes.byref(value)):raise RuntimeError('Input state unavailable')
        return value.tick

    def check(self,operation='screenshot'):
        self.policy.require(operation=operation,maximum_seconds=45 if operation=='ocr' else 5)
        state=desktop_state(self.policy)
        if not state.unlocked:raise PersonalError('桌面锁定。','locked',409)
        pid=w.DWORD();self.user.GetWindowThreadProcessId(self.hwnd,ctypes.byref(pid))
        if pid.value!=self.pid:raise PersonalError('窗口已变化。','window_changed',409)
        return state

    def activate(self):
        self.check()
        if self.original_minimized:self.user.ShowWindow(self.hwnd,9)
        self.user.SetForegroundWindow(self.hwnd)
        time.sleep(.3)
        if self.user.GetForegroundWindow()!=self.hwnd:
            raise PersonalError('系统拒绝前台切换；不使用键盘或输入绑定绕过。','foreground_denied',409)
        self.activated=True

    def capture(self):
        state=self.check()
        if state.foreground!=self.hwnd:raise PersonalError('微信不在前台。','foreground_changed',409)
        if self.input_tick()!=self.initial_input_tick:raise PersonalError('检测到用户输入，本次探索停止。','activity_changed',409)
        rect=w.RECT();origin=w.POINT()
        if not self.user.GetClientRect(self.hwnd,ctypes.byref(rect)) or not self.user.ClientToScreen(self.hwnd,ctypes.byref(origin)):
            raise PersonalError('窗口坐标不可用。','geometry_unavailable',409)
        width,height=rect.right,rect.bottom
        if not 500<=width<=8000 or not 400<=height<=8000:raise PersonalError('窗口尺寸不可用。','unverified_geometry',409)
        self.gdi.CreateCompatibleDC.argtypes=[w.HDC];self.gdi.CreateCompatibleDC.restype=w.HDC
        self.gdi.CreateCompatibleBitmap.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int];self.gdi.CreateCompatibleBitmap.restype=w.HBITMAP
        self.gdi.SelectObject.argtypes=[w.HDC,w.HGDIOBJ];self.gdi.SelectObject.restype=w.HGDIOBJ
        self.gdi.BitBlt.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HDC,ctypes.c_int,ctypes.c_int,w.DWORD]
        self.gdi.DeleteObject.argtypes=[w.HGDIOBJ];self.gdi.DeleteDC.argtypes=[w.HDC]
        class Header(ctypes.Structure):
            _fields_=[('size',w.DWORD),('width',w.LONG),('height',w.LONG),('planes',w.WORD),('bits',w.WORD),
                      ('compression',w.DWORD),('image_size',w.DWORD),('x',w.LONG),('y',w.LONG),('used',w.DWORD),('important',w.DWORD)]
        class Info(ctypes.Structure):_fields_=[('header',Header),('colors',w.DWORD*3)]
        self.gdi.GetDIBits.argtypes=[w.HDC,w.HBITMAP,w.UINT,w.UINT,ctypes.c_void_p,ctypes.POINTER(Info),w.UINT]
        dc=memory=bitmap=old=None
        try:
            dc=self.user.GetDC(None);memory=self.gdi.CreateCompatibleDC(dc)
            bitmap=self.gdi.CreateCompatibleBitmap(dc,width,height)
            if not dc or not memory or not bitmap:raise RuntimeError('capture allocation failed')
            old=self.gdi.SelectObject(memory,bitmap)
            if not self.gdi.BitBlt(memory,0,0,width,height,dc,origin.x,origin.y,0x00CC0020):raise RuntimeError('capture failed')
            self.gdi.SelectObject(memory,old);old=None
            info=Info();info.header=Header(ctypes.sizeof(Header),width,-height,1,32,0,width*height*4,0,0,0,0)
            pixels=ctypes.create_string_buffer(width*height*4)
            if self.gdi.GetDIBits(memory,bitmap,0,height,pixels,ctypes.byref(info),0)!=height:raise RuntimeError('incomplete capture')
            after=self.check()
            if after.foreground!=self.hwnd or after.mouse!=state.mouse or self.input_tick()!=self.initial_input_tick:
                raise PersonalError('捕获期间有活动，未保存。','activity_changed',409)
            return Image.frombytes('RGB',(width,height),pixels.raw,'raw','BGRX'),[origin.x,origin.y]
        finally:
            if old and memory:self.gdi.SelectObject(memory,old)
            if bitmap:self.gdi.DeleteObject(bitmap)
            if memory:self.gdi.DeleteDC(memory)
            if dc:self.user.ReleaseDC(None,dc)

    def restore(self):
        if self.activated and self.user.GetForegroundWindow()==self.hwnd:
            state=self.check()
            if state.mouse==self.before.mouse and self.input_tick()==self.initial_input_tick:
                if self.original_minimized:self.user.ShowWindow(self.hwnd,6)
                if self.before.foreground and self.before.foreground!=self.hwnd:
                    self.user.SetForegroundWindow(self.before.foreground)

    def scroll_history(self,delta,layout):
        """Only WM_MOUSEWHEEL, at the verified message viewport; no button/key events."""
        state=self.check()
        if state.foreground!=self.hwnd or self.input_tick()!=self.initial_input_tick:
            raise PersonalError('检测到用户活动，停止滚动。','activity_changed',409)
        if delta not in {-240,-120,120,240}:raise ValueError('Unbounded scroll')
        left,top,right,bottom=layout['message_viewport']
        deny=layout['input_denylist']
        x,y=(left+right)//2,(top+bottom)//2
        if not (top<y<bottom<=deny[1]):raise ValueError('Scroll intersects input denylist')
        point=w.POINT(x,y)
        if not self.user.ClientToScreen(self.hwnd,ctypes.byref(point)):raise RuntimeError('Scroll coordinates unavailable')
        target=self.hwnd;child=self.user.GetWindow(self.hwnd,5)
        for _ in range(32):
            if not child:break
            name=ctypes.create_unicode_buffer(256);self.user.GetClassNameW(child,name,len(name))
            if name.value=='MMUIRenderSubWindow' and self.user.GetAncestor(child,2)==self.hwnd:
                target=child;break
            child=self.user.GetWindow(child,2)
        result=ctypes.c_size_t()
        # This literal is intentionally fixed; callers cannot send arbitrary messages.
        okay=self.user.SendMessageTimeoutW(target,0x020A,(delta&0xffff)<<16,
                                            ((point.y&0xffff)<<16)|(point.x&0xffff),2,1500,ctypes.byref(result))
        if not okay:raise PersonalError('滚动请求未完成。','scroll_timeout',409)
        time.sleep(.45)


def history_probe(window,chat_name,root,pages=3):
    # Imports remain optional for the small header/snapshot diagnostics.
    from wechat_phase0_cv import calibrate,bubbles,compare_views,safe_bubbles,translation
    report={'phase0_only':True,'level':None,'verification_status':'in_progress',
            'captures':0,'ocr_calls':0,'scroll_operations':0,'messages_sent':0,
            'keyboard_operations':0,'drafts_modified':0,'group_settings_modified':False,
            'deepseek_calls':0,'vision_calls':0,'views':[],'transitions':[]}
    layout=None;images=[];all_bubbles=[]
    def sample(label):
        nonlocal layout
        image,origin=window.capture();report['captures']+=1
        with (root/'operations.jsonl').open('a',encoding='utf-8') as log:
            log.write(json.dumps({'operation':'capture','view':label,'at':window.policy.clock().isoformat()})+'\n')
        window.check('ocr');report['ocr_calls']+=1
        parsed=local_ocr(image,root,window.policy)
        with (root/'operations.jsonl').open('a',encoding='utf-8') as log:
            log.write(json.dumps({'operation':'local_ocr','view':label,'at':window.policy.clock().isoformat()})+'\n')
        safe=sanitized_layout(parsed.get('lines',[]),chat_name,image.size)
        if not safe['target_header_match']:raise PersonalError('当前标题不再匹配指定测试群。','group_identity_lost',409)
        if layout is None:layout=calibrate(image,safe['target_header_boxes'][0],window.dpi)
        found=bubbles(image,parsed.get('lines',[]),layout,window.dpi)
        overlay=redacted_image(image.size,safe);draw=ImageDraw.Draw(overlay)
        for index,row in enumerate(found):draw.rectangle(row['box'],outline='#2468a2',width=3)
        draw.rectangle(layout['message_viewport'],outline='#148348',width=2)
        draw.rectangle(layout['input_denylist'],outline='#ad2525',width=3)
        overlay.save(root/(label+'-redacted.png'))
        report['views'].append({'label':label,'header_match':True,'size':list(image.size),
                                'bubbles':safe_bubbles(found),'ocr_lines':len(parsed.get('lines',[]))})
        if images:
            comparison=compare_views(all_bubbles[-1],found)
            comparison['pixel_translation']=translation(images[-1],image,layout['message_viewport'])
            comparison['from']=report['views'][-2]['label'];comparison['to']=label
            report['transitions'].append(comparison)
        images.append(image);all_bubbles.append(found)
    window.activate()
    try:
        sample('baseline');sample('repeat')
        for index in range(pages):
            window.scroll_history(120,layout);report['scroll_operations']+=1;sample(f'up-{index+1}')
        for index in range(pages):
            window.scroll_history(-120,layout);report['scroll_operations']+=1;sample(f'down-{index+1}')
        report['roundtrip']=compare_views(all_bubbles[0],all_bubbles[-1])
        moved=[row for row in report['transitions'] if abs(row['pixel_translation']['dy'])>=8 and row['pixel_translation']['response']>.15]
        report['actual_scroll_transitions']=len(moved)
        report['layout']=layout
        repeat=report['transitions'][0]['same_visible_sequence']
        reliable=repeat and len(moved)>=2 and all(row['boundary_verified'] for row in moved) and report['roundtrip']['same_visible_sequence']
        report['level']='IDLE_ONLY' if reliable else 'NO_GO'
        report['verification_status']='bounded_history_verified' if reliable else 'message_boundaries_not_yet_reliable'
        report['automatic_collection_enabled']=False
        report['limitations']=['Only the explicitly tested foreground layout; no production adapter certification.',
                              'OCR is derived text; source time, sender and media types require separate validation.',
                              'No new incoming message was deliberately generated; no messages were sent.']
    except Exception as exc:
        report['error_code']=getattr(exc,'code','history_probe_failed');report['error_type']=type(exc).__name__
        report['level']='NO_GO';report['verification_status']='incomplete'
    finally:
        window.restore()
    (root/'history-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def local_ocr(image,root,policy):
    policy.require(operation='ocr',maximum_seconds=45)
    folder=Path(tempfile.mkdtemp(prefix='local-only-',dir=root)).resolve()
    if not folder.is_relative_to(root.resolve()):raise RuntimeError('temporary path escaped')
    path=folder/'client.png'
    try:
        image.save(path)
        powershell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        script=Path(__file__).resolve().parents[1]/'server/documents/windows_ocr.ps1'
        result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),'-ImagePath',str(path)],
                              stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not result.stdout.strip():
            raise PersonalError('本地 OCR 进程未完成。','ocr_process_failed',409)
        try:value=json.loads(result.stdout.decode('utf-8-sig'))
        except (ValueError,UnicodeError):raise PersonalError('本地 OCR 响应无效。','ocr_response_failed',409) from None
        if result.returncode or value.get('error'):raise PersonalError('本地 OCR 未完成。','ocr_failed',409)
        return value
    finally:
        path.unlink(missing_ok=True)
        folder.rmdir()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window-handle',type=int,required=True)
    parser.add_argument('--chat-name',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=['snapshot','history'],default='snapshot')
    parser.add_argument('--pages',type=int,choices=range(1,5),default=3)
    args=parser.parse_args();root=_output_directory(args.output);root.mkdir(parents=True,exist_ok=True)
    report={'phase0_only':True,'level':'NO_GO','messages_sent':0,'keyboard_operations':0,'drafts_modified':0,
            'group_settings_modified':False,'deepseek_calls':0,'vision_calls':0,'captures':0,'ocr_calls':0}
    window=None
    try:
        window=Phase0Window(args.window_handle,args.chat_name)
        if args.mode=='history':
            history=history_probe(window,args.chat_name,root,args.pages)
            print(json.dumps({key:value for key,value in history.items() if key not in {'views','transitions'}},ensure_ascii=False))
            return
        window.activate()
        try:image,origin=window.capture();report['captures']=1
        finally:window.restore()
        report['ocr_calls']=1
        parsed=local_ocr(image,root,window.policy)
        layout=sanitized_layout(parsed.get('lines',[]),args.chat_name,image.size)
        report.update({'size':list(image.size),'client_origin':origin,'language':parsed.get('language'),
                       'layout':layout,'group_binding':'partial' if layout['target_header_match'] else 'not_verified',
                       'versions':sorted({row['file_version'] for row in window.processes if row.get('file_version')})})
        redacted_image(image.size,layout).save(root/'layout-redacted.png')
        report['raw_capture_retained']=False
        report['level_reason']='Full local layout probe; ordered messages and incremental boundaries still require verification.'
    except Exception as exc:
        report['error_code']=getattr(exc,'code','phase0_failed');report['error_type']=type(exc).__name__
        report['error_frames']=[{'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name}
                                for frame in traceback.extract_tb(exc.__traceback__)]
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='layout'},ensure_ascii=False))


if __name__=='__main__':main()
