-- Eric OS v1 only. Existing LifeLedger/IELTS/notes tables and auth settings are untouched.
-- Browsers use the authenticated Edge API. Privileged RPCs are never client-callable.
CREATE SEQUENCE public.eric_app_revision_seq;

CREATE TABLE public.eric_app_workspaces (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL UNIQUE,
  notes_installation_id uuid UNIQUE,
  timezone text NOT NULL DEFAULT 'Asia/Shanghai' CHECK(timezone='Asia/Shanghai'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.eric_app_devices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES public.eric_app_workspaces(id),
  user_id uuid NOT NULL,
  session_id uuid NOT NULL,
  label text NOT NULL CHECK(length(label) BETWEEN 1 AND 80),
  platform text NOT NULL CHECK(platform IN ('windows','iphone','ipad','web')),
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  UNIQUE(workspace_id,session_id)
);
CREATE TABLE public.eric_app_records (
  workspace_id uuid NOT NULL REFERENCES public.eric_app_workspaces(id),
  kind text NOT NULL CHECK(kind ~ '^[a-z_]{1,40}$'),
  id text NOT NULL CHECK(length(id) BETWEEN 1 AND 512),
  payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
  source_hash text NOT NULL CHECK(source_hash ~ '^[a-f0-9]{64}$'),
  source_epoch uuid,
  source_generation bigint NOT NULL DEFAULT 0 CHECK(source_generation>=0),
  revision bigint NOT NULL DEFAULT 0,
  deleted boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,kind,id)
);
CREATE TABLE public.eric_app_resources (
  workspace_id uuid NOT NULL REFERENCES public.eric_app_workspaces(id),
  id text NOT NULL CHECK(length(id) BETWEEN 1 AND 256),
  filename text NOT NULL CHECK(length(filename) BETWEEN 1 AND 240),
  mime text NOT NULL CHECK(length(mime) BETWEEN 1 AND 100),
  sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
  bytes bigint NOT NULL CHECK(bytes BETWEEN 1 AND 512000000),
  part_count integer NOT NULL CHECK(part_count BETWEEN 1 AND 128),
  complete boolean NOT NULL DEFAULT false,
  revision bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,id)
);
CREATE TABLE public.eric_app_resource_parts (
  workspace_id uuid NOT NULL,
  resource_id text NOT NULL,
  part_index integer NOT NULL CHECK(part_index BETWEEN 0 AND 127),
  sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
  bytes integer NOT NULL CHECK(bytes BETWEEN 1 AND 16000000),
  object_path text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,resource_id,part_index),
  FOREIGN KEY(workspace_id,resource_id) REFERENCES public.eric_app_resources(workspace_id,id),
  CHECK(object_path = workspace_id::text||'/'||sha256)
);
CREATE TABLE public.eric_app_captures (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES public.eric_app_workspaces(id),
  device_id text NOT NULL CHECK(length(device_id) BETWEEN 1 AND 100),
  source text NOT NULL CHECK(source IN ('pwa','shortcut','local')),
  request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
  title text NOT NULL CHECK(length(title) BETWEEN 1 AND 240),
  text_content text NOT NULL DEFAULT '' CHECK(length(text_content)<=100000),
  purpose text NOT NULL CHECK(purpose IN ('capture','lecture_note','textbook')),
  files jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(files)='array' AND jsonb_array_length(files)<=10),
  course_candidates jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(course_candidates)='array' AND jsonb_array_length(course_candidates)<=10),
  shared_at timestamptz,
  received_at timestamptz,
  state text NOT NULL DEFAULT 'uploading' CHECK(state IN ('uploading','received','processing','completed','failed','needs_confirmation')),
  raw_item_id text,
  local_receipt_id text,
  learning_capture_id text,
  course_id text,
  lecture_id text,
  error jsonb,
  lease_token uuid,
  lease_until timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  local_version integer NOT NULL DEFAULT 0,
  revision bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.eric_app_commands (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES public.eric_app_workspaces(id),
  device_id uuid NOT NULL REFERENCES public.eric_app_devices(id),
  resource_key text NOT NULL CHECK(length(resource_key) BETWEEN 1 AND 512),
  method text NOT NULL CHECK(method IN ('POST','PUT','PATCH')),
  path text NOT NULL CHECK(length(path) BETWEEN 1 AND 512 AND path LIKE '/%'),
  body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
  request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
  expected_revision bigint NOT NULL,
  state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','processing','completed','failed','needs_confirmation')),
  result jsonb,
  error jsonb,
  lease_token uuid,
  lease_until timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  revision bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX eric_app_one_pending_command ON public.eric_app_commands(workspace_id,resource_key)
  WHERE state IN ('pending','processing');
CREATE TABLE public.eric_app_changes (
  revision bigint PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES public.eric_app_workspaces(id),
  kind text NOT NULL,
  record_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.eric_app_workers (
  workspace_id uuid PRIMARY KEY REFERENCES public.eric_app_workspaces(id),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  phase text NOT NULL DEFAULT 'connected',
  error_code text,
  published_at timestamptz,
  publisher_epoch uuid,
  source_generation bigint NOT NULL DEFAULT 0
);
CREATE TABLE public.eric_app_receipts (
  workspace_id uuid NOT NULL REFERENCES public.eric_app_workspaces(id),
  request_id uuid NOT NULL,
  request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
  result jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,request_id)
);
CREATE INDEX eric_app_changes_cursor ON public.eric_app_changes(workspace_id,revision);
CREATE INDEX eric_app_record_kind ON public.eric_app_records(workspace_id,kind,updated_at DESC);
CREATE INDEX eric_app_capture_work ON public.eric_app_captures(workspace_id,state,next_attempt_at,created_at);
CREATE INDEX eric_app_command_work ON public.eric_app_commands(workspace_id,state,next_attempt_at,created_at);
CREATE INDEX eric_app_capture_raw ON public.eric_app_captures(workspace_id,raw_item_id) WHERE raw_item_id IS NOT NULL;
CREATE INDEX eric_app_parts_fk ON public.eric_app_resource_parts(workspace_id,resource_id);
CREATE INDEX eric_app_device_user ON public.eric_app_devices(user_id,workspace_id);
CREATE INDEX eric_app_command_device ON public.eric_app_commands(device_id);

CREATE FUNCTION public.eric_app_before_change() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
  -- Serialize before assigning a revision, so a later committed cursor cannot
  -- skip an earlier uncommitted update. Identity/sequence defaults alone do not.
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||NEW.workspace_id::text,0));
  NEW.revision:=nextval('public.eric_app_revision_seq');
  NEW.updated_at:=now();
  RETURN NEW;
