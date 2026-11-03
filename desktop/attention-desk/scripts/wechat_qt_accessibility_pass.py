"""Current-session accessibility experiment, optionally restarting WeChat normally.

No forced termination, login automation, message input or HKLM writes.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
sys.path.insert(0,str(PROJECT/'scripts'))
from pyweixin_environment_spike import CurrentUserAccessibility,temporary_accessibility
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy
from server.wechat_collect.spike import _process_metadata


def require_only_wechat(pids,allowed):
    if not pids or not set(pids).issubset(set(allowed)):
        raise RuntimeError('Restart Manager returned an unapproved process')


def graceful_restart(pid,inventory,policy):
    """Register only one verified WeChat process; RmForceShutdown is never used."""
    policy.require(operation='uia',maximum_seconds=90)
    by_pid={row['pid']:row for row in inventory}
    require_only_wechat([pid],by_pid)
    executable=Path(by_pid[pid]['path']).resolve(strict=True)
    if executable.name.lower() not in {'weixin.exe','wechat.exe'}:
        raise RuntimeError('Unexpected executable')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    rm=ctypes.WinDLL('rstrtmgr',use_last_error=True)
    class UniqueProcess(ctypes.Structure):
        _fields_=[('pid',w.DWORD),('started',w.FILETIME)]
    class ProcessInfo(ctypes.Structure):
        _fields_=[('process',UniqueProcess),('app_name',w.WCHAR*256),
                  ('service_name',w.WCHAR*64),('app_type',ctypes.c_int),
                  ('status',w.ULONG),('session_id',w.DWORD),('restartable',w.BOOL)]
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
    kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
    rm.RmStartSession.argtypes=[ctypes.POINTER(w.DWORD),w.DWORD,w.LPWSTR]
    rm.RmEndSession.argtypes=[w.DWORD]
    rm.RmRegisterResources.argtypes=[w.DWORD,w.UINT,ctypes.c_void_p,w.UINT,ctypes.POINTER(UniqueProcess),w.UINT,ctypes.c_void_p]
    rm.RmGetList.argtypes=[w.DWORD,ctypes.POINTER(w.UINT),ctypes.POINTER(w.UINT),ctypes.POINTER(ProcessInfo),ctypes.POINTER(w.DWORD)]
    rm.RmShutdown.argtypes=[w.DWORD,w.ULONG,ctypes.c_void_p]
    rm.RmRestart.argtypes=[w.DWORD,w.DWORD,ctypes.c_void_p]
    process=kernel.OpenProcess(0x1000,False,pid)  # Query only; not PROCESS_TERMINATE.
    if not process:raise ctypes.WinError(ctypes.get_last_error())
    session=w.DWORD();started=False
    record={'old_main_pid':pid,'method':'RestartManager','force_shutdown':False}
    def exited():
        code=w.DWORD()
        if not kernel.GetExitCodeProcess(process,ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value!=259
    try:
        buffer=ctypes.create_unicode_buffer(32768);length=w.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(process,0,buffer,ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        if Path(buffer.value).resolve()!=executable:raise RuntimeError('Process identity changed')
        times=[w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(process,*[ctypes.byref(value) for value in times]):
            raise ctypes.WinError(ctypes.get_last_error())
        key=ctypes.create_unicode_buffer(33)
        code=rm.RmStartSession(ctypes.byref(session),0,key)
        if code:raise OSError(code,'RmStartSession')
        started=True
        registered=UniqueProcess(pid,times[0])
        code=rm.RmRegisterResources(session,0,None,1,ctypes.byref(registered),0,None)
        if code:raise OSError(code,'RmRegisterResources')
        needed=w.UINT();count=w.UINT();reboot=w.DWORD()
        code=rm.RmGetList(session,ctypes.byref(needed),ctypes.byref(count),None,ctypes.byref(reboot))
        if code not in {0,234} or not 0<needed.value<=32:
            raise RuntimeError('Unable to verify Restart Manager scope')
        infos=(ProcessInfo*needed.value)();count.value=needed.value
        code=rm.RmGetList(session,ctypes.byref(needed),ctypes.byref(count),infos,ctypes.byref(reboot))
        if code:raise OSError(code,'RmGetList')
        targets=[row.process.pid for row in infos[:count.value]]
        require_only_wechat(targets,by_pid)
        record['registered_pids']=targets
        record['shutdown_result']=int(rm.RmShutdown(session,0,None))
        deadline=time.monotonic()+12
        while not exited() and time.monotonic()<deadline:time.sleep(.25)
        record['old_main_exited']=exited()
        # Use the documented companion restart for registered applications.
        record['rm_restart_result']=int(rm.RmRestart(session,0,None))
        if not record['old_main_exited']:
            record['status']='graceful_exit_not_completed'
            return record
        time.sleep(1)
        fresh=_process_metadata()
        record['old_processes_remaining']=[row['pid'] for row in fresh if row['pid'] in by_pid]
        new=[row for row in fresh if row['pid'] not in by_pid]
        if not new:
            startup=subprocess.STARTUPINFO()
            startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
            launched=subprocess.Popen([str(executable)],cwd=str(executable.parent),
                                      stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                      startupinfo=startup)
            record['launched_pid']=launched.pid
        record['status']='restarted'
        return record
    finally:
        if started:rm.RmEndSession(session)
        kernel.CloseHandle(process)


def probe(label,root,policy):
    policy.require(operation='uia',maximum_seconds=40)
    path=root/(label+'.json')
    result=subprocess.run([sys.executable,'-X','utf8',str(PROJECT/'scripts/wechat_qt_accessibility_probe.py'),
                           '--attach-default-desktop','--show-main-window','--output',str(path)],
                          stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not path.exists():raise RuntimeError('Structural worker failed')
    report=json.loads(path.read_text(encoding='utf-8'))
    if report['status']!='complete':raise RuntimeError('Structural probe incomplete')
    print(json.dumps({'probe':label,'roots':[{'hwnd':row['hwnd'],'pid':row['pid'],
          'direct_children':row['direct_child_count'],'descendants':row['descendant_count'],
          'mmui':row['mmui_count'],'expected_classes':row['class_counts'],
          'expected_ids':row['automation_id_counts']} for row in report['windows']]},ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart-wechat',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();root=args.output.resolve()
    if not root.is_relative_to((PROJECT/'artifacts/pyweixin-spike').resolve()):raise ValueError('Invalid output path')
    policy=SchedulerPolicy(WeChatConfig(enabled=True));policy.require(operation='uia',maximum_seconds=240)
    root.mkdir(parents=True,exist_ok=True)
    backend=CurrentUserAccessibility();saved=backend.read()
    (root/'restore-snapshot.json').write_text(json.dumps(saved),encoding='utf-8')
    report={'messages_sent':0,'hklm_writes':0,'login_automation':False}
    try:
        with temporary_accessibility(backend,policy,report):
            before=probe('before-restart',root,policy)
            windows=before['windows']
            if args.restart_wechat and windows and not any(row['mmui_count'] for row in windows):
                main_window=max(windows,key=lambda row:row['width']*row['height'])
                print('Requesting normal WeChat process restart; no forced termination.',flush=True)
                report['restart']=graceful_restart(main_window['pid'],before['processes'],policy)
                (root/'restart-result.json').write_text(json.dumps(report['restart'],indent=2),encoding='utf-8')
                print(json.dumps({'restart':report['restart']}),flush=True)
                if report['restart']['status']=='restarted':
                    time.sleep(3)
                    probe('after-restart-early',root,policy)
                    time.sleep(15)
                    probe('after-restart-settled',root,policy)
    except Exception as exc:
        report['error_type']=type(exc).__name__
        report['error_code']=getattr(exc,'winerror',getattr(exc,'errno',None))
        raise
    finally:
        (root/'environment-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
