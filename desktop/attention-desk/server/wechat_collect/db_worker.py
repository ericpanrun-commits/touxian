"""Isolated one-shot local DB worker. JSON protocol has no client-control commands."""
from __future__ import annotations

from datetime import datetime
import json
import sys
from zoneinfo import ZoneInfo

from .db_reader import DBReadError, make_reader, collect_increment, UPSTREAM_COMMIT, wechat_version
from .db_media import preserve_local_media


def main():
    try:
        request = json.loads(sys.stdin.buffer.read(100_000))
        # Real wall time, checked before opening processes, keys, DBs or media.
        now = datetime.now(ZoneInfo('Asia/Shanghai'))
        if now.hour < 6:
            raise DBReadError('quiet')
        name = request['chat']
        if name not in request['allowed_chats']:
            raise DBReadError('chat_not_allowlisted')
        selected=request.get('selected_chatroom')
        if selected and selected not in request.get('allowed_chatrooms',[]):
            raise DBReadError('chat_not_allowlisted')
        if wechat_version() != request['verified_version']:
            raise DBReadError('wechat_version_changed_requires_verification')
        reader = make_reader(request['allowed_chats'], workdir=request.get('workdir'),selected_chatroom=selected)
        if request.get('expected_account_scope'):
            import hashlib
            if hashlib.sha256(reader.account.encode()).hexdigest()[:24]!=request['expected_account_scope']:
                raise DBReadError('database_account_or_chat_changed')
        if request.get('action') == 'resolve':
            internal_id = reader.resolve(name)
            import hashlib
            result = {'internal_chat_id': internal_id, 'account_scope': hashlib.sha256(reader.account.encode()).hexdigest()[:24], 'commit': UPSTREAM_COMMIT}
        elif request.get('action') == 'increment':
            result = collect_increment(reader, name, request.get('cursor'), now=now)
            for message in result['messages']:
                if datetime.now(ZoneInfo('Asia/Shanghai')).hour < 6:
                    raise DBReadError('quiet')
                message['media_path'] = preserve_local_media(reader, result['cursor']['internal_chat_id'], message, request['workdir'] + '/media')
            result['denied_queries'] = reader.boundary.denied_queries
        else:
            raise DBReadError('unsupported_read_operation')
        # JSON travels through a Windows pipe (often CP936 under pythonw).
        # ASCII escaping preserves every Unicode code point without locale loss.
        sys.stdout.write(json.dumps({'ok': True, 'result': result}, ensure_ascii=True))
    except Exception as exc:
        sys.stdout.write(json.dumps({'ok': False, 'code': str(exc) if isinstance(exc, DBReadError) else 'database_read_error', 'error_type': type(exc).__name__}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