END $$;
CREATE FUNCTION public.eric_app_after_change() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE entity_kind text;
BEGIN
  entity_kind:=CASE TG_TABLE_NAME
    WHEN 'eric_app_records' THEN to_jsonb(NEW)->>'kind'
    WHEN 'eric_app_captures' THEN 'cloud_capture'
    WHEN 'eric_app_commands' THEN 'command'
    ELSE 'resource' END;
  INSERT INTO public.eric_app_changes(revision,workspace_id,kind,record_id)
    VALUES(NEW.revision,NEW.workspace_id,entity_kind,NEW.id::text);
  RETURN NEW;
END $$;
DO $$ DECLARE name text; BEGIN
  FOREACH name IN ARRAY ARRAY['eric_app_records','eric_app_resources','eric_app_captures','eric_app_commands'] LOOP
    EXECUTE format('CREATE TRIGGER eric_app_before_change BEFORE INSERT OR UPDATE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.eric_app_before_change()',name);
    EXECUTE format('CREATE TRIGGER eric_app_after_change AFTER INSERT OR UPDATE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.eric_app_after_change()',name);
  END LOOP;
END $$;

CREATE FUNCTION public.eric_app_publish_records(p_workspace uuid,p_records jsonb,p_epoch uuid DEFAULT NULL,p_generation bigint DEFAULT 0)
RETURNS bigint LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE item jsonb; highwater bigint;
BEGIN
  IF p_workspace IS NULL OR p_records IS NULL OR jsonb_typeof(p_records)<>'array' OR jsonb_array_length(p_records)>100
    OR p_generation IS NULL OR p_generation<0 THEN RAISE EXCEPTION 'eric_invalid_batch'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_epoch IS DISTINCT FROM (SELECT publisher_epoch FROM public.eric_app_workers WHERE workspace_id=p_workspace)
    OR p_generation<coalesce((SELECT source_generation FROM public.eric_app_workers WHERE workspace_id=p_workspace),0) THEN RAISE EXCEPTION 'eric_stale_projection'; END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(p_records) LOOP
    IF item->>'kind' IN ('review_progress','inbox_progress','task_progress','cloud_capture','command','resource') THEN RAISE EXCEPTION 'eric_reserved_record_kind'; END IF;
    INSERT INTO public.eric_app_records(workspace_id,kind,id,payload,source_hash,deleted,source_epoch,source_generation)
      VALUES(p_workspace,item->>'kind',item->>'id',item->'payload',item->>'source_hash',coalesce((item->>'deleted')::boolean,false),p_epoch,p_generation)
      ON CONFLICT(workspace_id,kind,id) DO UPDATE SET payload=excluded.payload,source_hash=excluded.source_hash,deleted=excluded.deleted,source_epoch=excluded.source_epoch,source_generation=excluded.source_generation
      WHERE eric_app_records.source_hash IS DISTINCT FROM excluded.source_hash OR eric_app_records.deleted IS DISTINCT FROM excluded.deleted;
  END LOOP;
  SELECT coalesce(max(revision),0) INTO highwater FROM public.eric_app_changes WHERE workspace_id=p_workspace;
  INSERT INTO public.eric_app_workers(workspace_id,last_seen_at,published_at) VALUES(p_workspace,now(),now())
    ON CONFLICT(workspace_id) DO UPDATE SET last_seen_at=now(),published_at=now(),error_code=NULL,source_generation=p_generation;
  RETURN highwater;
