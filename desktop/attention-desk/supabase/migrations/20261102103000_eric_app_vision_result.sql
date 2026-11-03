-- Vision landing for one image at a time.
-- History is append-only in eric_app_records (kind vision_result).
-- The inbox list reads inbox_item.preview, so the latest card text is written there
-- and mirrored onto eric_app_captures.vision_* for other readers.
-- Windows publish must not put unverified OCR back into that preview.

ALTER TABLE public.eric_app_captures
  ADD COLUMN IF NOT EXISTS vision_status text,
  ADD COLUMN IF NOT EXISTS vision_provider text,
  ADD COLUMN IF NOT EXISTS vision_analysis jsonb,
  ADD COLUMN IF NOT EXISTS vision_confidence text,
  ADD COLUMN IF NOT EXISTS vision_processed_at timestamptz,
  ADD COLUMN IF NOT EXISTS vision_error jsonb;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='eric_app_captures_vision_status_check') THEN
    ALTER TABLE public.eric_app_captures
      ADD CONSTRAINT eric_app_captures_vision_status_check
      CHECK (vision_status IS NULL OR vision_status IN ('pending','completed','failed'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='eric_app_captures_vision_analysis_check') THEN
    ALTER TABLE public.eric_app_captures
      ADD CONSTRAINT eric_app_captures_vision_analysis_check
      CHECK (vision_analysis IS NULL OR jsonb_typeof(vision_analysis)='object');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='eric_app_captures_vision_confidence_check') THEN
    ALTER TABLE public.eric_app_captures
      ADD CONSTRAINT eric_app_captures_vision_confidence_check
      CHECK (vision_confidence IS NULL OR vision_confidence IN ('high','medium','low'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='eric_app_captures_vision_provider_check') THEN
    ALTER TABLE public.eric_app_captures
      ADD CONSTRAINT eric_app_captures_vision_provider_check
      CHECK (vision_provider IS NULL OR length(vision_provider) BETWEEN 1 AND 80);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='eric_app_captures_vision_error_check') THEN
    ALTER TABLE public.eric_app_captures
      ADD CONSTRAINT eric_app_captures_vision_error_check
      CHECK (vision_error IS NULL OR jsonb_typeof(vision_error)='object');
  END IF;
END $$;

COMMENT ON COLUMN public.eric_app_captures.vision_status IS 'Latest vision snapshot only. History lives in eric_app_records kind vision_result.';
COMMENT ON COLUMN public.eric_app_captures.vision_analysis IS 'Latest eric-os-vision-result analysis object. Not OCR and not the learning_capture payload.';

CREATE INDEX IF NOT EXISTS eric_app_vision_result_capture
  ON public.eric_app_records (workspace_id, ((payload->>'capture_id')), revision DESC)
  WHERE kind='vision_result' AND NOT deleted;

-- Applies a vision patch onto an inbox payload without dropping the rest of the record.
CREATE OR REPLACE FUNCTION public.eric_app_apply_inbox_vision(p_existing jsonb, p_patch jsonb)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SET search_path TO pg_catalog, public
AS $function$
DECLARE merged jsonb := coalesce(p_existing, '{}'::jsonb) || coalesce(p_patch, '{}'::jsonb);
BEGIN
  IF coalesce(p_existing->>'preview_source','')='human' THEN
    merged := merged || jsonb_build_object('preview', p_existing->'preview', 'preview_source', 'human');
  END IF;
  IF p_existing ? 'ocr_preview' THEN
    merged := merged || jsonb_build_object('ocr_preview', p_existing->'ocr_preview');
  ELSIF coalesce(p_existing->>'preview_source','') NOT IN ('vision','human','waiting','failed','low_confidence')
        AND coalesce(p_existing->>'preview','')<>'' THEN
    merged := merged || jsonb_build_object('ocr_preview', to_jsonb(p_existing->>'preview'));
  END IF;
  RETURN merged;
END $function$;

-- Keeps a vision/waiting/human preview when the Windows publisher sends a fresh OCR preview.
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

