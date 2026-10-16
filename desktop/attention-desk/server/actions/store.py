"""Remote official-state adapter; desktop parsing/classification is reused."""
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..official_rules import classify
from ..official_sources import SourceConfig, SourceFailure, url_identity
from ..personal_store import encode, now


class PostgresOfficialStore:
    def __init__(self, database):
        self.database = database

    def meta(self):
        with self.database.connection() as db:
            row=db.execute('SELECT * FROM zqu_worker.meta WHERE id=1').fetchone()
        if not row or not row['import_completed_at'] or not row['activation_at']:
            raise ValueError('远程学校数据尚未完成静默迁移，拒绝抓取或推送。')
        return row

    def configure(self, sources):
        with self.database.connection() as db:
            for source in sources:
                db.execute('''INSERT INTO zqu_worker.sources(id,config,state) VALUES(%s,%s,%s)
                    ON CONFLICT(id) DO UPDATE SET config=excluded.config''',
                    (source.id,Jsonb(source.model_dump()),Jsonb({'enabled':source.enabled,'status':'idle','failure_count':0})))

    def source_rows(self):
        with self.database.connection() as db:
            rows=db.execute('SELECT * FROM zqu_worker.sources ORDER BY id').fetchall()
        return [{'backfill_completed_at':None,**r['config'],**r['state'],'id':r['id'],
            'next_check_at':r['next_check_at'].isoformat() if r['next_check_at'] else None} for r in rows]

    def begin(self, source_id, batch_id=None):
        run_id=str(uuid4())
        with self.database.connection() as db:
            db.execute("UPDATE zqu_worker.sources SET state=state||%s WHERE id=%s",
                (Jsonb({'status':'checking','last_checked_at':now()}),source_id))
            db.execute("INSERT INTO zqu_worker.crawl_runs(id,batch_id,source_id,status) VALUES(%s,%s,%s,'running')",(run_id,batch_id,source_id))
        return run_id

    def finish(self, source_id, counts, error=None, run_id=None):
        with self.database.connection() as db:
            row=db.execute('SELECT * FROM zqu_worker.sources WHERE id=%s FOR UPDATE',(source_id,)).fetchone()
            state,config=row['state'],row['config']
            failures=state.get('failure_count',0)+1 if error else 0
            # Hourly Actions never increases school polling frequency.
            interval=max(3600,config['polling_interval'])
            delay=max(interval,min(86400*4,1800*2**min(max(0,failures-1),7))) if error else interval
            status='partial' if error and counts.get('saved',0) else error.status if error else 'ok'
            state.update(status=status,failure_count=failures,error=error.code if error else '',
                last_new_count=counts['new'],last_changed_count=counts['changed'],
                last_discovered_count=counts.get('discovered',0),last_unchanged_count=counts['unchanged'],
                last_request_count=counts['requests'])
            if not error:
                state.update(last_success_at=now(),backfill_completed_at=state.get('backfill_completed_at') or now())
            db.execute('UPDATE zqu_worker.sources SET state=%s,next_check_at=%s WHERE id=%s',
                (Jsonb(state),datetime.now(timezone.utc)+timedelta(seconds=delay),source_id))
            if run_id:
                db.execute('UPDATE zqu_worker.crawl_runs SET finished_at=clock_timestamp(),status=%s,counts=%s,error_code=%s WHERE id=%s',
                    (status,Jsonb(counts),error.code if error else '',run_id))

    @staticmethod
    def _find(db, url):
        return db.execute('''SELECT i.* FROM zqu_worker.items i LEFT JOIN zqu_worker.aliases a ON a.item_id=i.id
            WHERE i.canonical_url=%s OR a.url IN (%s,%s) LIMIT 1''',(url_identity(url),url,url_identity(url))).fetchone()

    @staticmethod
    def _associate(db, item_id, source_id):
        db.execute('''INSERT INTO zqu_worker.source_refs(item_id,source_id) VALUES(%s,%s)
            ON CONFLICT(item_id,source_id) DO UPDATE SET last_seen_at=clock_timestamp()''',(item_id,source_id))

    def discover(self, source, entry, *, backfill=False):
        with self.database.connection() as db:
            row=self._find(db,entry['url'])
            item_id=row['id'] if row else str(uuid4())
            if not row:
                body={**entry,'raw_item_id':item_id,'canonical_url':url_identity(entry['url']),
                    'discovered_at':now(),'official_backfill':int(backfill)}
                db.execute('INSERT INTO zqu_worker.items(id,canonical_url,body) VALUES(%s,%s,%s)',(item_id,body['canonical_url'],Jsonb(body)))
            for url in {entry['url'],url_identity(entry['url'])}:
                db.execute('INSERT INTO zqu_worker.aliases VALUES(%s,%s) ON CONFLICT DO NOTHING',(url,item_id))
            self._associate(db,item_id,source.id)
            return {'raw_item_id':item_id,'new':row is None}

    def fetch_error(self, raw_id, error):
        with self.database.connection() as db:
            db.execute('UPDATE zqu_worker.items SET body=body||%s WHERE id=%s',(Jsonb({'fetch_error':error.code}),raw_id))

    def fetch_state(self, url):
        with self.database.connection() as db:
            row=self._find(db,url)
            if not row or not row['content_hash']:return None
            return {**row['body'],'raw_item_id':row['id'],'latest_snapshot_id':row['id']}

    def unchanged(self, source_id, url, state):
        with self.database.connection() as db:
            self._associate(db,state['raw_item_id'],source_id)
            db.execute('UPDATE zqu_worker.items SET body=body||%s WHERE id=%s',(Jsonb({'last_fetched_at':now(),'fetch_error':''}),state['raw_item_id']))
        return {'kind':'unchanged','raw_snapshot_id':state['raw_item_id']}

    def collect(self, source, original_url, canonical, article, result, *, backfill=False):
        fingerprint=hashlib.sha256(encode({k:article[k] for k in ('original_text','title','published_at','attachments','images','body_status')}).encode()).hexdigest()
        canonical=url_identity(canonical)
        with self.database.connection() as db:
            discovered=self._find(db,original_url)
            existing=db.execute('SELECT * FROM zqu_worker.items WHERE canonical_url=%s',(canonical,)).fetchone()
            if existing and discovered and existing['id']!=discovered['id']:
                has_history=db.execute('SELECT 1 FROM zqu_worker.notification_events WHERE item_id=%s LIMIT 1',(discovered['id'],)).fetchone()
                if discovered['content_hash'] or has_history:
                    raise SourceFailure('canonical_conflict','两个已保存原文现在指向同一地址，保留历史。')
                for ref in db.execute('SELECT source_id FROM zqu_worker.source_refs WHERE item_id=%s',(discovered['id'],)).fetchall():
                    self._associate(db,existing['id'],ref['source_id'])
                db.execute('UPDATE zqu_worker.aliases SET item_id=%s WHERE item_id=%s',(existing['id'],discovered['id']))
                # Only a new unprojected placeholder can be merged. References were
                # copied above; no cascading FK can erase historical state.
                db.execute('DELETE FROM zqu_worker.source_refs WHERE item_id=%s',(discovered['id'],))
                db.execute("DELETE FROM zqu_worker.items WHERE id=%s AND content_hash='' AND NOT EXISTS (SELECT 1 FROM zqu_worker.notification_events WHERE item_id=%s)",
                    (discovered['id'],discovered['id']))
            prior=existing or discovered
            if not prior:
                raise ValueError('必须先保存列表 identity，再保存原文。')
            item_id=prior['id']
            self._associate(db,item_id,source.id)
            configs=[r['config'] for r in db.execute('''SELECT s.config FROM zqu_worker.sources s JOIN zqu_worker.source_refs r ON r.source_id=s.id
                WHERE r.item_id=%s''',(item_id,)).fetchall()]
            value=max((classify(article,SourceConfig.model_validate(config)) for config in configs),key=lambda v:v['relevance'])
            duplicate=db.execute('''SELECT display_group FROM zqu_worker.items WHERE id<>%s AND content_hash=%s
                AND (title_hash=%s OR %s>=200) ORDER BY id LIMIT 1''',(item_id,value['content_hash'],value['title_hash'],len(article['original_text']))).fetchone()
            group=duplicate['display_group'] if duplicate else prior['display_group'] or item_id
            body={**prior['body'],**value,**{k:article[k] for k in ('title','published_at','original_text','attachments','images','body_status')},
                'raw_item_id':item_id,'canonical_url':canonical,'display_group':group,'etag':result.etag,
                'last_modified':result.last_modified,'last_fetched_at':now(),'fetched_at':now(),'fetch_error':'','version_hash':fingerprint}
            kind='new' if not prior['content_hash'] else 'unchanged' if prior['body'].get('version_hash')==fingerprint else 'changed'
            db.execute('UPDATE zqu_worker.items SET canonical_url=%s,body=%s,content_hash=%s,title_hash=%s,display_group=%s WHERE id=%s',
                (canonical,Jsonb(body),value['content_hash'],value['title_hash'],group,item_id))
            for url in {canonical,original_url,url_identity(original_url)}:
                db.execute('INSERT INTO zqu_worker.aliases VALUES(%s,%s) ON CONFLICT DO NOTHING',(url,item_id))
            return {'kind':kind,'raw_item_id':item_id,'raw_snapshot_id':item_id}

    def revalidation_entries(self, source, excluded):
        cutoff=(datetime.now(timezone.utc)-timedelta(seconds=source.revalidate_interval)).isoformat()
        with self.database.connection() as db:
            rows=db.execute('''SELECT i.body FROM zqu_worker.items i JOIN zqu_worker.source_refs r ON r.item_id=i.id
                WHERE r.source_id=%s AND i.content_hash<>'' AND i.body->>'last_fetched_at'<%s
                ORDER BY i.body->>'last_fetched_at' LIMIT %s''',(source.id,cutoff,source.revalidate_limit+len(excluded))).fetchall()
        return [{'url':r['body']['canonical_url'],'title':r['body']['title'],'published_at':r['body']['published_at']}
                for r in rows if r['body']['canonical_url'] not in excluded][:source.revalidate_limit]

    def acquire_lease(self, owner, name='sync',seconds=90):
        with self.database.connection() as db:
            row=db.execute('''INSERT INTO zqu_worker.leases(name,owner,expires_at) VALUES(%s,%s,clock_timestamp()+%s*interval '1 second')
                ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at
                WHERE zqu_worker.leases.expires_at<clock_timestamp() RETURNING name''',(name,owner,seconds)).fetchone()
            return bool(row)

    def renew_lease(self, owner):
        with self.database.connection() as db:
            return db.execute("UPDATE zqu_worker.leases SET expires_at=clock_timestamp()+interval '90 seconds' WHERE name='sync' AND owner=%s AND expires_at>clock_timestamp()",(owner,)).rowcount==1

    def release_lease(self, owner,name='sync'):
        with self.database.connection() as db:db.execute('DELETE FROM zqu_worker.leases WHERE name=%s AND owner=%s',(name,owner))

    @staticmethod
    def candidate(db, raw_id):
        row=db.execute('SELECT body FROM zqu_worker.items WHERE id=%s',(raw_id,)).fetchone()
        sources=db.execute('''SELECT s.config,s.state FROM zqu_worker.sources s JOIN zqu_worker.source_refs r ON r.source_id=s.id
            WHERE r.item_id=%s ORDER BY s.id''',(raw_id,)).fetchall()
        return (row['body'] if row else None),[r['config'] for r in sources if r['state'].get('enabled',r['config']['enabled'])]


class OneShotRuntimeStore:
    """No resident scheduler bookkeeping or SQLite mount requirement in Actions."""
    def initialize(self):pass
    def release(self):pass