END $$;

CREATE FUNCTION public.eric_app_reserve_capture(p_workspace uuid,p_capture jsonb)
RETURNS SETOF public.eric_app_captures LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE old public.eric_app_captures;
BEGIN
  IF p_workspace IS NULL OR p_capture IS NULL OR jsonb_typeof(p_capture)<>'object'
    OR coalesce(p_capture->>'request_hash','') !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'eric_invalid_capture'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_capture->>'source'='pwa' AND NOT EXISTS(SELECT 1 FROM public.eric_app_devices d JOIN public.eric_app_workspaces w ON w.id=d.workspace_id
    WHERE d.workspace_id=p_workspace AND d.id::text=p_capture->>'device_id' AND d.user_id=w.owner_id AND d.revoked_at IS NULL) THEN RAISE EXCEPTION 'eric_device_revoked'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app-capture:'||(p_capture->>'id'),0));
  SELECT * INTO old FROM public.eric_app_captures WHERE id=(p_capture->>'id')::uuid;
  IF FOUND THEN
    IF old.workspace_id IS DISTINCT FROM p_workspace OR old.request_hash IS DISTINCT FROM p_capture->>'request_hash' THEN RAISE EXCEPTION 'eric_idempotency_conflict'; END IF;
    RETURN NEXT old;RETURN;
  END IF;
  RETURN QUERY INSERT INTO public.eric_app_captures(id,workspace_id,device_id,source,request_hash,title,text_content,purpose,files,course_candidates,shared_at,state,received_at)
    VALUES((p_capture->>'id')::uuid,p_workspace,p_capture->>'device_id',p_capture->>'source',p_capture->>'request_hash',p_capture->>'title',coalesce(p_capture->>'text_content',''),
      p_capture->>'purpose',coalesce(p_capture->'files','[]'),coalesce(p_capture->'course_candidates','[]'),(p_capture->>'shared_at')::timestamptz,
      CASE WHEN jsonb_array_length(coalesce(p_capture->'files','[]'))=0 THEN 'received' ELSE 'uploading' END,
      CASE WHEN jsonb_array_length(coalesce(p_capture->'files','[]'))=0 THEN now() ELSE NULL END) RETURNING *;
END $$;

CREATE FUNCTION public.eric_app_finish_resource(p_workspace uuid,p_id text)
RETURNS SETOF public.eric_app_resources LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE resource public.eric_app_resources; count_parts integer; total bigint; first_part integer; last_part integer;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  SELECT * INTO resource FROM public.eric_app_resources WHERE workspace_id=p_workspace AND id=p_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'eric_resource_not_found'; END IF;
  SELECT count(*),sum(bytes),min(part_index),max(part_index) INTO count_parts,total,first_part,last_part
    FROM public.eric_app_resource_parts WHERE workspace_id=p_workspace AND resource_id=p_id;
  IF count_parts<>resource.part_count OR total<>resource.bytes OR first_part<>0 OR last_part<>resource.part_count-1 THEN RAISE EXCEPTION 'eric_resource_incomplete'; END IF;
  IF EXISTS(SELECT 1 FROM public.eric_app_resource_parts p WHERE p.workspace_id=p_workspace AND p.resource_id=p_id
      AND NOT EXISTS(SELECT 1 FROM storage.objects o WHERE o.bucket_id='eric-app-private' AND o.name=p.object_path
        AND (o.metadata->>'size')::bigint=p.bytes)) THEN RAISE EXCEPTION 'eric_resource_missing'; END IF;
  IF NOT resource.complete THEN UPDATE public.eric_app_resources SET complete=true WHERE workspace_id=p_workspace AND id=p_id; END IF;
  RETURN QUERY SELECT * FROM public.eric_app_resources WHERE workspace_id=p_workspace AND id=p_id;
END $$;

