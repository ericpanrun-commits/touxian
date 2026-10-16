"""Manual hosted acceptance: one shared pipeline push, durable one-attempt budget.

No crawler requests or ordinary queue dispatch. Clearly labelled synthetic items
exercise the existing parser/classifier/policy/outbox/dispatcher/Bark path. The
permanent receipt in leases survives item cleanup, runner restarts and log expiry.
"""
from dataclasses import replace
from datetime import datetime, timezone
import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..notifications.config import BarkConfig
from ..notifications.service import NotificationDispatcher
from ..official_http import HTMLResult
from ..official_parsers import parse_article
from ..official_sources import SourceConfig
from .notifications import PostgresNotificationStore
from .store import PostgresOfficialStore

RECEIPT='cloud-acceptance-bark-v1'


class AcceptanceNotifications(PostgresNotificationStore):
    def start_request(self,row,*,at):
        # Commit the irreversible budget BEFORE HTTP. An uncertain outcome
        # spends the budget too; there is no automatic reset or resend option.
        with self.database.connection() as db:
            reserved=db.execute("""INSERT INTO zqu_worker.leases(name,owner,expires_at)
                VALUES(%s,'reserved','9999-01-01T00:00:00Z') ON CONFLICT DO NOTHING RETURNING name""",
                (RECEIPT,)).fetchone()
        return bool(reserved) and super().start_request(row,at=at)

    def finish(self,row,result,*,at):
        super().finish(row,result,at=at)
        with self.database.connection() as db:
            db.execute('UPDATE zqu_worker.leases SET owner=%s WHERE name=%s',
                ('accepted' if result.accepted else 'failed_or_unknown',RECEIPT))


def run_acceptance(runtime,database,*,event,bark_config=None,client=None):
    started=time.monotonic()
    if (event!='workflow_dispatch' or runtime.environment!='production' or
            not runtime.execution_verified or not runtime.auto_sync or
            not runtime.notification_authority or runtime.dry_run):
        raise ValueError('受控验收只允许明确的手动生产运行，且不能使用 dry run。')
    official=PostgresOfficialStore(database)
    meta=official.meta()
    if (meta['database_id']!=runtime.expected_database_id or meta['schema_version']!=1 or
            not meta['first_dry_run_verified']):
        raise ValueError('受控验收需要匹配的生产身份与已完成的首轮真实静默验证。')
    configuration=bark_config or BarkConfig.from_env(dict(os.environ))
    if not configuration.enabled or not configuration.configured or configuration.error or not meta['enabled']:
        raise ValueError('受控验收需要有效 Bark 配置与已启用的通知策略。')
    runtime=replace(runtime,storage_verified=True)
    owner=str(uuid4());items=[];run_id=str(uuid4())
    summary={'status':'failed','dry_run':False,'sent':0,'candidates':0,'suppressed':0,'would_send':0,
             'duplicate_prevented':0,'low_relevance_suppressed':0}
    if not official.acquire_lease(owner,'actions',900):return {**summary,'status':'skipped_concurrent'}
    try:
        with database.connection() as db:
            receipt=db.execute('SELECT owner FROM zqu_worker.leases WHERE name=%s',(RECEIPT,)).fetchone()
            if receipt:
                summary.update(status='ok' if receipt['owner']=='accepted' else 'failed',duplicate_prevented=1)
                return summary
            row=db.execute("""SELECT config FROM zqu_worker.sources
                WHERE COALESCE((state->>'enabled')::boolean,(config->>'enabled')::boolean)
                AND config->>'source_type' IN ('official_school','official_college','official_department')
                ORDER BY id LIMIT 1""").fetchone()
            if not row:raise ValueError('没有可信且启用的官方来源用于受控验收。')
            source=SourceConfig.model_validate(row['config'])
            db.execute("INSERT INTO zqu_worker.worker_runs(id,github_run,status,dry_run) VALUES(%s,%s,'running',false)",
                (run_id,runtime.run_id))
        date=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        titles=['[test] ZQU GitHub Worker：2026级车辆工程选课截止时间变更',
                '[test] 教师教学竞赛报名通知']
        for index,title in enumerate(titles):
            address=urlsplit(source.list_url)
            query=parse_qsl(address.query,keep_blank_values=True)+[('zqu_acceptance_test',owner+':'+str(index))]
            url=urlunsplit(address._replace(query=urlencode(query)))
            entry={'url':url,'title':title,'published_at':date}
            with database.connection() as db:
                if official._find(db,url):raise ValueError('受控验收地址与已有记录冲突。')
            item_id=official.discover(source,entry)['raw_item_id'];items.append(item_id)
            with database.connection() as db:
                db.execute('UPDATE zqu_worker.items SET body=body||%s WHERE id=%s',
                    (Jsonb({'acceptance_test':True}),item_id))
            # Locally generated fixture, explicitly not a real school announcement.
            html=f'<h1>{title}</h1><div class="v_news_content">[test] 云端通知链路验收，不是学校实际公告，无需办理任何事项。</div>'.encode()
            article=parse_article(html,url,source,entry)
            official.collect(source,url,url,article,HTMLResult(url,b'',200))
        repository=AcceptanceNotifications(database,owner,item_ids=items)
        dispatcher=NotificationDispatcher(None,config=configuration,client=client,runtime=runtime,
            repository=repository,official_repository=official)
        # Check the negative control before spending the one HTTP attempt. Never
        # change production preferences to make an acceptance test pass.
        repository.reconcile(configuration,at=datetime.now(timezone.utc))
        with database.connection() as db:
            low=db.execute('SELECT status FROM zqu_worker.notification_events WHERE item_id=%s',(items[1],)).fetchone()
            low_item=db.execute('SELECT body FROM zqu_worker.items WHERE id=%s',(items[1],)).fetchone()['body']
        summary.update(repository.counters)
        summary['low_relevance_suppressed']=int(bool(low and low['status']=='suppressed' and
            low_item['relevance']<meta['policy']['minimum_relevance']))
        if not summary['low_relevance_suppressed']:
            raise ValueError('当前策略未抑制低相关受控样本；未发送测试通知。')
        dispatcher.tick(limit=1)
        summary.update(repository.counters)
        summary['status']='ok' if summary['sent']==1 and summary['low_relevance_suppressed']==1 and not dispatcher.last_cycle_error else 'failed'
        return summary
    finally:
        summary['elapsed_seconds']=round(time.monotonic()-started,2)
        try:
            with database.connection() as db:
                # Delete only this run's synthetic rows, in FK order. The receipt
                # persists; ordinary queue code excludes any surviving test rows.
                for table,column in [('notification_events','item_id'),('source_refs','item_id'),('aliases','item_id'),('items','id')]:
                    db.execute(f'DELETE FROM zqu_worker.{table} WHERE {column}=ANY(%s)',(items,))
                db.execute('UPDATE zqu_worker.worker_runs SET status=%s,finished_at=clock_timestamp(),summary=%s WHERE id=%s',
                    (summary['status'],Jsonb({**summary,'mode':'acceptance'}),run_id))
        finally:official.release_lease(owner,'actions')
