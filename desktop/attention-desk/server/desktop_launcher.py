"""Per-user, windowless Windows entry. The browser never owns the Collector."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .desktop_runtime import APP_ID, APP_ROOT, bundle_ready, workspace_id

PORT = 8765
LOG = logging.getLogger('attention.launcher')
MESSAGES = {
    'runtime_missing': 'Attention Desk 的运行环境不完整。请重新安装项目运行环境后再打开。',
    'frontend_failed': '工作台页面准备失败。请查看启动日志，或重新安装桌面入口后再试。',
    'address_in_use': '工作台暂时无法启动：本地服务地址被其他程序占用。请稍后重试。',
    'startup_failed': '工作台后台未能启动。请稍后重新双击 Attention Desk；若仍无法打开，请查看启动日志。',
    'startup_timeout': '工作台后台启动超时。请稍后重新双击 Attention Desk；若仍无法打开，请查看启动日志。',
    'browser_failed': '工作台已准备好，但默认浏览器无法打开。请在 Windows 设置中选择默认浏览器后重试。',
    'unexpected': 'Attention Desk 暂时无法打开。请稍后重试；若仍有问题，请查看启动日志。',
}


class LaunchError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES[code])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def local_get(url, timeout=1):
    # Readiness must never use a configured network proxy or follow a redirect.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url, headers={'User-Agent': 'AttentionDeskLauncher/1'}), timeout=timeout) as response:
        return response.read(32769)


def probe(root=APP_ROOT, port=PORT):
    # Windows may report a closed loopback port as a short connect timeout.
    # Establish TCP first so that only an existing listener gets a readiness wait.
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.4):
            pass
    except OSError:
        return {'state': 'stopped'}
    try:
        value = json.loads(local_get(f'http://127.0.0.1:{port}/api/health?check_integrations=false'))
        if value.get('application')!=APP_ID or value.get('workspace_id')!=workspace_id(root):
            return {'state': 'other'}
        ready = value.get('personal_storage', {}).get('initialized') is True and value.get('frontend_ready') is True
        return {'state': 'ready' if ready else 'starting', 'process_id': value.get('process_id')}
    except (HTTPError, ValueError, TypeError, AttributeError):
        return {'state': 'other'}
    except URLError as error:
        return {'state': 'starting' if isinstance(error.reason, (TimeoutError, socket.timeout)) else 'stopped'}
    except (OSError, TimeoutError):
        return {'state': 'starting'}


def prepare_frontend(root):
    node = shutil.which('node.exe' if os.name=='nt' else 'node')
    vite = root/'node_modules/vite/bin/vite.js'
    if not node or not vite.is_file():
        raise LaunchError('frontend_failed')
    LOG.info('frontend_prepare')
    try:
        result = subprocess.run([node, str(vite), 'build'], cwd=root,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=120, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode or not bundle_ready(root):
            raise LaunchError('frontend_failed')
    except (OSError, subprocess.TimeoutExpired):
        raise LaunchError('frontend_failed') from None


def start_backend(root, port):
    python = root.parent/'.venv/Scripts/pythonw.exe'
    if not python.is_file():
        raise LaunchError('runtime_missing')
    env = dict(os.environ, ATTENTION_API_PORT=str(port))
    try:
        child = subprocess.Popen([str(python), '-m', 'server.desktop_entrypoint'], cwd=root, env=env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    except OSError:
        raise LaunchError('startup_failed') from None
    LOG.info('backend_start pid=%d', child.pid)
    return child


def ensure_ready(root=APP_ROOT, port=PORT, *, timeout=45, check=probe, start=start_backend,
                 prepare=prepare_frontend, clock=time.monotonic, pause=time.sleep):
    root = Path(root).resolve()
    if not bundle_ready(root):
        prepare(root)
    deadline = clock()+timeout
    child = None
    # A slow existing listener is waited on; it is never replaced or multiplied.
    first = check(root, port)
    if first['state']=='other':
        raise LaunchError('address_in_use')
    if first['state']=='stopped':
        child = start(root, port)
    state = first
    while True:
        if state['state']=='other':
            raise LaunchError('address_in_use')
        if state['state']=='ready':
            try:
                page = local_get(f'http://127.0.0.1:{port}/?personal_view=today')
                expected = (root/'dist/index.html').read_bytes()
                if len(page)>32768 or hashlib.sha256(page).digest()!=hashlib.sha256(expected).digest():
                    raise LaunchError('frontend_failed')
            except (OSError, URLError):
                raise LaunchError('frontend_failed') from None
            LOG.info('ready mode=%s pid=%s', 'started' if child else 'reused', state.get('process_id'))
            return {'url': f'http://127.0.0.1:{port}/?personal_view=today',
                    'mode': 'started' if child else 'reused', 'process_id': state.get('process_id')}
        if child and child.poll() not in {None, 0}:
            raise LaunchError('startup_failed')
        if clock()>=deadline:
            raise LaunchError('startup_timeout')
        pause(.2)
        state = check(root, port)


class LauncherMutex:
    def __init__(self, root=APP_ROOT):
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        ctypes.set_last_error(0)
        self.handle = self.kernel.CreateMutexW(None, False, 'Local\\AttentionDeskLauncher-'+workspace_id(root))
        if not self.handle:
            raise LaunchError('unexpected')
        self.acquired = ctypes.get_last_error()!=183

    def __enter__(self):
        return self.acquired

    def __exit__(self, *args):
        self.kernel.CloseHandle(self.handle)


def open_home(url):
    try:
        os.startfile(url)
    except OSError:
        raise LaunchError('browser_failed') from None
    LOG.info('browser_open')


def show_error(code):
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user.MessageBoxW(None, MESSAGES.get(code, MESSAGES['unexpected'])+'\n\n启动日志：项目 data\\attention-desk\\logs\\launcher.log',
                     'Attention Desk', 0x30 | 0x10000)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Attention Desk Windows Launcher')
    parser.add_argument('--check', action='store_true', help='Print saved service readiness; do not launch or open a browser')
    parser.add_argument('--port', type=int, choices=range(1, 65536), metavar='PORT', default=PORT)
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(probe(port=args.port)))
        return 0
    try:
        logs = APP_ROOT.parent/'data/attention-desk/logs'
        logs.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(logs/'launcher.log', maxBytes=500_000, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        LOG.addHandler(handler); LOG.setLevel(logging.INFO)
        with LauncherMutex() as acquired:
            if not acquired:
                LOG.info('launch_coalesced')
                return 0
            result = ensure_ready(port=args.port)
            open_home(result['url'])
        return 0
    except LaunchError as error:
        LOG.error('launch_failed code=%s', error.code)
        show_error(error.code)
    except Exception as error:
        # Log categories only: never echo environment variables or API secrets.
        LOG.error('launch_failed code=unexpected kind=%s', type(error).__name__)
        show_error('unexpected')
    return 1


if __name__=='__main__':
    raise SystemExit(main())
