"""Production adapter: selective DB worker -> existing Astra observation pipeline."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

from ..personal_store import PersonalError
from .models import Observation, ObservedMessage

ROOT = Path(__file__).resolve().parents[2]
COMMIT = 'd322c4a9b1124228820bab363e5676016f188fc6'


class ReadOnlyWeChatDBAdapter:
    is_real = True
    is_database = True

    def __init__(self, *, data_directory=None, python=None, runner=None, report_path=None):
        self.data_directory = Path(data_directory) if data_directory else None
        self.python = Path(python) if python else ROOT / 'artifacts' / 'wechat-db0' / 'venv' / 'Scripts' / 'python.exe'
        self.runner = runner or subprocess.run
        self.report_path = Path(report_path) if report_path else ROOT / 'artifacts/wechat-db0/results/report.json'
        self.last_metrics = {}

    @property
    def certified(self):
        try:
            report = json.loads(self.report_path.read_text(encoding='utf-8'))
            baseline = (report['DB_READ'] == 'PASS' and report['commit'] == COMMIT
                    and report.get('sender_prefix_mismatches') == 0 and report.get('count') == 20
                    and report.get('unauthorized_message_bodies_read') == 0 and self.python.is_file())
            if report.get('wechat_version') == '4.1.15.13' and 'groups' not in report:
                return baseline  # Preserve the original certificate for rollback.
            return (baseline and bool(report.get('groups'))
                    and all(group.get('PASS') is True for group in report['groups'])
                    and report.get('stable_ids_unique') is True
                    and report.get('stable_ids_repeatable') is True
                    and report.get('chronological_output') is True
                    and all(report.get('source_files', {}).get(name) == hashlib.sha256(
                        (ROOT / 'server/wechat_collect' / name).read_bytes()).hexdigest()
                        for name in ('db_reader.py', 'replica_db_core.py', 'sender.py', 'db_media.py')))
        except (KeyError, OSError, ValueError):
            return False

    def _call(self, target, config, cursor, *, action, policy, cancelled, expected_scope=None, expected_chatroom=None):
        policy.require(operation='database', maximum_seconds=60)
        if target.name not in config.allowed_chats:
            raise PersonalError('群未列入 allowed_chats，未读取。', 'chat_not_allowlisted', 403)
        selected=getattr(target,'chatroom_id',None)
        if selected and selected not in getattr(config,'allowed_chatrooms',[]):
            raise PersonalError('群 ID 未获得授权，未读取。','chat_not_allowlisted',403)
        if selected and expected_chatroom and selected!=expected_chatroom:
            raise PersonalError('群绑定不一致，未读取。','binding_mismatch',409)
        # Preserve and pin pre-existing name-based grants to their verified
        # binding. A changed/duplicated display name cannot select another room.
        selected=selected or expected_chatroom
        room_grants=list(getattr(config,'allowed_chatrooms',[]))
        if expected_chatroom and expected_chatroom not in room_grants:
            room_grants.append(expected_chatroom)
        if not self.certified:
            raise PersonalError('本机数据库读取尚未通过验证。', 'database_not_verified', 409)
        if cancelled():
            raise PersonalError('采集已停止。', 'cancelled', 409)
        request = {'action': action, 'chat': target.name, 'allowed_chats': config.allowed_chats,
                   'selected_chatroom': selected, 'allowed_chatrooms': room_grants,
                   'expected_account_scope': expected_scope,
                   'cursor': cursor, 'workdir': str(ROOT / 'artifacts' / 'wechat-db-runtime'),
                   'verified_version': json.loads(self.report_path.read_text(encoding='utf-8'))['wechat_version']}
        try:
            completed = self.runner([str(self.python), '-m', 'server.wechat_collect.db_worker'],
                                    input=json.dumps(request, ensure_ascii=False), text=True, encoding='utf-8',
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(ROOT), timeout=55,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if len(completed.stdout) > 12_000_000:
                raise ValueError('oversized protocol')
            value = json.loads(completed.stdout)
            if not value['ok']:
                raise PersonalError('微信数据库暂时无法读取；游标未推进。', value.get('code', 'database_read_error'), 503)
            if cancelled():
                raise PersonalError('采集已停止。', 'cancelled', 409)
            policy.require(operation='database')
            return value['result']
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise PersonalError('微信数据库读取进程未完成；游标未推进。', 'database_worker_failed', 503) from None

    def resolve(self, target, *, policy, cancelled=lambda: False):
        return self._call(target, policy.config, None, action='resolve', policy=policy, cancelled=cancelled)

    def search(self, query, *, policy, offset=0, cancelled=lambda:False, chatroom_id=None):
        # Discovery is independent of enabled collection and existing grants.
        if policy.clock().astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).hour<6:
            raise PersonalError('夜间暂停微信数据库访问，06:00 后可搜索。','quiet',409)
        if not self.certified:
            raise PersonalError('本机数据库尚未通过验证。','database_not_verified',409)
        if cancelled():
            raise PersonalError('搜索已取消。','cancelled',409)
        request={'query':query,'offset':offset,'chatroom_id':chatroom_id,'workdir':str(ROOT/'artifacts/wechat-db-search'),
                 'verified_version':json.loads(self.report_path.read_text(encoding='utf-8'))['wechat_version']}
        try:
            result=self.runner([str(self.python),'-m','server.wechat_collect.group_search_worker'],input=json.dumps(request,ensure_ascii=False),text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,cwd=str(ROOT),timeout=55,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            value=json.loads(result.stdout)
            if not value['ok']:
                raise PersonalError('暂时无法读取群名，请确认微信已登录后重试。',value.get('code','metadata_search_failed'),503)
            return value['result']
        except (OSError,ValueError,subprocess.TimeoutExpired):
            raise PersonalError('群名搜索未完成，请稍后重试。','metadata_search_failed',503) from None

    def read(self, chat, binding, cursor, *, policy, override=None, cancelled=lambda: False):
        value = self._call(chat, policy.config, cursor.get('database') if cursor else None,
                           action='increment', policy=policy, cancelled=cancelled,expected_scope=binding.get('account_scope') if binding else None,
                           expected_chatroom=binding.get('internal_chat_id') if binding else None)
        if not binding or binding['internal_chat_id'] != value['cursor']['internal_chat_id'] or binding['account_scope'] != value['cursor']['account_scope']:
            raise PersonalError('数据库账号或群绑定不一致，未保存。', 'binding_mismatch', 409)
        messages = []
        for row in value['messages']:
            reference = row.get('media_reference')
            if reference and self.data_directory:
                for field in ('evidence_path','encrypted_evidence_path'):
                    if not reference.get(field):
                        continue
                    original = Path(reference[field]).resolve()
                    if not original.is_relative_to((ROOT / 'artifacts/wechat-db-runtime/media').resolve()):
                        raise PersonalError('媒体依据不属于本地暂存。', 'invalid_media_path', 409)
                    if original.stat().st_size > 50_000_000:
                        raise PersonalError('媒体超过本地依据大小上限。', 'media_too_large', 409)
                    with original.open('rb') as stream:
                        digest=hashlib.file_digest(stream,'sha256').hexdigest()
                    destination=self.data_directory/'files'/digest[:2]/(digest+original.suffix)
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    if not destination.exists():
                        temporary=destination.with_suffix(destination.suffix+'.tmp')
                        shutil.copyfile(original,temporary)
                        temporary.replace(destination)
                    reference[field]=str(destination)
            path = row.get('media_path')
            if path and self.data_directory:
                source = Path(path).resolve()
                if not source.is_relative_to((ROOT / 'artifacts/wechat-db-runtime/media').resolve()):
                    raise PersonalError('媒体路径不属于本地暂存。', 'invalid_media_path', 409)
                spool = self.data_directory / 'wechat-spool'
                spool.mkdir(parents=True, exist_ok=True)
                destination = spool / source.name
                shutil.copyfile(source, destination)
                path = str(destination)
            kind = 'image' if row['type'] == '图片' else 'system' if row['type'] == '系统消息' else ('file' if reference and reference['kind'] == 'file' else 'link') if reference else 'text'
            messages.append(ObservedMessage(row['content'], row.get('sender') or '', kind, row['create_time'], 'reliable',
                                            json.dumps(reference, ensure_ascii=False, sort_keys=True) if reference else '',
                                            path, 'reliable' if row.get('sender_username') else 'unknown', row))
        self.last_metrics = {key: value[key] for key in ('metadata_queries', 'message_body_queries', 'messages_sent', 'unauthorized_message_bodies_read')}
        return Observation(chat.id, value['cursor']['account_scope'], tuple(messages), not value['has_more'],
                           True, 1, 'GO', (), value['cursor'])
