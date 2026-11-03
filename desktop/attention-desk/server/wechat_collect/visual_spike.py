"""Phase 0 only: bounded header crop and local OCR. No message-body reader."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

from ..personal_store import PersonalError
from .spike import _process_metadata
from .windows import require_idle


def header_region(width,height,dpi,layout='chat_header'):
    """A calibrated title band only, never a message-pane or whole-window crop."""
    if width<500 or height<400 or not 96<=dpi<=384:
        raise PersonalError('窗口尺寸或缩放未验证，未截图。','unverified_geometry',409)
    if layout=='window_top':
        left,bottom=width//5,min(round(32*dpi/96),height//10)
    elif layout=='chat_header':
        # The user identified the chat title beneath the top button row.
        # 64 logical pixels = 96 physical pixels at this machine's 150% DPI.
        left,bottom=width//3,min(round(64*dpi/96),height//8)
    else:
        raise PersonalError('未验证的标题布局，未截图。','unverified_geometry',409)
    return left,0,width,bottom


def exact_group_header(text,chat_name):
    def normalize(value):
        return ''.join(unicodedata.normalize('NFKC',value).split())
    # The user corrected a space-separated purpose suffix to parentheses.
    # Ignore only spaces/round brackets and a final participant count, keeping
    # every letter, Chinese character and digit that identifies the group.
    target=normalize(chat_name).translate(str.maketrans('','','()'))
    candidate=re.sub(r'\(\d{1,5}\)$','',normalize(text)).translate(str.maketrans('','','()'))
    return bool(target) and candidate==target


def _output_directory(value):
    root=Path(__file__).resolve().parents[2]/'artifacts'/'wechat-spike'
    value=Path(value).resolve()
    if not value.is_relative_to(root.resolve()):
        raise PersonalError('诊断图片只能保存在本项目隔离目录。','invalid_output_path',400)
    return value


def _verify_visible_region(region,owner_at,hwnd,check_activity):
    """Reject any pixel outside the target; never photograph an occluding app."""
    left,top,right,bottom=region
    for y in range(top,bottom):
        if (y-top)%8==0:
            check_activity()
        for x in range(left,right):
            if owner_at(x,y)!=hwnd:
                raise PersonalError('标题区域被遮挡或不在微信窗口内，未保存截图。','header_occluded',409)


def _validate_render_surface(parent_pid,child_pid,parent_origin,child_origin,parent_size,child_size):
    if (parent_pid!=child_pid or parent_origin!=child_origin or parent_size!=child_size):
        raise PersonalError('渲染子窗与已确认窗口不一致，未截图。','render_surface_mismatch',409)


def capture_header(hwnd,chat_name,output,policy,method='printwindow',layout='chat_header'):
    # Gate before imports, process/window inspection, allocation or image writes.
    policy.require(operation='screenshot',maximum_seconds=10)
    before=require_idle(policy)
    if method not in {'printwindow','printwindow_render','render_child','visible_header'}:
        raise PersonalError('不支持的诊断截图方式。','invalid_capture_mode',400)
    if os.name!='nt':
        raise PersonalError('本诊断需要 Windows。','unsupported_platform',409)
    import ctypes
    from ctypes import wintypes as w
    from PIL import Image

    output=_output_directory(output)
    user=ctypes.WinDLL('user32',use_last_error=True)
    gdi=ctypes.WinDLL('gdi32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    user.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    user.IsIconic.argtypes=[w.HWND]
    user.IsWindowVisible.argtypes=[w.HWND]
    user.GetClientRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    user.GetDpiForWindow.argtypes=[w.HWND]
    user.ClientToScreen.argtypes=[w.HWND,ctypes.POINTER(w.POINT)]
    user.WindowFromPoint.argtypes=[w.POINT];user.WindowFromPoint.restype=w.HWND
    user.GetAncestor.argtypes=[w.HWND,w.UINT];user.GetAncestor.restype=w.HWND
    user.GetWindow.argtypes=[w.HWND,w.UINT];user.GetWindow.restype=w.HWND
    user.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    user.GetDC.argtypes=[w.HWND];user.GetDC.restype=w.HDC
    user.ReleaseDC.argtypes=[w.HWND,w.HDC]
    user.PrintWindow.argtypes=[w.HWND,w.HDC,w.UINT];user.PrintWindow.restype=w.BOOL
    gdi.CreateCompatibleDC.argtypes=[w.HDC];gdi.CreateCompatibleDC.restype=w.HDC
    gdi.CreateCompatibleBitmap.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int];gdi.CreateCompatibleBitmap.restype=w.HBITMAP
    gdi.SelectObject.argtypes=[w.HDC,w.HGDIOBJ];gdi.SelectObject.restype=w.HGDIOBJ
    gdi.SetViewportOrgEx.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int,ctypes.c_void_p]
    gdi.PatBlt.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.DWORD]
    gdi.BitBlt.argtypes=[w.HDC,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HDC,ctypes.c_int,ctypes.c_int,w.DWORD]
    gdi.DeleteObject.argtypes=[w.HGDIOBJ]
    gdi.DeleteDC.argtypes=[w.HDC]

    pid=w.DWORD()
    user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    processes=_process_metadata()
    if pid.value not in {item['pid'] for item in processes}:
        raise PersonalError('窗口不是已确认的微信进程。','window_identity_mismatch',409)
    title=ctypes.create_unicode_buffer(1024)
    user.GetWindowTextW(hwnd,title,len(title))
    if title.value not in {'微信','Weixin','WeChat'} and not exact_group_header(title.value,chat_name):
        raise PersonalError('窗口标题不是主微信或指定群，未截图。','window_title_not_allowed',409)
    if user.IsIconic(hwnd) or not user.IsWindowVisible(hwnd):
        raise PersonalError('窗口最小化或不可见，未恢复窗口。','window_not_visible',409)
    client=w.RECT()
    if not user.GetClientRect(hwnd,ctypes.byref(client)):
        raise PersonalError('无法取得窗口尺寸。','geometry_unavailable',409)
    dpi=user.GetDpiForWindow(hwnd)
    region=header_region(client.right,client.bottom,dpi,layout)
    left,top,right,bottom=region
    width,height=right-left,bottom-top
    render_hwnd=hwnd
    if method=='render_child':
        matches=[]
        child=user.GetWindow(hwnd,5)  # GW_CHILD, native metadata only.
        for _ in range(32):
            if not child:break
            require_idle(policy)
            name=ctypes.create_unicode_buffer(256)
            user.GetClassNameW(child,name,len(name))
            if name.value=='MMUIRenderSubWindow' and user.GetAncestor(child,2)==hwnd:
                matches.append(child)
            child=user.GetWindow(child,2)  # GW_HWNDNEXT.
        if len(matches)!=1:
            raise PersonalError('未找到唯一的微信渲染子窗。','render_surface_unverified',409)
        render_hwnd=matches[0]
        child_pid=w.DWORD();child_rect=w.RECT();parent_origin=w.POINT();child_origin=w.POINT()
        user.GetWindowThreadProcessId(render_hwnd,ctypes.byref(child_pid))
        if not (user.GetClientRect(render_hwnd,ctypes.byref(child_rect)) and
                user.ClientToScreen(hwnd,ctypes.byref(parent_origin)) and
                user.ClientToScreen(render_hwnd,ctypes.byref(child_origin))):
            raise PersonalError('渲染子窗尺寸无法验证。','render_surface_unverified',409)
        _validate_render_surface(pid.value,child_pid.value,(parent_origin.x,parent_origin.y),
                                 (child_origin.x,child_origin.y),(client.right,client.bottom),
                                 (child_rect.right,child_rect.bottom))
    policy.require(operation='screenshot',maximum_seconds=10)
    stable=require_idle(policy)
    if stable.foreground!=before.foreground or stable.mouse!=before.mouse:
        raise PersonalError('用户活动，未截图。','activity_changed',409)

    def check_activity():
        policy.require(operation='screenshot',maximum_seconds=5)
        current=require_idle(policy)
        if current.foreground!=before.foreground or current.mouse!=before.mouse:
            raise PersonalError('前台或鼠标状态变化，未保存截图。','activity_changed',409)

    screen_region=None
    if method=='visible_header':
        origin=w.POINT(left,top)
        if not user.ClientToScreen(hwnd,ctypes.byref(origin)):
            raise PersonalError('无法确认标题屏幕坐标。','geometry_unavailable',409)
        screen_region=(origin.x,origin.y,origin.x+width,origin.y+height)
        owner_at=lambda x,y:int(user.GetAncestor(user.WindowFromPoint(w.POINT(x,y)),2) or 0)
        _verify_visible_region(screen_region,owner_at,hwnd,check_activity)

    class BitmapHeader(ctypes.Structure):
        _fields_=[('size',w.DWORD),('width',w.LONG),('height',w.LONG),('planes',w.WORD),('bit_count',w.WORD),
                  ('compression',w.DWORD),('image_size',w.DWORD),('x_resolution',w.LONG),('y_resolution',w.LONG),('used',w.DWORD),('important',w.DWORD)]
    class BitmapInfo(ctypes.Structure):
        _fields_=[('header',BitmapHeader),('colors',w.DWORD*3)]
    gdi.GetDIBits.argtypes=[w.HDC,w.HBITMAP,w.UINT,w.UINT,ctypes.c_void_p,ctypes.POINTER(BitmapInfo),w.UINT]
    gdi.GetDIBits.restype=ctypes.c_int

    dc=memory=bitmap=previous=None
    dc_owner=None if method=='visible_header' else render_hwnd
    try:
        dc=user.GetDC(dc_owner)
        if not dc:
            raise PersonalError('无法读取窗口绘制区域。','capture_unavailable',503)
        memory=gdi.CreateCompatibleDC(dc)
        # The backing bitmap is ONLY the requested header, never the window.
        bitmap=gdi.CreateCompatibleBitmap(dc,width,height)
        if not dc or not memory or not bitmap:
            raise PersonalError('无法创建局部截图缓冲。','capture_unavailable',503)
        previous=gdi.SelectObject(memory,bitmap)
        if not previous or not gdi.PatBlt(memory,0,0,width,height,0x00000042):  # BLACKNESS; no stale pixels.
            raise PersonalError('无法初始化局部截图缓冲。','capture_unavailable',503)
        if method!='visible_header':
            if not gdi.SetViewportOrgEx(memory,-left,-top,None):
                raise PersonalError('无法限定标题截图区域。','capture_unavailable',503)
            # Optional Windows composition diagnostic; the destination bitmap
            # remains header-sized even when PW_RENDERFULLCONTENT is requested.
            flags=3 if method in {'printwindow_render','render_child'} else 1
            if not user.PrintWindow(render_hwnd,memory,flags):
                raise PersonalError('后台标题截图不可用。','background_capture_unavailable',409)
        else:
            check_activity()
            if not gdi.BitBlt(memory,0,0,width,height,dc,screen_region[0],screen_region[1],0x00CC0020):
                raise PersonalError('局部屏幕标题不可读取。','capture_unavailable',503)
            _verify_visible_region(screen_region,owner_at,hwnd,check_activity)
        gdi.SelectObject(memory,previous)
        previous=None
        info=BitmapInfo()
        info.header=BitmapHeader(ctypes.sizeof(BitmapHeader),width,-height,1,32,0,width*height*4,0,0,0,0)
        pixels=ctypes.create_string_buffer(width*height*4)
        if gdi.GetDIBits(memory,bitmap,0,height,pixels,ctypes.byref(info),0)!=height:
            raise PersonalError('局部截图不完整。','capture_incomplete',409)
        after=require_idle(policy)
        if after.foreground!=before.foreground or after.mouse!=before.mouse:
            raise PersonalError('截图时检测到用户活动，结果未保存。','activity_changed',409)
        current=w.RECT()
        user.GetClientRect(hwnd,ctypes.byref(current))
        current_pid=w.DWORD()
        user.GetWindowThreadProcessId(hwnd,ctypes.byref(current_pid))
        if current_pid.value!=pid.value or current.right!=client.right or current.bottom!=client.bottom:
            raise PersonalError('窗口状态变化，结果未保存。','window_changed',409)
        if render_hwnd!=hwnd:
            current_render_pid=w.DWORD()
            user.GetWindowThreadProcessId(render_hwnd,ctypes.byref(current_render_pid))
            if current_render_pid.value!=pid.value or user.GetAncestor(render_hwnd,2)!=hwnd:
                raise PersonalError('渲染子窗已变化，结果未保存。','render_surface_mismatch',409)
        if screen_region:
            current_origin=w.POINT(left,top)
            if not user.ClientToScreen(hwnd,ctypes.byref(current_origin)) or (current_origin.x,current_origin.y)!=screen_region[:2]:
                raise PersonalError('窗口位置变化，结果未保存。','window_changed',409)
        image=Image.frombytes('RGB',(width,height),pixels.raw,'raw','BGRX')
        low,high=image.convert('L').getextrema()
        output.mkdir(parents=True,exist_ok=True)
        content_key=hashlib.sha256(image.tobytes()).hexdigest()[:24]
        path=output/f'header-{hwnd}-{content_key}.png'
        image.save(path,format='PNG')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        return {'path':str(path),'sha256':digest,'region':list(region),'client_size':[client.right,client.bottom],
                'crop_size':[width,height],'blank':high-low<8,'foreground_unchanged':True,'mouse_unchanged':True,
                'capture_kind':'header_only_'+('screen_bitblt' if screen_region else method),
                'header_layout':layout,'dpi':dpi,'window_is_foreground':before.foreground==hwnd,
                'render_handle':int(render_hwnd),
                'screen_region':list(screen_region) if screen_region else None,'wechat_body_captured':False}
    finally:
        if previous and memory:
            gdi.SelectObject(memory,previous)
        if bitmap:gdi.DeleteObject(bitmap)
        if memory:gdi.DeleteDC(memory)
        if dc:user.ReleaseDC(dc_owner,dc)


def recognize_header(capture,chat_name,policy):
    policy.require(operation='ocr',maximum_seconds=60)
    if capture['blank']:
        return {'status':'unreadable','reason':'blank_header','ocr_calls':0,'group_match':False}
    path=Path(capture['path']).resolve()
    _output_directory(path.parent)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=capture['sha256']:
        raise PersonalError('标题截图版本已变化。','image_changed',409)
    # Each image has a separate workspace: the existing native OCR parser uses
    # fixed tile names inside its work directory.
    workdir=path.parent/(path.stem+'-ocr')
    result=workdir/'result.json'
    if not result.exists():
        require_idle(policy)
        workdir.mkdir(parents=True,exist_ok=True)
        try:
            subprocess.run([sys.executable,'-m','server.documents.worker',str(path),'png',str(result)],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                           timeout=45,check=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except (subprocess.TimeoutExpired,subprocess.CalledProcessError,OSError):
            return {'status':'unreadable','reason':'local_ocr_failed','ocr_calls':1,'group_match':False}
        calls=1
    else:
        calls=0
    parsed=json.loads(result.read_text(encoding='utf-8'))
    if parsed.get('error'):
        return {'status':'unreadable','reason':parsed['error']['code'],'ocr_calls':calls,'group_match':False}
    blocks=parsed.get('blocks',[])
    matched=any(exact_group_header(block['text'],chat_name) for block in blocks)
    return {'status':'matched' if matched else 'not_matched','ocr_calls':calls,'group_match':matched,
            'ocr_line_count':len(blocks),'ocr_engine':'windows.media.ocr','derived_text':True}


def inspect_header(hwnd,chat_name,output,policy,capture_mode='printwindow',header_layout='chat_header'):
    """Partial feasibility evidence only; never certifies a message reader."""
    report={'recorded_at':policy.clock().isoformat(),'allowlisted_test_chat':chat_name,
            'level':'NO_GO','runtime_verified':False,'header_captures':0,'ocr_calls':0,
            'deepseek_calls':0,'vision_calls':0,'message_bodies_read':0,
            'safety':{'messages_sent':0,'drafts_modified':0,'send_triggered':False,
                      'group_settings_modified':False}}
    try:
        policy.require(operation='screenshot',maximum_seconds=60)
        if not isinstance(chat_name,str) or not chat_name.strip() or len(chat_name)>120 or '\n' in chat_name:
            raise PersonalError('需要明确的单行测试群名。','invalid_chat_name',400)
        capture=capture_header(hwnd,chat_name,output,policy,capture_mode,header_layout)
        report['header_captures']=1
        report['capture']=capture
        ocr=recognize_header(capture,chat_name,policy)
        report['ocr']=ocr
        report['ocr_calls']=ocr['ocr_calls']
        report['target_header']={'status':'partial' if ocr['group_match'] else 'fail',
                                 'reason':'local_ocr_match_requires_message_boundary_verification'
                                          if ocr['group_match'] else ocr.get('reason','target_not_recognized')}
        report['level_reason']='Header-only probe cannot verify message order, history or incremental boundaries.'
    except Exception as exc:
        report['error_code']=getattr(exc,'code','header_probe_failed')
        report['error_type']=type(exc).__name__
        report['target_header']={'status':'not_tested','reason':report['error_code']}
        report['level_reason']='Bounded header probe incomplete; no message bodies read.'
    return report