CREATE OR REPLACE FUNCTION public.eric_app_publish_records(p_workspace uuid, p_records jsonb, p_epoch uuid DEFAULT NULL::uuid, p_generation bigint DEFAULT 0)
RETURNS bigint
LANGUAGE plpgsql
SET search_path TO pg_catalog, public
AS $function$
DECLARE item jsonb; highwater bigint; existing public.eric_app_records; payload jsonb;
BEGIN
  IF p_workspace IS NULL OR p_records IS NULL OR jsonb_typeof(p_records)<>'array' OR jsonb_array_length(p_records)>100
    OR p_generation IS NULL OR p_generation<0 THEN RAISE EXCEPTION 'eric_invalid_batch'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_epoch IS DISTINCT FROM (SELECT publisher_epoch FROM public.eric_app_workers WHERE workspace_id=p_workspace)
    OR p_generation<coalesce((SELECT source_generation FROM public.eric_app_workers WHERE workspace_id=p_workspace),0) THEN RAISE EXCEPTION 'eric_stale_projection'; END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(p_records) LOOP
    IF item->>'kind' IN ('review_progress','inbox_progress','task_progress','cloud_capture','command','resource','vision_result') THEN RAISE EXCEPTION 'eric_reserved_record_kind'; END IF;
    payload := item->'payload';
    IF item->>'kind' IN ('inbox','inbox_item') THEN
      SELECT * INTO existing FROM public.eric_app_records
        WHERE workspace_id=p_workspace AND kind=item->>'kind' AND id=item->>'id';
      IF FOUND THEN
        payload := public.eric_app_preserve_inbox_vision(existing.payload, payload);
      END IF;
    END IF;
    INSERT INTO public.eric_app_records(workspace_id,kind,id,payload,source_hash,deleted,source_epoch,source_generation)
      VALUES(p_workspace,item->>'kind',item->>'id',payload,item->>'source_hash',coalesce((item->>'deleted')::boolean,false),p_epoch,p_generation)
      ON CONFLICT(workspace_id,kind,id) DO UPDATE SET payload=excluded.payload,source_hash=excluded.source_hash,deleted=excluded.deleted,source_epoch=excluded.source_epoch,source_generation=excluded.source_generation
      WHERE eric_app_records.source_hash IS DISTINCT FROM excluded.source_hash OR eric_app_records.deleted IS DISTINCT FROM excluded.deleted;
  END LOOP;
  SELECT coalesce(max(revision),0) INTO highwater FROM public.eric_app_changes WHERE workspace_id=p_workspace;
  INSERT INTO public.eric_app_workers(workspace_id,last_seen_at,published_at) VALUES(p_workspace,now(),now())
    ON CONFLICT(workspace_id) DO UPDATE SET last_seen_at=now(),published_at=now(),error_code=NULL,source_generation=p_generation;
  RETURN highwater;
END $function$;

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

REVOKE ALL ON FUNCTION public.eric_app_apply_inbox_vision(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.eric_app_preserve_inbox_vision(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.eric_app_append_vision_result(uuid, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.eric_app_apply_inbox_vision(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.eric_app_preserve_inbox_vision(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.eric_app_append_vision_result(uuid, jsonb) TO service_role;

COMMENT ON FUNCTION public.eric_app_append_vision_result(uuid, jsonb) IS
  'Append one vision_result (id vision:{capture_id}:{sha256 of canonical payload}). Updates inbox preview and the capture vision_* mirror. Does not overwrite course, lecture, learning_capture, or image bytes.';

-- IMG_1080 only: stop the stored list preview from being Windows OCR.
-- The previous preview is kept on ocr_preview for a collapsed debug view.
UPDATE public.eric_app_records
SET payload = payload || jsonb_build_object(
  'ocr_preview', COALESCE(payload->'ocr_preview', to_jsonb(payload->>'preview')),
  'preview', '等待 AI 分析…',
  'preview_source', 'waiting',
  'vision_status', 'pending',
  'vision_notice', '等待 AI 分析…'
)
WHERE workspace_id='50e5a3ac-dd15-4f0a-a93c-ace4b735d16d'
  AND id='e32765f5-43c9-4a20-8e50-d7afc9e7c773'
  AND kind IN ('inbox','inbox_item')
  AND NOT deleted
  AND coalesce(payload->>'preview_source','') NOT IN ('vision','human','waiting','failed','low_confidence');

UPDATE public.eric_app_captures
SET vision_status='pending'
WHERE id='88584ff1-867f-47e1-8276-a8412fb96830'
  AND workspace_id='50e5a3ac-dd15-4f0a-a93c-ace4b735d16d'
  AND vision_status IS NULL;
