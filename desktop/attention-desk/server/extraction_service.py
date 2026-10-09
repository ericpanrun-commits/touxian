"""Persisted, bounded jobs and cache. Analysis never creates formal events."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from .extraction_errors import ExtractionFailure
from .extraction_models import OUTPUT_SCHEMA_VERSION, PROMPT_VERSION
from .extraction_provider import DeepSeekProvider
from .extraction_validation import parse_output, validate_candidate
from .personal_store import PersonalError, encode, identifier, now
from .review_store import ReviewStore
from .source_metadata import model_source_context


class ExtractionService:
    def __init__(self, personal, settings, provider=None):
        self.personal, self.settings = personal, settings
        self.provider = provider or DeepSeekProvider()
        self.reviews = ReviewStore(personal)
        self.tasks = {}
        self.semaphore = asyncio.Semaphore(2)
        from .extraction_long import LongExtraction
        self.long = LongExtraction(self)

    def recover_interrupted(self):
        self.long.recover()
        with self.personal.connection(write=True) as connection:
            connection.execute("UPDATE ai_extraction SET status='failed',analysis_state='failed',completed_at=?,error_json=? WHERE status IN ('running','waiting')",
                               (now(), encode({"code": "interrupted", "message": "上次分析因应用退出而中断。原文保留，请主动重新分析或手动整理。"})))

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def start(self, snapshot_id, force=False, request_id=None, *, confirm_budget=False, scope='auto'):
        config = self.settings._read()[1]
        snapshot = self.reviews.get(snapshot_id)["snapshot"]
        if snapshot.get('attachments') and not snapshot.get('blocks') and not snapshot.get('web'):
            raise PersonalError('请先完成本地文件解析，再分析提取内容。','files_pending',409)
        from .extraction_chunks import is_long
        if is_long(snapshot):
            return await self.long.start(snapshot,config,force,request_id,confirm_budget,scope)
        cache_data = {"snapshot_hash": snapshot["hash"], "source_time": snapshot["source_time"], "provider": self.provider.name,
                      "source_context": model_source_context(snapshot),
                      "document_context": {k:snapshot.get(k) for k in ('document','input_budget')},
                      "file_context": [{k:a.get(k) for k in ('id','filename','parse_status','needs_review')} for a in snapshot.get('attachments',[])],
                      "model": config.model, "prompt_version": PROMPT_VERSION, "schema_version": OUTPUT_SCHEMA_VERSION,
                      "thinking": config.thinking, "base_url": config.base_url, "max_output_tokens": config.max_output_tokens}
        cache_key = hashlib.sha256(encode(cache_data).encode("utf-8")).hexdigest()
        request_hash = hashlib.sha256(encode({"snapshot_id": snapshot_id, "force": force, "cache_key": cache_key}).encode("utf-8")).hexdigest()
        schedule = False
        with self.personal.connection(write=True) as connection:
            ReviewStore._review(connection, snapshot_id, editable=True)
            if request_id:
                existing = connection.execute("SELECT * FROM extraction_request WHERE request_id=?", (request_id,)).fetchone()
                if existing:
                    if existing["raw_snapshot_id"] != snapshot_id or existing["request_hash"] != request_hash:
                        raise PersonalError("分析请求内容已改变，请重新发起。", "idempotency_conflict", 409)
                    return ReviewStore._bundle(connection, snapshot_id)
            # A concurrent forced call also joins the already-running job.
            active = connection.execute("SELECT * FROM ai_extraction WHERE cache_key=? AND status='running' ORDER BY created_at DESC LIMIT 1", (cache_key,)).fetchone()
            cached = connection.execute("SELECT * FROM ai_extraction WHERE cache_key=? AND status IN ('valid','needs_review','failed') ORDER BY created_at DESC LIMIT 1", (cache_key,)).fetchone() if not force else None
            shared = active or cached
            if shared and shared["raw_snapshot_id"] == snapshot_id:
                if request_id:
                    connection.execute("INSERT INTO extraction_request VALUES (?,?,?,?)", (request_id, snapshot_id, request_hash, shared["id"]))
                review = ReviewStore._review(connection, snapshot_id)
                if review["extraction_id"] != shared["id"]:
                    connection.execute("UPDATE raw_review SET extraction_id=?,version=version+1,updated_at=? WHERE raw_snapshot_id=?", (shared["id"], now(), snapshot_id))
                return ReviewStore._bundle(connection, snapshot_id)
            extraction_id, stamp = identifier(), now()
            status = "waiting" if active else cached["status"] if cached else "running"
            connection.execute("""INSERT INTO ai_extraction
                (id,raw_snapshot_id,provider,model,prompt_version,schema_version,cache_key,request_id,request_hash,created_at,status,thinking,config_json,reused_from_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (extraction_id, snapshot_id, self.provider.name, config.model, PROMPT_VERSION, OUTPUT_SCHEMA_VERSION, cache_key, request_id, request_hash, stamp,
                 status, int(config.thinking), encode(config.model_dump()), shared["id"] if shared else None))
            if request_id:
                connection.execute("INSERT INTO extraction_request VALUES (?,?,?,?)", (request_id, snapshot_id, request_hash, extraction_id))
            if cached:
                for column in ("completed_at", "raw_output", "parsed_output", "validation_json", "error_json", "token_usage_json", "latency_ms", "analysis_state"):
                    connection.execute(f"UPDATE ai_extraction SET {column}=? WHERE id=?", (cached[column], extraction_id))
                if cached["parsed_output"]:
                    parsed = parse_output(cached["raw_output"])
                    validation = validate_candidate(parsed, snapshot)
                    connection.execute("UPDATE ai_extraction SET parsed_output=?,validation_json=?,status=? WHERE id=?", (encode(parsed), encode(validation), validation["status"], extraction_id))
            connection.execute("UPDATE raw_review SET extraction_id=?,version=version+1,updated_at=? WHERE raw_snapshot_id=?", (extraction_id, now(), snapshot_id))
            if force:
                connection.execute("UPDATE raw_review SET draft_json=NULL WHERE raw_snapshot_id=?", (snapshot_id,))
            schedule = shared is None
            result = ReviewStore._bundle(connection, snapshot_id)
        if schedule:
            task = asyncio.create_task(self._run(extraction_id, snapshot, config))
            self.tasks[extraction_id] = task
            task.add_done_callback(lambda _: self.tasks.pop(extraction_id, None))
        return result

    async def _run(self, extraction_id, snapshot, config):
        started, raw, usage, secret = time.monotonic(), None, {}, ""
        try:
            async with self.semaphore:
                _, api_key = self.settings.resolved()
                secret = api_key.get_secret_value()
                response = await self.provider.extract(snapshot, config, api_key)
                # A provider cannot smuggle a secret into persisted output.
                raw = response.raw_output.replace(secret, "[redacted]") if secret else response.raw_output
                usage = response.usage
                parsed = parse_output(raw)
                validation = validate_candidate(parsed, snapshot)
                status, error = validation["status"], None
                provenance = {**config.model_dump(), "response_model": response.response_model}
        except asyncio.CancelledError:
            parsed, validation, status = None, None, "failed"
            error = {"code": "interrupted", "message": "分析因应用退出中断。原文仍保留，重新打开后可主动重试。"}
            provenance = config.model_dump()
        except ExtractionFailure as exc:
            raw = exc.raw_output if exc.raw_output is not None else raw
            usage = exc.usage if exc.usage is not None else usage
            parsed, validation, status = None, None, "failed"
            error = {"code": exc.code, "message": str(exc)}
            provenance = config.model_dump()
        except PersonalError as exc:
            parsed, validation, status = None, None, "failed"
            error = {"code": exc.code, "message": str(exc)}
            provenance = config.model_dump()
        except Exception:
            parsed, validation, status = None, None, "failed"
            error = {"code": "analysis_error", "message": "分析未能完成。原文已保存，可重新分析或手动整理。"}
            provenance = config.model_dump()
        latency = int((time.monotonic() - started) * 1000)
        if raw is not None and secret:
            raw = raw.replace(secret, "[redacted]")
        with self.personal.connection(write=True) as connection:
            connection.execute("""UPDATE ai_extraction SET completed_at=?,status=?,raw_output=?,parsed_output=?,validation_json=?,error_json=?,token_usage_json=?,latency_ms=?,config_json=? WHERE id=?""",
                               (now(), status, raw, encode(parsed) if parsed is not None else None, encode(validation) if validation else None,
                                encode(error) if error else None, encode(usage), latency, encode(provenance), extraction_id))
            connection.execute('UPDATE ai_extraction SET analysis_state=? WHERE id=?',
                               ('validated' if parsed is not None else 'truncated' if error and error['code']=='truncated' else 'failed',extraction_id))
            waiting = connection.execute("SELECT id,raw_snapshot_id FROM ai_extraction WHERE reused_from_id=? AND status='waiting'", (extraction_id,)).fetchall()
            for follower in waiting:
                follower_validation = None
                follower_parsed = None
                if parsed is not None:
                    from .source_metadata import snapshot_with_metadata
                    follower_snapshot = snapshot_with_metadata(connection,connection.execute("SELECT * FROM raw_snapshot WHERE id=?", (follower["raw_snapshot_id"],)).fetchone())
                    follower_parsed = parse_output(raw)
                    follower_validation = validate_candidate(follower_parsed, follower_snapshot)
                connection.execute("""UPDATE ai_extraction SET completed_at=?,status=?,raw_output=?,parsed_output=?,validation_json=?,error_json=?,token_usage_json=?,latency_ms=?,config_json=? WHERE id=?""",
                                   (now(), follower_validation["status"] if follower_validation else status, raw, encode(follower_parsed) if follower_parsed is not None else None,
                                    encode(follower_validation) if follower_validation else None, encode(error) if error else None, encode(usage), latency, encode(provenance), follower["id"]))
            ids = [extraction_id] + [row["id"] for row in waiting]
            for value in ids:
                connection.execute("UPDATE raw_review SET version=version+1,updated_at=? WHERE extraction_id=? AND status='pending'", (now(), value))
