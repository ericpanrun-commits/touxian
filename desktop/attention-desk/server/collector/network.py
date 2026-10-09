"""Physical IPv4 enumeration and firewall inspection; never change either."""
import ipaddress
import json
import shutil
import subprocess
import sys
from pathlib import Path
from .security import CollectorError


def lan_address(value):
    try:
        ip = ipaddress.IPv4Address(value)
        return any(ip in ipaddress.ip_network(network) for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    except ValueError:
        return False


def discover(port=8473):
    if sys.platform != 'win32':
        return {'interfaces': [], 'firewall': {'checked': False}, 'message': '当前平台无法检测 Windows 物理网卡。'}
    executable = shutil.which('pwsh') or shutil.which('powershell')
    try:
        command = [executable, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(Path(__file__).with_suffix('.ps1')), '-Port', str(port)]
        result = subprocess.run(command, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError('network_inspection')
        data = json.loads(result.stdout.decode('utf-8-sig'))
        data['interfaces'] = [entry for entry in data['interfaces'] if lan_address(entry['address'])]
        try:
            firewall = subprocess.run([*command, '-FirewallOnly'], capture_output=True, timeout=4, creationflags=subprocess.CREATE_NO_WINDOW)
            if firewall.returncode == 0:
                data['firewall'] = json.loads(firewall.stdout.decode('utf-8-sig'))['firewall']
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            pass  # Inaccessible/slow diagnostics must not hide a valid current LAN.
        return data
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return {'interfaces': [], 'firewall': {'checked': False}, 'message': '暂时无法检查网络，请使用普通 Windows 用户启动工作台后重试。'}


def select_interface(data, selected=None):
    interfaces = data['interfaces']
    if not interfaces:
        raise CollectorError('no_lan', '未连接可用局域网。', 409)
    candidates = [entry for entry in interfaces if entry['id'] == selected] if selected else interfaces
    if len(candidates) != 1:
        raise CollectorError('choose_interface', '请在电脑选择要使用的局域网接口。', 409)
    entry = candidates[0]
    if entry['profile'] != 'Private':
        raise CollectorError('private_network_required', '请先将所选网络设为专用网络，再启用手机收集。', 409)
    if not lan_address(entry['address']):
        raise CollectorError('no_lan', '未连接可用局域网。', 409)
    return entry
