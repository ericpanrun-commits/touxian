"""Local-only lifecycle for a separate, exact-interface LAN listener."""
from __future__ import annotations

import io
import json
import os
import hashlib
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
        self.notes_qr_bytes = self.notes_setup_id = None
        self.revision = 0
        self.message = ''
        self.cached_network, self.network_checked = None, 0
        self.settings_path = personal.path.with_name('collector-settings.json')
        self.port, self.interface_id = 8473, None
        self.notes_auto=None
        try:
            saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if set(saved) <= {'port', 'interface_id','notes_auto'} and type(saved.get('port')) is int and 1024 <= saved['port'] <= 65535:
                self.port, self.interface_id = saved['port'], saved.get('interface_id')
                policy=saved.get('notes_auto')
                if isinstance(policy,dict) and policy.get('scope_version')=='notes-private-wifi-v1' and policy.get('confirmation_text')=='允许上述范围，减少重配':
                    self.notes_auto=policy
                    if policy.get('enabled'):self.authority.restore_notes(personal.path.with_name('collector-notes-verifier.json'))
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def _save_settings(self):
        value={'interface_id':self.interface_id,'port':self.port}
        if self.notes_auto is not None:value['notes_auto']=self.notes_auto
        temporary=self.settings_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temporary.replace(self.settings_path)

    @staticmethod
    def note_network(entry):
        if entry.get('profile')!='Private' or entry.get('wireless') is not True or not entry.get('network_id') or not entry.get('interface_guid'):
            raise CollectorError('private_wifi_required','请选择已连接的私人 Wi-Fi；其他网络不会自动接收笔记。',409)
        return {k:entry[k] for k in ['id','address','interface_guid','network_id']}

    def _network_unverified(self,data,expected):
        if data.get('inspection_failed'):return True
        for entry in data['interfaces']:
            if entry.get('id')!=expected.get('id') or entry.get('address')!=expected.get('address'):continue
            automatic=bool(self.notes_auto and self.notes_auto.get('enabled'))
            if automatic:
                allowed=self.notes_auto['network']
                if any(entry.get(k) and entry[k]!=allowed.get(k) for k in ['interface_guid','network_id']):return False
            if entry.get('profile')=='Unknown':return True
            if automatic and entry.get('profile')=='Private' and entry.get('wireless') is True and (not entry.get('network_id') or not entry.get('interface_guid')):return True
        return False

    def authorize_notes_auto(self,enabled,confirmation_source,confirmation_text):
        if enabled and confirmation_text!='允许上述范围，减少重配':raise CollectorError('consent_required','请明确允许90天笔记连接与当前私人Wi-Fi上的自动接收。',403)
        with self.lock:
            if not enabled:
                self.stop(False);return self.status()
            entry=select_interface(self.network(force=True),self.interface_id)
            binding=self.note_network(entry)
            if self.bound and self.note_network(self.bound)!=binding:raise CollectorError('network_conflict','当前接收网络不同，请停止后选择私人Wi-Fi。',409)
            from .security import timestamp,utc_now
            self.notes_auto={'enabled':True,'scope_version':'notes-private-wifi-v1','confirmation_source':confirmation_source,
                'confirmation_text':confirmation_text,'confirmed_at':timestamp(utc_now()),'network':binding,'maximum_days':90}
            self._save_settings()
            self.authority.restore_notes(self.personal.path.with_name('collector-notes-verifier.json'))
            if not self.server or not self.thread.is_alive():self.enable(entry['id'],self.port)
            return self.create_notes_setup()

    def resume_notes(self):
        if not self.notes_auto or not self.notes_auto.get('enabled') or not self.authority.notes_ready():return
        try:
            expected=self.notes_auto['network']
            data=self.network(force=True)
            if self._network_unverified(data,expected):
                self.message='暂时无法确认私人 Wi-Fi，笔记接收已暂停，确认原网络后会自动恢复。'
                self._start_network_watch(expected)
                return
            entries=[e for e in data['interfaces'] if e.get('wireless') is True and e.get('profile')=='Private' and self.note_network(e)==expected]
            if len(entries)!=1:raise CollectorError('network_changed','网络已变化，笔记接收保持关闭。',409)
            self.enable(entries[0]['id'],self.port)
        except (CollectorError,KeyError) as error:
            self.stop(False,reason='network_changed');self.message=str(error) if isinstance(error,CollectorError) else '网络关联缺失，手机收集保持关闭。'

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
                self.authority.notes_path=None
                if self.notes_auto:self.notes_auto['enabled']=False;self._save_settings()
                self.bound = self.origin = self.qr_bytes = None
                self.stop_watch.set()
                if self.listener:
                    self.listener.close()
                self.server = self.thread = self.listener = None
            anywhere=getattr(self,'anywhere',None)
            notes_setup=anywhere.active_setup() if anywhere else None
            return {'enabled': running, 'port': self.port, 'preferred_interface_id': self.interface_id,
                    'interfaces': network['interfaces'], 'firewall': network['firewall'],
                    'address': self.bound['address'] if running else None, 'public_base_url': self.origin if running else None,
                    'collector_url': self.origin + '/collect' if running else None,
                    'message': self.message or network.get('message', ''), 'revision': self.revision,
                    'anywhere':self.anywhere.public() if getattr(self,'anywhere',None) else None,
                    'anywhere_setup_id':notes_setup['id'] if notes_setup else None,
                    'notes_shortcut_ready':self.authority.notes_ready(),'notes_setup_id':self.notes_setup_id,
                    'notes_auto_enabled':bool(self.notes_auto and self.notes_auto.get('enabled')),'notes_maximum_days':90 if self.notes_auto and self.notes_auto.get('enabled') else 7, **self.authority.public()}

    def create_notes_setup(self):
        with self.lock:
            if not self.server or not self.thread.is_alive():
                raise CollectorError('disabled','请先启用现有手机收集，再配置笔记分享。',409)
            value=self.authority.issue_notes()
            qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=6,border=4)
            qr.add_data(self.origin+'/notes-setup#key='+value+('&days=90' if self.notes_auto and self.notes_auto.get('enabled') else ''));qr.make(fit=True)
            output=io.BytesIO();qr.make_image(image_factory=qrcode.image.svg.SvgPathFillImage).save(output)
            self.notes_qr_bytes=output.getvalue();self.notes_setup_id=hashlib.sha256(value.encode()).hexdigest()[:16]
            return self.status()

    def notes_qr(self,setup_id):
        with self.lock:
            if not self.notes_qr_bytes or setup_id!=self.notes_setup_id or not self.authority.notes_ready():
                raise CollectorError('notes_setup_unavailable','请先生成笔记分享连接。',409)
            return self.notes_qr_bytes

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
            self.authority.notes_path=None
            if self.notes_auto:self.notes_auto['enabled']=False;self._save_settings()
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
            if self.notes_auto and self.notes_auto.get('enabled'):
                try:authorized=self.note_network(entry)==self.notes_auto['network']
                except (CollectorError,KeyError):authorized=False
                if not authorized:
                    self.stop(False,reason='network_changed')
                    raise CollectorError('network_changed','当前网络与已允许的私人 Wi-Fi 不同，笔记接收保持关闭。',409)
            origin = public_base_url(f'http://{entry["address"]}:{port}')
            app = create_app(self.service, self.authority, origin, self.app_root,
                             notes_setup=lambda nonce:self.anywhere.setup_connection(nonce) if getattr(self,'anywhere',None) else None)
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
                self._save_settings()
            except OSError:
                self.message = '手机收集已启用，但接口偏好未保存。重启后请重新选择。'
            self._start_network_watch(entry)
            return self.status()

    def _start_network_watch(self, expected):
        self.stop_watch = threading.Event()
        self.watcher = threading.Thread(target=self._watch_network, args=(self.stop_watch, dict(expected)), name='attention-mobile-network', daemon=True)
        self.watcher.start()

    def _watch_network(self, stop_event, expected=None):
        with self.lock:
            expected=dict(expected or self.bound or {})
        while not stop_event.wait(20):
            data = self.network(force=True)
            with self.lock:
                if stop_event.is_set() or stop_event is not self.stop_watch:
                    return
                automatic=bool(self.notes_auto and self.notes_auto.get('enabled'))
                if automatic and not self.authority.notes_ready():
                    self.stop(False,reason='notes_expired')
                    self.message='笔记分享连接已到期，请重新配置。'
                    return
                if self._network_unverified(data,expected):
                    # Close the listener before retrying. An unavailable inspection
                    # is not evidence that the owner changed the allowed network.
                    self.stop(False,reason='network_unverified')
                    self.message='暂时无法确认私人 Wi-Fi，手机接收已暂停。'+('确认原网络后会自动恢复。' if automatic else '请检查网络后重新启用。')
                    if not automatic:return
                    continue
                if automatic:
                    try:valid=any(i.get('wireless') is True and i.get('profile')=='Private' and self.note_network(i)==self.notes_auto['network'] for i in data['interfaces'])
                    except (CollectorError,KeyError,TypeError):valid=False
                else:
                    valid=any(i['id']==expected.get('id') and i['address']==expected.get('address') and i['profile']=='Private' for i in data['interfaces'])
                if not valid:
                    self.stop(False, reason='network_changed')
                    self.message = '网络已变化，手机收集已停止。请选择当前局域网后重新启用。'
                    return
                if not self.server:
                    try:self.enable(self.interface_id,self.port)
                    except CollectorError:
                        if self.notes_auto and self.notes_auto.get('enabled') and self.authority.notes_ready() and self._network_unverified(self.cached_network,expected):
                            self.message='暂时无法确认私人 Wi-Fi，手机接收已暂停，确认原网络后会自动恢复。'
                            continue
                        self.stop(False,reason='resume_failed')
                        self.message='手机接收恢复失败，请检查网络后重新启用。'
                    return

    def stop(self, inspect=True, *, reason='collector_stopped'):
        with self.lock:
            preserve=reason in {'collector_shutdown','network_unverified'} and bool(self.notes_auto and self.notes_auto.get('enabled'))
            if not preserve and self.notes_auto:
                self.notes_auto['enabled']=False;self._save_settings()
            self.authority.revoke(reason,preserve_notes=preserve)
            if not preserve:self.authority.notes_path=None
            if not (preserve and reason=='network_unverified'):
                self.notes_qr_bytes=self.notes_setup_id=None
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
        await asyncio.to_thread(self.stop, False, reason='collector_shutdown')
        await self.service.close()
