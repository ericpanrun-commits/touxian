"""Bounded durable chunk extraction; every output still passes source validation."""
import asyncio
from copy import deepcopy
import json
import re
import time
from .extraction_chunks import (LONG_PROMPT, LONG_SCHEMA, MAX_CHUNKS, AUTO_CHUNKS, AUTO_CALLS, MAX_CALLS,
    AUTO_TOKENS, MAX_TOKENS, chunk_snapshot, hash_value, plan_chunks, split_chunk)
from .extraction_errors import ExtractionFailure
from .extraction_merge import merge_candidates
from .extraction_provider import extraction_messages
from .extraction_validation import parse_output, validate_candidate
from .personal_store import PersonalError, encode, identifier, now
from .review_store import ReviewStore
from .source_metadata import model_source_context


class LongExtraction:
    def __init__(self, service):
        self.service = service
        self.personal = service.personal
        self.locks = {}

    def keys(self, snapshot, chunk, config):
        content = {k: config.model_dump()[k] for k in ('base_url','model','thinking','max_output_tokens')}
        source_context = model_source_context(snapshot)
        if source_context:
            source_context = {**source_context, 'parsed_files': [{k:f.get(k) for k in ('filename','extractor_version')} for f in source_context.get('parsed_files',[])]}
        content.update(provider=self.service.provider.name, prompt_version=LONG_PROMPT, schema_version=LONG_SCHEMA,
                       chunk_hash=chunk['hash'], source_time=snapshot.get('source_time'), title_hint=snapshot.get('title_hint',''),
                       source_context=source_context)
        reuse = hash_value(content)
        exact = hash_value({**content, 'source_snapshot_hash': snapshot['hash']})
        return exact, reuse

    def cached(self, snapshot, chunk, config):
        exact, reuse = self.keys(snapshot, chunk, config)
        with self.personal.connection() as c:
            row = c.execute('SELECT * FROM extraction_chunk_cache WHERE cache_key=? OR reuse_key=? ORDER BY created_at DESC LIMIT 1', (exact,reuse)).fetchone()
        if not row:
            return None
        try:
            parsed = json.loads(row['parsed_output'])
            for q in parsed['evidence']:
                view = chunk['views'][int(q['block_id'].removeprefix('B'))]
                q['block_id'] = view['block']['block_id']
            parsed = parse_output(encode(parsed), consolidated=True)
            validation = validate_candidate(deepcopy(parsed), snapshot)
            if any(not q['valid'] for q in validation['evidence']):
                return None
            return parsed, validation, row['cache_key']
        except (ValueError, KeyError, IndexError, TypeError, ExtractionFailure):
            return None

    def cache_store(self, snapshot, chunk, config, parsed, usage):
        exact, reuse = self.keys(snapshot, chunk, config)
        canonical = deepcopy(parsed)
        bindings = {v['block']['block_id']: 'B'+str(i) for i,v in enumerate(chunk['views'])}
        if any(q.get('block_id') not in bindings for q in canonical['evidence']):
            return
        for q in canonical['evidence']:
            q['block_id'] = bindings[q['block_id']]
        with self.personal.connection(write=True) as c:
            c.execute('INSERT OR REPLACE INTO extraction_chunk_cache VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (exact,reuse,snapshot['hash'],chunk['hash'],self.service.provider.name,config.model,LONG_PROMPT,LONG_SCHEMA,int(config.thinking),encode(canonical),encode(usage),now()))

    async def start(self, snapshot, config, force, request_id, confirm_budget, scope):
        request_hash = hash_value({'snapshot_id':snapshot['id'],'snapshot_hash':snapshot['hash'],'config':config.model_dump(),
            'force':force,'confirm_budget':confirm_budget,'scope':scope,'prompt':LONG_PROMPT,'schema':LONG_SCHEMA})
        # Idempotency follows immutable request inputs, never changing cache coverage.
        with self.personal.connection(write=True) as c:
            ReviewStore._review(c,snapshot['id'],editable=True)
            if request_id:
                old = c.execute('SELECT * FROM extraction_request WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if old['raw_snapshot_id'] != snapshot['id'] or old['request_hash'] != request_hash:
                        raise PersonalError('分析请求内容已改变，请重新发起。','idempotency_conflict',409)
                    return ReviewStore._bundle(c,snapshot['id'])
            previous = c.execute("SELECT * FROM ai_extraction WHERE raw_snapshot_id=? AND prompt_version=? AND config_json=? AND (status='running' OR ?=0 AND json_extract(plan_json,'$.scope')=?) ORDER BY created_at DESC LIMIT 1",
                (snapshot['id'],LONG_PROMPT,encode(config.model_dump()),int(force),scope)).fetchone()
            if previous:
                if request_id:
                    c.execute('INSERT INTO extraction_request VALUES (?,?,?,?)',(request_id,snapshot['id'],request_hash,previous['id']))
                c.execute('UPDATE raw_review SET extraction_id=? WHERE raw_snapshot_id=?',(previous['id'],snapshot['id']))
                return ReviewStore._bundle(c,snapshot['id'])
        snapshot = {**snapshot, 'title_hint': self.service.reviews.get(snapshot['id'])['title_hint']}
        plan = plan_chunks(snapshot, scope)
        if not snapshot.get('blocks'):
            # Persist a derived source owner once. Immutable RawSnapshot text is untouched.
            with self.personal.connection(write=True) as c:
                for b in plan['blocks']:
                    c.execute('INSERT OR IGNORE INTO raw_source_block (raw_snapshot_id,block_id,source_type,ordinal,text,section,words_json) VALUES (?,?,?,?,?,?,?)',
                        (snapshot['id'],b['block_id'],b['source_type'],b['ordinal'],b['text'],b['section'],'[]'))
            snapshot = {**snapshot, 'blocks': plan['blocks']}
        cached = {ch['id']: self.cached(snapshot, ch, config) for ch in plan['selected']}
        missing = [ch for ch in plan['selected'] if not cached[ch['id']]]
        if len(missing) > AUTO_CHUNKS and not confirm_budget:
            raise PersonalError(f'长文需要分析 {len(missing)} 个区域。常规最多 {AUTO_CHUNKS} 个；确认后每轮最多 {MAX_CHUNKS} 个新区域、{MAX_CALLS} 次 HTTP 请求。成功区域会缓存，可随时取消。', 'budget_confirmation_required', 409)
        if scope == 'remaining':
            plan['selected'] = [ch for ch in missing[:MAX_CHUNKS]] + [ch for ch in plan['selected'] if cached[ch['id']]]
        else:
            selected_ids = {ch['id'] for ch in missing[:MAX_CHUNKS]}
            plan['selected'] = [ch for ch in plan['selected'] if cached[ch['id']] or ch['id'] in selected_ids]
        selected_ids = {ch['id'] for ch in plan['selected']}
        plan['omitted'] = [ch['id'] for ch in plan['all'] if ch['id'] not in selected_ids]
        budget = {'max_calls': MAX_CALLS if confirm_budget else AUTO_CALLS, 'max_tokens': MAX_TOKENS if confirm_budget else AUTO_TOKENS}
        public_plan = {'chunk_count': len(plan['all']), 'selected_count': len(plan['selected']), 'omitted': plan['omitted'],
                       'scope': scope, 'budget': budget, 'confirmed_budget': confirm_budget,
                       'chunks': [{'id': ch['id'], 'hash': ch['hash'], 'refs': ch['refs']} for ch in plan['all']]}
        cache_key = hash_value({'snapshot_id':snapshot['id'], 'hash':snapshot['hash'], 'config':config.model_dump(), 'plan':public_plan, 'prompt':LONG_PROMPT,'schema':LONG_SCHEMA})
        with self.personal.connection(write=True) as c:
            ReviewStore._review(c,snapshot['id'],editable=True)
            if request_id:
                old = c.execute('SELECT * FROM extraction_request WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if old['raw_snapshot_id'] != snapshot['id'] or old['request_hash'] != request_hash:
                        raise PersonalError('分析请求内容已改变，请重新发起。','idempotency_conflict',409)
                    return ReviewStore._bundle(c,snapshot['id'])
            old = c.execute("SELECT * FROM ai_extraction WHERE cache_key=? AND (status='running' OR ?=0) ORDER BY created_at DESC LIMIT 1",(cache_key,int(force))).fetchone()
            if old:
                if request_id:
                    c.execute('INSERT INTO extraction_request VALUES (?,?,?,?)',(request_id,snapshot['id'],request_hash,old['id']))
                c.execute('UPDATE raw_review SET extraction_id=? WHERE raw_snapshot_id=?',(old['id'],snapshot['id']))
                return ReviewStore._bundle(c,snapshot['id'])
            job = identifier()
            progress = {'phase':'planning','total':len(plan['selected']),'processed':0,'successful_regions':0,'calls':0,'cached':0,'token_ceiling_spent':0}
            c.execute('INSERT INTO ai_extraction (id,raw_snapshot_id,provider,model,prompt_version,schema_version,cache_key,request_id,request_hash,created_at,status,thinking,config_json,analysis_state,progress_json,plan_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (job,snapshot['id'],self.service.provider.name,config.model,LONG_PROMPT,LONG_SCHEMA,cache_key,request_id,request_hash,now(),'running',int(config.thinking),encode(config.model_dump()),'running',encode(progress),encode(public_plan)))
            if request_id:
                c.execute('INSERT INTO extraction_request VALUES (?,?,?,?)',(request_id,snapshot['id'],request_hash,job))
            c.execute('UPDATE raw_review SET extraction_id=?,version=version+1,updated_at=? WHERE raw_snapshot_id=?',(job,now(),snapshot['id']))
            if force:
                c.execute('UPDATE raw_review SET draft_json=NULL WHERE raw_snapshot_id=?',(snapshot['id'],))
            result = ReviewStore._bundle(c,snapshot['id'])
        task = asyncio.create_task(self.run(job,snapshot,config,plan,budget,progress))
        self.service.tasks[job] = task
        task.add_done_callback(lambda _: self.service.tasks.pop(job,None))
        return result

    def progress(self, job, value):
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE ai_extraction SET progress_json=? WHERE id=?',(encode(value),job))

    def usage(self, job):
        # Failed/truncated responses also cost tokens. Cached/local restoration
        # rows contain no new paid usage; unknown interrupted outcomes stay in
        # the separately persisted conservative budget reservation.
        total = {}
        with self.personal.connection() as c:
            rows = c.execute('SELECT token_usage_json FROM ai_chunk_extraction WHERE extraction_id=?',(job,)).fetchall()
        for row in rows:
            for key,value in json.loads(row['token_usage_json'] or '{}').items():
                total[key] = total.get(key,0)+value
        return total

    def record(self, job, snapshot, chunk, config, attempt, reason, cached=None, recovered=None):
        key,_ = self.keys(snapshot,chunk,config)
        record = identifier()
        with self.personal.connection(write=True) as c:
            c.execute('INSERT INTO ai_chunk_extraction (id,extraction_id,chunk_id,parent_chunk_id,attempt,status,input_hash,cache_key,source_snapshot_hash,scope_json,config_json,retry_reason,reused_from_key,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (record,job,chunk['id'],chunk['parent'],attempt,'running',chunk['hash'],key,snapshot['hash'],encode(chunk['refs']),encode({**config.model_dump(),'prompt_version':LONG_PROMPT,'schema_version':LONG_SCHEMA,'recovered_from_record_id':recovered}),reason,cached,now()))
        return record

    def finish_record(self, record, state, *, raw=None, parsed=None, validation=None, error=None, usage=None):
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE ai_chunk_extraction SET status=?,raw_output=?,parsed_output=?,validation_json=?,error_json=?,token_usage_json=?,completed_at=? WHERE id=?',
                (state,raw,encode(parsed) if parsed is not None else None,encode(validation) if validation else None,encode(error) if error else None,encode(usage or {}),now(),record))

    def translate(self, parsed, chunk):
        values = {v['block']['block_id']:v for v in chunk['views']}
        reanchored = 0
        for quote in parsed['evidence']:
            view = values.get(quote.get('block_id'))
            if not view:
                raise ExtractionFailure('invalid_evidence','模型引用了未提供的来源块。')
            text = view['block']['text'][view['start']:view['end']]
            start, end = quote['start'], quote['end']
            if text[start:end] != quote['quote']:
                if text.count(quote['quote']) != 1:
                    raise ExtractionFailure('invalid_evidence','模型引用无法唯一对应本区域原文。')
                start = text.index(quote['quote']); end = start+len(quote['quote']); reanchored += 1
            quote['start'], quote['end'] = start+view['start'], end+view['start']
        return reanchored

    def restore_phrases(self, parsed, chunk):
        """Restore only a unique whitespace-equivalent original date phrase.

        Evidence quotes must still match exactly; this never repairs rewritten
        facts, chooses years or changes the immutable source text.
        """
        quotes = {q['id']: q for q in parsed['evidence']}
        restored = 0
        for event in parsed['events']:
            for deadline in event['deadlines']:
                phrase = deadline['original_text']
                sources = {quotes[q]['block_id'] for q in deadline['evidence_ids'] if q in quotes}
                views = [v for v in chunk['views'] if v['block']['block_id'] in sources]
                if not phrase or any(phrase in v['block']['text'][v['start']:v['end']] for v in views):
                    continue
                compact = re.sub(r'\s+', '', phrase)
                if not compact:
                    continue
                matches = []
                for view in views:
                    text = view['block']['text'][view['start']:view['end']]
                    positions = [i for i,char in enumerate(text) if not char.isspace()]
                    normalized = ''.join(text[i] for i in positions)
                    for match in re.finditer(re.escape(compact), normalized):
                        matches.append(text[positions[match.start()]:positions[match.end()-1]+1])
                if len(matches) == 1 and len(matches[0]) <= 500:
                    deadline['original_text'] = matches[0]
                    restored += 1
        return restored

    def recover_phrase_output(self, snapshot, chunk, config):
        # Revalidate bounded prior responses before paying again. Only this exact
        # input/config and whitespace restoration qualify; truncated JSON never does.
        exact,_ = self.keys(snapshot,chunk,config)
        with self.personal.connection() as c:
            rows = c.execute("SELECT id,raw_output FROM ai_chunk_extraction WHERE cache_key=? AND status='failed' AND json_extract(error_json,'$.code')='invalid_evidence' AND raw_output IS NOT NULL ORDER BY created_at DESC,id DESC LIMIT 6",(exact,)).fetchall()
        for row in rows:
            try:
                parsed = parse_output(row['raw_output'])
                self.translate(parsed,chunk)
                if not self.restore_phrases(parsed,chunk):
                    continue
                validation = validate_candidate(deepcopy(parsed),snapshot)
                if any(not q['valid'] for q in validation['evidence']) or any(w['code'] in {'missing_evidence','invalid_evidence','invalid_original_phrase','invalid_action_reference'} for w in validation['warnings']):
                    continue
                validation['warnings'].append({'field_path':'sources','code':'original_phrase_reanchored','message':'已重新校验旧响应，并按唯一原文恢复日期表述的空白与换行；本区域没有重新调用模型，请核对。'})
                return parsed,validation,dict(row)
            except (ExtractionFailure,KeyError,TypeError,ValueError):
                continue
        return None

    async def extract_chunk(self, job, snapshot, chunk, config, plan, budget, progress, key, *, reason='initial'):
        _,reuse = self.keys(snapshot,chunk,config)
        lock = self.locks.setdefault(reuse,asyncio.Lock())
        try:
            async with lock:
                return await self._extract_chunk(job,snapshot,chunk,config,plan,budget,progress,key,reason=reason)
        finally:
            if not lock.locked() and not getattr(lock,'_waiters',None):
                self.locks.pop(reuse,None)

    async def _extract_chunk(self, job, snapshot, chunk, config, plan, budget, progress, key, *, reason='initial'):
        hit = self.cached(snapshot,chunk,config)
        if hit:
            parsed,validation,reused = hit
            row = self.record(job,snapshot,chunk,config,0,'cache_hit',cached=reused)
            self.finish_record(row,'validated',parsed=parsed,validation=validation)
            progress['cached'] += 1
            return True
        recovered = self.recover_phrase_output(snapshot,chunk,config)
        if recovered:
            parsed,validation,old = recovered
            row = self.record(job,snapshot,chunk,config,0,'local_phrase_recovery',recovered=old['id'])
            self.finish_record(row,'validated',raw=old['raw_output'],parsed=parsed,validation=validation)
            self.cache_store(snapshot,chunk,config,parsed,{})
            progress['locally_recovered'] = progress.get('locally_recovered',0)+1
            return True
        current = config
        for attempt in range(1,4):
            view = chunk_snapshot(snapshot,chunk)
            ceiling = len(encode(extraction_messages(view)).encode('utf-8')) + current.max_output_tokens
            available = min(2,budget['max_calls']-progress['calls'],(budget['max_tokens']-progress['token_ceiling_spent'])//max(1,ceiling))
            if available <= 0:
                row = self.record(job,snapshot,chunk,current,attempt,'budget_exhausted')
                self.finish_record(row,'failed',error={'code':'budget_exhausted','message':'本轮调用预算已到上限，成功区域与原文保留。'})
                return False
            view['_http_attempts_remaining'] = available
            row = self.record(job,snapshot,chunk,current,attempt,reason)
            raw,usage,used,response_attempts = None,{},available,None
            progress['phase'] = 'analyzing'
            # Reserve before awaiting. Unknown/cancelled HTTP outcomes stay counted.
            progress['calls'] += available; progress['token_ceiling_spent'] += ceiling*available
            self.progress(job,progress)
            try:
                async with self.service.semaphore:
                    response = await self.service.provider.extract(view,current,key)
                used = min(available,max(1,response.attempts))
                response_attempts = used
                raw,usage = response.raw_output,response.usage
                secret = key.get_secret_value()
                if secret:
                    raw = raw.replace(secret,'[redacted]')
                if response.finish_reason != 'stop' or usage.get('completion_tokens',0) >= current.max_output_tokens:
                    raise ExtractionFailure('truncated','输出未完整结束。',raw_output=raw,usage=usage,attempts=used)
                parsed = parse_output(raw)
                reanchored = self.translate(parsed,chunk)
                restored = self.restore_phrases(parsed,chunk)
                validation = validate_candidate(deepcopy(parsed),snapshot)
                if any(not q['valid'] for q in validation['evidence']):
                    raise ExtractionFailure('invalid_evidence','区域引用校验未通过。',raw_output=raw,usage=usage,attempts=used)
                if any(w['code'] in {'missing_evidence','invalid_evidence','invalid_original_phrase','invalid_action_reference'} for w in validation['warnings']):
                    raise ExtractionFailure('invalid_evidence','重要字段的原文依据或行动关联未通过校验。',raw_output=raw,usage=usage,attempts=used)
                if reanchored:
                    validation['warnings'].append({'field_path':'sources','code':'span_reanchored','message':f'{reanchored} 条引用已按唯一原句校正坐标，请核对。'})
                if restored:
                    validation['warnings'].append({'field_path':'sources','code':'original_phrase_reanchored','message':f'{restored} 条日期表述已按唯一原文恢复空白与换行，请核对；没有推测日期。'})
                self.finish_record(row,'validated',raw=raw,parsed=parsed,validation=validation,usage=usage)
                self.cache_store(snapshot,chunk,current,parsed,usage)
                # Recovery with a temporary output setting is also valid for this
                # original plan/config. The attempt provenance remains explicit.
                if current.max_output_tokens != config.max_output_tokens:
                    self.cache_store(snapshot,chunk,config,parsed,usage)
                return True
            except asyncio.CancelledError:
                self.finish_record(row,'cancelled',error={'code':'cancelled','message':'已取消当前区域；已保存结果保留。'})
                raise
            except ExtractionFailure as exc:
                used = response_attempts if response_attempts is not None else min(available,max(1,exc.attempts))
                raw = exc.raw_output if exc.raw_output is not None else raw
                usage = exc.usage if exc.usage is not None else usage
                secret = key.get_secret_value()
                if raw and secret:
                    raw = raw.replace(secret,'[redacted]')
                error = {'code':exc.code,'message':str(exc).replace(secret,'[redacted]') if secret else str(exc),'finish_reason':exc.finish_reason}
                self.finish_record(row,'truncated' if exc.code=='truncated' else 'failed',raw=raw,error=error,usage=usage)
                if exc.code in {'missing_key','unauthorized','forbidden','insufficient_balance','provider_error','connection_error'}:
                    raise
                if exc.code in {'truncated','invalid_json','invalid_schema'}:
                    children = split_chunk(plan['blocks'],chunk)
                    if children:
                        complete = True
                        for child in children:
                            complete = await self.extract_chunk(job,snapshot,child,config,plan,budget,progress,key,reason=exc.code+'_split') and complete
                        self.finish_record(row,'split',raw=raw,error=error,usage=usage)
                        if complete:
                            child_ids = [child['id'] for child in children]
                            with self.personal.connection() as c:
                                successful = c.execute("SELECT chunk_id,parsed_output FROM ai_chunk_extraction WHERE extraction_id=? AND status='validated'",(job,)).fetchall()
                            parts = [(r['chunk_id'],json.loads(r['parsed_output'])) for r in successful if r['chunk_id'] in child_ids]
                            if len(parts)==len(child_ids):
                                combined,_ = merge_candidates(parts,snapshot)
                                self.cache_store(snapshot,chunk,config,combined,{})
                        return complete
                    if attempt==2 and current.max_output_tokens < 8192:
                        current = config.model_copy(update={'max_output_tokens':min(8192,max(config.max_output_tokens+1024,config.max_output_tokens*2))})
                elif exc.code not in {'timeout','rate_limited','empty_output','invalid_evidence','incomplete_output'}:
                    return False
                if attempt>=2 and exc.code not in {'truncated','invalid_json','invalid_schema'}:
                    return False
                reason = exc.code
            finally:
                progress['calls'] -= available-used
                progress['token_ceiling_spent'] -= ceiling*(available-used)
                self.progress(job,progress)
        return False

    async def run(self, job, snapshot, config, plan, budget, progress):
        started = time.monotonic()
        complete_roots, cancelled, fatal, secret = [],False,None,''
        try:
            _,key = self.service.settings.resolved()
            secret = key.get_secret_value()
            for chunk in plan['selected']:
                complete = await self.extract_chunk(job,snapshot,chunk,config,plan,budget,progress,key)
                if complete:
                    complete_roots.append(chunk['id'])
                progress['processed'] += 1
                self.progress(job,progress)
        except asyncio.CancelledError:
            cancelled = True
        except (ExtractionFailure,PersonalError) as exc:
            fatal = {'code':exc.code,'message':str(exc).replace(secret,'[redacted]') if secret else str(exc)}
        except Exception:
            fatal = {'code':'analysis_error','message':'分析未完成，已保存的区域与原文保留。'}
        progress['phase'] = 'merging'
        self.progress(job,progress)
        with self.personal.connection(write=True) as c:
            c.execute("UPDATE ai_chunk_extraction SET status=?,error_json=?,completed_at=? WHERE extraction_id=? AND status='running'",
                ('cancelled' if cancelled else 'failed',encode(fatal or {'code':'interrupted','message':'区域未完成，原文保留。'}),now(),job))
        with self.personal.connection() as c:
            rows = c.execute("SELECT * FROM ai_chunk_extraction WHERE extraction_id=? AND status='validated' ORDER BY created_at,id",(job,)).fetchall()
        outputs = [(r['chunk_id'],json.loads(r['parsed_output'])) for r in rows]
        parsed, validation, raw, usage = None,None,None,self.usage(job)
        missing = len(plan['selected'])-len(complete_roots)
        warnings = []
        if outputs:
            try:
                parsed,warnings = merge_candidates(outputs,snapshot)
                parsed = parse_output(encode(parsed),consolidated=True)
                raw = encode(parsed)
                source = {**snapshot,'document_warnings':[w for w in snapshot.get('document_warnings',[]) if w['code']!='limited_document_analysis']}
                validation = validate_candidate(parsed,source)
                for row in rows:
                    warnings.extend(w for w in json.loads(row['validation_json'] or '{}').get('warnings',[]) if w['code'] in {'span_reanchored','original_phrase_reanchored'})
                if missing or plan['omitted'] or cancelled:
                    warnings.append({'field_path':'sources','code':'partial_analysis','message':f'{missing} 个选中区域未完整分析，{len(plan["omitted"])} 个区域尚未覆盖；结果可能不完整。原文与成功区域保留，可重试或人工确认。'})
                validation['warnings'].extend(warnings)
                validation['status'] = 'needs_review' if validation['warnings'] else 'valid'
            except ExtractionFailure:
                fatal = {'code':'merge_error','message':'合并未完成，逐区域结果与原文仍保留。'}
                parsed,validation = None,None
        state = 'cancelled' if cancelled else 'partial' if outputs and (missing or plan['omitted'] or fatal or any(w['code']=='candidate_limit' for w in warnings)) else 'validated' if parsed is not None else 'failed'
        if state=='failed':
            with self.personal.connection() as c:
                truncated = c.execute("SELECT 1 FROM ai_chunk_extraction WHERE extraction_id=? AND (status='truncated' OR error_json LIKE '%truncated%') LIMIT 1",(job,)).fetchone()
            if truncated:
                state = 'truncated'
        status = validation['status'] if validation else 'failed'
        error = fatal or ({'code':state,'message':'已取消分析，成功区域和原文保留。' if cancelled else f'{missing} 个区域分析未完成，结果可能不完整。'} if state in {'cancelled','partial'} else None)
        progress.update(phase='review' if parsed is not None else state,successful_regions=len(rows),failed_regions=missing,omitted_regions=len(plan['omitted']))
        with self.personal.connection(write=True) as c:
            c.execute('UPDATE ai_extraction SET status=?,analysis_state=?,completed_at=?,raw_output=?,parsed_output=?,validation_json=?,error_json=?,token_usage_json=?,latency_ms=?,progress_json=? WHERE id=?',
                (status,state,now(),raw,encode(parsed) if parsed is not None else None,encode(validation) if validation else None,encode(error) if error else None,encode(usage),int((time.monotonic()-started)*1000),encode(progress),job))
            c.execute("UPDATE raw_review SET version=version+1,updated_at=? WHERE extraction_id=? AND status='pending'",(now(),job))

    async def cancel(self, snapshot_id):
        bundle = self.service.reviews.get(snapshot_id)
        task = self.service.tasks.get(bundle['extraction_id'])
        if task:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        return self.service.reviews.get(snapshot_id)

    def recover(self):
        with self.personal.connection() as c:
            jobs = [dict(r) for r in c.execute("SELECT * FROM ai_extraction WHERE prompt_version=? AND status IN ('running','waiting')",(LONG_PROMPT,))]
        for job in jobs:
            snapshot = self.service.reviews.get(job['raw_snapshot_id'])['snapshot']
            with self.personal.connection() as c:
                rows = c.execute("SELECT chunk_id,parsed_output FROM ai_chunk_extraction WHERE extraction_id=? AND status='validated' ORDER BY created_at,id",(job['id'],)).fetchall()
            parsed,validation = None,None
            if rows:
                try:
                    parsed,warnings = merge_candidates([(r['chunk_id'],json.loads(r['parsed_output'])) for r in rows],snapshot)
                    parsed = parse_output(encode(parsed),consolidated=True)
                    validation = validate_candidate(deepcopy(parsed),snapshot)
                    validation['warnings'].extend([*warnings,{'field_path':'sources','code':'interrupted','message':'上次分析被应用退出中断，成功区域已恢复；结果可能不完整，请重试或人工核对。'}])
                    validation['status']='needs_review'
                except ExtractionFailure:
                    parsed,validation = None,None
            progress = json.loads(job['progress_json'] or '{}')
            progress.update(phase='review' if parsed is not None else 'failed',successful_regions=len(rows))
            error = {'code':'interrupted','message':'上次分析被应用退出中断，成功区域与原文保留。'}
            usage = self.usage(job['id'])
            with self.personal.connection(write=True) as c:
                c.execute('UPDATE ai_extraction SET status=?,analysis_state=?,completed_at=?,parsed_output=?,validation_json=?,error_json=?,progress_json=?,token_usage_json=? WHERE id=?',
                    ('needs_review' if parsed is not None else 'failed','partial' if parsed is not None else 'failed',now(),encode(parsed) if parsed is not None else None,encode(validation) if validation else None,encode(error),encode(progress),encode(usage),job['id']))
                c.execute("UPDATE ai_chunk_extraction SET status='failed',error_json=?,completed_at=? WHERE extraction_id=? AND status='running'",(encode(error),now(),job['id']))
                c.execute("UPDATE raw_review SET version=version+1,updated_at=? WHERE extraction_id=? AND status='pending'",(now(),job['id']))
