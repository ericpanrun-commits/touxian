"""Only temporary SQLite and synchronous simulated network boundaries."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import subprocess
import sys
import threading

import pytest

from server.cloud_sync.wechat_dispatch_authority import (
    DispatchAuthority, DispatchDenied, Grant, locked_source,
)


@pytest.fixture
def rig(tmp_path):
    authority = DispatchAuthority(tmp_path / 'grants.sqlite', clock=lambda: 10)
    authority.initialize()
    source = tmp_path / 'source.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE content(value BLOB)')
        db.execute('INSERT INTO content VALUES (?)', (b'approved',))
    return authority, source


def grant(authority, *, once=False, identity='g'):
    descriptor = Grant(identity, 'a' * 64, 20, once)
    authority.import_verified(descriptor, lambda exact: exact == descriptor)
    return descriptor


def send(authority, source, *, accept, attempt='a', payload=b'approved'):
    return authority.handoff('g', 'a' * 64, attempt, payload,
        protected_snapshot=lambda: locked_source(source,
            lambda db: db.execute('SELECT value FROM content').fetchone()[0]), accept=accept)


def test_unverified_grant_cannot_be_imported(rig):
    authority, _ = rig
    with pytest.raises(DispatchDenied, match='independent_approval'):
        authority.import_verified(Grant('g', 'a' * 64, 20, False), lambda _: False)
    with pytest.raises(DispatchDenied, match='grant_not_current'):
        authority.reserve('g', 'a' * 64, 'a', b'approved')


@pytest.mark.parametrize('once', [False, True])
def test_revocation_after_reservation_prevents_handoff(rig, once):
    authority, source = rig
    grant(authority, once=once)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    # Independently opened owner, as after process restart/reconnection.
    other = DispatchAuthority(authority.path, clock=lambda: 10)
    other.revoke('g')
    sent = []
    with pytest.raises(DispatchDenied, match='grant_not_current'):
        send(authority, source, accept=sent.append)
    assert sent == []


def test_changed_source_never_reaches_transport(rig):
    authority, source = rig
    grant(authority)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    with sqlite3.connect(source) as db:
        db.execute('UPDATE content SET value=?', (b'changed',))
    sent = []
    with pytest.raises(DispatchDenied, match='content_changed'):
        send(authority, source, accept=sent.append)
    assert sent == []
    assert authority.attempt_state('a') == 'unconfirmed'


def test_content_and_authority_remain_locked_through_actual_acceptance(rig):
    authority, source = rig
    grant(authority)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    sent = []
    def accept(payload):
        # These independent connections do not use any cooperative Python lock.
        with sqlite3.connect(source, timeout=0) as writer:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                writer.execute('UPDATE content SET value=?', (b'changed',))
        other = DispatchAuthority(authority.path, clock=lambda: 10)
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            other.revoke('g')
        sent.append(payload)
    send(authority, source, accept=accept)
    authority.revoke('g')  # completes only after acceptance, cannot retract it
    with sqlite3.connect(source) as db:
        db.execute('UPDATE content SET value=?', (b'changed',))
    assert sent == [b'approved']


@pytest.mark.parametrize('once', [False, True])
def test_unknown_handoff_cannot_replay_attempt_after_restart(rig, once):
    authority, source = rig
    grant(authority, once=once)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    sent = []
    def lost(payload):
        sent.append(payload)
        raise OSError('synthetic lost response')
    with pytest.raises(OSError):
        send(authority, source, accept=lost)
    reopened = DispatchAuthority(authority.path, clock=lambda: 10)
    assert reopened.attempt_state('a') == 'unconfirmed'
    with pytest.raises(DispatchDenied, match='attempt_not_available'):
        send(reopened, source, accept=sent.append)
    if once:
        with pytest.raises(DispatchDenied, match='grant_consumed'):
            reopened.reserve('g', 'a' * 64, 'b', b'approved')
    assert sent == [b'approved']


def test_single_use_reservation_is_durable_before_handoff(rig):
    authority, _ = rig
    grant(authority, once=True)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    reopened = DispatchAuthority(authority.path, clock=lambda: 10)
    with pytest.raises(DispatchDenied, match='grant_consumed'):
        reopened.reserve('g', 'a' * 64, 'b', b'approved')
    with pytest.raises(sqlite3.IntegrityError):
        grant(reopened, once=True)  # cannot restore consumption by re-import


def test_expiry_while_building_prevents_acceptance(rig):
    authority, _ = rig
    grant(authority)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    @contextmanager
    def expires():
        authority.clock = lambda: 21
        yield b'approved'
    sent = []
    with pytest.raises(DispatchDenied, match='grant_not_current'):
        authority.handoff('g', 'a' * 64, 'a', b'approved',
                          protected_snapshot=expires, accept=sent.append)
    assert sent == []


def test_reconnect_wait_does_not_hold_grant_and_revocation_wins(rig):
    authority, source = rig
    grant(authority)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    connecting, connected = threading.Event(), threading.Event()
    sent = []
    def reconnect():
        connecting.set()
        assert connected.wait(2)
        with pytest.raises(DispatchDenied, match='grant_not_current'):
            send(authority, source, accept=sent.append)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(reconnect)
        assert connecting.wait(2)
        authority.revoke('g')
        connected.set()
        task.result(timeout=3)
    assert sent == []


def test_changed_binding_or_bytes_cannot_reuse_reservation(rig):
    authority, source = rig
    grant(authority)
    with pytest.raises(DispatchDenied, match='grant_not_current'):
        authority.reserve('g', 'b' * 64, 'a', b'approved')
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    with pytest.raises(DispatchDenied, match='attempt_not_available'):
        send(authority, source, payload=b'changed', accept=lambda _: pytest.fail('sent'))
    with pytest.raises(DispatchDenied, match='invalid_payload'):
        authority.reserve('g', 'a' * 64, 'b', bytearray(b'approved'))


def test_failed_source_lock_never_calls_transport_or_refunds(rig):
    authority, source = rig
    grant(authority, once=True)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    with sqlite3.connect(source) as writer:
        writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            send(authority, source, accept=lambda _: pytest.fail('sent'))
    assert authority.attempt_state('a') == 'unconfirmed'
    with pytest.raises(DispatchDenied, match='grant_consumed'):
        authority.reserve('g', 'a' * 64, 'b', b'approved')


def test_independent_process_revocation_prevents_later_handoff(rig):
    authority, source = rig
    grant(authority)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    script = '''
import sys
from server.cloud_sync.wechat_dispatch_authority import DispatchAuthority
DispatchAuthority(sys.argv[1], clock=lambda: 10).revoke('g')
'''
    result = subprocess.run([sys.executable, '-c', script, str(authority.path)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    with pytest.raises(DispatchDenied, match='grant_not_current'):
        send(authority, source, accept=lambda _: pytest.fail('sent'))


def test_independent_process_cannot_write_source_at_handoff(rig):
    authority, source = rig
    grant(authority)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    script = '''
import sqlite3, sys
with sqlite3.connect(sys.argv[1], timeout=0) as db:
    try:
        db.execute("UPDATE content SET value=x'00'")
    except sqlite3.OperationalError as error:
        assert 'locked' in str(error)
    else:
        raise AssertionError('source changed during handoff')
'''
    sent = []
    def accept(payload):
        result = subprocess.run([sys.executable, '-c', script, str(source)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        sent.append(payload)
    send(authority, source, accept=accept)
    assert sent == [b'approved']


def test_slow_snapshot_cannot_cross_handoff_deadline(rig, monkeypatch):
    import server.cloud_sync.wechat_dispatch_authority as module
    authority, _ = rig
    grant(authority)
    authority.reserve('g', 'a' * 64, 'a', b'approved')
    tick = [0.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: tick[0])
    @contextmanager
    def slow():
        tick[0] = 1.0
        yield b'approved'
    with pytest.raises(DispatchDenied, match='handoff_deadline'):
        authority.handoff('g', 'a' * 64, 'a', b'approved', protected_snapshot=slow,
                          accept=lambda _: pytest.fail('sent after deadline'))
    assert authority.attempt_state('a') == 'unconfirmed'