CREATE FUNCTION public.eric_app_commit_capture(p_workspace uuid,p_id uuid)
RETURNS SETOF public.eric_app_captures LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE capture public.eric_app_captures; file jsonb;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  SELECT * INTO capture FROM public.eric_app_captures WHERE workspace_id=p_workspace AND id=p_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'eric_capture_not_found'; END IF;
  FOR file IN SELECT value FROM jsonb_array_elements(capture.files) LOOP
    IF NOT EXISTS(SELECT 1 FROM public.eric_app_resources r WHERE r.workspace_id=p_workspace AND r.id=file->>'resource_id' AND r.complete
       AND r.sha256=file->>'sha256' AND r.bytes=(file->>'bytes')::bigint) THEN RAISE EXCEPTION 'eric_resource_incomplete'; END IF;
  END LOOP;
  IF capture.state='uploading' THEN UPDATE public.eric_app_captures SET state='received',received_at=now() WHERE id=p_id; END IF;
  RETURN QUERY SELECT * FROM public.eric_app_captures WHERE workspace_id=p_workspace AND id=p_id;
END $$;

CREATE FUNCTION public.eric_app_enqueue_command(p_workspace uuid,p_command jsonb)
RETURNS SETOF public.eric_app_commands LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE old public.eric_app_commands; source_revision bigint; key text:=p_command->>'resource_key';
BEGIN
  IF p_workspace IS NULL OR p_command IS NULL OR jsonb_typeof(p_command)<>'object'
    OR coalesce(p_command->>'request_hash','') !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'eric_invalid_command'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF NOT EXISTS(SELECT 1 FROM public.eric_app_devices d JOIN public.eric_app_workspaces w ON w.id=d.workspace_id
    WHERE d.workspace_id=p_workspace AND d.id::text=p_command->>'device_id' AND d.user_id=w.owner_id AND d.revoked_at IS NULL) THEN RAISE EXCEPTION 'eric_device_revoked'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app-command:'||(p_command->>'id'),0));
  SELECT * INTO old FROM public.eric_app_commands WHERE id=(p_command->>'id')::uuid;
  IF FOUND THEN
    IF old.workspace_id IS DISTINCT FROM p_workspace OR old.request_hash IS DISTINCT FROM p_command->>'request_hash' THEN RAISE EXCEPTION 'eric_idempotency_conflict'; END IF;
    RETURN NEXT old;RETURN;
  END IF;
  SELECT revision INTO source_revision FROM public.eric_app_records WHERE workspace_id=p_workspace AND kind=split_part(key,':',1)
    AND id=substr(key,length(split_part(key,':',1))+2) AND NOT deleted;
  IF source_revision IS NULL AND p_command->>'path' NOT IN ('/courses','/school/lectures','/learning/capture','/learning/external-reviews') THEN RAISE EXCEPTION 'eric_record_not_found'; END IF;
  IF coalesce(source_revision,0) IS DISTINCT FROM (p_command->>'expected_revision')::bigint THEN RAISE EXCEPTION 'eric_version_conflict'; END IF;
  IF EXISTS(SELECT 1 FROM public.eric_app_commands WHERE workspace_id=p_workspace AND resource_key=key AND state IN ('pending','processing')) THEN RAISE EXCEPTION 'eric_command_pending'; END IF;
  RETURN QUERY INSERT INTO public.eric_app_commands(id,workspace_id,device_id,resource_key,method,path,body,request_hash,expected_revision)
    VALUES((p_command->>'id')::uuid,p_workspace,(p_command->>'device_id')::uuid,key,p_command->>'method',p_command->>'path',p_command->'body',p_command->>'request_hash',(p_command->>'expected_revision')::bigint) RETURNING *;
END $$;

CREATE FUNCTION public.eric_app_claim_work(p_workspace uuid,p_kind text,p_limit integer DEFAULT 3)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result jsonb;
BEGIN
  IF p_workspace IS NULL OR p_limit IS NULL OR p_kind IS NULL OR p_limit NOT BETWEEN 1 AND 5 OR p_kind NOT IN ('captures','commands') THEN RAISE EXCEPTION 'eric_invalid_claim'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_kind='captures' THEN
    WITH selected AS (SELECT id FROM public.eric_app_captures WHERE workspace_id=p_workspace AND source='pwa' AND raw_item_id IS NULL
      AND (state='received' OR state='processing' AND lease_until<now()) AND next_attempt_at<=now() ORDER BY created_at,id LIMIT p_limit FOR UPDATE SKIP LOCKED),
    claimed AS (UPDATE public.eric_app_captures SET state='processing',lease_token=gen_random_uuid(),lease_until=now()+interval '5 minutes',attempts=attempts+1
      WHERE id IN (SELECT id FROM selected) RETURNING *) SELECT coalesce(jsonb_agg(to_jsonb(claimed)),'[]') INTO result FROM claimed;
  ELSE
    WITH selected AS (SELECT id FROM public.eric_app_commands WHERE workspace_id=p_workspace AND
      (state='pending' OR state='processing' AND lease_until<now()) AND next_attempt_at<=now() ORDER BY created_at,id LIMIT p_limit FOR UPDATE SKIP LOCKED),
    claimed AS (UPDATE public.eric_app_commands SET state='processing',lease_token=gen_random_uuid(),lease_until=now()+interval '5 minutes',attempts=attempts+1
      WHERE id IN (SELECT id FROM selected) RETURNING *) SELECT coalesce(jsonb_agg(to_jsonb(claimed)),'[]') INTO result FROM claimed;
  END IF;
  INSERT INTO public.eric_app_workers(workspace_id,last_seen_at) VALUES(p_workspace,now())
    ON CONFLICT(workspace_id) DO UPDATE SET last_seen_at=now();
  RETURN result;
