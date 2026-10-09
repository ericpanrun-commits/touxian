"""Local-only lifecycle for a separate, exact-interface LAN listener."""
from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
from pathlib import Path
import qrcode
import qrcode.image.svg
import uvicorn
from .app import create_app
from .network import discover, select_interface
from .security import CollectorError, PairingAuthority
from .submissions import SubmissionService
from .safe_logs import record
from .endpoint import public_base_url


class CollectorController:
    def __init__(self, personal, documents, extraction, app_root, *, detector=discover, server_factory=uvicorn.Server):
        self.personal, self.app_root = personal, Path(app_root)
        self.service = SubmissionService(personal, documents, extraction)
        # Control routes and the LAN server thread share this exact authority.
        # The diagnostic file contains metadata only, never credentials.
        self.authority = PairingAuthority(diagnostics_path=personal.path.with_name('collector-pairing-state.json'))
        self.detector, self.server_factory = detector, server_factory
        self.lock = threading.RLock()
        self.server = self.thread = self.listener = self.watcher = None
        self.stop_watch = threading.Event()
        self.bound = None
        self.origin = None
        self.qr_bytes = None
        self.revision = 0
        self.message = ''
        self.cached_network, self.network_checked = None, 0
        self.settings_path = personal.path.with_name('collector-settings.json')
        self.port, self.interface_id = 8473, None
        try:
            saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if set(saved) <= {'port', 'interface_id'} and type(saved.get('port')) is int and 1024 <= saved['port'] <= 65535:
                self.port, self.interface_id = saved['port'], saved.get('interface_id')
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def network(self, force=False):
        with self.lock:
            if force or self.cached_network is None or time.monotonic() - self.network_checked > 20:
                self.cached_network = self.detector(self.port)
                self.network_checked = time.monotonic()
            return self.cached_network

    def status(self):
        with self.lock:
            network = self.network()
            running = bool(self.server and self.thread and self.thread.is_alive())
            if self.server and not running:
                self.authority.revoke('listener_stopped')
                self.bound = self.origin = self.qr_bytes = None
                self.stop_watch.set()
                if self.listener:
                    self.listener.close()
                self.server = self.thread = self.listener = None
            return {'enabled': running, 'port': self.port, 'preferred_interface_id': self.interface_id,
                    'interfaces': network['interfaces'], 'firewall': network['firewall'],
                    'address': self.bound['address'] if running else None, 'public_base_url': self.origin if running else None,
                    'collector_url': self.origin + '/collect' if running else None,
                    'message': self.message or network.get('message', ''), 'revision': self.revision, **self.authority.public()}

    def _regenerate(self):
        token = self.authority.regenerate()
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=4)
        qr.add_data(self.origin + '/pair?token=' + token)
        qr.make(fit=True)
        output = io.BytesIO()
        qr.make_image(image_factory=qrcode.image.svg.SvgPathFillImage).save(output)
        self.qr_bytes, self.revision = output.getvalue(), self.revision + 1

    def regenerate(self):
        with self.lock:
            if not self.server or not self.thread.is_alive():
                raise CollectorError('disabled', '请先启用手机收集。', 409)
            self._regenerate()
            return self.status()

    def revoke_devices(self):
        with self.lock:
            if not self.server or not self.thread.is_alive():
                raise CollectorError('disabled', '请先启用手机收集。', 409)
            self.authority.revoke('devices_revoked')
            self.qr_bytes = None
            return self.status()

    def qr(self, pairing_id):
        with self.lock:
            state = self.authority.public()
            if not self.qr_bytes or not state['pairing_available'] or pairing_id != state['pairing_id']:
                raise CollectorError('pairing_unavailable', '二维码已使用或过期，请重新生成配对。', 409)
            return self.qr_bytes

    def enable(self, interface_id=None, port=8473):
        with self.lock:
            if self.server and self.thread.is_alive():
                raise CollectorError('already_enabled', '手机收集已启用，请先停止再更换接口。', 409)
            reserved = {int(os.getenv('ATTENTION_API_PORT', '8765')), int(os.getenv('ATTENTION_WEB_PORT', '5173'))}
            if type(port) is not int or not 1024 <= port <= 65535 or port in reserved:
                raise CollectorError('invalid_port', '请选择未被桌面前后端使用的 1024–65535 端口。')
            self.port = port
            # Always enumerate again; neither saved settings nor the prior URL holds an IP.
            entry = select_interface(self.network(force=True), interface_id or self.interface_id)
            origin = public_base_url(f'http://{entry["address"]}:{port}')
            app = create_app(self.service, self.authority, origin, self.app_root)
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind((entry['address'], port))
                listener.listen(32)
            except OSError:
                listener.close()
                raise CollectorError('port_unavailable', '端口已被占用或网络地址已变化，请重新检查或更换端口。', 409)
            self.listener, self.bound, self.origin, self.interface_id = listener, dict(entry), origin, entry['id']
            try:
                config = uvicorn.Config(app, host=entry['address'], port=port, access_log=False, log_config=None, log_level='critical',
                                        proxy_headers=False, server_header=False, timeout_keep_alive=5, timeout_graceful_shutdown=3,
                                        limit_concurrency=12, h11_max_incomplete_event_size=8192)
                self.server = self.server_factory(config)
                # Start the invitation's five-minute clock only on an explicit
                # desktop generation action, when the user and phone are ready.
                self.qr_bytes = None
                self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [listener]}, name='attention-mobile-collector', daemon=True)
                self.thread.start()
                deadline = time.monotonic() + 4
                while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.02)
                if not self.server.started:
                    raise RuntimeError('collector_start_failed')
            except Exception as error:
                record(self.personal, 'start', error)
                self.stop(False)
                raise CollectorError('start_failed', '手机收集未能启动，桌面功能仍可使用。', 503)
            self.message = ''
            # Persist preference only; disabled and every capability starts fresh after restart.
            try:
                temporary = self.settings_path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'interface_id': self.interface_id, 'port': self.port}), encoding='utf-8')
                temporary.replace(self.settings_path)
            except OSError:
                self.message = '手机收集已启用，但接口偏好未保存。重启后请重新选择。'
            self.stop_watch = threading.Event()
            self.watcher = threading.Thread(target=self._watch_network, args=(self.stop_watch,), name='attention-mobile-network', daemon=True)
            self.watcher.start()
            return self.status()

    def _watch_network(self, stop_event):
        while not stop_event.wait(20):
            data = self.network(force=True)
            with self.lock:
                if stop_event.is_set() or stop_event is not self.stop_watch:
                    return
                bound = self.bound
                if not bound:
                    return
                valid = any(i['id'] == bound['id'] and i['address'] == bound['address'] and i['profile'] == 'Private' for i in data['interfaces'])
                if not valid:
                    self.stop(False, reason='network_changed')
                    self.message = '网络已变化，手机收集已停止。请选择当前局域网后重新启用。'
                    return

    def stop(self, inspect=True, *, reason='collector_stopped'):
        with self.lock:
            self.authority.revoke(reason)
            self.stop_watch.set()
            server, thread, listener = self.server, self.thread, self.listener
            self.server = self.thread = self.listener = self.bound = self.origin = self.qr_bytes = None
            if server:
                server.should_exit = True
        if thread and thread.ident is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        if listener:
            try:
                listener.close()
            except OSError:
                pass
        return self.status() if inspect else {'enabled': False}

    async def close(self):
        import asyncio
        await asyncio.to_thread(self.stop, False)
        await self.service.close()
