"""Automatic saved-media recovery. Separate from DB intake and its cursors."""
from __future__ import annotations
import asyncio,hashlib,json,mimetypes,os,shutil,subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path

from ..personal_store import encode,identifier,now,PersonalError
from ..documents.storage import AttachmentStore
from .batching import BatchPlan
from .ingestion import prepare_batch
from .media import MediaStore
from .quality import usable_text,register_media
from .file_evidence import enrich_existing

ROOT=Path(__file__).resolve().parents[2]
NOTICE=__import__('re').compile(r'通知|见图|看图|上面|提交|截止|完成|考试|作业|报名|活动|比赛|集合|材料|安排')
REASONS={
 'local_cache_missing':'微信尚未缓存这个附件，会自动继续检查。',
 'missing':'微信尚未缓存原件，会自动继续检查。',
 'local_cache_unavailable':'本地缓存暂时不可读，稍后自动继续。',
 'requires_message_bound_resource_path':'文件名已保留，尚缺少这条消息对应的本地文件路径。',
 'encrypted_evidence_saved':'加密原件已保留，当前解密条件不足。',
 'unsupported_local_format':'缓存格式暂不支持，可查看已保存原件。',
 'ocr_insufficient_no_notice_context':'图片文字不足，附近也没有通知线索，未使用付费识别。',
 'vision_unreadable':'视觉识别后仍无法确认内容，原件保留。',
 'vision_image_budget':'图片过长或过大，需分区识别，原件保留。',
 'budget_deferred':'等待现有识别预算允许后自动继续。',
 'timeout':'识别请求结果未知，已保留原件，避免重复计费。',
 'media_pending':'文字仍不足以理解通知，原件保留。',
 'ocr_failed':'本地文字识别未完成，原件保留。',
 'media_recovery_context':'媒体关联不完整，原件已保留，请在原通知中重新核对。',
 'media_recovery_reviewed':'已有分析结果或人工核对内容，保留当前版本，请在原核对流程中处理。',
 'media_recovery_scope':'来源当前未启用或未授权，恢复分析已暂停，原件保留。',
 'media_job_enqueue_failed':'分析任务未能入队，原件已保留，后续处理会继续恢复。',
 'media_job_scope_conflict':'相同输入已有其他批次的分析，未重复付费；如需重做，请在原核对流程中显式重新分析。',
}

def reason_text(code):return REASONS.get(code,'媒体内容尚未可靠识别，原件和上下文已保留。')

def media_status(personal):
    with personal.connection() as c:
        rows=c.execute("SELECT q.message_id,m.message_type,w.status,w.last_error,w.next_attempt_at FROM wechat_attention_media q JOIN wechat_message m ON m.id=q.message_id LEFT JOIN wechat_media_work w ON w.message_id=q.message_id WHERE q.status='media_pending'").fetchall()
        return {'pending':len(rows),'images':sum(r['message_type']=='image' for r in rows),'files':sum(r['message_type']=='file' for r in rows),'processing':sum(r['status']=='analyzing' for r in rows)}

def parse_time(value):
    try:
        result=datetime.fromisoformat(value)
        return result if result.tzinfo else result.replace(tzinfo=__import__('zoneinfo').ZoneInfo('Asia/Shanghai'))
    except (TypeError,ValueError):return None

def reference(value):
    try:
        result=json.loads(value or '{}')
        return result if isinstance(result,dict) else {}
    except (ValueError,TypeError):return {}

def related_groups(rows):
    groups=[]
    for row in sorted(rows,key=lambda r:(r['chat_id'],r['ingest_seq'])):
        identity=reference(row.get('sender_identity'))
        sender=identity.get('username') if identity.get('status')=='resolved' else row['sender'] if row.get('sender_reliability')=='reliable' else row['id']
        key=(row['chat_id'],sender)
        stamp=parse_time(row.get('source_time')) or parse_time(row.get('captured_at'))
        previous=groups[-1][-1] if groups else None
        if previous and previous['_sender']==key and len(groups[-1])<4 and row['ingest_seq']-previous['ingest_seq']<=3 and stamp and previous['_stamp'] and abs((stamp-previous['_stamp']).total_seconds())<=120:
            groups[-1].append({**row,'_sender':key,'_stamp':stamp})
        else:groups.append([{**row,'_sender':key,'_stamp':stamp}])
    return groups