END $$;

CREATE FUNCTION public.eric_app_register_resource(p_workspace uuid,p_resource jsonb)
RETURNS SETOF public.eric_app_resources LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE old public.eric_app_resources;
BEGIN
  IF p_workspace IS NULL OR p_resource IS NULL OR jsonb_typeof(p_resource)<>'object' THEN RAISE EXCEPTION 'eric_invalid_resource'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  SELECT * INTO old FROM public.eric_app_resources WHERE workspace_id=p_workspace AND id=p_resource->>'id';
  IF FOUND THEN
    IF old.sha256 IS DISTINCT FROM p_resource->>'sha256' OR old.bytes IS DISTINCT FROM (p_resource->>'bytes')::bigint OR old.part_count IS DISTINCT FROM (p_resource->>'part_count')::integer THEN
      RAISE EXCEPTION 'eric_resource_conflict';
    END IF;
    RETURN NEXT old;RETURN;
  END IF;
  RETURN QUERY INSERT INTO public.eric_app_resources(workspace_id,id,filename,mime,sha256,bytes,part_count)
    VALUES(p_workspace,p_resource->>'id',p_resource->>'filename',p_resource->>'mime',p_resource->>'sha256',(p_resource->>'bytes')::bigint,(p_resource->>'part_count')::integer) RETURNING *;
END $$;

CREATE FUNCTION public.eric_app_register_part(p_workspace uuid,p_id text,p_index integer,p_sha256 text,p_bytes integer)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE resource public.eric_app_resources; old public.eric_app_resource_parts;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  SELECT * INTO resource FROM public.eric_app_resources WHERE workspace_id=p_workspace AND id=p_id FOR UPDATE;
  IF NOT FOUND OR p_index<0 OR p_index>=resource.part_count THEN RAISE EXCEPTION 'eric_invalid_part'; END IF;
  IF NOT EXISTS(SELECT 1 FROM storage.objects WHERE bucket_id='eric-app-private' AND name=p_workspace::text||'/'||p_sha256
      AND (metadata->>'size')::bigint=p_bytes) THEN RAISE EXCEPTION 'eric_resource_missing'; END IF;
  SELECT * INTO old FROM public.eric_app_resource_parts WHERE workspace_id=p_workspace AND resource_id=p_id AND part_index=p_index;
  IF FOUND THEN
    IF old.sha256<>p_sha256 OR old.bytes<>p_bytes THEN RAISE EXCEPTION 'eric_resource_conflict'; END IF;
    RETURN;
  END IF;
  IF resource.complete THEN RAISE EXCEPTION 'eric_resource_immutable'; END IF;
  INSERT INTO public.eric_app_resource_parts(workspace_id,resource_id,part_index,sha256,bytes,object_path)
    VALUES(p_workspace,p_id,p_index,p_sha256,p_bytes,p_workspace::text||'/'||p_sha256);
END $$;

