"""Current-user background entrypoint. Never starts or logs in to WeChat."""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import sys


def main():
    if os.name!='nt':
        raise RuntimeError('This entrypoint requires a Windows user session')
    from ctypes import wintypes
    from .personal_store import default_database_path
    path=default_database_path()
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
    kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    name='Local\\AttentionDesk-'+hashlib.sha256(str(path).encode()).hexdigest()[:20]
    handle=kernel.CreateMutexW(None,False,name)
    if not handle:
        raise RuntimeError('Unable to create desktop instance lock')
    if ctypes.get_last_error()==183:
        kernel.CloseHandle(handle)
        return
    try:
        logs=path.parent/'logs'
        logs.mkdir(parents=True,exist_ok=True)
        from logging.handlers import RotatingFileHandler
        handler=RotatingFileHandler(logs/'desktop-background.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
        logging.basicConfig(level=logging.INFO,handlers=[handler],format='%(asctime)s %(name)s %(message)s')
        # Some existing parsers expect a writable stream even under pythonw.
        if sys.stdout is None:
            sys.stdout=open(os.devnull,'w',encoding='utf-8')
        if sys.stderr is None:
            sys.stderr=open(os.devnull,'w',encoding='utf-8')
        os.environ['ATTENTION_DESKTOP_BACKGROUND']='1'
        # Separate, explicitly authorized text consumer. The original desktop
        # process retains its paid/cloud holds and Collector lifecycle.
        from .wechat_collect.deepseek_worker import launch_if_authorized
        try:
            launch_if_authorized(path.parent)
        except OSError:
            logging.getLogger(__name__).warning('DeepSeek text worker launch failed; holds preserved')
        import uvicorn
        # pythonw has no stdout/stderr. Use the configured rotating file handler;
        # Uvicorn's default console formatter otherwise calls None.isatty().
        try:
            uvicorn.run('server.main:app',host='127.0.0.1',port=int(os.getenv('ATTENTION_API_PORT','8765')),workers=1,access_log=False,log_config=None)
        except BaseException:
            logging.getLogger(__name__).exception('Desktop background startup failed')
            raise
    finally:
        kernel.CloseHandle(handle)


if __name__=='__main__':
    main()
