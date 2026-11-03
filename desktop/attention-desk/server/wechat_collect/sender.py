"""Sender identity is shard-scoped evidence; display names never resolve identity."""
import re
import sqlite3

ENVELOPE = re.compile(r'^([^\s:]+):\n')
INTERNAL_USER = re.compile(r'^(?:wxid_[A-Za-z0-9_-]+|[A-Za-z0-9_-]+@chatroom)$')


def display_name(connection, room, username):
    """Join contact.id only after resolving username in the message's own shard."""
    fallback = (username, 'username')
    if connection is None:
        return fallback
    try:
        rows = connection.execute('SELECT m.display_name,c.remark,c.nick_name FROM contact c LEFT JOIN chat_room r ON r.username=? LEFT JOIN chatroom_member m ON m.room_id=r.id AND m.member_id=c.id WHERE c.username=?', (room, username)).fetchall()
        if len(rows) == 1:
            for index, source in enumerate(('group_nickname', 'remark', 'nickname')):
                if isinstance(rows[0][index], str) and rows[0][index].strip():
                    return rows[0][index], source
    except sqlite3.Error:
        pass
    return fallback


def resolve_sender(message, room, *, known_usernames=(), display=None):
    content = message.get('content') or ''
    mapped = message.get('sender_username') or None
    raw_id = message.get('sender_id')
    match = ENVELOPE.match(content)
    envelope = match[1] if match and (match[1] == mapped or match[1] in known_usernames or INTERNAL_USER.fullmatch(match[1])) else None
    if envelope:
        content = content[match.end():]
    base_type = int(message.get('local_type') or 0) & 0xFFFFFFFF
    system = base_type in {10000, 10002} or message.get('type') == '系统消息'
    crosscheck = None
    if system:
        username, label, source, status, reason = None, '系统消息', 'system', 'system', 'system_record_is_not_a_person'
    elif envelope and mapped and envelope != mapped:
        username, label, source, status, reason = None, None, 'raw_id', 'unresolved', 'envelope_and_shard_mapping_conflict'
        crosscheck = False
    elif not mapped or mapped.endswith('@chatroom'):
        username, label, source, status, reason = None, None, 'raw_id', 'unresolved', 'shard_sender_mapping_missing'
    else:
        username, status, reason = mapped, 'resolved', 'same_shard_name2id'
        label, source = display(room, username) if display else (username, 'username')
        crosscheck = True if envelope else None
    identity = {'raw_sender_id': raw_id, 'shard': message.get('shard'), 'mapped_username': mapped,
                'envelope_sender': envelope, 'username': username, 'status': status, 'reason': reason,
                'display_name': label, 'display_source': source}
    return {'content': content, 'sender': label or f'发送者待核实（原始 ID {raw_id}）',
            'sender_username': username, 'sender_status': status,
            'sender_reliability': 'reliable' if status == 'resolved' else 'unknown',
            'sender_prefix_matches': crosscheck, 'sender_identity': identity}
