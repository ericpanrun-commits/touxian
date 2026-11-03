-- Bounded cloud work recovery. Existing attempts, errors, identities and backoff remain authoritative.
-- Preserve explicit human previews even when later automatic vision arrives.
-- Reject empty AI success and fence out-of-order stage retries at the database boundary.
CREATE OR REPLACE FUNCTION public.eric_app_append_vision_result(p_workspace uuid, p_payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path TO pg_catalog, public
AS $function$
DECLARE
  v_capture_id uuid;
  v_resource text;
  v_sha text;
  v_status text;
  v_analyzer text;
  v_provider text;
  v_result jsonb;
  v_error jsonb;
  v_band text;
  v_raw_conf text;
  v_analyzed timestamptz;
  v_analyzed_text text;
  v_preview text;
  v_source text;
  v_notice text;
  v_stored jsonb;
  v_hash text;
  v_id text;
  v_revision bigint;
  v_inserted boolean := true;
  v_capture public.eric_app_captures;
  v_raw text;
  v_latest boolean := true;
  v_existing_at timestamptz;
  v_patch jsonb;
  v_kind text;
  v_key text;
BEGIN
  IF p_workspace IS NULL OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR p_payload ? 'object_path' THEN
    RAISE EXCEPTION 'eric_invalid_vision';
  END IF;
  IF p_payload->>'schema' IS DISTINCT FROM 'eric-os-vision-result.v1'
     OR coalesce(p_payload->>'source','')<>'original_image'
     OR octet_length(p_payload::text)>200000 THEN
    RAISE EXCEPTION 'eric_invalid_vision';
  END IF;
  BEGIN
    v_capture_id := (p_payload->>'capture_id')::uuid;
  EXCEPTION WHEN others THEN
    RAISE EXCEPTION 'eric_invalid_vision';
  END;
  v_resource := p_payload->>'resource_id';
  v_sha := p_payload->>'sha256';
  v_status := p_payload->>'status';
  v_analyzed_text := p_payload->>'analyzed_at';
  IF v_resource IS NULL OR v_sha IS NULL OR v_status IS NULL OR v_analyzed_text IS NULL
     OR v_resource <> 'capture:'||v_capture_id::text||':'||v_sha
     OR v_sha !~ '^[a-f0-9]{64}$'
     OR v_status NOT IN ('pending','completed','failed')
     OR v_analyzed_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.+(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
    RAISE EXCEPTION 'eric_invalid_vision';
  END IF;
  BEGIN
    v_analyzed := v_analyzed_text::timestamptz;
  EXCEPTION WHEN others THEN
    RAISE EXCEPTION 'eric_invalid_vision';
  END;
  v_analyzer := coalesce(nullif(p_payload->>'analyzer',''), nullif(p_payload->>'provider',''));
  v_provider := coalesce(nullif(p_payload->>'provider',''), v_analyzer);
  IF v_analyzer IS NULL OR v_provider IS NULL OR length(v_analyzer)>80 OR length(v_provider)>80
     OR v_analyzer ~ '[[:cntrl:]]' OR v_provider ~ '[[:cntrl:]]' THEN
    RAISE EXCEPTION 'eric_invalid_vision';
  END IF;
  v_result := coalesce(p_payload->'result','{}'::jsonb);
  IF jsonb_typeof(v_result)<>'object' THEN RAISE EXCEPTION 'eric_invalid_vision'; END IF;
  FOREACH v_key IN ARRAY ARRAY['title','probable_course','topic','summary','readable_content'] LOOP
    IF v_result ? v_key AND jsonb_typeof(v_result->v_key)<>'string' THEN RAISE EXCEPTION 'eric_invalid_vision'; END IF;
  END LOOP;
  IF length(coalesce(v_result->>'title',''))>240
     OR length(coalesce(v_result->>'probable_course',''))>200
     OR length(coalesce(v_result->>'topic',''))>240
     OR length(coalesce(v_result->>'summary',''))>4000
     OR length(coalesce(v_result->>'readable_content',''))>20000 THEN
    RAISE EXCEPTION 'eric_invalid_vision';
  END IF;
  FOREACH v_key IN ARRAY ARRAY['equations','key_points','uncertain_regions'] LOOP
    IF NOT (v_result ? v_key) THEN CONTINUE; END IF;
    IF jsonb_typeof(v_result->v_key)<>'array' OR jsonb_array_length(v_result->v_key)>100 THEN
      RAISE EXCEPTION 'eric_invalid_vision';
    END IF;
  END LOOP;
  IF p_payload ? 'error' AND p_payload->'error' IS NOT NULL AND jsonb_typeof(p_payload->'error')<>'null'
     AND jsonb_typeof(p_payload->'error')<>'object' THEN
    RAISE EXCEPTION 'eric_invalid_vision';
  END IF;
  v_error := CASE WHEN p_payload->'error' IS NULL OR jsonb_typeof(p_payload->'error')='null' THEN NULL ELSE p_payload->'error' END;
  IF v_status='completed' AND (v_error IS NOT NULL OR NOT EXISTS (
    SELECT 1 FROM jsonb_each(v_result) AS item(key,value)
    WHERE (key IN ('title','probable_course','topic','summary','readable_content')
           AND jsonb_typeof(value)='string' AND btrim(value #>> '{}')<>'')
       OR (key IN ('equations','key_points','uncertain_regions') AND
           CASE WHEN jsonb_typeof(value)='array' THEN jsonb_array_length(value)>0 ELSE false END)
  )) THEN RAISE EXCEPTION 'eric_invalid_vision'; END IF;
  v_raw_conf := coalesce(nullif(p_payload->>'confidence',''), nullif(v_result->>'confidence',''));
  IF v_raw_conf IS NULL THEN
    v_band := NULL;
  ELSIF v_raw_conf IN ('high','medium','low') THEN
    v_band := v_raw_conf;
  ELSIF v_raw_conf ~ '^[0-9]+(\.[0-9]+)?$' AND v_raw_conf::numeric>=0 AND v_raw_conf::numeric<=1 THEN
    v_band := CASE WHEN v_raw_conf::numeric>=0.8 THEN 'high' WHEN v_raw_conf::numeric>=0.5 THEN 'medium' ELSE 'low' END;
  ELSE
    RAISE EXCEPTION 'eric_invalid_vision';
  END IF;
  IF v_status='failed' THEN
    v_preview := 'AI 分析失败，可重新处理';
    v_source := 'failed';
    v_notice := v_preview;
    v_error := coalesce(v_error, jsonb_build_object('message', v_preview));
  ELSIF v_status='completed' AND v_band='low' THEN
    v_preview := '部分内容需要确认';
    v_source := 'low_confidence';
    v_notice := v_preview;
    v_error := NULL;
  ELSIF v_status='completed' THEN
    v_preview := left(coalesce(nullif(v_result->>'summary',''), nullif(v_result->>'readable_content',''), nullif(v_result->>'title',''), '等待 AI 分析…'), 240);
    v_source := 'vision';
    v_notice := NULL;
    v_error := NULL;
  ELSE
    v_preview := '等待 AI 分析…';
    v_source := 'waiting';
    v_notice := v_preview;
    v_error := NULL;
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  SELECT * INTO v_capture FROM public.eric_app_captures WHERE workspace_id=p_workspace AND id=v_capture_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'eric_capture_not_found'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(v_capture.files) AS file
    WHERE file->>'resource_id'=v_resource AND file->>'sha256'=v_sha AND coalesce(file->>'mime','') LIKE 'image/%'
  ) THEN RAISE EXCEPTION 'eric_resource_not_found'; END IF;

  v_stored := jsonb_strip_nulls(jsonb_build_object(
    'schema','eric-os-vision-result.v1',
    'capture_id', v_capture_id::text,
    'resource_id', v_resource,
    'sha256', v_sha,
    'status', v_status,
    'analyzed_at', v_analyzed_text,
    'analyzer', v_analyzer,
    'provider', v_provider,
    'source','original_image',
    'confidence', v_band,
    'result', v_result,
    'error', v_error
  ));
  v_hash := encode(sha256(convert_to(v_stored::text,'UTF8')),'hex');
  v_id := 'vision:'||v_capture_id::text||':'||v_hash;
  INSERT INTO public.eric_app_records(workspace_id,kind,id,payload,source_hash,deleted,source_epoch,source_generation)
    VALUES(p_workspace,'vision_result',v_id,v_stored,v_hash,false,NULL,0)
    ON CONFLICT(workspace_id,kind,id) DO NOTHING
    RETURNING revision INTO v_revision;
  IF v_revision IS NULL THEN
    v_inserted := false;
    SELECT revision INTO v_revision FROM public.eric_app_records
      WHERE workspace_id=p_workspace AND kind='vision_result' AND id=v_id;
  END IF;

  v_raw := v_capture.raw_item_id;
  SELECT max(stamp) INTO v_existing_at FROM (
    SELECT v_capture.vision_processed_at AS stamp
    UNION ALL
    SELECT CASE
      WHEN payload->>'vision_processed_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.+(Z|[+-][0-9]{2}:[0-9]{2})$'
      THEN (payload->>'vision_processed_at')::timestamptz
      ELSE NULL
    END
    FROM public.eric_app_records
    WHERE v_raw IS NOT NULL AND workspace_id=p_workspace AND id=v_raw AND kind IN ('inbox','inbox_item') AND NOT deleted
  ) stamps;
  IF v_existing_at IS NOT NULL AND v_existing_at > v_analyzed THEN
    v_latest := false;
  END IF;

  -- A late retry of the pending/failed stage must not regress a completed result
  -- from the same analysis timestamp. Keep the append-only event for diagnostics.
  IF v_existing_at=v_analyzed AND v_capture.vision_status='completed' AND v_status<>'completed' THEN
    v_latest:=false;
  END IF;
  IF v_latest THEN
    UPDATE public.eric_app_captures SET
      vision_status=v_status,
      vision_provider=v_provider,
      vision_analysis=v_result,
      vision_confidence=v_band,
      vision_processed_at=v_analyzed,
      vision_error=v_error
    WHERE workspace_id=p_workspace AND id=v_capture_id;
    v_patch := jsonb_build_object(
      'preview', v_preview,
      'preview_source', v_source,
      'vision_status', v_status,
      'vision_provider', v_provider,
      'vision_analysis', v_result,
      'vision_confidence', CASE WHEN v_band IS NULL THEN 'null'::jsonb ELSE to_jsonb(v_band) END,
      'vision_processed_at', v_analyzed_text,
      'vision_error', coalesce(v_error, 'null'::jsonb),
      'vision_notice', CASE WHEN v_notice IS NULL THEN 'null'::jsonb ELSE to_jsonb(v_notice) END,
      'vision_resource_id', v_resource,
      'vision_sha256', v_sha
    );
    IF v_raw IS NOT NULL THEN
      FOREACH v_kind IN ARRAY ARRAY['inbox','inbox_item'] LOOP
        UPDATE public.eric_app_records r
          SET payload=public.eric_app_apply_inbox_vision(r.payload, v_patch),
              source_hash=encode(sha256(convert_to(public.eric_app_apply_inbox_vision(r.payload, v_patch)::text,'UTF8')),'hex')
          WHERE r.workspace_id=p_workspace AND r.kind=v_kind AND r.id=v_raw AND NOT r.deleted;
      END LOOP;
    END IF;
  END IF;

  RETURN jsonb_build_object(
    'ok', true,
    'kind', 'vision_result',
    'id', v_id,
    'revision', v_revision,
    'inserted', v_inserted,
    'latest', v_latest,
    'capture_id', v_capture_id,
    'inbox_id', v_raw
  );
END $function$;

CREATE OR REPLACE FUNCTION public.eric_app_preserve_inbox_vision(p_existing jsonb, p_incoming jsonb)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SET search_path TO pg_catalog, public
AS $function$
DECLARE
  merged jsonb := coalesce(p_incoming, '{}'::jsonb);
  existing_at timestamptz;
  incoming_at timestamptz;
BEGIN
  -- A later automatic timestamp cannot outrank an explicit human correction.
  IF p_existing->>'preview_source'='human' AND p_incoming->>'preview_source' IS DISTINCT FROM 'human' THEN
    merged:=merged || jsonb_build_object('preview',p_existing->'preview','preview_source','human');
  END IF;
  IF p_existing IS NULL OR jsonb_typeof(p_existing)<>'object' THEN
    RETURN merged;
  END IF;
  IF coalesce(p_existing->>'preview_source','') NOT IN ('vision','human','waiting','failed','low_confidence')
     AND NOT (p_existing ? 'vision_status') THEN
    RETURN merged;
  END IF;
  BEGIN
    existing_at := nullif(p_existing->>'vision_processed_at','')::timestamptz;
    incoming_at := nullif(p_incoming->>'vision_processed_at','')::timestamptz;
  EXCEPTION WHEN others THEN
    existing_at := NULL;
    incoming_at := NULL;
  END;
  IF incoming_at IS NOT NULL AND (existing_at IS NULL OR incoming_at >= existing_at)
     AND coalesce(p_incoming->>'vision_status','') IN ('pending','completed','failed')
     AND coalesce(p_incoming->>'preview_source','') IN ('vision','human','waiting','failed','low_confidence') THEN
    RETURN merged;
  END IF;
  IF coalesce(p_incoming->>'preview_source','')='human' THEN
    RETURN merged || jsonb_build_object(
      'vision_status', p_existing->'vision_status',
      'vision_provider', p_existing->'vision_provider',
      'vision_analysis', p_existing->'vision_analysis',
      'vision_confidence', p_existing->'vision_confidence',
      'vision_processed_at', p_existing->'vision_processed_at',
      'vision_error', p_existing->'vision_error',
      'vision_notice', p_existing->'vision_notice',
      'ocr_preview', COALESCE(p_existing->'ocr_preview', to_jsonb(NULLIF(p_incoming->>'preview','')))
    );
  END IF;
  merged := merged || jsonb_build_object(
    'preview', p_existing->'preview',
    'preview_source', p_existing->'preview_source',
    'vision_status', p_existing->'vision_status',
    'vision_provider', p_existing->'vision_provider',
    'vision_analysis', p_existing->'vision_analysis',
    'vision_confidence', p_existing->'vision_confidence',
    'vision_processed_at', p_existing->'vision_processed_at',
    'vision_error', p_existing->'vision_error',
    'vision_notice', p_existing->'vision_notice'
  );
  IF p_existing ? 'ocr_preview' THEN
    merged := merged || jsonb_build_object('ocr_preview', p_existing->'ocr_preview');
  ELSIF coalesce(p_incoming->>'preview','')<>'' THEN
    merged := merged || jsonb_build_object('ocr_preview', to_jsonb(p_incoming->>'preview'));
  END IF;
  RETURN merged;
END $function$;

-- Five attempts total (initial attempt plus at most four retries). No historical data is deleted.
ALTER TABLE public.eric_app_captures
  ADD COLUMN last_attempt_at timestamptz,
  ADD COLUMN completed_at timestamptz;
ALTER TABLE public.eric_app_commands
  ADD COLUMN last_attempt_at timestamptz,
  ADD COLUMN completed_at timestamptz;

CREATE FUNCTION public.eric_app_job_lifecycle() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.attempts>0 THEN NEW.last_attempt_at:=now(); END IF;
  ELSIF NEW.attempts>OLD.attempts THEN
    NEW.last_attempt_at:=now();
  END IF;
  IF NEW.state IN ('completed','failed','needs_confirmation') THEN
    NEW.completed_at:=coalesce(NEW.completed_at,now());
  ELSE
    NEW.completed_at:=NULL;
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.eric_app_job_lifecycle() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.eric_app_job_lifecycle() TO service_role;
CREATE TRIGGER eric_app_job_lifecycle BEFORE INSERT OR UPDATE ON public.eric_app_captures
  FOR EACH ROW EXECUTE FUNCTION public.eric_app_job_lifecycle();
CREATE TRIGGER eric_app_job_lifecycle BEFORE INSERT OR UPDATE ON public.eric_app_commands
  FOR EACH ROW EXECUTE FUNCTION public.eric_app_job_lifecycle();

CREATE OR REPLACE FUNCTION public.eric_app_claim_work(p_workspace uuid, p_kind text, p_limit integer DEFAULT 3)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE result jsonb;
BEGIN
  IF p_workspace IS NULL OR p_limit IS NULL OR p_kind IS NULL OR p_limit NOT BETWEEN 1 AND 5 OR p_kind NOT IN ('captures','commands') THEN RAISE EXCEPTION 'eric_invalid_claim'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_kind='captures' THEN
    UPDATE public.eric_app_captures SET state='failed',lease_token=NULL,lease_until=NULL,
      error=jsonb_build_object('code','retry_exhausted','message','重试次数已用尽，原件保留；请核对后再处理。','last_error',error)
      WHERE workspace_id=p_workspace AND source='pwa' AND raw_item_id IS NULL AND attempts>=5
        AND (state='received' OR state='processing' AND (lease_until IS NULL OR lease_until<=now()));
    WITH selected AS (SELECT id FROM public.eric_app_captures WHERE workspace_id=p_workspace AND source='pwa' AND raw_item_id IS NULL AND attempts<5
      AND (state='received' OR state='processing' AND (lease_until IS NULL OR lease_until<=now())) AND next_attempt_at<=now() ORDER BY created_at,id LIMIT p_limit FOR UPDATE SKIP LOCKED),
    claimed AS (UPDATE public.eric_app_captures SET state='processing',lease_token=gen_random_uuid(),lease_until=now()+interval '5 minutes',attempts=attempts+1
      WHERE id IN (SELECT id FROM selected) RETURNING *) SELECT coalesce(jsonb_agg(to_jsonb(claimed)),'[]') INTO result FROM claimed;
  ELSE
    UPDATE public.eric_app_commands SET state='failed',lease_token=NULL,lease_until=NULL,
      error=jsonb_build_object('code','retry_exhausted','message','重试次数已用尽，已保存修改和原件保留；请核对后再处理。','last_error',error)
      WHERE workspace_id=p_workspace AND attempts>=5
        AND (state='pending' OR state='processing' AND (lease_until IS NULL OR lease_until<=now()));
    WITH selected AS (SELECT id FROM public.eric_app_commands WHERE workspace_id=p_workspace AND attempts<5 AND
      (state='pending' OR state='processing' AND (lease_until IS NULL OR lease_until<=now())) AND next_attempt_at<=now() ORDER BY created_at,id LIMIT p_limit FOR UPDATE SKIP LOCKED),
    claimed AS (UPDATE public.eric_app_commands SET state='processing',lease_token=gen_random_uuid(),lease_until=now()+interval '5 minutes',attempts=attempts+1
      WHERE id IN (SELECT id FROM selected) RETURNING *) SELECT coalesce(jsonb_agg(to_jsonb(claimed)),'[]') INTO result FROM claimed;
  END IF;
  INSERT INTO public.eric_app_workers(workspace_id,last_seen_at) VALUES(p_workspace,now())
    ON CONFLICT(workspace_id) DO UPDATE SET last_seen_at=now();
  RETURN result;
END $function$;

CREATE OR REPLACE FUNCTION public.eric_app_finish_work(p_workspace uuid, p_kind text, p_id uuid, p_token uuid, p_state text, p_result jsonb, p_records jsonb DEFAULT '[]'::jsonb, p_epoch uuid DEFAULT NULL::uuid, p_generation bigint DEFAULT 0)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE capture public.eric_app_captures; command public.eric_app_commands; result jsonb;
BEGIN
  IF p_workspace IS NULL OR p_kind IS NULL OR p_token IS NULL OR p_state IS NULL OR p_kind NOT IN ('captures','commands') THEN RAISE EXCEPTION 'eric_invalid_claim'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_kind='commands' THEN
    SELECT * INTO command FROM public.eric_app_commands WHERE workspace_id=p_workspace AND id=p_id FOR UPDATE;
    IF NOT FOUND OR command.state<>'processing' OR command.lease_token IS DISTINCT FROM p_token OR command.lease_until<=now() THEN RAISE EXCEPTION 'eric_stale_lease'; END IF;
    IF p_state NOT IN ('completed','failed','needs_confirmation','pending') THEN RAISE EXCEPTION 'eric_invalid_state'; END IF;
    IF p_state='pending' AND command.attempts>=5 THEN
      p_state:='failed';
      p_result:=jsonb_build_object('code','retry_exhausted','message','重试次数已用尽，原件保留；请核对后再处理。','last_error',p_result);
    END IF;
    IF p_state='completed' THEN PERFORM public.eric_app_publish_records(p_workspace,p_records,p_epoch,p_generation); END IF;
    UPDATE public.eric_app_commands SET state=p_state,result=CASE WHEN p_state='completed' THEN p_result ELSE NULL END,
      error=CASE WHEN p_state='completed' THEN NULL ELSE p_result END,lease_token=NULL,lease_until=NULL,
      next_attempt_at=now()+make_interval(secs=>least(300,power(2,least(attempts,8))::integer)) WHERE id=p_id RETURNING to_jsonb(eric_app_commands.*) INTO result;
  ELSE
    SELECT * INTO capture FROM public.eric_app_captures WHERE workspace_id=p_workspace AND id=p_id FOR UPDATE;
    IF NOT FOUND OR capture.state<>'processing' OR capture.lease_token IS DISTINCT FROM p_token OR capture.lease_until<=now() THEN RAISE EXCEPTION 'eric_stale_lease'; END IF;
    IF p_state NOT IN ('processing','completed','failed','needs_confirmation','received') THEN RAISE EXCEPTION 'eric_invalid_state'; END IF;
    IF p_state IN ('processing','completed','needs_confirmation') AND (nullif(p_result->>'raw_item_id','') IS NULL OR nullif(p_result->>'local_receipt_id','') IS NULL) THEN
      RAISE EXCEPTION 'eric_local_receive_unconfirmed';
    END IF;
    IF p_state='received' AND capture.attempts>=5 THEN
      p_state:='failed';
      p_result:=jsonb_build_object('code','retry_exhausted','message','重试次数已用尽，原件保留；请核对后再处理。','last_error',p_result);
    END IF;
    PERFORM public.eric_app_publish_records(p_workspace,p_records,p_epoch,p_generation);
    UPDATE public.eric_app_captures SET state=p_state,raw_item_id=coalesce(p_result->>'raw_item_id',raw_item_id),
      local_receipt_id=coalesce(p_result->>'local_receipt_id',local_receipt_id),
      error=CASE WHEN p_state IN ('failed','received') THEN p_result ELSE NULL END,lease_token=NULL,lease_until=NULL,
      next_attempt_at=now()+make_interval(secs=>least(300,power(2,least(attempts,8))::integer)) WHERE id=p_id RETURNING to_jsonb(eric_app_captures.*) INTO result;
  END IF;
  RETURN result;
END $function$;
