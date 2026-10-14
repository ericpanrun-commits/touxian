"""Official sources feed the existing personal database and RawSnapshot review."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from .personal_store import encode, identifier, now
from .review_store import ReviewStore
from .official_sources import SourceFailure, SourceConfig, url_identity
from .official_rules import classify, load_profile


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
                c.execute('''INSERT INTO official_source (source_id,config_json,config_hash,enabled,polling_interval,fetch_interval,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id) DO UPDATE SET config_json=excluded.config_json,config_hash=excluded.config_hash,
                    enabled=enabled,
                    polling_interval=CASE WHEN config_hash!=excluded.config_hash THEN excluded.polling_interval ELSE polling_interval END,
                    fetch_interval=CASE WHEN config_hash!=excluded.config_hash THEN excluded.fetch_interval ELSE fetch_interval END,updated_at=excluded.updated_at''',
                    (source.id, config, digest, int(source.enabled), max(3600,source.polling_interval),source.polling_interval,now(),now()))
            if ids:
                c.execute('UPDATE official_source SET enabled=0 WHERE source_id NOT IN (' + ','.join('?' for _ in ids) + ')', ids)
            if not c.execute('SELECT 1 FROM official_job_lease WHERE expires_at>?',(now(),)).fetchone():
                c.execute("UPDATE official_source SET status='unreachable',error='上次检查因应用退出而中断；原文保留。' WHERE status='checking'")
            configs={s.id:s for s in sources}
            rule_version=load_profile()['version']
            for row in c.execute('''SELECT r.id,r.source_id,s.original_text,w.* FROM official_fetch_state f
                JOIN raw_item r ON r.id=f.raw_item_id JOIN raw_snapshot s ON s.id=f.latest_snapshot_id
                JOIN raw_snapshot_web w ON w.raw_snapshot_id=s.id LEFT JOIN official_item i ON i.raw_item_id=r.id
                WHERE i.raw_item_id IS NULL OR i.rule_version!=?''',(rule_version,)).fetchall():
                if row['source_id'] in configs:
                    self._project(c,row['id'],configs[row['source_id']],{'title':row['page_title'],'original_text':row['original_text']},row['fetched_at'],changed=False)

    def source_rows(self):
        with self.personal.connection() as c:
            result = []
            for row in c.execute('SELECT o.*,s.name FROM official_source o JOIN source s ON s.id=o.source_id ORDER BY o.rowid'):
                item = dict(row)
                config = json.loads(item.pop('config_json'))
                item.pop('config_hash')
                item = {**config, **item, 'id': row['source_id'], 'enabled': bool(row['enabled'])}
                item['polling_interval'] = row['fetch_interval']
                item.update(organization=config['department'],base_url='https://'+urlsplit(config['list_url']).hostname,
                            official_domain=urlsplit(config['list_url']).hostname,adapter_type=config['parser_type'],
                            last_fetch_at=row['last_checked_at'],last_error=row['error'],consecutive_failures=row['failure_count'],
                            health='disabled' if not row['enabled'] else 'degraded' if row['failure_count']>=3 else 'warning' if row['error'] else row['status'])
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

    def begin(self, source_id, batch_id=None):
        with self.personal.connection(write=True) as c:
            stamp, run_id = now(), identifier()
            c.execute("UPDATE official_source SET status='checking',last_checked_at=?,updated_at=? WHERE source_id=?", (stamp,stamp, source_id))
            c.execute("INSERT INTO official_run (id,batch_id,source_id,started_at,status) VALUES (?,?,?,?,'running')",(run_id,batch_id or identifier(),source_id,stamp))
            return run_id

    def finish(self, source_id, counts, error=None, run_id=None):
        with self.personal.connection(write=True) as c:
            row = c.execute('SELECT * FROM official_source WHERE source_id=?', (source_id,)).fetchone()
            failed = row['failure_count'] + 1 if error else 0
            interval = max(row['fetch_interval'],row['polling_interval']) if row['polling_interval']>3600 else row['fetch_interval']
            delay = max(interval, min(86400 * 4, 1800 * (2 ** min(failed - 1, 7)))) if error else interval
            status = 'partial' if error and counts.get('saved', 0) else error.status if error else 'ok'
            stamp = now()
            c.execute('''UPDATE official_source SET status=?,last_success_at=?,next_check_at=?,error=?,failure_count=?,
                last_new_count=?,last_discovered_count=?,last_changed_count=?,last_unchanged_count=?,last_request_count=? WHERE source_id=?''',
                (status, row['last_success_at'] if error else stamp, (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(),
                 str(error)[:300] if error else '', failed, counts['new'], counts.get('discovered', 0), counts['changed'], counts['unchanged'], counts['requests'], source_id))
            c.execute('UPDATE official_source SET updated_at=?,last_error_at=CASE WHEN ? THEN ? ELSE last_error_at END,backfill_completed_at=CASE WHEN ? THEN backfill_completed_at ELSE COALESCE(backfill_completed_at,?) END WHERE source_id=?',
                      (stamp,bool(error),stamp,bool(error),stamp,source_id))
            if run_id:
                c.execute('''UPDATE official_run SET finished_at=?,status=?,scanned=?,discovered=?,inserted=?,updated=?,skipped=?,failed=?,requests=?,error_code=?,error=? WHERE id=?''',
                    (stamp,status,counts.get('scanned',0),counts.get('discovered',0),counts['new'],counts['changed'],counts['unchanged'],counts.get('failed',int(bool(error))),counts['requests'],error.code if error else '',str(error)[:300] if error else '',run_id))

    def discover(self, source, entry):
        """Persist the public list identity even if fetching its body subsequently fails."""
        stamp = now()
        with self.personal.connection(write=True) as c:
            row = c.execute('''SELECT r.id FROM raw_item r LEFT JOIN official_url_alias a ON a.raw_item_id=r.id
                WHERE r.kind='official_web' AND (r.canonical_url=? OR a.url IN (?,?)) LIMIT 1''', (entry['url'], entry['url'],url_identity(entry['url']))).fetchone()
            raw_id = row['id'] if row else identifier()
            if not row:
                external = urlsplit(entry['url']).hostname + ':' + urlsplit(entry['url']).path
                c.execute('''INSERT INTO raw_item (id,source_id,kind,submission_hash,created_at,external_id,canonical_url,original_url,title,published_at,discovered_at)
                    VALUES (?,?,'official_web',?,?,?,?,?,?,?,?)''',
                    (raw_id, source.id, hashlib.sha256(entry['url'].encode()).hexdigest(), stamp, external, entry['url'], entry['url'], entry['title'], entry['published_at'], stamp))
            c.execute('INSERT OR IGNORE INTO official_url_alias VALUES (?,?)', (entry['url'], raw_id))
            c.execute('INSERT OR IGNORE INTO official_url_alias VALUES (?,?)', (url_identity(entry['url']), raw_id))
            c.execute('INSERT INTO official_item_source VALUES (?,?,?,?) ON CONFLICT(raw_item_id,source_id) DO UPDATE SET last_seen_at=excluded.last_seen_at', (raw_id, source.id, stamp, stamp))
            return {'raw_item_id': raw_id, 'new': not bool(row)}

    def fetch_error(self, raw_id, error):
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE raw_item SET fetch_error=?,last_fetch_attempt_at=? WHERE id=?', (str(error)[:300], now(), raw_id))

    def fetch_state(self, url):
        with self.personal.connection() as c:
            row = c.execute('''SELECT f.*,r.title,r.published_at FROM official_fetch_state f JOIN raw_item r ON r.id=f.raw_item_id JOIN official_url_alias a ON a.raw_item_id=f.raw_item_id WHERE a.url IN (?,?) LIMIT 1''', (url,url_identity(url))).fetchone()
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
        canonical = url_identity(canonical)
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
            for alias in {canonical, original_url, url_identity(original_url)}:
                c.execute('INSERT OR IGNORE INTO official_url_alias VALUES (?,?)', (alias, raw_id))
            c.execute('UPDATE raw_item SET fetch_error=\'\',last_fetch_attempt_at=?,title=?,published_at=? WHERE id=?', (stamp, article['title'], article['published_at'], raw_id))
            c.execute('''INSERT INTO official_fetch_state VALUES (?,?,?,?,?) ON CONFLICT(raw_item_id) DO UPDATE SET etag=excluded.etag,
                last_modified=excluded.last_modified,last_fetched_at=excluded.last_fetched_at,latest_snapshot_id=excluded.latest_snapshot_id''',
                (raw_id, result.etag, result.last_modified, stamp, snapshot_id))
            self._project(c,raw_id,source,article,stamp,changed=kind!='unchanged')
            return {'kind': kind, 'raw_item_id': raw_id, 'raw_snapshot_id': snapshot_id}

    @staticmethod
    def _project(c, raw_id, source, article, stamp, changed=True):
        value=classify(article,source)
        # A school revalidation must not erase the relevance of an existing
        # college reference to the same item.
        for row in c.execute('SELECT o.config_json FROM official_item_source a JOIN official_source o ON o.source_id=a.source_id WHERE a.raw_item_id=?',(raw_id,)).fetchall():
            candidate=classify(article,SourceConfig.model_validate(json.loads(row['config_json'])))
            if candidate['relevance']>value['relevance']:
                value=candidate
        prior=c.execute('SELECT * FROM official_item WHERE raw_item_id=?',(raw_id,)).fetchone()
        duplicate=c.execute('''SELECT display_group FROM official_item WHERE raw_item_id!=? AND content_hash=?
            AND (title_hash=? OR ? >= 200) ORDER BY fetched_at LIMIT 1''',(raw_id,value['content_hash'],value['title_hash'],len(article['original_text']))).fetchone()
        group=duplicate['display_group'] if duplicate else prior['display_group'] if prior else raw_id
        c.execute('''INSERT INTO official_item (raw_item_id,category,summary,importance,relevance,reasons_json,content_hash,title_hash,display_group,
            deadline_at,event_start_at,event_end_at,time_evidence_json,fetched_at,updated_at,rule_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(raw_item_id) DO UPDATE SET category=excluded.category,summary=excluded.summary,importance=excluded.importance,
            relevance=excluded.relevance,reasons_json=excluded.reasons_json,content_hash=excluded.content_hash,title_hash=excluded.title_hash,
            display_group=excluded.display_group,deadline_at=excluded.deadline_at,event_start_at=excluded.event_start_at,event_end_at=excluded.event_end_at,
            time_evidence_json=excluded.time_evidence_json,fetched_at=excluded.fetched_at,updated_at=CASE WHEN ? THEN excluded.updated_at ELSE updated_at END,rule_version=excluded.rule_version''',
            (raw_id,value['category'],value['summary'],value['importance'],value['relevance'],encode(value['reasons']),value['content_hash'],value['title_hash'],group,
             value['deadline_at'],value['event_start_at'],value['event_end_at'],encode(value['time_evidence']),stamp,stamp,value['rule_version'],changed))

    def acquire_lease(self, owner):
        stamp=now(); expiry=(datetime.now(timezone.utc)+timedelta(seconds=90)).isoformat()
        with self.personal.connection(write=True) as c:
            active=c.execute("SELECT 1 FROM official_job_lease WHERE name='sync' AND expires_at>?",(stamp,)).fetchone()
            if active: return False
            c.execute("INSERT INTO official_job_lease VALUES ('sync',?,?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at,heartbeat_at=excluded.heartbeat_at",(owner,expiry,stamp))
            c.execute("UPDATE official_run SET finished_at=?,status='interrupted',failed=1,error_code='interrupted',error='上次同步已中断，已有内容保留。' WHERE finished_at IS NULL",(stamp,))
            return True

    def renew_lease(self, owner):
        with self.personal.connection(write=True) as c:
            return c.execute("UPDATE official_job_lease SET expires_at=?,heartbeat_at=? WHERE name='sync' AND owner=? AND expires_at>?",
                ((datetime.now(timezone.utc)+timedelta(seconds=90)).isoformat(),now(),owner,now())).rowcount==1

    def release_lease(self,owner):
        with self.personal.connection(write=True) as c:
            c.execute("DELETE FROM official_job_lease WHERE name='sync' AND owner=?",(owner,))

    def lease_active(self):
        with self.personal.connection() as c:
            return bool(c.execute('SELECT 1 FROM official_job_lease WHERE expires_at>?',(now(),)).fetchone())

    def recent_runs(self,source_id=None,limit=20):
        with self.personal.connection() as c:
            return [dict(r) for r in c.execute('SELECT * FROM official_run WHERE (? IS NULL OR source_id=?) ORDER BY started_at DESC LIMIT ?',(source_id,source_id,limit))]

    def last_scan(self):
        with self.personal.connection() as c:
            last=c.execute('SELECT batch_id FROM official_run ORDER BY started_at DESC LIMIT 1').fetchone()
            if not last: return None
            row=c.execute('''SELECT COUNT(*) AS total,SUM(finished_at IS NOT NULL) AS done,SUM(failed>0) AS failed,
                SUM(inserted) AS new_count,SUM(updated) AS changed_count FROM official_run WHERE batch_id=?''',(last[0],)).fetchone()
            current=c.execute("SELECT s.name FROM official_run r JOIN source s ON s.id=r.source_id WHERE batch_id=? AND finished_at IS NULL ORDER BY started_at DESC LIMIT 1",(last[0],)).fetchone()
            return {**dict(row),'current':current[0] if current else ''}

    def revalidation_entries(self, source, excluded):
        with self.personal.connection() as c:
            stamp=(datetime.now(timezone.utc)-timedelta(seconds=source.revalidate_interval)).isoformat()
            rows=c.execute('''SELECT r.canonical_url AS url,r.title,r.published_at FROM raw_item r JOIN official_item_source a ON a.raw_item_id=r.id
                JOIN official_fetch_state f ON f.raw_item_id=r.id WHERE a.source_id=? AND f.last_fetched_at<?
                ORDER BY f.last_fetched_at LIMIT ?''',(source.id,stamp,source.revalidate_limit+len(excluded))).fetchall()
            return [dict(r) for r in rows if r['url'] not in excluded][:source.revalidate_limit]

    def claim_notification(self, raw_id, max_count):
        with self.personal.connection(write=True) as c:
            item=c.execute('SELECT i.*,r.title,r.published_at FROM official_item i JOIN raw_item r ON r.id=i.raw_item_id WHERE raw_item_id=?',(raw_id,)).fetchone()
            if not item: return None
            recent=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
            if c.execute('SELECT COUNT(*) FROM official_notification WHERE claimed_at>?',(recent,)).fetchone()[0]>=max_count: return None
            if c.execute("INSERT OR IGNORE INTO official_notification VALUES (?,?,?,NULL,'claimed')",(item['display_group'],raw_id,now())).rowcount!=1: return None
            return dict(item)

    def finish_notification(self,group,result):
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE official_notification SET result=?,finished_at=? WHERE display_group=?',(result,now(),group))

    @staticmethod
    def _view(c,item):
        value=dict(item)
        value['id']=value['raw_item_id']
        value['reasons']=json.loads(value.pop('reasons_json'))
        value['time_evidence']=json.loads(value.pop('time_evidence_json'))
        state=c.execute('SELECT MAX(is_read),MAX(is_saved) FROM official_item WHERE display_group=?',(value['display_group'],)).fetchone()
        value['is_read'],value['is_saved']=bool(state[0]),bool(state[1])
        refs=c.execute('''SELECT DISTINCT s.id,s.name,o.config_json FROM official_item i JOIN official_item_source a ON a.raw_item_id=i.raw_item_id
            JOIN source s ON s.id=a.source_id JOIN official_source o ON o.source_id=s.id WHERE i.display_group=? ORDER BY s.name''',(value['display_group'],)).fetchall()
        value['sources']=[{'id':r['id'],'name':r['name'],'organization':json.loads(r['config_json'])['department']} for r in refs]
        value['organization']=value['sources'][0]['organization'] if value['sources'] else ''
        value['source_id']=value['sources'][0]['id'] if value['sources'] else None
        return value

    def feed(self,view='latest',offset=0,limit=30,source_id=None):
        filters={
            'latest':'1','all':'1','relevant':'i.relevance>=65',
            'academic':"i.category IN ('教务','课程','考试','选课','成绩','学籍')",
            'competition':"i.category='竞赛'",'international':"i.category='国际交流'",
            'employment':"i.category IN ('实习','就业','招聘')",
            'college':"EXISTS (SELECT 1 FROM official_item_source a JOIN official_source o ON o.source_id=a.source_id WHERE a.raw_item_id=i.raw_item_id AND json_extract(o.config_json,'$.source_type')='official_college')",
            'saved':"EXISTS (SELECT 1 FROM official_item g WHERE g.display_group=i.display_group AND g.is_saved=1)",
            'unread':"NOT EXISTS (SELECT 1 FROM official_item g WHERE g.display_group=i.display_group AND g.is_read=1)"}
        if view not in filters: raise ValueError('信息筛选无效。')
        limit=max(1,min(int(limit),50)); offset=max(0,int(offset))
        with self.personal.connection() as c:
            where=filters[view]+(' AND EXISTS (SELECT 1 FROM official_item_source a WHERE a.raw_item_id=i.raw_item_id AND a.source_id=?)' if source_id else '')
            params=[source_id] if source_id else []
            base=f'''WITH ranked AS (SELECT i.*,r.title,r.original_url AS url,r.canonical_url,r.published_at,f.latest_snapshot_id AS raw_snapshot_id,
                ROW_NUMBER() OVER (PARTITION BY i.display_group ORDER BY i.updated_at DESC,i.relevance DESC,r.id) AS rank,
                MAX(i.is_read) OVER (PARTITION BY i.display_group) AS group_read,MAX(i.is_saved) OVER (PARTITION BY i.display_group) AS group_saved
                FROM official_item i JOIN raw_item r ON r.id=i.raw_item_id JOIN official_fetch_state f ON f.raw_item_id=i.raw_item_id WHERE {where})'''
            total=c.execute(base+' SELECT COUNT(*) FROM ranked WHERE rank=1',params).fetchone()[0]
            order='relevance DESC,' if view=='relevant' else ''
            rows=c.execute(base+f' SELECT * FROM ranked WHERE rank=1 ORDER BY {order}COALESCE(published_at,substr(fetched_at,1,10)) DESC,fetched_at DESC,raw_item_id LIMIT ? OFFSET ?',[*params,limit,offset]).fetchall()
            return {'items':[self._view(c,r) for r in rows],'total':total,'offset':offset,'limit':limit,'has_more':offset+len(rows)<total}

    def detail(self,raw_id):
        with self.personal.connection() as c:
            row=c.execute('''SELECT i.*,r.title,r.original_url AS url,r.canonical_url,r.published_at,f.latest_snapshot_id AS raw_snapshot_id,
                (SELECT MAX(g.is_read) FROM official_item g WHERE g.display_group=i.display_group) AS group_read,
                (SELECT MAX(g.is_saved) FROM official_item g WHERE g.display_group=i.display_group) AS group_saved
                FROM official_item i JOIN raw_item r ON r.id=i.raw_item_id JOIN official_fetch_state f ON f.raw_item_id=i.raw_item_id WHERE i.raw_item_id=?''',(raw_id,)).fetchone()
            if not row: raise ValueError('信息不存在或正文尚未取得，请返回列表重试。')
            value=self._view(c,row)
            snap=c.execute('SELECT s.original_text,w.attachments_json,w.images_json,w.version,w.body_status FROM raw_snapshot s JOIN raw_snapshot_web w ON w.raw_snapshot_id=s.id WHERE s.id=?',(value['raw_snapshot_id'],)).fetchone()
            value.update(content=snap['original_text'],attachments=json.loads(snap['attachments_json']),images=json.loads(snap['images_json']),version=snap['version'],body_status=snap['body_status'])
            value['references']=[dict(r) for r in c.execute('''SELECT a.url FROM official_url_alias a JOIN official_item i ON i.raw_item_id=a.raw_item_id WHERE i.display_group=? ORDER BY a.url''',(value['display_group'],))]
            return value

    def set_item_state(self,raw_id,*,is_read=None,is_saved=None):
        if is_read is None and is_saved is None: raise ValueError('请选择阅读或收藏状态。')
        if any(type(v) is not bool for v in (is_read,is_saved) if v is not None): raise ValueError('状态必须是布尔值。')
        with self.personal.connection(write=True) as c:
            group=c.execute('SELECT display_group FROM official_item WHERE raw_item_id=?',(raw_id,)).fetchone()
            if not group: raise ValueError('信息不存在。')
            for column,value in [('is_read',is_read),('is_saved',is_saved)]:
                if value is not None: c.execute(f'UPDATE official_item SET {column}=? WHERE display_group=?',(int(value),group[0]))
        return self.detail(raw_id)
