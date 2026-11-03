"""Normal-user diagnostic while the separately approved HKLM lease is active.

No registry writes, automatic login, keyboard input, OCR or network model calls.
The administrator helper independently rolls back on completion or lease expiry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import sys
import time
import winreg

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'scripts'))
from pyweixin_environment_spike import CurrentUserAccessibility, temporary_accessibility
from wechat_qt_accessibility_pass import graceful_restart, probe
from server.wechat_collect.config import WeChatConfig
from server.wechat_collect.scheduler import SchedulerPolicy

ROOT = PROJECT / 'artifacts/pyweixin-spike/qt-accessibility-pass/force-uia-controlled-test'
REGISTRY_PATH = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Accessibility'
CHAT_NAME = '26车辆2班（公告群）'


def helper_is_running(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = w.DWORD()
        name = ctypes.create_unicode_buffer(32768)
        length = w.DWORD(len(name))
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259
                    and kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length))
                    and Path(name.value).name.lower() == 'pwsh.exe')
    finally:
        kernel.CloseHandle(handle)


def read_force_uia(view):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_PATH, 0, winreg.KEY_READ | view) as key:
            result = {'key_exists': True, 'value_exists': False}
            try:
                value, kind = winreg.QueryValueEx(key, 'ForceUIAForQt')
                result.update(value_exists=True, value=value, kind=kind)
            except FileNotFoundError:
                pass
            return result
    except FileNotFoundError:
        return {'key_exists': False, 'value_exists': False}


def require_active_lease():
    status = json.loads((ROOT / 'status.json').read_text(encoding='utf-8-sig'))
    if status['state'] != 'applied':
        raise RuntimeError('Approved registry experiment is not active')
    if not helper_is_running(status['pid']):
        raise RuntimeError('The registry rollback helper is not running')
    expires = datetime.fromisoformat(status['lease_expires_at'])
    if (expires - datetime.now(timezone.utc)).total_seconds() < 100:
        raise RuntimeError('Insufficient time remains in the registry experiment')
    state = read_force_uia(winreg.KEY_WOW64_64KEY)
    if not (state.get('value') == 1 and state.get('kind') == winreg.REG_DWORD):
        raise RuntimeError('ForceUIAForQt is not currently REG_DWORD 1')
    return state


def write_report(report):
    destination = ROOT / 'diagnostic-report.json'
    temporary = ROOT / 'diagnostic-report.tmp'
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(destination)


def main():
    policy = SchedulerPolicy(WeChatConfig(enabled=True))
    policy.require(operation='uia', maximum_seconds=240)
    ROOT.mkdir(parents=True, exist_ok=True)
    backend = CurrentUserAccessibility()
    report = {'status': 'starting', 'messages_sent': 0, 'drafts_modified': 0,
              'group_settings_modified': 0, 'deepseek_calls': 0, 'vision_calls': 0,
              'ocr_calls': 0, 'screenshots': 0, 'hklm_writes_by_normal_user': 0,
              'automatic_login': False, 'api_results': {}}
    (ROOT / 'user-settings-before.json').write_text(json.dumps(backend.read()), encoding='utf-8')
    try:
        report['force_uia_during_test'] = require_active_lease()
        with temporary_accessibility(backend, policy, report):
            before = probe('with-hklm-before-restart', ROOT, policy)
            if not before['windows']:
                raise RuntimeError('No verified WeChat main window is available')
            window = max(before['windows'], key=lambda row: row['width'] * row['height'])
            require_active_lease()
            report['restart'] = graceful_restart(window['pid'], before['processes'], policy)
            write_report(report)
            if report['restart']['status'] != 'restarted':
                raise RuntimeError('Normal WeChat restart did not complete')
            time.sleep(3)
            first = probe('with-hklm-after-restart', ROOT, policy)
            report['status'] = 'waiting_for_manual_login'
            report['login_ui_observed'] = any(row['class_name'] == 'mmui::LoginWindow' for row in first['windows'])
            write_report(report)
            print(json.dumps({'status': report['status'], 'login_ui_observed': report['login_ui_observed']}), flush=True)
            # A human logs in. No login control is ever invoked by this process.
            until = time.monotonic() + 15 * 60
            current = first
            sample = 0
            while time.monotonic() < until:
                require_active_lease()
                candidates = [row for row in current['windows']
                              if row['width'] >= 800 and row['height'] >= 600
                              and row['class_name'] != 'mmui::LoginWindow']
                if candidates:
                    window = max(candidates, key=lambda row: row['width'] * row['height'])
                    break
                time.sleep(10)
                sample += 1
                current = probe(f'login-wait-{sample:03d}', ROOT, policy)
            else:
                raise TimeoutError('Manual login was not completed within the diagnostic lease')
            require_active_lease()
            current = probe('with-hklm-after-manual-login', ROOT, policy)
            report['status'] = 'retesting_read_apis'
            report['logged_in_roots'] = current['windows']
            write_report(report)
            isolated = PROJECT / 'artifacts/pyweixin-spike/venv/Scripts/python.exe'
            for method in ('pull_messages', 'check_new_messages'):
                require_active_lease()
                policy.require(operation='uia', maximum_seconds=40)
                destination = ROOT / method
                completed = subprocess.run(
                    [str(isolated), '-X', 'utf8', str(PROJECT / 'scripts/pyweixin_readonly_spike.py'),
                     '--method', method, '--chat-name', CHAT_NAME,
                     '--window-handle', str(window['hwnd']), '--output', str(destination)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
                require_active_lease()
                result_path = destination / 'result.json'
                if completed.returncode or not result_path.exists():
                    raise RuntimeError('Read-only API diagnostic failed to produce a record')
                result = json.loads(result_path.read_text(encoding='utf-8'))
                report['api_results'][method] = {name: result.get(name) for name in (
                    'status', 'actual_api_invoked', 'connected', 'error_type', 'guards', 'result')}
                print(json.dumps({'method': method, **report['api_results'][method]}, ensure_ascii=False), flush=True)
                write_report(report)
            report['force_uia_after_measurements'] = require_active_lease()
            report['status'] = 'measurements_complete'
    except Exception as exc:
        report['status'] = 'not_completed'
        report['error_type'] = type(exc).__name__
        report['error'] = str(exc)
    finally:
        # The helper also rolls back if this process crashes before requesting it.
        (ROOT / 'finish.flag').write_text('Restore the recorded original registry state.\n', encoding='utf-8')
        report['rollback_requested'] = True
        write_report(report)
        print(json.dumps({key: report.get(key) for key in (
            'status', 'error_type', 'error', 'before', 'active', 'restored', 'restoration_matches',
            'rollback_requested')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