class MediaPipeline:
    def __init__(self,service,*,locator=None):
        self.service=service;self.personal=service.personal
        self.media=MediaStore(self.personal,self);self.files=AttachmentStore(self.personal)
        self.locator=locator or self._locate;self.lock=asyncio.Lock();self.last_tick=None

    @property
    def policy(self):return self.service.policy

    def require(self,**kwargs):return self.service.policy.require(**kwargs)

    def _locate(self,rows,target):
        self.policy.require(operation='media',maximum_seconds=60)
        adapter=self.service.adapter
        if not getattr(adapter,'is_database',False) or not getattr(adapter,'is_real',False):return []
        if not self.service.document.wechat.allows(target) or not adapter.certified:return []
        with self.personal.connection() as c:
            cursor=c.execute('SELECT * FROM wechat_db_cursor WHERE chat_id=?',(target.id,)).fetchone()
            if not cursor:return []
            messages=[]
            for row in rows:
                identity=c.execute('SELECT * FROM wechat_db_message WHERE message_id=?',(row['id'],)).fetchone()
                if not identity:continue
                messages.append({'message_id':row['id'],'local_id':identity['local_id'],'shard':identity['shard'],'source_content_sha256':identity['secondary_fingerprint'],'reference':reference(row.get('reference_json')) or reference(row.get('attachment_identity'))})
        report=json.loads((ROOT/'artifacts/wechat-db0/results/report.json').read_text(encoding='utf-8'))
        config=self.service.document.wechat
        request={'chat':target.name,'chatroom_id':cursor['internal_chat_id'],'account_scope':cursor['account_scope'],'allowed_chats':config.allowed_chats,'allowed_chatrooms':list(set(config.allowed_chatrooms)|{cursor['internal_chat_id']}),'messages':messages,'verified_version':report['wechat_version'],'workdir':str(ROOT/'artifacts/wechat-db-runtime')}
        result=subprocess.run([str(adapter.python),'-m','server.wechat_collect.media_recovery_worker'],input=json.dumps(request,ensure_ascii=False),text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,cwd=ROOT,timeout=55,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        value=json.loads(result.stdout)
        if not value.get('ok'):raise PersonalError('本地媒体暂时无法读取。',value.get('code','media_lookup_failed'),503)
        self.policy.require(operation='media')
        return value['result']['items']

    def _owned_reference(self,ref):
        result=ref.copy()
        for field in ('evidence_path','encrypted_evidence_path'):
            if not ref.get(field):continue
            path=Path(ref[field]).resolve()
            if not path.is_file():continue
            if not path.is_relative_to(self.files.root.resolve()) and not path.is_relative_to((ROOT/'artifacts/wechat-db-runtime/media').resolve()):
                raise PersonalError('媒体来源不属于授权暂存区。','invalid_media_path',409)
            saved,digest,_=self.files.preserve(path,path.suffix.lstrip('.'))
            result[field]=saved
            result['encrypted_evidence_sha256' if field.startswith('encrypted') else 'evidence_sha256']=digest
        return result

    def _seed(self):
        with self.personal.connection(write=True) as c:
            c.execute("INSERT OR IGNORE INTO wechat_media_work(message_id) SELECT message_id FROM wechat_attention_media WHERE status='media_pending'")
            # Filename metadata must survive even when the native DB row labels
            # a file/card generically. Ownership is checked before file access.
            c.execute("INSERT OR IGNORE INTO wechat_media_work(message_id) SELECT message_id FROM wechat_db_message WHERE json_extract(media_reference_json,'$.kind')='file'")
            for row in c.execute("SELECT w.message_id,m.attachment_identity FROM wechat_media_work w JOIN wechat_message m ON m.id=w.message_id WHERE w.reference_json='{}'").fetchall():
                c.execute('UPDATE wechat_media_work SET reference_json=? WHERE message_id=?',(encode(reference(row['attachment_identity'])),row['message_id']))
            c.execute("""INSERT OR IGNORE INTO wechat_attention_media(message_id,batch_id,snapshot_id,reason,created_at)
                SELECT w.message_id,b.id,b.snapshot_id,?,? FROM wechat_media_work w
                JOIN wechat_batch_message bm ON bm.message_id=w.message_id AND bm.role='primary'
                JOIN wechat_batch b ON b.id=bm.batch_id AND b.superseded_by IS NULL
                WHERE w.status!='completed' AND json_extract(w.reference_json,'$.kind')='file'
                ORDER BY b.created_at""",(reason_text('local_cache_missing'),now()))

    def _enqueue_prepared(self,c,batch_id):
        # Legacy batches could share input_hash across media revisions. Reusing a
        # foreign job as if it belonged here caused the original orphan; generating
        # a fresh key/profile automatically would instead repeat a possibly billed input.
        if c.execute("""SELECT 1 FROM wechat_analysis_job j JOIN wechat_batch previous ON previous.id=j.batch_id
            JOIN wechat_batch current ON current.id=?
            WHERE previous.input_hash=current.input_hash AND previous.id!=current.id LIMIT 1""",(batch_id,)).fetchone():
            raise PersonalError(reason_text('media_job_scope_conflict'),'media_job_scope_conflict',409)
        job_id=self.service.analysis.enqueue(batch_id,self.service.context.read(),connection=c)
        if not c.execute('SELECT 1 FROM wechat_analysis_job WHERE id=? AND batch_id=?',(job_id,batch_id)).fetchone():
            raise PersonalError(reason_text('media_job_scope_conflict'),'media_job_scope_conflict',409)
        return job_id

    def recover_missing_jobs(self):
        """Repair prepared media locally; never dispatch HTTP or regenerate source/result IDs."""
        created=linked=blocked=0
        targets={t.id:t for t in self.service.document.wechat.priority_chats if t.enabled}
        with self.personal.connection(write=True) as c:
            work=c.execute("""SELECT w.*,b.chat_id,b.snapshot_id,b.superseded_by,b.status AS batch_status,
                a.input_key AS prepared_key,r.status AS review_status,r.extraction_id,r.draft_json
                FROM wechat_media_work w LEFT JOIN wechat_batch b ON b.id=w.batch_id
                LEFT JOIN wechat_media_analysis a ON a.batch_id=b.id
                LEFT JOIN raw_review r ON r.raw_snapshot_id=b.snapshot_id
                WHERE w.status IN ('pending','analyzing','recovery_needed')
                  AND (w.batch_id IS NOT NULL OR w.status IN ('analyzing','recovery_needed'))""").fetchall()
            for row in work:
                # BEGIN IMMEDIATE serializes this check with concurrent preparation/recovery.
                job=c.execute('SELECT id FROM wechat_analysis_job WHERE batch_id=? LIMIT 1',(row['batch_id'],)).fetchone()
                if job:
                    if row['status']!='analyzing':
                        c.execute("UPDATE wechat_media_work SET status='analyzing',last_error=NULL WHERE message_id=?",(row['message_id'],))
                        linked+=1
                    continue
                target=targets.get(row['chat_id'])
                code=None
                if row['status']=='recovery_needed' and row['last_error'] in {'media_job_scope_conflict','media_recovery_reviewed','media_recovery_context','media_recovery_scope'}:
                    code=row['last_error']
                elif not row['prepared_key'] or row['prepared_key']!=row['input_key'] or row['superseded_by'] or not row['snapshot_id']:
                    code='media_recovery_context'
                elif row['review_status']!='pending' or row['draft_json'] is not None or row['extraction_id'] is not None or row['batch_status'] not in {'pending','queued','media_pending','waiting_context'}:
                    code='media_recovery_reviewed'
                elif c.execute("SELECT 1 FROM wechat_candidate WHERE batch_id=? AND model_item_id!='_pending' LIMIT 1",(row['batch_id'],)).fetchone():
                    code='media_recovery_reviewed'
                elif not self.service.document.wechat.enabled or target is None or (getattr(self.service.adapter,'is_database',False) and not self.service.document.wechat.allows(target)):
                    code='media_recovery_scope'
                else:
                    c.execute('SAVEPOINT media_job_recovery')
                    try:
                        self._enqueue_prepared(c,row['batch_id'])
                    except Exception as exc:
                        # An enqueue failure can occur after inserting the extraction row.
                        # Roll back that entire attempt before exposing a recoverable state.
                        c.execute('ROLLBACK TO media_job_recovery')
                        c.execute('RELEASE media_job_recovery')
                        code='media_job_scope_conflict' if isinstance(exc,PersonalError) and exc.code=='media_job_scope_conflict' else 'media_job_enqueue_failed'
                    else:
                        c.execute('RELEASE media_job_recovery')
                        c.execute("UPDATE wechat_media_work SET status='analyzing',last_error=NULL WHERE message_id=?",(row['message_id'],))
                        created+=1
                if code:
                    c.execute("UPDATE wechat_media_work SET status='recovery_needed',last_error=? WHERE message_id=? AND (status!='recovery_needed' OR last_error IS NOT ?)",
                              (code,row['message_id'],code))
                    c.execute("UPDATE wechat_attention_media SET reason=? WHERE message_id=? AND status='media_pending'",(reason_text(code),row['message_id']))
                    if code!='media_job_enqueue_failed':
                        # The generic text queue also scans pending batches. Manual recovery
                        # must not be bypassed there after a later group change/profile edit.
                        c.execute("UPDATE wechat_batch SET status='manual_review' WHERE id=? AND status IN ('pending','waiting_context','queued','media_pending')",(row['batch_id'],))
                    blocked+=1
        return {'created':created,'linked':linked,'recovery_needed':blocked}

    def _observe(self):
        with self.personal.connection(write=True) as c:
            for work in c.execute("SELECT w.*,j.status AS job_status,j.error_code,b.snapshot_id FROM wechat_media_work w JOIN wechat_analysis_job j ON j.batch_id=w.batch_id JOIN wechat_batch b ON b.id=j.batch_id WHERE w.status='analyzing'").fetchall():
                if work['job_status']=='parsed':
                    c.execute("UPDATE wechat_media_work SET status='completed',completed_at=?,last_error=NULL WHERE message_id=?",(now(),work['message_id']))
                    c.execute("UPDATE wechat_attention_media SET status='resolved',resolved_at=?,snapshot_id=? WHERE message_id=?",(now(),work['snapshot_id'],work['message_id']))
                    c.execute("UPDATE wechat_candidate SET status='media_resolved' WHERE status='media_pending' AND EXISTS(SELECT 1 FROM json_each(data_json,'$.evidence_message_ids') WHERE value=?)",(work['message_id'],))
                elif work['job_status'] not in {'pending','retry','budget_deferred','running'}:
                    c.execute("UPDATE wechat_media_work SET status='unreadable',last_error=? WHERE message_id=?",(work['error_code'] or work['job_status'],work['message_id']))
                    c.execute("UPDATE wechat_attention_media SET status='media_pending',reason=? WHERE message_id=?",(reason_text(work['error_code']),work['message_id']))
            for cache in c.execute("SELECT * FROM wechat_media_parse_cache WHERE engine='vision' AND status='running'").fetchall():
                detail=reference(cache['detail_json']);attempt=c.execute('SELECT status FROM wechat_llm_attempt WHERE id=?',(detail.get('attempt_id'),)).fetchone()
                if attempt and attempt['status'] not in {'running','success'}:c.execute("UPDATE wechat_media_parse_cache SET status='unknown' WHERE sha256=? AND engine=? AND version=?",(cache['sha256'],cache['engine'],cache['version']))

    def _rows(self):
        with self.personal.connection() as c:
            return [dict(r) for r in c.execute("SELECT m.*,w.status AS work_status,w.reference_json,w.media_hash,w.input_key,w.next_attempt_at,d.media_reference_json,d.local_id,d.shard,d.secondary_fingerprint,s.data_json AS sender_identity FROM wechat_media_work w JOIN wechat_message m ON m.id=w.message_id JOIN wechat_chat ch ON ch.id=m.chat_id LEFT JOIN wechat_db_message d ON d.message_id=m.id LEFT JOIN wechat_sender_identity s ON s.message_id=m.id WHERE w.status!='completed' AND ch.enabled=1 ORDER BY m.ingest_seq LIMIT 40")]

    async def tick(self,*,force=False):
        at=self.service.clock()
        if not self.policy.authorize(operation='media',maximum_seconds=60).allowed or self.lock.locked() or self.service.lock.locked():return
        if not force and self.last_tick and (at-self.last_tick).total_seconds()<60:return
        self.last_tick=at
        async with self.lock:
            self._seed();self.recover_missing_jobs();self._observe();enrich_existing(self.personal);rows=self._rows()
            targets={t.id:t for t in self.service.document.wechat.priority_chats if t.enabled}
            ready=[]
            for row in rows:
                target=targets.get(row['chat_id'])
                if not target or getattr(self.service.adapter,'is_database',False) and not self.service.document.wechat.allows(target):continue
                if row['work_status'] in {'analyzing','recovery_needed'}:continue
                ref=reference(row['reference_json'])
                with self.personal.connection() as c:latest=c.execute('SELECT * FROM wechat_media WHERE message_id=? ORDER BY revision DESC,created_at DESC LIMIT 1',(row['id'],)).fetchone()
                if not latest and row['next_attempt_at'] and parse_time(row['next_attempt_at'])>at:continue
                low_thumbnail=latest and not usable_text('\n'.join(b['text'] for b in json.loads(latest['blocks_json'] or '[]'))) and ref.get('evidence_tier')=='thumb'
                if (not latest or low_thumbnail) and (not row['next_attempt_at'] or parse_time(row['next_attempt_at'])<=at):
                    try:
                        results=await asyncio.to_thread(self.locator,[row],target)
                        if results:ref=self._owned_reference(results[0].get('reference',ref))
                        self.policy.require(operation='media')
                        with self.personal.connection(write=True) as c:c.execute('UPDATE wechat_media_work SET next_attempt_at=? WHERE message_id=?',((at+timedelta(minutes=15)).isoformat(),row['id']))
                    except (PersonalError,OSError,ValueError,subprocess.TimeoutExpired):ref['cache_status']='local_cache_unavailable'
                path=Path(ref['evidence_path']) if ref.get('evidence_path') else None
                if (not latest or ref.get('evidence_sha256') and ref['evidence_sha256']!=latest['sha256']) and path and path.is_file() and (row['message_type']=='image' or path.suffix.lower() in {'.png','.jpg','.jpeg','.gif','.webp'}):
                    try:
                        self.media.spool.mkdir(parents=True,exist_ok=True)
                        copy=self.media.spool/(hashlib.sha256(path.read_bytes()).hexdigest()+path.suffix)
                        if not copy.exists():shutil.copyfile(path,copy)
                        media_id=self.media.preserve(row['id'],copy,capture_kind='local_cache')
                        with self.personal.connection() as c:latest=c.execute('SELECT * FROM wechat_media WHERE id=?',(media_id,)).fetchone()
                    except (OSError,ValueError,PersonalError):ref['cache_status']='unsupported_local_format'
                if latest:
                    latest=dict(latest)
                    with self.personal.connection() as c:cached=c.execute("SELECT * FROM wechat_media_parse_cache WHERE sha256=? AND engine='vision' AND status='parsed' ORDER BY created_at DESC LIMIT 1",(latest['sha256'],)).fetchone()
                    if cached and latest['engine']!='deepseek-vision':
                        with self.personal.connection(write=True) as c:
                            media_id=identifier();rev=c.execute('SELECT max(revision)+1 FROM wechat_media WHERE message_id=?',(row['id'],)).fetchone()[0]
                            c.execute("INSERT INTO wechat_media(id,message_id,sha256,phash,local_path,capture_kind,width,height,status,revision,engine,blocks_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(media_id,row['id'],latest['sha256'],latest['phash'],latest['local_path'],latest['capture_kind'],latest['width'],latest['height'],'parsed',rev,'deepseek-vision',cached['blocks_json'],now()))
                            latest=dict(c.execute('SELECT * FROM wechat_media WHERE id=?',(media_id,)).fetchone())
                    elif latest['engine']!='deepseek-vision':
                        result=await asyncio.to_thread(self.media.ocr,latest['id'],engine=self.service.media_engine)
                        with self.personal.connection() as c:latest=dict(c.execute('SELECT * FROM wechat_media WHERE id=?',(result.get('media_id',latest['id']),)).fetchone())
                    row['_media']=latest;row['_text']='\n'.join(b['text'] for b in json.loads(latest['blocks_json'] or '[]'));digest=latest['sha256']
                elif ref.get('kind')=='file' and path and path.is_file():
                    digest=ref.get('evidence_sha256');row['_media']=None;row['_text']=ref.get('title') or row['raw_text']
                else:
                    with self.personal.connection(write=True) as c:c.execute("UPDATE wechat_media_work SET status='waiting_cache',reference_json=?,last_error=?,attempts=attempts+1,last_attempt_at=?,next_attempt_at=? WHERE message_id=?",(encode(ref),ref.get('cache_status') or 'local_cache_missing',at.isoformat(),(at+timedelta(minutes=15)).isoformat(),row['id']))
                    continue
                with self.personal.connection(write=True) as c:c.execute('UPDATE wechat_media_work SET media_hash=?,reference_json=?,last_attempt_at=? WHERE message_id=?',(digest,encode(ref),at.isoformat(),row['id']))
                row['media_hash']=digest;row['reference_json']=encode(ref);ready.append(row)
            bound_files=enrich_existing(self.personal)
            for group in related_groups([r for r in ready if r['id'] not in bound_files]):
                await self._prepare(group,at)
            with self.personal.connection() as c:
                from .recovery import eligible_job_ids
                candidates=c.execute("SELECT DISTINCT j.* FROM wechat_analysis_job j JOIN wechat_media_analysis a ON a.batch_id=j.batch_id WHERE j.status IN ('pending','retry','budget_deferred') ORDER BY j.created_at,j.id")
                jobs=eligible_job_ids(c,candidates,at=at,limit=2)
            for job in jobs:await self.service.analysis.run_one(job)
            self._observe()

    async def _prepare(self,group,at):
        ids={r['id'] for r in group};chat_id=group[0]['chat_id']
        with self.personal.connection() as c:
            all_rows=[dict(r) for r in c.execute('SELECT * FROM wechat_message WHERE chat_id=? AND ingest_seq BETWEEN ? AND ? ORDER BY ingest_seq',(chat_id,min(r['ingest_seq'] for r in group)-5,max(r['ingest_seq'] for r in group)+5))]
        anchor=group[0]['_stamp'];context=[];chars=sum(len(r['raw_text'])+len(r['_text']) for r in group)
        for row in all_rows:
            if row['id'] in ids:continue
            stamp=parse_time(row['source_time'])
            if stamp and anchor and abs((stamp-anchor).total_seconds())>300:continue
            if len(context)<8 and chars+len(row['raw_text'])<=10000:context.append(row);chars+=len(row['raw_text'])
        hints='\n'.join([r['raw_text'] for r in context]+[r['raw_text'] for r in group])
        vision_allowed=any(r.get('_media') and not usable_text(r['_text']) for r in group) and bool(NOTICE.search(hints))
        if any(r.get('_media') and not usable_text(r['_text']) for r in group) and not vision_allowed:
            with self.personal.connection(write=True) as c:
                for row in group:c.execute("UPDATE wechat_media_work SET status='unreadable',last_error='ocr_insufficient_no_notice_context' WHERE message_id=?",(row['id'],))
            return
        key=hashlib.sha256(encode([[(r['id'],r['media_hash'],r['_text']) for r in group],[(r['id'],r['content_signature']) for r in context]]).encode()).hexdigest()
        with self.personal.connection(write=True) as c:
            if c.execute('SELECT 1 FROM wechat_media_analysis WHERE input_key=?',(key,)).fetchone():return
            revisions={mid:c.execute('SELECT coalesce(max(content_revision),0)+1 FROM wechat_batch_message WHERE message_id=?',(mid,)).fetchone()[0] for mid in ids}
            chat=dict(c.execute('SELECT * FROM wechat_chat WHERE id=?',(chat_id,)).fetchone())
            scan_id=group[0]['first_scan_id']
            batch=prepare_batch(self.personal,chat,scan_id,BatchPlan(tuple(r['id'] for r in group),tuple(r['id'] for r in context),False),at=at,connection=c,media_revisions=revisions)
            if not batch:return
            c.execute('INSERT INTO wechat_media_analysis VALUES (?,?,?,?)',(key,batch,int(vision_allowed),now()))
            # Retire only untouched placeholder batches fully represented by
            # this media/context set. Never discard a sibling task or draft.
            represented=ids|{r['id'] for r in context}
            old=c.execute("SELECT DISTINCT b.id,b.snapshot_id,r.draft_json FROM wechat_batch b JOIN wechat_batch_message bm ON bm.batch_id=b.id JOIN raw_review r ON r.raw_snapshot_id=b.snapshot_id WHERE b.id!=? AND NOT EXISTS(SELECT 1 FROM wechat_media_analysis a WHERE a.batch_id=b.id) AND b.status IN ('pending','waiting_context','media_pending','queued') AND bm.message_id IN ("+','.join('?' for _ in ids)+")",(batch,*ids)).fetchall()
            for previous in old:
                primary={r[0] for r in c.execute("SELECT message_id FROM wechat_batch_message WHERE batch_id=? AND role='primary'",(previous['id'],))}
                real=c.execute("SELECT 1 FROM wechat_candidate WHERE batch_id=? AND (attention_level IN ('action','fyi') OR status IN ('accepted','ignored')) LIMIT 1",(previous['id'],)).fetchone()
                if primary<=represented and not previous['draft_json'] and not real:
                    c.execute("UPDATE wechat_batch SET status='superseded',superseded_by=? WHERE id=?",(batch,previous['id']))
                    c.execute("UPDATE wechat_analysis_job SET status='superseded' WHERE batch_id=? AND status IN ('pending','retry','budget_deferred')",(previous['id'],))
                    c.execute("UPDATE raw_review SET status='archived' WHERE raw_snapshot_id=? AND status='pending'",(previous['snapshot_id'],))
                    c.execute("UPDATE wechat_candidate SET status='superseded' WHERE batch_id=? AND model_item_id='_pending'",(previous['id'],))
            snapshot=c.execute('SELECT snapshot_id FROM wechat_batch WHERE id=?',(batch,)).fetchone()[0]
            for row in group:
                register_media(c,batch,snapshot,row['id'])
                c.execute("UPDATE wechat_attention_media SET batch_id=?,snapshot_id=?,reason='正在补充媒体内容与上下文。' WHERE message_id=? AND status='media_pending'",(batch,snapshot,row['id']))
                c.execute("UPDATE wechat_media_work SET status='analyzing',batch_id=?,input_key=?,last_error=NULL WHERE message_id=?",(batch,key,row['id']))
            c.execute('SAVEPOINT media_prepare_enqueue')
            try:
                self._enqueue_prepared(c,batch)
            except PersonalError as exc:
                if exc.code!='media_job_scope_conflict':raise
                c.execute('ROLLBACK TO media_prepare_enqueue')
                c.execute('RELEASE media_prepare_enqueue')
                for row in group:
                    c.execute("UPDATE wechat_media_work SET status='recovery_needed',last_error=? WHERE message_id=?",(exc.code,row['id']))
                    c.execute("UPDATE wechat_attention_media SET reason=? WHERE message_id=? AND status='media_pending'",(reason_text(exc.code),row['id']))
                c.execute("UPDATE wechat_batch SET status='manual_review' WHERE id=?",(batch,))
            else:
                c.execute('RELEASE media_prepare_enqueue')
