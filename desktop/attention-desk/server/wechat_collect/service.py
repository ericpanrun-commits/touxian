"""Local lifecycle and intake orchestration; production UIA remains fail-closed."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta

from ..personal_store import PersonalError, identifier
from ..review_store import ReviewStore
from ..user_context import UserContextStore
from .db_adapter import ReadOnlyWeChatDBAdapter
from .analysis import AnalysisQueue
from .batching import build_batches
from .config import ConfigStore
from .filtering import RULE_VERSION, classify
from .ingestion import prepare_batch
from .scheduler import ManualOverride, ScheduleState, SchedulerPolicy, SHANGHAI, utc_now
from .store import WeChatStore

LOG=logging.getLogger(__name__)


class WeChatService:
    def __init__(self, personal, settings, *, adapter=None, clock=utc_now, transport=None, activity_guard=None, media_engine=None):
        self.personal,self.clock=personal,clock
        self.settings=ConfigStore(personal.path.parent/'wechat.yaml')
        self.document=self.settings.read()
        self.adapter=adapter or ReadOnlyWeChatDBAdapter(data_directory=personal.path.parent)
        self.repository=WeChatStore(personal)
        self.repository.configure(self.document,enforce_allowlist=getattr(self.adapter,'is_database',False))
        self.repository.recover()
        self.context=UserContextStore(personal.path.parent)
        from ..recovery_controls import controls
        if not controls(personal.path,use_override=False)['pause_paid_ai']:
            # A restart under the global hold cannot reclassify saved backlog.
            from .quality import reclassify_saved,prune_processing
            reclassify_saved(personal,self.context.read().model_dump())
            prune_processing(personal,self.context.read().model_dump(),clock())
        self.policy=SchedulerPolicy(self.document.wechat,clock)
        self.schedule=ScheduleState(self.policy)
        self.analysis=AnalysisQueue(personal,settings,self.policy,transport=transport,clock=clock,
                                    allow_vision=not getattr(self.adapter,'is_database',False))
        self.activity_guard=activity_guard
        self.media_engine=media_engine
        self.lock=asyncio.Lock()
        self.settings_lock=asyncio.Lock()
        self.stop=asyncio.Event()
        self.task=None
        self.scan_tasks=set()
        self.last_result='not_tested'
        self.last_tick=None
        self.changed_chat_ids=None
        self.group_search_cache={}
        from .media_pipeline import MediaPipeline
        self.media_pipeline=MediaPipeline(self)

    async def start(self):
        if self.task is None:
            # Local bookkeeping only; policy/budget gates still own all model dispatch.
            self.media_pipeline.recover_missing_jobs()
            self.task=asyncio.create_task(self._run(),name='wechat-scheduler')

    async def close(self):
        self.stop.set()
        for task in self.scan_tasks:
            task.cancel()
        if self.scan_tasks:
            await asyncio.gather(*list(self.scan_tasks),return_exceptions=True)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
            self.task=None

    async def save_settings(self,document,expected_revision):
        async with self.settings_lock:
            if self.lock.locked() or self.analysis.lock.locked():
                raise PersonalError('扫描或分析进行中，请完成后再保存设置。','busy',409)
            saved=self.settings.save(document,expected_revision)
            self.repository.configure(saved,enforce_allowlist=getattr(self.adapter,'is_database',False))
            self.document=saved
            self.policy.config=saved.wechat
            self.schedule.pending.add('configuration')
            return saved

    async def search_groups(self,query,offset=0,*,chatroom_id=None):
        query=query.strip()
        if not 1<=len(query)<=120:
            raise PersonalError('请输入 1–120 字的群名。')
        search=getattr(self.adapter,'search',None)
        if search is None:
            raise PersonalError('当前读取器尚不支持会话搜索。','search_unavailable',409)
        key=(query,offset,chatroom_id)
        cached=self.group_search_cache.get(key)
        if cached and (self.clock()-cached[0]).total_seconds()<30:
            return cached[1]
        async with self.lock:
            result=await asyncio.to_thread(search,query,policy=self.policy,offset=offset,chatroom_id=chatroom_id,cancelled=self.stop.is_set)
        if result.get('message_body_queries') or result.get('unauthorized_message_bodies_read'):
            raise PersonalError('搜索没有满足只读元数据边界，已停止。','metadata_boundary_violation',503)
        self.group_search_cache[key]=(self.clock(),result)
        if len(self.group_search_cache)>20:
            self.group_search_cache.pop(next(iter(self.group_search_cache)))
        return result

    async def add_group(self,payload):
        # A UI grant is concrete: exact selected ID + current metadata + explicit revision.
        if payload.expected_revision!=self.document.revision:
            raise PersonalError('授权列表已更新，请重新读取。','conflict',409)
        result=await self.search_groups(payload.display_name,chatroom_id=payload.chatroom_id)
        selected=next((group for group in result['items'] if group['chatroom_id']==payload.chatroom_id and group['display_name']==payload.display_name),None)
        if not selected:
            raise PersonalError('群名或账号已变化，请重新搜索后授权。','group_metadata_changed',409)
        current=self.document.model_dump()
        config=current['wechat']
        registry=self.repository.chats()
        existing=next((chat for chat in registry if json.loads(chat['binding_json'] or '{}').get('internal_chat_id')==payload.chatroom_id),None)
        chat_id=existing['id'] if existing else 'room_'+__import__('hashlib').sha256(payload.chatroom_id.encode()).hexdigest()[:20]
        old=next((chat for chat in config['priority_chats'] if chat['id']==chat_id or chat.get('chatroom_id')==payload.chatroom_id),None)
        if old and self.document.wechat.allows(next(chat for chat in self.document.wechat.priority_chats if chat.id==old['id'])):
            return self.document
        if len(config['priority_chats'])>=30:
            raise PersonalError('最多授权 30 个群，请先移除不用的群。')
        value={'id':chat_id,'name':payload.display_name,'chatroom_id':payload.chatroom_id,'priority':payload.priority,'enabled':True,'core':payload.priority=='high','aliases':[],'trusted_senders':[]}
        config['priority_chats']=[chat for chat in config['priority_chats'] if chat['id']!=chat_id]+[value]
        config['allowed_chats']=list(dict.fromkeys([*config['allowed_chats'],payload.display_name]))
        config['allowed_chatrooms']=list(dict.fromkeys([*config['allowed_chatrooms'],payload.chatroom_id]))
        from .config import SettingsDocument
        return await self.save_settings(SettingsDocument.model_validate(current),payload.expected_revision)

    async def update_group(self,chat_id,payload,*,remove=False):
        if payload.expected_revision!=self.document.revision:
            raise PersonalError('授权列表已更新，请重新读取。','conflict',409)
        document=self.document.model_dump()
        config=document['wechat']
        selected=next((chat for chat in config['priority_chats'] if chat['id']==chat_id),None)
        if selected is None:
            raise PersonalError('这个群已被移除，请刷新列表。','not_found',404)
        if remove:
            config['priority_chats']=[chat for chat in config['priority_chats'] if chat['id']!=chat_id]
            config['allowed_chatrooms']=[value for value in config['allowed_chatrooms'] if value!=selected.get('chatroom_id')]
            if not any(chat['name']==selected['name'] for chat in config['priority_chats']):
                config['allowed_chats']=[value for value in config['allowed_chats'] if value!=selected['name']]
        else:
            if payload.enabled is not None: selected['enabled']=payload.enabled
            if payload.priority is not None:
                selected.update(priority=payload.priority,core=payload.priority=='high')
        from .config import SettingsDocument
        return await self.save_settings(SettingsDocument.model_validate(document),payload.expected_revision)

    def status(self):
        from .health import wechat_health
        permission=self.policy.authorize()
        status='running' if self.lock.locked() else permission.reason if not permission.allowed else self.last_result
        chats=self.repository.chats()
        verified=bool(getattr(self.adapter,'certified',False))
        configured={chat.id for chat in self.document.wechat.priority_chats}
        chats=[chat for chat in chats if chat['id'] in configured]
        scans=self.repository.scans(1)['items']
        last=scans[0] if scans else None
        readable=True if last and last['status'] in {'changed','no_change'} else False if last and last['status']=='error' else None
        database_state='readable' if readable is True else 'unavailable' if readable is False else 'not_checked'
        return {'health':wechat_health(self.personal,self.document,self.clock()),'status':status,'capability':'database' if verified else 'not_tested','go_level':'GO' if verified else 'NO_GO','runtime_verified':verified,
                'capability_reason':'本机只读数据库选择性读取已验证。' if verified else '本机数据库读取尚未通过验证。',
                'wechat_available':readable,'database_state':database_state,'availability_checked_at':last['completed_at'] if last else None,
                'collector_alive':bool(self.task and not self.task.done()),'last_heartbeat':self.last_tick.isoformat() if self.last_tick else None,
                'authorized_chat_count':sum(self.document.wechat.allows(chat) for chat in self.document.wechat.priority_chats),'active_chat_count':sum(bool(chat['enabled']) for chat in chats),
                'last_scan':scans,
                'next_scan':self.policy.next_scan().isoformat() if self.document.wechat.enabled else None,
                'timezone':'Asia/Shanghai','quiet':self.policy.quiet(),
                'chats':self.repository.group_cards(self.document),
                'stats':self.repository.stats(self.clock().astimezone(SHANGHAI).date().isoformat()),
                'safety':{'messages_sent':0,'unauthorized_message_bodies_read':0,'sending_supported':False,'automatic_formal_writes':False}}

    def _safe_to_read(self,override):
        if getattr(self.adapter,'is_database',False):
            self.policy.require(operation='database',maximum_seconds=60)
            return
        self.policy.require(operation='uia',maximum_seconds=8,override=override)
        if self.activity_guard is not None:
            if not self.activity_guard():
                raise PersonalError('用户正在操作，扫描已延期。','waiting_idle',409)
        elif self.adapter.is_real:
            from .windows import require_idle
            require_idle(self.policy,override)

    async def request_scan(self,*,manual=False,override_quiet=False,request_key=None,reason='manual'):
        if getattr(self.adapter,'is_database',False) and override_quiet:
            raise PersonalError('数据库采集固定在 06:00–24:00 运行。','quiet_override_disabled',409)
        if override_quiet and not manual:
            raise PersonalError('只有本次手动操作可以覆盖静默限制。','manual_required',400)
        override=ManualOverride('pending',self.clock()+timedelta(minutes=5)) if manual and override_quiet else None
        self.policy.require(override=override)
        row,created=self.repository.begin_scan(reason,self.document.revision,request_key=request_key,
                                              override_expires_at=override.expires_at.isoformat() if override else None)
        if created:
            task=asyncio.create_task(self.scan(row,override=override))
            self.scan_tasks.add(task)
            task.add_done_callback(self.scan_tasks.discard)
        return {'scan_id':row['id'],'status':row['status'],'reused':not created}

    async def scan(self,row,*,override=None):
        stats={}
        statuses=[]
        if self.lock.locked():
            self.repository.finish_scan(row['id'],'deferred',{},'scan_busy')
            return
        async with self.lock:
            try:
                config=self.document.wechat
                self.policy.require(override=override)
                database=getattr(self.adapter,'is_database',False)
                if database:
                    with self.personal.connection() as c:
                        last_ok=c.execute("SELECT MAX(completed_at) FROM wechat_scan WHERE status IN ('changed','no_change')").fetchone()[0]
                    if last_ok and self.clock()-datetime.fromisoformat(last_ok)>=timedelta(hours=24):
                        # Capture the outage safely; analysis of a large historical
                        # catch-up must be cost-reviewed independently of recovery.
                        stats['recovery_requires_approval']=1
                self.changed_chat_ids=set()
                errors=[]
                chats=sorted((ch for ch in config.priority_chats if ch.enabled and (not database or config.allows(ch))),key=lambda ch:({'high':0,'medium':1,'low':2}[ch.priority],not ch.core))
                for target in chats:
                    state,counts,error=await self._scan_target(target,row,override)
                    statuses.append(state)
                    if error: errors.append(error)
                    for key,value in counts.items():
                        stats[key]=stats.get(key,0)+value
                    if error in {'quiet','quiet_boundary','disabled'}: break
                failed=any(state=='error' for state in statuses)
                final=('partial' if len(set(statuses)-{'error'}) else 'error') if failed else 'partial' if 'partial' in statuses else 'changed' if 'changed' in statuses else 'deferred' if not statuses or 'deferred' in statuses else 'no_change'
                self.repository.finish_scan(row['id'],final,stats,errors[0] if final=='error' else None)
                self.last_result=errors[0] if final=='error' else final
            except asyncio.CancelledError:
                self.repository.finish_scan(row['id'],'interrupted',stats,'interrupted')
                raise
            except (PersonalError,sqlite3.DatabaseError) as exc:
                code=exc.code if isinstance(exc,PersonalError) else 'storage_error'
                self.repository.finish_scan(row['id'],'deferred' if code in {'quiet','quiet_boundary','waiting_idle','disabled'} else 'error',stats,code)
                self.last_result=code
            except Exception:
                self.repository.finish_scan(row['id'],'error',stats,'adapter_error')
                self.last_result='adapter_error'

    async def _scan_target(self,target,row,override):
        counts={}
        captured=False
        database=getattr(self.adapter,'is_database',False)
        if hasattr(self.adapter,'last_metrics'): self.adapter.last_metrics={}
        try:
            chat=self.repository.chat(target.id)
            binding=json.loads(chat['binding_json']) if chat['binding_json'] else None
            if database and not binding:
                self._safe_to_read(override)
                binding=await asyncio.to_thread(self.adapter.resolve,target,policy=self.policy,cancelled=lambda:self.stop.is_set())
                self.repository.bind_database(target.id,binding)
                chat=self.repository.chat(target.id)
            if self.adapter.is_real and (not binding or chat['capability'] not in {'GO','IDLE_ONLY'}):
                raise PersonalError('群尚未完成本机读取验证。','uia_not_verified',409)
            self._safe_to_read(override)
            observation=await asyncio.to_thread(self.adapter.read,target,binding,self.repository.cursor(target.id),
                                                policy=self.policy,override=override,cancelled=lambda:self.stop.is_set())
            self._safe_to_read(override)
            result,messages,counts=self.repository.capture(row['id'],chat,observation)
            captured=True
            if not messages:
                state='no_change' if result.complete else 'partial'
                self.repository.record_group_scan(target.id,row['id'],state,counts,metrics=getattr(self.adapter,'last_metrics',{}))
                return state,counts,None
            self.changed_chat_ids.add(target.id)
            from .media import MediaStore
            media_store=MediaStore(self.personal,self.policy)
            for index,message in zip(result.new_indices,messages):
                observed=observation.messages[index]
                if observed and observed.media_path and message['message_type']=='image':
                    try:
                        media_id=media_store.preserve(message['id'],observed.media_path,capture_kind='local_cache' if database else 'screenshot',override=override)
                        media_store.ocr(media_id,engine=self.media_engine,override=override)
                    except (PersonalError,OSError,ValueError):
                        # An unavailable image must not orphan already committed messages.
                        with self.personal.connection(write=True) as c:
                            c.execute("UPDATE wechat_message_state SET error_code='media_unavailable' WHERE message_id=?",(message['id'],))
            filtered=self._stage_messages(chat,row['id'],messages,target)
            counts['messages_filtered']=filtered
            state='changed' if result.complete else 'partial'
            self.repository.record_group_scan(target.id,row['id'],state,counts,metrics=getattr(self.adapter,'last_metrics',{}))
            return state,counts,None
        except asyncio.CancelledError:
            self.repository.record_group_scan(target.id,row['id'],'interrupted',counts,error='interrupted')
            raise
        except Exception as exc:
            code=exc.code if isinstance(exc,PersonalError) else 'storage_error' if isinstance(exc,sqlite3.DatabaseError) else 'adapter_error'
            state='deferred' if code in {'quiet','quiet_boundary','waiting_idle','disabled','uia_not_verified'} else 'error'
            counts['errors']=int(state=='error')
            if captured:
                counts['staging_errors']=1
            self.repository.record_group_scan(target.id,row['id'],state,counts,error=code,metrics=getattr(self.adapter,'last_metrics',{}))
            return state,counts,code

    def _stage_messages(self,chat,scan_id,messages,target):
        """Idempotent post-capture work; safely resumed without reading WeChat again."""
        context=self.context.read()
        decisions=[classify(m,aliases=context.wechat_self_aliases,trusted_senders=target.trusted_senders,priority=target.priority) for m in messages]
        primary=[]
        filtered=0
        for index,(message,decision) in enumerate(zip(messages,decisions)):
            nearby=any(d.level!='fast_reject' for d in decisions[max(0,index-2):index+3])
            if decision.level=='fast_reject' and nearby:
                decision=classify(message,nearby_candidate=True,priority=target.priority)
            if decision.level not in {'fast_reject','await_context'}:
                from .quality import repeated_notice
                from .filtering import FilterDecision
                with self.personal.connection() as c:
                    if repeated_notice(c,chat['id'],message): decision=FilterDecision('fast_reject','duplicate_notice')
            self.repository.filter_message(message['id'],decision,RULE_VERSION)
            if decision.level=='await_context':
                from .quality import remember
                with self.personal.connection(write=True) as c: remember(c,chat['id'],message,self.clock())
            elif decision.level!='fast_reject':
                primary.append(message['id'])
            else:
                filtered+=1
        if primary:
            low=min(m['ingest_seq'] for m in messages)-8
            high=max(m['ingest_seq'] for m in messages)+2
            with self.personal.connection(write=True) as c:
                history=[dict(r) for r in c.execute("SELECT m.* FROM wechat_message m JOIN wechat_message_state s ON s.message_id=m.id WHERE m.chat_id=? AND m.ingest_seq BETWEEN ? AND ? AND COALESCE(s.filter_reason,'')!='duplicate_notice' ORDER BY m.ingest_seq",(chat['id'],low,high))]
                from .quality import context_for
                held=context_for(c,chat['id'],[m for m in messages if m['id'] in primary],self.clock())
            known={m['id'] for m in history}
            history=sorted([*history,*[m for m in held if m['id'] not in known]],key=lambda m:m['ingest_seq'])
            for plan in build_batches(history,primary):
                from dataclasses import replace
                extra=[m['id'] for m in held if m['id'] not in set(plan.primary)|set(plan.context)]
                selected=set(plan.primary)|set(plan.context)
                remaining=12000-sum(len(m['raw_text']) for m in history if m['id'] in selected)
                kept=[]
                for mid in extra:
                    length=len(next(m['raw_text'] for m in held if m['id']==mid))
                    if length<=remaining and len(selected)+len(kept)<40: kept.append(mid); remaining-=length
                if extra: plan=replace(plan,context=tuple(kept)+plan.context,context_truncated=plan.context_truncated or len(kept)<len(extra))
                followup=self.document.wechat.context_followup
                delay=followup.delay_seconds if plan.wait_for_context and followup.enabled else 0
                batch_id=prepare_batch(self.personal,chat,scan_id,plan,at=self.clock(),delay_seconds=delay)
                if batch_id:
                    from .quality import usable_text
                    with self.personal.connection(write=True) as c:
                        bodies=[m for m in messages if m['id'] in plan.primary]
                        from .quality import register_media,media_text
                        snapshot_id=c.execute('SELECT snapshot_id FROM wechat_batch WHERE id=?',(batch_id,)).fetchone()[0]
                        for body in bodies:
                            if body['message_type'] in {'image','file'} and not usable_text(media_text(c,snapshot_id,body['id'])):
                                register_media(c,batch_id,snapshot_id,body['id'])
                        derived='\n'.join(r[0] for r in c.execute("SELECT text FROM raw_source_block WHERE raw_snapshot_id=(SELECT snapshot_id FROM wechat_batch WHERE id=?) AND source_type IN ('image_ocr','image_vision')",(batch_id,)))
                        from .vision import pending_media
                        can_vision=self.analysis.allow_vision and bool(pending_media(self.personal,batch_id))
                        caption_texts=[m['raw_text'] for m in bodies if m['message_type']=='text']
                        dependent_media=any(m['message_type']=='image' for m in bodies) and any(__import__('re').search(r'见图|看图|如图|上面|附件|二维码',t) for t in caption_texts) and all(len(t)<120 for t in caption_texts)
                        if not usable_text(derived) and (dependent_media or bodies and all(m['message_type'] in {'image','file'} for m in bodies) and not can_vision):
                            c.execute("UPDATE wechat_batch SET status='media_pending' WHERE id=?",(batch_id,))
                            c.execute("UPDATE wechat_candidate SET status='media_pending' WHERE batch_id=? AND model_item_id='_pending'",(batch_id,))
        return filtered

    def _recover_staging(self):
        if self.lock.locked():
            return
        targets={ch.id:ch for ch in self.document.wechat.priority_chats if ch.enabled and
                 (not getattr(self.adapter,'is_database',False) or self.document.wechat.allows(ch))}
        with self.personal.connection() as c:
            pending=[dict(r) for r in c.execute("SELECT m.* FROM wechat_message m JOIN wechat_message_state s ON s.message_id=m.id JOIN wechat_chat ch ON ch.id=m.chat_id WHERE ch.enabled=1 AND s.status IN ('new','candidate') AND NOT EXISTS(SELECT 1 FROM wechat_batch_message bm WHERE bm.message_id=m.id AND bm.role='primary') ORDER BY m.chat_id,m.ingest_seq LIMIT 200")]
        groups={}
        for message in pending:
            if message['chat_id'] in targets:
                groups.setdefault((message['chat_id'],message['first_scan_id']),[]).append(message)
        for (chat_id,scan_id),messages in groups.items():
            filtered=self._stage_messages(self.repository.chat(chat_id),scan_id,messages,targets[chat_id])
            if filtered:
                with self.personal.connection(write=True) as c:
                    row=c.execute('SELECT stats_json FROM wechat_scan WHERE id=?',(scan_id,)).fetchone()
                    stats=json.loads(row[0])
                    stats['messages_filtered']=stats.get('messages_filtered',0)+filtered
                    c.execute('UPDATE wechat_scan SET stats_json=? WHERE id=?',(json.dumps(stats),scan_id))

    async def process_queue(self):
        database=getattr(self.adapter,'is_database',False)
        if database and (self.policy.quiet() or self.lock.locked()):
            return
        if self.policy.authorize(operation='scan').allowed:
            self._recover_staging()
        automatic_allowed=self.policy.authorize(operation='llm',maximum_seconds=60).allowed
        if not automatic_allowed:
            with self.personal.connection() as c:
                manual=c.execute('SELECT 1 FROM wechat_scan WHERE override_expires_at>? LIMIT 1',(self.clock().isoformat(),)).fetchone()
            if not manual or not self.document.wechat.enabled:
                return
        if database and self.last_result=='no_change':
            return  # Local staging may resume; no HTTP/LLM work on an empty scan.
        self.analysis.recover_responses()
        scope=[]
        scoped=''
        if getattr(self.adapter,'is_database',False) and self.changed_chat_ids is not None:
            scope=sorted(self.changed_chat_ids)
            scoped=' AND b.chat_id IN ('+','.join('?' for _ in scope)+')' if scope else ' AND 0'
        with self.personal.connection() as c:
            batches=[dict(r) for r in c.execute("SELECT b.* FROM wechat_batch b JOIN wechat_chat ch ON ch.id=b.chat_id WHERE ch.enabled=1 AND b.status IN ('pending','waiting_context') AND b.superseded_by IS NULL AND b.ready_at<=?"+scoped+" ORDER BY b.created_at LIMIT ?",(self.clock().isoformat(),*scope,self.document.wechat.budget.batches_per_cycle))]
        for batch in batches:
            with self.personal.connection() as c:
                origin=c.execute('SELECT override_expires_at FROM wechat_scan WHERE id=?',(batch['scan_id'],)).fetchone()
            override=ManualOverride(batch['scan_id'],datetime.fromisoformat(origin[0])) if origin and origin[0] else None
            if not self.policy.authorize(operation='llm',maximum_seconds=60,override=override).allowed:
                continue
            if batch['status']=='waiting_context':
                if await self._followup(batch,override):
                    continue
            self.analysis.enqueue(batch['id'],self.context.read())
        with self.personal.connection() as c:
            from .recovery import eligible_job_ids
            candidates=c.execute("SELECT j.* FROM wechat_analysis_job j JOIN wechat_batch b ON b.id=j.batch_id JOIN wechat_chat ch ON ch.id=b.chat_id WHERE ch.enabled=1 AND j.status IN ('pending','budget_deferred','retry')"+scoped+" ORDER BY j.created_at,j.id",scope)
            jobs=eligible_job_ids(c,candidates,at=self.clock(),limit=self.document.wechat.budget.batches_per_cycle)
        for job in jobs:
            await self.analysis.run_one(job)

    async def _followup(self,batch,override=None):
        """One bounded follow-up, with atomic replacement of an unanalysed preview.

        A real, uncertified adapter is never called. Synthetic observations exercise
        the complete batching contract without making a runtime capability claim.
        """
        chat=self.repository.chat(batch['chat_id'])
        if self.adapter.is_real or self.lock.locked():
            with self.personal.connection(write=True) as c:
                warnings=json.loads(batch['warnings_json'])+['followup_not_verified']
                c.execute("UPDATE wechat_batch SET warnings_json=?,followup_count=1,status='pending' WHERE id=?",(json.dumps(warnings),batch['id']))
            return False
        target=next(ch for ch in self.document.wechat.priority_chats if ch.id==chat['id'])
        self._safe_to_read(override)
        scan,_=self.repository.begin_scan('context_followup',self.document.revision,request_key='followup:'+batch['id'])
        async with self.lock:
            try:
                observation=await asyncio.to_thread(self.adapter.read,target,None,self.repository.cursor(chat['id']),policy=self.policy,override=override,cancelled=lambda:self.stop.is_set())
                self._safe_to_read(override)
                result,new,counts=self.repository.capture(scan['id'],chat,observation)
            except PersonalError as exc:
                self.repository.finish_scan(scan['id'],'deferred',{},exc.code)
                return False
            self.repository.finish_scan(scan['id'],'changed' if new else 'no_change' if result.complete else 'partial',counts)
            with self.personal.connection(write=True) as c:
                c.execute('UPDATE wechat_batch SET followup_count=1 WHERE id=?',(batch['id'],))
                if not new:
                    c.execute("UPDATE wechat_batch SET status='pending' WHERE id=?",(batch['id'],))
                    return False
                old_ids=[r[0] for r in c.execute("SELECT message_id FROM wechat_batch_message WHERE batch_id=? AND role='primary'",(batch['id'],))]
                review=c.execute('SELECT draft_json FROM raw_review WHERE raw_snapshot_id=?',(batch['snapshot_id'],)).fetchone()
                # A saved human draft is not silently replaced by an automatic update.
                merge=not review['draft_json']
                if merge:
                    c.execute("UPDATE wechat_batch_message SET role='context' WHERE batch_id=?",(batch['id'],))
                primary=([*old_ids] if merge else [])+[m['id'] for m in new]
                history=[dict(r) for r in c.execute('SELECT * FROM wechat_message WHERE chat_id=? ORDER BY ingest_seq',(chat['id'],))]
                created=[]
                for plan in build_batches(history,primary):
                    new_id=prepare_batch(self.personal,chat,batch['scan_id'],plan,at=self.clock(),connection=c)
                    if new_id:
                        created.append(new_id)
                        c.execute('UPDATE wechat_batch SET previous_batch_id=?,followup_count=1 WHERE id=?',(batch['id'],new_id))
                if merge and created:
                    c.execute("UPDATE wechat_batch SET status='superseded',superseded_by=? WHERE id=?",(created[0],batch['id']))
                else:
                    c.execute("UPDATE wechat_batch SET status='manual_review' WHERE id=?",(batch['id'],))
            # New batches are analysed on the next queue tick; no extra scan/delay.
            return True

    async def reanalyze(self,snapshot_id,force=False):
        self.policy.require(operation='llm',maximum_seconds=60)
        with self.personal.connection() as c:
            ReviewStore._review(c,snapshot_id,editable=True)
            batch=c.execute('SELECT id FROM wechat_batch WHERE snapshot_id=?',(snapshot_id,)).fetchone()
        if not batch:
            raise PersonalError('微信候选不存在。','not_found',404)
        self.analysis.enqueue(batch['id'],self.context.read(),force=force)
        return ReviewStore(self.personal).get(snapshot_id)

    async def _run(self):
        previous_tick=self.clock()
        last_fyi_refresh=None
        while not self.stop.is_set():
            try:
                current=self.clock()
                if last_fyi_refresh is None or (current-last_fyi_refresh).total_seconds()>=60:
                    from .fyi import refresh
                    refresh(self.personal,current)
                    last_fyi_refresh=current
                self.last_tick=current
                if (current-previous_tick).total_seconds()>60:
                    self.schedule.resumed()
                previous_tick=current
                reasons=self.schedule.due()
                if reasons:
                    await self.request_scan(reason=','.join(sorted(reasons)))
                    if self.scan_tasks:
                        await asyncio.gather(*list(self.scan_tasks),return_exceptions=True)
                self.analysis.repair_saved_failures()
                # Local saved-response recovery does not spend an LLM call and
                # remains useful even when every DB cursor is unchanged.
                self.analysis.recover_responses()
                await self.process_queue()
                await self.media_pipeline.tick()
            except Exception:
                LOG.warning('wechat_scheduler cycle_failed')
            try:
                await asyncio.wait_for(self.stop.wait(),timeout=5)
            except TimeoutError:
                pass
