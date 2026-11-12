"""Supervise existing desktop child workers; never kill a slow API request."""
from collections import deque
import json
from pathlib import Path
import threading
import time


def _token(kernel,handle):
    import ctypes
    from ctypes import wintypes
    fields=[wintypes.FILETIME() for _ in range(4)]
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
    if not kernel.GetProcessTimes(handle,*[ctypes.byref(f) for f in fields]):return None
    return str((fields[0].dwHighDateTime<<32)|fields[0].dwLowDateTime)


def process_token():
    import os
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    return _token(kernel,kernel.GetCurrentProcess())


def adopt(state_path,service):
    """An orphaned worker is observed through a process handle, never killed."""
    import os
    if os.name!='nt':return None
    try:
        value=json.loads(Path(state_path).read_text('utf-8'))
        if value.get('service')!=service or type(value.get('pid')) is not int:return None
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        handle=kernel.OpenProcess(0x1000|0x100000,False,value['pid'])
        if not handle:return None
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        if not value.get('process_token') or _token(kernel,handle)!=value['process_token']:
            kernel.CloseHandle(handle);return None
        image=ctypes.create_unicode_buffer(32768);length=wintypes.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(handle,0,image,ctypes.byref(length)) or Path(image.value).name.lower() not in {'python.exe','pythonw.exe'}:
            kernel.CloseHandle(handle);return None
        class ExistingWorker:
            pid=value['pid']
            def poll(self):
                code=wintypes.DWORD()
                if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return 1
                return None if code.value==259 else code.value
            def __del__(self):kernel.CloseHandle(handle)
        result=ExistingWorker()
        return result if result.poll() is None else None
    except (OSError,ValueError,KeyError):return None


class Supervisor:
    def __init__(self, directory, launchers, *, clock=time.time):
        self.path=Path(directory)/'notification-supervisor-state.json'
        self.launchers=launchers; self.clock=clock; self.children={}; self.started={}
        try: prior=json.loads(self.path.read_text('utf-8'))
        except (OSError,ValueError): prior={}
        self.failures={name:deque(prior.get(name,{}).get('exits',[])) for name in launchers}
        self.next_at={name:prior.get(name,{}).get('next_at',0) for name in launchers}

    def tick(self):
        at=self.clock(); status={}
        for name,launch in self.launchers.items():
            exits=self.failures[name]
            while exits and exits[0]<at-900: exits.popleft()
            child=self.children.get(name)
            if child is not None and child.poll() is None:
                status[name]={'state':'running','pid':child.pid,'exits':list(exits),'next_at':self.next_at[name]}
                continue
            if child is not None:
                exits.append(at); self.children.pop(name,None)
                delay=min(300,15*2**min(len(exits)-1,5))
                self.next_at[name]=at+(900 if len(exits)>=5 else delay)
            if at<self.next_at[name]:
                status[name]={'state':'restart_backoff','exits':list(exits),'next_at':self.next_at[name],
                              'alert_required':len(exits)>=3}
                continue
            try: child=launch()
            except (OSError,ValueError): child=None
            self.children[name]=child
            self.started[name]=at
            if child is None: self.next_at[name]=at+60
            status[name]={'state':'running' if child else 'awaiting_authorization',
                          'pid':child.pid if child else None,'exits':list(exits),'next_at':self.next_at[name]}
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(status),'utf-8'); temporary.replace(self.path)
        return status

    def start(self):
        self.stop=threading.Event()
        def loop():
            while not self.stop.is_set():
                try: self.tick()
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning('notification_supervision_tick_failed')
                self.stop.wait(5)
        self.thread=threading.Thread(target=loop,name='notification-workers',daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        # Child requests keep their own singleton, grant and durable receipt.
        # No heartbeat timeout or parent shutdown may kill a paid request.
