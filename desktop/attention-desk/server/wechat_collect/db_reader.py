"""Selective, read-only use of replica's DB core; never import its automation package.

Upstream: fanyuantaier/wechatauto-replica, d322c4a9b1124228820bab363e5676016f188fc6.
Only replica_db_core.py is loaded. Its logger import is the sole vendor change.
Physical decryption of encrypted shards is permitted; SQL access to other chats is not.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone

UPSTREAM_COMMIT = 'd322c4a9b1124228820bab363e5676016f188fc6'
PROJECT_ROOT = Path(__file__).resolve().parents[2]
METADATA_COLUMNS = {
    'contact': {'id', 'username', 'nick_name', 'remark', 'alias', 'local_type'},
    'SessionTable': {'username', 'last_timestamp', 'sort_timestamp', 'is_hidden', 'unread_count'},
    'Name2Id': {'rowid', 'user_name'},
    'chat_room': {'id', 'username', 'owner'},
    'chatroom_member': {'room_id', 'member_id', 'display_name'},
}


class DBReadError(RuntimeError):
    pass


def wechat_version():
    import psutil
    for process in psutil.process_iter(['name', 'exe']):
        if (process.info['name'] or '').lower() != 'weixin.exe' or not process.info['exe']:
            continue
        version = ctypes.WinDLL('version', use_last_error=True)
        path = ctypes.c_wchar_p(process.info['exe'])
        size = version.GetFileVersionInfoSizeW(path, None)
        if not 0 < size < 1_000_000:
            continue
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, buffer):
            continue
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if version.VerQueryValueW(buffer, ctypes.c_wchar_p('\\'), ctypes.byref(pointer), ctypes.byref(length)):
            numbers = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
            return '.'.join(map(str, (numbers[2] >> 16, numbers[2] & 65535, numbers[3] >> 16, numbers[3] & 65535)))
    raise DBReadError('wechat_version_unavailable')


class QueryBoundary:
    """SQLite authorizer rejects unauthorized tables/preview columns before execution."""
    def __init__(self):
        self.tables = set()
        self.denied_queries = 0
        self.denied_metadata = set()
        self.authorized_body_reads = 0

    def allow(self, internal_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]+@chatroom', internal_id):
            raise DBReadError('invalid_group_id')
        self.tables.add('Msg_' + hashlib.md5(internal_id.encode()).hexdigest())

    def authorize(self, action, first, second, database, source):
        allowed = False
        if action == sqlite3.SQLITE_READ:
            if first in {'sqlite_master', 'sqlite_schema'}:
                allowed = True
            elif first in self.tables:
                allowed = True
                if second in {'message_content', 'compress_content', 'packed_info_data', 'source'}:
                    self.authorized_body_reads += 1
            elif first in METADATA_COLUMNS:
                allowed = (second or '').lower() in {c.lower() for c in METADATA_COLUMNS[first]}
        elif action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}:
            allowed = True
        elif action == sqlite3.SQLITE_PRAGMA:
            allowed = first in {'database_list', 'table_info', 'query_only'}
        if not allowed:
            self.denied_queries += 1
            self.denied_metadata.add((action, first, second))
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

    def protect(self, connection):
        connection.execute('PRAGMA query_only=ON')
        connection.set_authorizer(self.authorize)
        return connection


def protected_bytes(data, *, decrypt=False):
    """Current-user DPAPI; keys never go into JSON/plaintext caches or logs."""
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    if decrypt:
        result = crypt.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    else:
        result = crypt.CryptProtectData(ctypes.byref(incoming), 'Attention Desk WeChat DB', None, None, None, 1, ctypes.byref(outgoing))
    if not result:
        raise DBReadError('key_protection_failed')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def private_directory(path):
    path = Path(path).resolve()
    if not path.is_relative_to(PROJECT_ROOT / 'artifacts'):
        raise DBReadError('decryption_directory_outside_project_artifacts')
    path.mkdir(parents=True, exist_ok=True)
    return path


def detect_account_root(core):
    # Upstream tries every file in several application directories. Only known
    # configuration files, registry paths and directory metadata are necessary.
    for directory in core._config_candidates():
        for path in Path(directory).glob('*.ini'):
            try:
                raw=path.read_bytes()[:8192]
            except OSError:
                continue
            for encoding in ('utf-8-sig','gbk'):
                try:
                    candidate=core._extract_path_from_config(raw.decode(encoding))
                except UnicodeError:
                    continue
                found=core._locate_account_root(candidate)
                if found:
                    return found
    roots=[*core._registry_data_dirs(),str(Path(os.environ.get('USERPROFILE',''))/'Documents'),os.environ.get('USERPROFILE','')]
    for root in roots:
        found=core._locate_account_root(root)
        if found:
            return found
    raise DBReadError('wechat_database_directory_not_found')


def make_reader(allowed_chats, *, workdir=None, db_dir=None, account=None, metadata_only=False, selected_chatroom=None):
    """No disk/process inspection occurs until this explicitly authorized factory call."""
    from . import replica_db_core as core
    if (not allowed_chats and not metadata_only) or any(not isinstance(n, str) or not n.strip() for n in allowed_chats):
        raise DBReadError('empty_allowlist')
    boundary = QueryBoundary()
    private = private_directory(workdir or PROJECT_ROOT / 'artifacts' / 'wechat-db-runtime')

    class SelectiveWeChatDB(core.WeChatDB):
        def _collect_db_files(self):
            return [(rel, path, size) for rel, path, size in super()._collect_db_files()
                    if Path(path).name in {'contact.db', 'session.db'}
                    or not metadata_only and re.fullmatch(r'message_\d+\.db', Path(path).name)]

        def _find_weixin_pids(self):
            import psutil
            candidates = []
            for process in psutil.process_iter(['pid', 'name', 'memory_info']):
                if (process.info['name'] or '').lower() == 'weixin.exe':
                    candidates.append((process.info['memory_info'].rss if process.info['memory_info'] else 0, process.pid))
            return [pid for _, pid in sorted(candidates, reverse=True)]

        def _stable_key_dirs(self):
            return []

        def _save_keys(self):
            if self._keys:
                payload = json.dumps({k: v.hex() for k, v in self._keys.items()}).encode()
                temporary = Path(self.workdir) / 'keys.dpapi.tmp'
                temporary.write_bytes(protected_bytes(payload))
                temporary.replace(Path(self.workdir) / 'keys.dpapi')

        def _load_or_extract_keys(self, master_key=None):
            self.master_key = self.cfg_dword = None
            saved = Path(self.workdir) / 'keys.dpapi'
            if saved.exists():
                try:
                    self._keys = {k: bytes.fromhex(v) for k, v in json.loads(protected_bytes(saved.read_bytes(), decrypt=True)).items()}
                    self._keys = {rel: key for rel, key in self._keys.items() if self._key_works(rel)}
                except (ValueError, OSError, DBReadError):
                    self._keys = {}
            if any(rel not in self._keys for rel, _, _ in self._db_files):
                self._keys.update(self.extract_keys())
                self._save_keys()
            self.unkeyed = [rel for rel, _, _ in self._db_files if not self._key_works(rel)]

        def _open_unlocked(self, rel):
            # The original databases are only ever opened rb by the decryption core.
            if rel not in {r for r, _, _ in self._db_files}:
                raise DBReadError('unknown_database')
            connection = boundary.protect(super()._open_unlocked(rel))
            stamp = Path(self.workdir) / (rel.replace(os.sep, '__') + '.stamp')
            fields = stamp.read_text().split(',')
            if int(fields[5]) < 0:
                connection.close()
                raise DBReadError('wal_snapshot_incomplete')
            return connection

        @staticmethod
        def _check_merged(dst):
            # Upstream quick_check walks every table. Validate schema only; selected
            # table queries validate their own pages and fail closed on corruption.
            if not Path(dst).resolve().is_relative_to(private):
                raise DBReadError('snapshot_outside_private_directory')
            try:
                connection = sqlite3.connect(Path(dst).resolve().as_uri() + '?mode=ro', uri=True)
                try:
                    boundary.protect(connection).execute('SELECT name FROM sqlite_master LIMIT 1').fetchall()
                    return True
                finally:
                    connection.close()
            except sqlite3.Error:
                return False

        def _msg_conns(self, user, _retry=True):
            if user not in self.allowed_ids:
                raise DBReadError('chat_not_allowlisted')
            return super()._msg_conns(user, _retry=_retry)

        def _msg_row_to_dict(self, row, maps=None):
            result = super()._msg_row_to_dict(row, maps)
            result.update(server_id=str(row['server_id']), local_type=int(row['local_type']))
            return result

        def sender_display_name(self, room, username):
            key = (room, username)
            cache = self._sender_display_cache
            if key in cache:
                return cache[key]
            value = (username, 'username')
            connection = self._contact_conn()
            if connection is not None:
                try:
                    from .sender import display_name
                    value = display_name(connection, room, username)
                finally:
                    connection.close()
            cache[key] = value
            return value

        def get_sessions(self, limit=100):
            for rel, path, _ in self._db_files:
                if Path(path).name != 'session.db':
                    continue
                connection = self._open(rel)
                try:
                    return [dict(row) for row in connection.execute(
                        'SELECT username,last_timestamp FROM SessionTable WHERE is_hidden=0 ORDER BY sort_timestamp DESC LIMIT ?', (limit,))]
                finally:
                    connection.close()
            return []

        def resolve(self, name):
            if name not in self.allowed_names:
                raise DBReadError('chat_not_allowlisted')
            connection = self._contact_conn()
            if connection is None:
                raise DBReadError('contact_database_unavailable')
            try:
                if selected_chatroom:
                    rows = connection.execute("SELECT username,nick_name,remark FROM contact WHERE username=? AND (nick_name=? OR remark=?)", (selected_chatroom, name, name)).fetchall()
                else:
                    rows = connection.execute("SELECT username,nick_name,remark FROM contact WHERE username LIKE '%@chatroom' AND (nick_name=? OR remark=?)", (name, name)).fetchall()
            finally:
                connection.close()
            exact = [r for r in rows if (r['remark'] or r['nick_name']) == name or r['nick_name'] == name]
            if len(exact) != 1:
                raise DBReadError('group_name_missing_or_ambiguous')
            internal_id = exact[0]['username']
            self.allowed_ids.add(internal_id)
            boundary.allow(internal_id)
            return internal_id

    # __init__ reads only file metadata/page-1 HMAC/key material, not message rows.
    reader = SelectiveWeChatDB(db_dir=db_dir or detect_account_root(core), account=account, workdir=str(private), keys_file=str(private / 'unused-plaintext-key-cache'))
    reader.allowed_names = frozenset(allowed_chats)
    reader.allowed_ids = set()
    reader._sender_display_cache = {}
    reader.boundary = boundary
    return reader


def search_group_metadata(reader, query, *, limit=30, offset=0, chatroom_id=None):
    """Contact names and activity timestamps only. No preview or message tables."""
    if chatroom_id and not re.fullmatch(r'[A-Za-z0-9_-]+@chatroom',chatroom_id):
        raise DBReadError('invalid_group_id')
    connection = reader._contact_conn()
    if connection is None:
        raise DBReadError('contact_database_unavailable')
    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    pattern = '%' + escaped + '%'
    selected=' AND username=?' if chatroom_id else ''
    parameters=(pattern,pattern,*([chatroom_id] if chatroom_id else []),limit+1,offset)
    try:
        rows = connection.execute("SELECT username,nick_name,remark FROM contact WHERE username LIKE '%@chatroom' AND (nick_name LIKE ? ESCAPE '\\' OR remark LIKE ? ESCAPE '\\')"+selected+" ORDER BY COALESCE(NULLIF(remark,''),nick_name),username LIMIT ? OFFSET ?", parameters).fetchall()
    finally:
        connection.close()
    sessions = {row['username']: row['last_timestamp'] for row in reader.get_sessions(limit=2000)}
    return {'account_scope': hashlib.sha256(reader.account.encode()).hexdigest()[:24],
            'items': [{'chatroom_id': row['username'], 'display_name': row['remark'] or row['nick_name'] or row['username'],
                       'nickname': row['nick_name'], 'last_activity_at': sessions.get(row['username'])} for row in rows[:limit]],
            'has_more': len(rows)>limit, 'offset': offset, 'limit': limit,
            'message_body_queries': 0, 'unauthorized_message_bodies_read': 0, 'messages_sent': 0,
            'denied_queries': reader.boundary.denied_queries}


def collect_increment(reader, name, cursor, *, now, limit=512):
    """Read only new local IDs per shard; metadata-only no-change checks.

    local_id is shard-local, not global. Persist each watermark only with the
    corresponding messages in the Attention Desk transaction.
    """
    internal_id = reader.resolve(name)
    reader._sender_display_cache = {}  # Fresh metadata each increment, scoped by room + username.
    scope = hashlib.sha256(reader.account.encode()).hexdigest()[:24]
    if cursor and (cursor['internal_chat_id'] != internal_id or cursor['account_scope'] != scope):
        raise DBReadError('database_account_or_chat_changed')
    bootstrap = cursor['bootstrap_since'] if cursor else int(now.timestamp()) - 86400
    watermarks = json.loads(cursor.get('shard_watermarks_json', '{}')) if cursor else {}
    next_watermarks = dict(watermarks)
    tables = reader._msg_conns(internal_id)
    if any(rel not in reader._keys for rel in reader._message_dbs()):
        for connection, _ in tables:
            connection.close()
        raise DBReadError('message_shard_key_missing')
    messages = []
    has_more = False
    metadata_queries = 0
    body_queries = 0
    try:
        for connection, table in tables:
            shard = reader._shard_label(connection)
            high = int(watermarks.get(shard, 0))
            maximum = int(connection.execute(f'SELECT COALESCE(MAX(local_id),0) FROM {table}').fetchone()[0])
            metadata_queries += 1
            if maximum < high:
                raise DBReadError('local_id_reset_requires_reconciliation')
            if maximum == high:
                continue
            eligible = connection.execute(f'SELECT local_id FROM {table} WHERE local_id>? AND create_time>=? LIMIT 1', (high, bootstrap)).fetchone()
            metadata_queries += 1
            if not eligible:
                next_watermarks[shard] = maximum
                continue
            rows = connection.execute(
                f'SELECT local_id,local_type,real_sender_id,create_time,message_content,source,packed_info_data,compress_content,server_id,sort_seq,? AS shard FROM {table} WHERE local_id>? AND create_time>=? ORDER BY local_id ASC LIMIT ?',
                (shard, high, bootstrap, limit + 1)).fetchall()
            body_queries += 1
            has_more = has_more or len(rows) > limit
            rows = rows[:limit]
            maps = reader._shard_maps([(connection, table)])
            known_usernames = set(maps.get(shard, {}).values())
            for row in rows:
                message = normalized_message(reader, name, internal_id, reader._msg_row_to_dict(row, maps),
                                             known_usernames=known_usernames)
                messages.append(message)
            if rows:
                next_watermarks[shard] = max(int(row['local_id']) for row in rows)
    finally:
        for connection, _ in tables:
            connection.close()
    messages.sort(key=lambda m: (m['sort_seq'], m['shard'], m['local_id']))
    last = messages[-1] if messages else None
    result_cursor = {
        'account_scope': scope, 'internal_chat_id': internal_id, 'bootstrap_since': bootstrap,
        'shard_watermarks_json': json.dumps(next_watermarks, sort_keys=True),
        'last_local_id': last['local_id'] if last else (cursor or {}).get('last_local_id'),
        'last_create_time': last['create_time_unix'] if last else (cursor or {}).get('last_create_time'),
        'last_sort_seq': last['sort_seq'] if last else (cursor or {}).get('last_sort_seq'),
        'secondary_fingerprint': last['source_content_sha256'] if last else (cursor or {}).get('secondary_fingerprint'),
        'last_successful_scan': now.isoformat() if not has_more else (cursor or {}).get('last_successful_scan'),
    }
    return {'messages': messages, 'cursor': result_cursor, 'has_more': has_more,
            'metadata_queries': metadata_queries, 'message_body_queries': body_queries,
            'messages_sent': 0, 'unauthorized_message_bodies_read': 0}


def normalized_message(reader, chat, internal_id, message, *, known_usernames=()):
    from .db_media import describe_content
    from .sender import resolve_sender
    display = getattr(reader, 'sender_display_name', lambda room, user: (reader.get_nickname(user), 'contact'))
    sender = resolve_sender(message, internal_id, known_usernames=known_usernames, display=display)
    content = sender.pop('content')
    safe_content, media_reference = describe_content(content, message)
    return {
        'chat': chat, 'internal_chat_id': internal_id,
        **sender, 'sender_id': message['sender_id'],
        'content': safe_content, 'type': message['type'],
        'local_type': message.get('local_type'), 'server_id': message.get('server_id'),
        'source_content_sha256': hashlib.sha256(content.encode()).hexdigest(),
        'media_reference': media_reference,
        'create_time': datetime.fromtimestamp(message['create_time'], timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).isoformat(),
        'create_time_unix': message['create_time'], 'local_id': message['local_id'],
        'sort_seq': message['sort_seq'], 'shard': message['shard'],
        'stable_id': f"{reader.account}:{internal_id}:{message['shard']}:{message['local_id']}",
    }
