"""Run synthetic analysis regression with outbound sockets denied in this process."""
import os
import ipaddress
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ['ATTENTION_DEEPSEEK_API_KEY'] = 'offline-test-key'
os.environ['DEEPSEEK_API_KEY'] = 'offline-test-key'


def forbidden(*args, **kwargs):
    raise AssertionError('Offline regression forbids network connections')


def local_only(original):
    def guarded(sock, address):
        # Windows asyncio implements socketpair using numeric loopback sockets.
        try:
            allowed = ipaddress.ip_address(address[0]).is_loopback
        except (ValueError, TypeError, IndexError):
            allowed = False
        if not allowed:
            forbidden()
        return original(sock, address)
    return guarded


socket.socket.connect = local_only(socket.socket.connect)
socket.socket.connect_ex = local_only(socket.socket.connect_ex)
socket.create_connection = forbidden
socket.getaddrinfo = forbidden

import pytest

raise SystemExit(pytest.main([
    '-q', '-p', 'no:cacheprovider',
    'server/tests_wechat/test_deepseek_destination.py',
    'server/tests_wechat/test_pipeline.py',
    'server/tests_wechat/test_cost_review.py',
    'server/tests_wechat/test_backlog_cost_plan.py',
    'server/tests_wechat/test_attention_quality.py',
    'server/tests_wechat/test_recovery_progress.py',
    'server/test_recovery_controls.py',
    *sys.argv[1:],
]))
