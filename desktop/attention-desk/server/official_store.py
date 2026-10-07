"""Official sources feed the existing personal database and RawSnapshot review."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from .personal_store import encode, identifier, now
from .review_store import ReviewStore
from .official_sources import SourceFailure


class OfficialStore:
    def __init__(self, personal):
        self.personal = personal

    def configure(self, sources):
        with self.personal.connection(write=True) as c:
            ids = [s.id for s in sources]
            for source in sources:
                config = encode(source.model_dump())
                digest = hashlib.sha256(config.encode()).hexdigest()
                c.execute('INSERT OR IGNORE INTO source (id,kind,name,created_at) VALUES (?,?,?,?)', (source.id, 'official_web', source.name, now()))
                c.execute('UPDATE source SET name=? WHERE id=? AND kind=?', (source.name, source.id, 'official_web'))
                c.execute('''INSERT INTO official_source (source_id,config_json,config_hash,enabled,polling_interval) VALUES (?,?,?,?,?)
                    ON CONFLICT(source_id) DO UPDATE SET config_json=excluded.config_json,config_hash=excluded.config_hash,
                    enabled=CASE WHEN config_hash!=excluded.config_hash THEN excluded.enabled ELSE enabled END,
                    polling_interval=CASE WHEN config_hash!=excluded.config_hash THEN excluded.polling_interval ELSE polling_interval END''',
                    (source.id, config, digest, int(source.enabled), source.polling_interval))
            if ids:
                c.execute('UPDATE official_source SET enabled=0 WHERE source_id NOT IN (' + ','.join('?' for _ in ids) + ')', ids)
            c.execute("UPDATE official_source SET status='unreachable',error='上次检查因应用退出而中断；原文保留。' WHERE status='checking'")

    def source_rows(self):
        with self.personal.connection() as c:
            result = []
            for row in c.execute('SELECT o.*,s.name FROM official_source o JOIN source s ON s.id=o.source_id ORDER BY o.rowid'):
                item = dict(row)
                config = json.loads(item.pop('config_json'))
                item.pop('config_hash')
                item = {**config, **item, 'id': row['source_id'], 'enabled': bool(row['enabled'])}
                item['raw_count'] = c.execute('SELECT COUNT(*) FROM official_item_source WHERE source_id=?', (row['source_id'],)).fetchone()[0]
                item['pending_count'] = c.execute('''SELECT COUNT(*) FROM raw_review r JOIN raw_snapshot s ON s.id=r.raw_snapshot_id
                    JOIN official_item_source o ON o.raw_item_id=s.raw_item_id WHERE o.source_id=? AND r.status='pending' ''', (row['source_id'],)).fetchone()[0]
                item['fetch_pending_count'] = c.execute('''SELECT COUNT(*) FROM raw_item r JOIN official_item_source o ON o.raw_item_id=r.id
                    LEFT JOIN official_fetch_state f ON f.raw_item_id=r.id WHERE o.source_id=? AND f.raw_item_id IS NULL''', (row['source_id'],)).fetchone()[0]
                result.append(item)
            return result

    def set_enabled(self, source_id, enabled):
        with self.personal.connection(write=True) as c:
            if c.execute('UPDATE official_source SET enabled=? WHERE source_id=?', (int(enabled), source_id)).rowcount != 1:
                raise ValueError('来源不存在。')

    def pending_count(self):
        with self.personal.connection() as c:
            return c.execute("SELECT COUNT(*) FROM raw_review r JOIN raw_snapshot s ON s.id=r.raw_snapshot_id JOIN raw_item i ON i.id=s.raw_item_id WHERE i.kind='official_web' AND r.status='pending'").fetchone()[0]

    def begin(self, source_id):
        with self.personal.connection(write=True) as c:
            c.execute("UPDATE official_source SET status='checking',last_checked_at=? WHERE source_id=?", (now(), source_id))

    def finish(self, source_id, counts, error=None):
        with self.personal.connection(write=True) as c:
            row = c.execute('SELECT * FROM official_source WHERE source_id=?', (source_id,)).fetchone()
            failed = row['failure_count'] + 1 if error else 0
            delay = max(row['polling_interval'], min(86400 * 4, 21600 * (2 ** min(failed - 1, 4)))) if error else row['polling_interval']
            status = 'partial' if error and counts.get('saved', 0) else error.status if error else 'ok'
            stamp = now()
            c.execute('''UPDATE official_source SET status=?,last_success_at=?,next_check_at=?,error=?,failure_count=?,
                last_new_count=?,last_discovered_count=?,last_changed_count=?,last_unchanged_count=?,last_request_count=? WHERE source_id=?''',
                (status, row['last_success_at'] if error else stamp, (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(),
                 str(error)[:300] if error else '', failed, counts['new'], counts.get('discovered', 0), counts['changed'], counts['unchanged'], counts['requests'], source_id))

    def discover(self, source, entry):
        """Persist the public list identity even if fetching its body subsequently fails."""
        stamp = now()
        with self.personal.connection(write=True) as c:
            row = c.execute('''SELECT r.id FROM raw_item r LEFT JOIN official_url_alias a ON a.raw_item_id=r.id
                WHERE r.kind='official_web' AND (r.canonical_url=? OR a.url=?) LIMIT 1''', (entry['url'], entry['url'])).fetchone()
            raw_id = row['id'] if row else identifier()
            if not row:
                external = urlsplit(entry['url']).hostname + ':' + urlsplit(entry['url']).path
                c.execute('''INSERT INTO raw_item (id,source_id,kind,submission_hash,created_at,external_id,canonical_url,original_url,title,published_at,discovered_at)
                    VALUES (?,?,'official_web',?,?,?,?,?,?,?,?)''',
                    (raw_id, source.id, hashlib.sha256(entry['url'].encode()).hexdigest(), stamp, external, entry['url'], entry['url'], entry['title'], entry['published_at'], stamp))
            c.execute('INSERT OR IGNORE INTO official_url_alias VALUES (?,?)', (entry['url'], raw_id))
            c.execute('INSERT INTO official_item_source VALUES (?,?,?,?) ON CONFLICT(raw_item_id,source_id) DO UPDATE SET last_seen_at=excluded.last_seen_at', (raw_id, source.id, stamp, stamp))
            return {'raw_item_id': raw_id, 'new': not bool(row)}

    def fetch_error(self, raw_id, error):
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE raw_item SET fetch_error=?,last_fetch_attempt_at=? WHERE id=?', (str(error)[:300], now(), raw_id))

    def fetch_state(self, url):
        with self.personal.connection() as c:
            row = c.execute('''SELECT f.* FROM official_fetch_state f JOIN official_url_alias a ON a.raw_item_id=f.raw_item_id WHERE a.url=?''', (url,)).fetchone()
            return dict(row) if row else None

    def unchanged(self, source_id, url, state):
        stamp = now()
        with self.personal.connection(write=True) as c:
            c.execute('INSERT INTO official_item_source VALUES (?,?,?,?) ON CONFLICT(raw_item_id,source_id) DO UPDATE SET last_seen_at=excluded.last_seen_at',
                      (state['raw_item_id'], source_id, stamp, stamp))
            c.execute('UPDATE official_fetch_state SET last_fetched_at=? WHERE raw_item_id=?', (stamp, state['raw_item_id']))
            c.execute("UPDATE raw_item SET fetch_error='',last_fetch_attempt_at=? WHERE id=?", (stamp, state['raw_item_id']))
        return {'kind': 'unchanged', 'raw_snapshot_id': state['latest_snapshot_id']}

    def collect(self, source, original_url, canonical, article, result):
        """One transaction; no Event/Deadline/Task and no AI calls."""
        stamp = now()
        fingerprint = hashlib.sha256(encode({k: article[k] for k in ('original_text', 'title', 'published_at', 'attachments', 'images', 'body_status')}).encode()).hexdigest()
        with self.personal.connection(write=True) as c:
            item = c.execute("SELECT * FROM raw_item WHERE kind='official_web' AND canonical_url=?", (canonical,)).fetchone()
            discovered = c.execute('SELECT r.* FROM raw_item r JOIN official_url_alias a ON a.raw_item_id=r.id WHERE a.url=?', (original_url,)).fetchone()
            if item and discovered and item['id'] != discovered['id']:
                if c.execute('SELECT 1 FROM raw_snapshot WHERE raw_item_id=?', (discovered['id'],)).fetchone():
                    raise SourceFailure('canonical_conflict', '两个已有原文记录现在指向同一地址，保留历史版本供人工核对。')
                # Only a newly discovered, snapshot-free placeholder can be coalesced.
                associations = c.execute('SELECT * FROM official_item_source WHERE raw_item_id=?', (discovered['id'],)).fetchall()
                for association in associations:
                    c.execute('INSERT OR IGNORE INTO official_item_source VALUES (?,?,?,?)', (item['id'], association['source_id'], association['first_seen_at'], association['last_seen_at']))
                c.execute('DELETE FROM official_item_source WHERE raw_item_id=?', (discovered['id'],))
                c.execute('UPDATE official_url_alias SET raw_item_id=? WHERE raw_item_id=?', (item['id'], discovered['id']))
                c.execute('DELETE FROM raw_item WHERE id=?', (discovered['id'],))
            elif not item and discovered:
                item = discovered
                c.execute('UPDATE raw_item SET canonical_url=? WHERE id=?', (canonical, discovered['id']))
            kind = 'changed' if item else 'new'
            if item:
                raw_id = item['id']
            else:
                raw_id = identifier()
                external = urlsplit(canonical).hostname + ':' + urlsplit(canonical).path
                c.execute('''INSERT INTO raw_item (id,source_id,kind,submission_hash,created_at,external_id,canonical_url,original_url,title,published_at,discovered_at)
                    VALUES (?,?,'official_web',?,?,?,?,?,?,?,?)''',
                    (raw_id, source.id, hashlib.sha256(canonical.encode()).hexdigest(), stamp, external, canonical, original_url, article['title'], article['published_at'], stamp))
            latest = c.execute('''SELECT w.* FROM raw_snapshot_web w JOIN raw_snapshot s ON s.id=w.raw_snapshot_id WHERE s.raw_item_id=? ORDER BY w.version DESC LIMIT 1''', (raw_id,)).fetchone()
            if not latest:
                kind = 'new'
            if latest and latest['version_hash'] == fingerprint:
                snapshot_id, kind = latest['raw_snapshot_id'], 'unchanged'
            else:
                snapshot_id = ReviewStore._new_snapshot(c, raw_id, article['original_text'], stamp, article['published_at'], article['title'])
                c.execute('''INSERT INTO raw_snapshot_web (raw_snapshot_id,source_id,page_title,original_url,canonical_url,published_at,fetched_at,
                    attachments_json,images_json,body_html,body_status,version_hash,version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (snapshot_id, source.id, article['title'], original_url, canonical, article['published_at'], stamp,
                     encode(article['attachments']), encode(article['images']), article['body_html'], article['body_status'], fingerprint, latest['version'] + 1 if latest else 1))
            c.execute('INSERT INTO official_item_source VALUES (?,?,?,?) ON CONFLICT(raw_item_id,source_id) DO UPDATE SET last_seen_at=excluded.last_seen_at', (raw_id, source.id, stamp, stamp))
            from .documents.storage import AttachmentStore
            AttachmentStore.seed(c,snapshot_id)
            for alias in {canonical, original_url}:
                c.execute('INSERT OR IGNORE INTO official_url_alias VALUES (?,?)', (alias, raw_id))
            c.execute('UPDATE raw_item SET fetch_error=\'\',last_fetch_attempt_at=?,title=?,published_at=? WHERE id=?', (stamp, article['title'], article['published_at'], raw_id))
            c.execute('''INSERT INTO official_fetch_state VALUES (?,?,?,?,?) ON CONFLICT(raw_item_id) DO UPDATE SET etag=excluded.etag,
                last_modified=excluded.last_modified,last_fetched_at=excluded.last_fetched_at,latest_snapshot_id=excluded.latest_snapshot_id''',
                (raw_id, result.etag, result.last_modified, stamp, snapshot_id))
            return {'kind': kind, 'raw_item_id': raw_id, 'raw_snapshot_id': snapshot_id}