CREATE FUNCTION public.eric_app_finish_work(p_workspace uuid,p_kind text,p_id uuid,p_token uuid,p_state text,p_result jsonb,p_records jsonb DEFAULT '[]',p_epoch uuid DEFAULT NULL,p_generation bigint DEFAULT 0)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE capture public.eric_app_captures; command public.eric_app_commands; result jsonb;
BEGIN
  IF p_workspace IS NULL OR p_kind IS NULL OR p_token IS NULL OR p_state IS NULL OR p_kind NOT IN ('captures','commands') THEN RAISE EXCEPTION 'eric_invalid_claim'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_kind='commands' THEN
    SELECT * INTO command FROM public.eric_app_commands WHERE workspace_id=p_workspace AND id=p_id FOR UPDATE;
    IF NOT FOUND OR command.state<>'processing' OR command.lease_token IS DISTINCT FROM p_token OR command.lease_until<=now() THEN RAISE EXCEPTION 'eric_stale_lease'; END IF;
    IF p_state NOT IN ('completed','failed','needs_confirmation','pending') THEN RAISE EXCEPTION 'eric_invalid_state'; END IF;
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
    PERFORM public.eric_app_publish_records(p_workspace,p_records,p_epoch,p_generation);
    UPDATE public.eric_app_captures SET state=p_state,raw_item_id=coalesce(p_result->>'raw_item_id',raw_item_id),
      local_receipt_id=coalesce(p_result->>'local_receipt_id',local_receipt_id),
      error=CASE WHEN p_state IN ('failed','received') THEN p_result ELSE NULL END,lease_token=NULL,lease_until=NULL,
      next_attempt_at=now()+make_interval(secs=>least(300,power(2,least(attempts,8))::integer)) WHERE id=p_id RETURNING to_jsonb(eric_app_captures.*) INTO result;
  END IF;
  RETURN result;
END $$;

CREATE FUNCTION public.eric_app_begin_publish(p_workspace uuid,p_epoch uuid)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
  IF p_workspace IS NULL OR p_epoch IS NULL THEN RAISE EXCEPTION 'eric_invalid_publisher'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  INSERT INTO public.eric_app_workers(workspace_id,publisher_epoch,source_generation) VALUES(p_workspace,p_epoch,0)
    ON CONFLICT(workspace_id) DO UPDATE SET publisher_epoch=p_epoch,source_generation=0,last_seen_at=now();
END $$;

