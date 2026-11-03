"""DB-0: exactly one authorized group, 20 latest rows; never a full-history export."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.wechat_collect.db_reader import make_reader, normalized_message, UPSTREAM_COMMIT, wechat_version
from server.wechat_collect.db_media import cache_references, preserve_local_media


def main():
    chat = '26车辆2班（公告群）'
    output = ROOT / 'artifacts' / 'wechat-db0' / 'results'
    output.mkdir(parents=True, exist_ok=True)
    report = {'DB_READ': 'FAIL', 'commit': UPSTREAM_COMMIT, 'target': chat,
              'messages_sent': 0, 'unauthorized_message_bodies_read': 0, 'llm_calls': 0}
    try:
        import psutil
        pids = [p for p in psutil.process_iter(['name', 'exe']) if (p.info['name'] or '').lower() == 'weixin.exe']
        report['wechat_processes'] = len(pids)
        report['wechat_version'] = wechat_version()
        reader = make_reader([chat], workdir=ROOT / 'artifacts' / 'wechat-db0' / 'private')
        report['account_fingerprint'] = hashlib.sha256(reader.account.encode()).hexdigest()[:16]
        report['keyed_databases'] = len(reader._keys)
        report['unkeyed_database_count'] = len(reader.unkeyed)
        internal_id = reader.resolve(chat)
        report['target_resolved'] = True
        report['internal_chat_id'] = internal_id
        messages = reader.get_messages(internal_id, limit=20)
        repeated = reader.get_messages(internal_id, limit=20)
        structured = [normalized_message(reader, chat, internal_id, m) for m in sorted(messages,key=lambda m:(m['sort_seq'],m['shard'],m['local_id']))]
        report['count'] = len(structured)
        report['sort_seq_descending_from_upstream'] = all(a['sort_seq'] >= b['sort_seq'] for a, b in zip(messages, messages[1:]))
        report['chronological_output'] = all(a['create_time_unix'] <= b['create_time_unix'] for a, b in zip(structured, structured[1:]))
        report['stable_ids_unique'] = len({m['stable_id'] for m in structured}) == len(structured)
        report['stable_ids_repeatable'] = [(m['shard'], m['local_id'], m['sort_seq']) for m in messages] == [(m['shard'], m['local_id'], m['sort_seq']) for m in repeated]
        report['sender_resolved'] = sum(bool(m['sender_username']) for m in structured)
        report['sender_prefix_checks'] = sum(m['sender_prefix_matches'] is True for m in structured)
        report['sender_prefix_mismatches'] = sum(m['sender_prefix_matches'] is False for m in structured)
        for message in structured:
            cache_references(reader, internal_id, message)
            if (message['media_reference'] or {}).get('kind') == 'image':
                preserve_local_media(reader, internal_id, message, output.parent / 'media')
        report['media_references'] = sum(m['media_reference'] is not None for m in structured)
        report['images_with_local_cache'] = sum(bool((m['media_reference'] or {}).get('cache_files')) for m in structured)
        report['images_decrypted'] = sum((m['media_reference'] or {}).get('cache_status') == 'decrypted' for m in structured)
        report['types'] = sorted({m['type'] for m in structured})
        report['denied_queries'] = reader.boundary.denied_queries
        report['denied_metadata'] = sorted(reader.boundary.denied_metadata, key=str)
        (output / 'latest-20.json').write_text(json.dumps(structured, ensure_ascii=False, indent=2), encoding='utf-8')
        report['DB_READ'] = 'PASS' if len(structured) == 20 and report['stable_ids_repeatable'] and report['chronological_output'] and report['sender_prefix_mismatches']==0 and report['sender_resolved']>=19 else 'PARTIAL'
    except Exception as exc:
        # Exception strings from native libraries can contain identifiers/secrets.
        report['error_type'] = type(exc).__name__
        report['error_code'] = str(exc) if type(exc).__name__ == 'DBReadError' else 'read_failed'
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['DB_READ'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
