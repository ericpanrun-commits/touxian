"""Append file Evidence to an already classified notice without another AI job."""
from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path

from ..personal_store import encode, identifier, now
from ..review_store import ReviewStore


def _reference(value):
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def file_info(personal, row):
    ref = _reference(row['reference_json'])
    name = ref.get('title') or row['raw_text'] or '微信附件'
    path = Path(ref['evidence_path']).resolve() if ref.get('evidence_path') else None
    if not (path and path.is_relative_to((personal.path.parent/'files').resolve()) and path.is_file()):
        path = None
    return {'message_id': row['message_id'], 'name': name, 'path': path,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path else None}


def _copy_row(c, table, row, **replace):
    values = {**dict(row), **replace}
    columns = ','.join(values)
    c.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))


def enrich_existing(personal):
    """Only primary files in an existing qualified notice can inherit its decision.

    Filenames are metadata, never document transcription. A new snapshot retains
    identical message blocks, quotes and draft; historical Evidence stays intact.
    """
    bound = set()
    with personal.connection(write=True) as c:
        batches = c.execute("""SELECT DISTINCT b.* FROM wechat_batch b
            JOIN wechat_chat ch ON ch.id=b.chat_id AND ch.enabled=1
            JOIN wechat_batch_message bm ON bm.batch_id=b.id AND bm.role='primary'
            JOIN wechat_media_work w ON w.message_id=bm.message_id
            WHERE b.superseded_by IS NULL AND json_extract(w.reference_json,'$.kind')='file'
              AND EXISTS (SELECT 1 FROM wechat_candidate ca WHERE ca.batch_id=b.id
                AND ca.model_item_id!='_pending' AND ca.status IN ('needs_review','fyi','accepted')
                AND ca.attention_level IN ('action','fyi'))""").fetchall()
        for batch in batches:
            rows = c.execute("""SELECT w.*,m.raw_text FROM wechat_media_work w
                JOIN wechat_message m ON m.id=w.message_id
                JOIN wechat_batch_message bm ON bm.message_id=w.message_id
                WHERE bm.batch_id=? AND bm.role='primary' AND json_extract(w.reference_json,'$.kind')='file'
                ORDER BY bm.ordinal""", (batch['id'],)).fetchall()
            files = [file_info(personal, r) for r in rows]
            bound.update(f['message_id'] for f in files)
            manifest = [[f['message_id'], f['name'], f['sha256']] for f in files]
            key = hashlib.sha256(encode([batch['id'], manifest]).encode()).hexdigest()
            if c.execute("SELECT 1 FROM wechat_media_parse_cache WHERE sha256=? AND engine='file_manifest' AND version='message-files-v1'", (key,)).fetchone():
                continue
            original = c.execute('SELECT * FROM raw_snapshot WHERE id=?', (batch['snapshot_id'],)).fetchone()
            review = c.execute('SELECT * FROM raw_review WHERE raw_snapshot_id=?', (original['id'],)).fetchone()
            stamp = now()
            snapshot = ReviewStore._new_snapshot(c, original['raw_item_id'], original['original_text'], stamp, original['source_time'], review['title_hint'])
            # Preserve user decisions and every draft field verbatim. Incrementing
            # the review version makes an already open editor refresh safely.
            fields = {k: review[k] for k in review.keys() if k not in {'raw_snapshot_id','created_at','updated_at','version'}}
            c.execute('UPDATE raw_review SET '+','.join(k+'=?' for k in fields)+',version=?,updated_at=? WHERE raw_snapshot_id=?', (*fields.values(), review['version']+1, stamp, snapshot))
            attachment_by_message = {}
            replaced = set()
            for f in files:
                mid = f['message_id']
                replaced.update(r[0] for r in c.execute('SELECT b.attachment_id FROM raw_source_block b JOIN wechat_snapshot_message sm ON sm.snapshot_id=b.raw_snapshot_id AND sm.block_id=b.block_id WHERE sm.snapshot_id=? AND sm.message_id=? AND b.attachment_id IS NOT NULL', (original['id'], mid)))
                att = attachment_by_message[mid] = identifier()
                path = f['path']
                c.execute('''INSERT INTO attachment (id,raw_item_id,origin_snapshot_id,filename,local_path,mime_type,extension,size,sha256,downloaded_at,parse_status,parse_error,version,parse_manifest_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (att, original['raw_item_id'], snapshot, f['name'], str(path) if path else None,
                     mimetypes.guess_type(f['name'])[0] or 'application/octet-stream', Path(f['name']).suffix.lstrip('.'),
                     path.stat().st_size if path else None, f['sha256'], stamp if path else None,
                     'downloaded' if path else 'not_downloaded', '' if path else '微信尚未缓存原件，会自动继续检查。', 1,
                     encode({'metadata': {'message_id': mid, 'capture_kind': 'local_cache'}, 'blocks': []}), stamp))
                c.execute('INSERT INTO snapshot_attachment VALUES (?,?)', (snapshot, att))
            for old in c.execute('SELECT * FROM snapshot_attachment WHERE raw_snapshot_id=?', (original['id'],)).fetchall():
                if old['attachment_id'] not in replaced:
                    _copy_row(c, 'snapshot_attachment', old, raw_snapshot_id=snapshot)
            mappings = {r['block_id']: r for r in c.execute('SELECT * FROM wechat_snapshot_message WHERE snapshot_id=?', (original['id'],))}
            for block in c.execute('SELECT * FROM raw_source_block WHERE raw_snapshot_id=?', (original['id'],)).fetchall():
                mid = mappings[block['block_id']]['message_id'] if block['block_id'] in mappings else None
                _copy_row(c, 'raw_source_block', block, raw_snapshot_id=snapshot,
                          attachment_id=attachment_by_message.get(mid, block['attachment_id']))
            for mapping in mappings.values():
                _copy_row(c, 'wechat_snapshot_message', mapping, snapshot_id=snapshot)
            warnings = [w for w in json.loads(batch['warnings_json']) if not any(w=='media_unavailable:'+f['message_id'] and f['path'] for f in files)]
            c.execute('UPDATE wechat_batch SET snapshot_id=?,warnings_json=? WHERE id=?', (snapshot, encode(warnings), batch['id']))
            c.execute("INSERT INTO wechat_media_parse_cache VALUES (?,'file_manifest','message-files-v1','parsed','[]',?,?)", (key, encode({'snapshot_id': snapshot}), stamp))
            for f in files:
                if f['path']:
                    c.execute("UPDATE wechat_media_work SET status='completed',last_error=NULL,completed_at=? WHERE message_id=?", (stamp, f['message_id']))
                    c.execute("UPDATE wechat_attention_media SET status='resolved',resolved_at=?,snapshot_id=? WHERE message_id=?", (stamp, snapshot, f['message_id']))
                else:
                    c.execute('UPDATE wechat_attention_media SET snapshot_id=? WHERE message_id=?', (snapshot, f['message_id']))
    return bound