CREATE FUNCTION public.eric_app_set_progress(p_workspace uuid,p_device uuid,p_kind text,p_id text,p_expected bigint,p_request uuid,p_hash text,p_value jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE receipt public.eric_app_receipts; old public.eric_app_records; result jsonb; target_kind text;
BEGIN
  IF p_workspace IS NULL OR p_device IS NULL OR p_request IS NULL OR p_expected IS NULL OR p_hash IS NULL OR p_hash !~ '^[a-f0-9]{64}$'
    OR p_kind IS NULL OR p_kind NOT IN ('review_progress','inbox_progress') OR p_value IS NULL OR jsonb_typeof(p_value)<>'object' THEN RAISE EXCEPTION 'eric_invalid_progress'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF NOT EXISTS(SELECT 1 FROM public.eric_app_devices d JOIN public.eric_app_workspaces w ON w.id=d.workspace_id
    WHERE d.workspace_id=p_workspace AND d.id=p_device AND d.user_id=w.owner_id AND d.revoked_at IS NULL) THEN RAISE EXCEPTION 'eric_device_revoked'; END IF;
  SELECT * INTO receipt FROM public.eric_app_receipts WHERE workspace_id=p_workspace AND request_id=p_request;
  IF FOUND THEN
    IF receipt.request_hash IS DISTINCT FROM p_hash THEN RAISE EXCEPTION 'eric_idempotency_conflict'; END IF;
    RETURN receipt.result;
  END IF;
  target_kind:=CASE WHEN p_kind='review_progress' THEN 'review' ELSE 'inbox' END;
  IF NOT EXISTS(SELECT 1 FROM public.eric_app_records WHERE workspace_id=p_workspace AND kind=target_kind AND id=p_id AND NOT deleted) THEN RAISE EXCEPTION 'eric_record_not_found'; END IF;
  IF (SELECT count(*) FROM jsonb_object_keys(p_value))<>1 OR p_expected<0 THEN RAISE EXCEPTION 'eric_invalid_progress'; END IF;
  IF p_kind='review_progress' AND ((p_value ? 'completed') IS FALSE OR jsonb_typeof(p_value->'completed')<>'boolean'
      OR NOT EXISTS(SELECT 1 FROM public.eric_app_records WHERE workspace_id=p_workspace AND kind='review' AND id=p_id AND payload->'content' IS NOT NULL AND jsonb_typeof(payload->'content')<>'null')) THEN RAISE EXCEPTION 'eric_review_unavailable'; END IF;
  IF p_kind='inbox_progress' AND ((p_value ? 'archived') IS FALSE OR jsonb_typeof(p_value->'archived')<>'boolean') THEN RAISE EXCEPTION 'eric_invalid_progress'; END IF;
  SELECT * INTO old FROM public.eric_app_records WHERE workspace_id=p_workspace AND kind=p_kind AND id=p_id;
  IF coalesce(old.revision,0)<>p_expected THEN RAISE EXCEPTION 'eric_version_conflict'; END IF;
  p_value:=p_value||jsonb_build_object('changed_at',now());
  INSERT INTO public.eric_app_records(workspace_id,kind,id,payload,source_hash)
    VALUES(p_workspace,p_kind,p_id,p_value,encode(sha256(convert_to(p_value::text,'UTF8')),'hex'))
    ON CONFLICT(workspace_id,kind,id) DO UPDATE SET payload=excluded.payload,source_hash=excluded.source_hash
    RETURNING to_jsonb(eric_app_records.*) INTO result;
  INSERT INTO public.eric_app_receipts(workspace_id,request_id,request_hash,result) VALUES(p_workspace,p_request,p_hash,result);
  RETURN result;
END $$;

CREATE FUNCTION public.eric_app_renew_work(p_workspace uuid,p_kind text,p_id uuid,p_token uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE affected integer;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_kind='commands' THEN
    UPDATE public.eric_app_commands SET lease_until=now()+interval '5 minutes' WHERE workspace_id=p_workspace AND id=p_id
      AND state='processing' AND lease_token=p_token AND lease_until>now();
  ELSIF p_kind='captures' THEN
    UPDATE public.eric_app_captures SET lease_until=now()+interval '5 minutes' WHERE workspace_id=p_workspace AND id=p_id
      AND state='processing' AND lease_token=p_token AND lease_until>now();
  ELSE RAISE EXCEPTION 'eric_invalid_claim'; END IF;
  GET DIAGNOSTICS affected=ROW_COUNT;
  RETURN affected=1;
END $$;

CREATE FUNCTION public.eric_app_heartbeat(p_workspace uuid,p_epoch uuid,p_phase text,p_published boolean DEFAULT false)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
  IF p_phase IS NULL OR p_phase NOT IN ('connected','preparing','processing') OR p_epoch IS NULL THEN RAISE EXCEPTION 'eric_invalid_publisher'; END IF;
  UPDATE public.eric_app_workers SET last_seen_at=now(),phase=p_phase,published_at=CASE WHEN p_published THEN now() ELSE published_at END
    WHERE workspace_id=p_workspace AND publisher_epoch=p_epoch;
  IF NOT FOUND THEN RAISE EXCEPTION 'eric_stale_projection'; END IF;
END $$;

CREATE FUNCTION public.eric_app_link_receipts(p_workspace uuid,p_items jsonb)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE item jsonb; total integer:=0; affected integer;
BEGIN
  IF p_workspace IS NULL OR p_items IS NULL OR jsonb_typeof(p_items)<>'array' OR jsonb_array_length(p_items)>100 THEN RAISE EXCEPTION 'eric_invalid_receipts'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  FOR item IN SELECT value FROM jsonb_array_elements(p_items) LOOP
    IF nullif(item->>'raw_item_id','') IS NULL OR nullif(item->>'local_receipt_id','') IS NULL THEN RAISE EXCEPTION 'eric_invalid_receipts'; END IF;
    IF EXISTS(SELECT 1 FROM public.eric_app_captures WHERE workspace_id=p_workspace AND id=(item->>'id')::uuid
      AND ((raw_item_id IS NOT NULL AND raw_item_id<>item->>'raw_item_id') OR (local_receipt_id IS NOT NULL AND local_receipt_id<>item->>'local_receipt_id'))) THEN RAISE EXCEPTION 'eric_idempotency_conflict'; END IF;
    UPDATE public.eric_app_captures SET raw_item_id=item->>'raw_item_id',local_receipt_id=item->>'local_receipt_id',state='processing'
      WHERE workspace_id=p_workspace AND id=(item->>'id')::uuid AND source='shortcut' AND raw_item_id IS NULL AND state='received';
    GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
  END LOOP;
  RETURN total;
END $$;

CREATE FUNCTION public.eric_app_device_session(p_workspace uuid,p_user uuid,p_session uuid,p_label text,p_platform text)
RETURNS SETOF public.eric_app_devices LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE device public.eric_app_devices;
BEGIN
  IF p_workspace IS NULL OR p_user IS NULL OR p_session IS NULL OR p_label IS NULL OR p_platform IS NULL THEN RAISE EXCEPTION 'eric_invalid_device'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF NOT EXISTS(SELECT 1 FROM public.eric_app_workspaces WHERE id=p_workspace AND owner_id=p_user) THEN RAISE EXCEPTION 'eric_invalid_device'; END IF;
  SELECT * INTO device FROM public.eric_app_devices WHERE workspace_id=p_workspace AND session_id=p_session;
  IF FOUND THEN
    IF device.revoked_at IS NOT NULL THEN RAISE EXCEPTION 'eric_device_revoked'; END IF;
    RETURN QUERY UPDATE public.eric_app_devices SET last_seen_at=now() WHERE id=device.id RETURNING *; RETURN;
  END IF;
  IF (SELECT count(*) FROM public.eric_app_devices WHERE workspace_id=p_workspace AND revoked_at IS NULL)>=40 THEN RAISE EXCEPTION 'eric_invalid_device_limit'; END IF;
  RETURN QUERY INSERT INTO public.eric_app_devices(workspace_id,user_id,session_id,label,platform)
    VALUES(p_workspace,p_user,p_session,p_label,p_platform) RETURNING *;
END $$;

CREATE FUNCTION public.eric_app_revoke_device(p_workspace uuid,p_device uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE affected integer;
BEGIN
  IF p_workspace IS NULL OR p_device IS NULL THEN RAISE EXCEPTION 'eric_invalid_device'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  UPDATE public.eric_app_devices SET revoked_at=coalesce(revoked_at,now()) WHERE workspace_id=p_workspace AND id=p_device;
  GET DIAGNOSTICS affected=ROW_COUNT;
  UPDATE public.eric_app_commands SET state='failed',error='{"code":"device_revoked","message":"发起操作的设备已撤销，未执行的修改已取消。"}'::jsonb
    WHERE workspace_id=p_workspace AND device_id=p_device AND state='pending';
  RETURN affected=1;
END $$;

CREATE FUNCTION public.eric_app_update_capture_results(p_workspace uuid,p_updates jsonb,p_epoch uuid DEFAULT NULL,p_generation bigint DEFAULT 0)
RETURNS integer LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE item jsonb; total integer:=0; affected integer;
BEGIN
  IF p_workspace IS NULL OR p_updates IS NULL OR jsonb_typeof(p_updates)<>'array' OR jsonb_array_length(p_updates)>100 THEN RAISE EXCEPTION 'eric_invalid_capture_results'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_epoch IS DISTINCT FROM (SELECT publisher_epoch FROM public.eric_app_workers WHERE workspace_id=p_workspace)
    OR p_generation<coalesce((SELECT source_generation FROM public.eric_app_workers WHERE workspace_id=p_workspace),0) THEN RAISE EXCEPTION 'eric_stale_projection'; END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(p_updates) LOOP
    IF item->>'state' NOT IN ('processing','completed','needs_confirmation','failed') OR (item->>'local_version')::integer<0 THEN RAISE EXCEPTION 'eric_invalid_capture_results'; END IF;
    UPDATE public.eric_app_captures SET state=item->>'state',local_version=(item->>'local_version')::integer,
      course_id=item->>'course_id',lecture_id=item->>'lecture_id',learning_capture_id=item->>'learning_capture_id',error=item->'error'
      WHERE workspace_id=p_workspace AND raw_item_id=item->>'raw_item_id' AND local_version<=(item->>'local_version')::integer
      AND (state,local_version,course_id,lecture_id,learning_capture_id,error) IS DISTINCT FROM
          (item->>'state',(item->>'local_version')::integer,item->>'course_id',item->>'lecture_id',item->>'learning_capture_id',item->'error');
    GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
  END LOOP;
  RETURN total;
END $$;

-- Default-deny on every new table. Only the Edge API's service principal can access them.
DO $$ DECLARE name text; BEGIN
  FOREACH name IN ARRAY ARRAY['eric_app_workspaces','eric_app_devices','eric_app_records','eric_app_resources','eric_app_resource_parts','eric_app_captures','eric_app_commands','eric_app_changes','eric_app_workers','eric_app_receipts'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',name);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,anon,authenticated',name);
    EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON public.%I TO service_role',name);
  END LOOP;
END $$;
REVOKE ALL ON SEQUENCE public.eric_app_revision_seq FROM PUBLIC,anon,authenticated;
GRANT USAGE,SELECT ON SEQUENCE public.eric_app_revision_seq TO service_role;
REVOKE DELETE ON public.eric_app_records,public.eric_app_captures,public.eric_app_resources,
  public.eric_app_resource_parts,public.eric_app_commands,public.eric_app_receipts,public.eric_app_changes FROM service_role;
DO $$ DECLARE routine record; BEGIN
  FOR routine IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND left(p.proname,9)='eric_app_' LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon,authenticated',routine.signature);
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role',routine.signature);
  END LOOP;
END $$;
INSERT INTO storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
  VALUES('eric-app-private','eric-app-private',false,16000000,ARRAY['application/octet-stream'])
  ON CONFLICT(id) DO NOTHING;
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM storage.buckets WHERE id='eric-app-private' AND name='eric-app-private' AND NOT public AND file_size_limit=16000000 AND allowed_mime_types=ARRAY['application/octet-stream']) THEN
    RAISE EXCEPTION 'eric_unexpected_storage_bucket';
  END IF;
END $$;
