"""Bounded revalidation against authorized live data; never activates a certificate.

Input is a private JSON manifest containing existing bindings and cursors. The
worker uses the existing read-only reader, never imports automation, sends a
message, advances a production cursor, or calls a model. Output contains hashes
and counts rather than message bodies. Run in the existing DB Python runtime.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.wechat_collect.db_reader import (DBReadError, UPSTREAM_COMMIT,
    collect_increment, make_reader, normalized_message, wechat_version)


def digest(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


def probe(manifest, workdir):
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    if now.hour < 6:
        raise DBReadError('quiet')
    version = wechat_version()
    if version != manifest['expected_version']:
        raise DBReadError('unexpected_live_version')
    report = {'DB_READ': 'FAIL', 'wechat_version': version, 'commit': UPSTREAM_COMMIT,
              'verified_at': now.isoformat(), 'messages_sent': 0, 'llm_calls': 0,
              'unauthorized_message_bodies_read': 0, 'groups': []}
    sample = None
    for target in manifest['groups']:
        if datetime.now(ZoneInfo('Asia/Shanghai')).hour < 6:
            raise DBReadError('quiet')
        reader = make_reader([target['name']], workdir=workdir,
                             selected_chatroom=target['binding']['internal_chat_id'])
        scope = hashlib.sha256(reader.account.encode()).hexdigest()[:24]
        if scope != target['binding']['account_scope']:
            raise DBReadError('database_account_or_chat_changed')
        cursor = target['cursor']
        rows = []
        for page in range(4):
            result = collect_increment(reader, target['name'], cursor, now=now)
            if result['unauthorized_message_bodies_read'] or result['messages_sent']:
                raise DBReadError('boundary_violation')
            rows.extend(result['messages'])
            cursor = result['cursor']
            if not result['has_more']:
                break
        else:
            raise DBReadError('verification_page_limit')
        repeated = collect_increment(reader, target['name'], target['cursor'], now=now)
        repeatable = digest(rows[:len(repeated['messages'])]) == digest(repeated['messages'])
        second = collect_increment(reader, target['name'], cursor, now=now)
        unique = len({row['stable_id'] for row in rows}) == len(rows)
        mismatches = sum(row['sender_prefix_matches'] is False for row in rows)
        complete = not result['has_more']
        # An arriving message is retained as an unverified concurrent change;
        # rerun verification rather than presenting it as an empty scan.
        unchanged = not second['messages'] and second['message_body_queries'] == 0
        report['groups'].append({'chat_id': target['id'], 'account_scope': scope,
            'internal_chat_id': cursor['internal_chat_id'], 'messages': len(rows),
            'source_digest': digest(rows), 'stable_ids_unique': unique,
            'stable_ids_repeatable': repeatable, 'sender_prefix_mismatches': mismatches,
            'catch_up_complete': complete, 'second_scan_no_change': unchanged,
            'second_scan_body_queries': second['message_body_queries'],
            'denied_queries': reader.boundary.denied_queries,
            'PASS': unique and repeatable and mismatches == 0 and complete and unchanged})
        if sample is None:
            room = cursor['internal_chat_id']
            latest = reader.get_messages(room, limit=20)
            again = reader.get_messages(room, limit=20)
            sample = [normalized_message(reader, target['name'], room, item)
                      for item in sorted(latest, key=lambda m: (m['sort_seq'], m['shard'], m['local_id']))]
            report.update(target=target['name'], count=len(sample),
                stable_ids_repeatable=[(m['shard'], m['local_id'], m['sort_seq']) for m in latest]
                    == [(m['shard'], m['local_id'], m['sort_seq']) for m in again],
                stable_ids_unique=len({m['stable_id'] for m in sample}) == len(sample),
                chronological_output=all(a['create_time_unix'] <= b['create_time_unix']
                                         for a, b in zip(sample, sample[1:])),
                sender_prefix_mismatches=sum(m['sender_prefix_matches'] is False for m in sample),
                sample_sha256=digest(sample))
    if wechat_version() != version:
        raise DBReadError('live_version_changed_during_verification')
    report['source_files'] = {name: hashlib.sha256((ROOT / 'server/wechat_collect' / name).read_bytes()).hexdigest()
        for name in ('db_reader.py', 'replica_db_core.py', 'sender.py', 'db_media.py')}
    if (report['groups'] and all(g['PASS'] for g in report['groups'])
            and report.get('count') == 20 and report.get('stable_ids_unique')
            and report.get('stable_ids_repeatable') and report.get('chronological_output')
            and report.get('sender_prefix_mismatches') == 0):
        report['DB_READ'] = 'PASS'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = probe(json.loads(args.manifest.read_text(encoding='utf-8')), args.workdir)
    except Exception as exc:
        report = {'DB_READ': 'FAIL', 'error_type': type(exc).__name__,
                  'error_code': str(exc) if isinstance(exc, DBReadError) else 'verification_failed'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report['DB_READ'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
