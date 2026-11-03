-- A completion is cloud-owned. The existing domain task keeps its original ID.
CREATE FUNCTION public.eric_app_task_progress(p_workspace uuid,p_device uuid,p_id text,p_expected bigint,p_source bigint,p_request uuid,p_hash text,p_completed boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE receipt public.eric_app_receipts; old public.eric_app_records; task public.eric_app_records; parent public.eric_app_records; value jsonb; result jsonb;
BEGIN
  IF p_workspace IS NULL OR p_device IS NULL OR p_request IS NULL OR p_id IS NULL OR p_completed IS NULL OR p_expected IS NULL OR p_source IS NULL
    OR p_expected<0 OR p_source<1 OR coalesce(p_hash,'') !~ '^[a-f0-9]{64}$' THEN RAISE EXCEPTION 'eric_invalid_progress'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF NOT EXISTS(SELECT 1 FROM public.eric_app_devices d JOIN public.eric_app_workspaces w ON w.id=d.workspace_id
    WHERE d.workspace_id=p_workspace AND d.id=p_device AND d.user_id=w.owner_id AND d.revoked_at IS NULL) THEN RAISE EXCEPTION 'eric_device_revoked'; END IF;
  SELECT * INTO receipt FROM public.eric_app_receipts WHERE workspace_id=p_workspace AND request_id=p_request;
  IF FOUND THEN
    IF receipt.request_hash IS DISTINCT FROM p_hash THEN RAISE EXCEPTION 'eric_idempotency_conflict'; END IF;
    RETURN receipt.result;
  END IF;
  SELECT * INTO task FROM public.eric_app_records WHERE workspace_id=p_workspace AND kind='task' AND id=p_id AND NOT deleted;
  IF NOT FOUND THEN RAISE EXCEPTION 'eric_record_not_found'; END IF;
  IF task.revision<>p_source THEN RAISE EXCEPTION 'eric_version_conflict'; END IF;
  SELECT * INTO parent FROM public.eric_app_records WHERE workspace_id=p_workspace AND kind='event' AND id=task.payload->>'event_id' AND NOT deleted;
  IF NOT FOUND OR parent.payload->>'status' IN ('done','cancelled') THEN RAISE EXCEPTION 'eric_version_conflict'; END IF;
  SELECT * INTO old FROM public.eric_app_records WHERE workspace_id=p_workspace AND kind='task_progress' AND id=p_id;
  IF coalesce(old.revision,0)<>p_expected THEN RAISE EXCEPTION 'eric_version_conflict'; END IF;
  value:=jsonb_build_object('id',p_id,'event_id',task.payload->>'event_id','completed',p_completed,'changed_at',now(),
    'source_revision',p_source,'source_title',task.payload->>'title','source_status',task.payload->>'status','application_state','pending');
  INSERT INTO public.eric_app_records(workspace_id,kind,id,payload,source_hash)
    VALUES(p_workspace,'task_progress',p_id,value,encode(sha256(convert_to(value::text,'UTF8')),'hex'))
    ON CONFLICT(workspace_id,kind,id) DO UPDATE SET payload=excluded.payload,source_hash=excluded.source_hash
    RETURNING to_jsonb(eric_app_records.*) INTO result;
  INSERT INTO public.eric_app_receipts(workspace_id,request_id,request_hash,result) VALUES(p_workspace,p_request,p_hash,result);
  RETURN result;
END $$;

CREATE FUNCTION public.eric_app_ack_task(p_workspace uuid,p_id text,p_revision bigint,p_state text,p_message text,p_epoch uuid,p_generation bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE affected integer;
BEGIN
  IF p_state IS NULL OR p_state NOT IN ('applied','needs_confirmation') OR length(coalesce(p_message,''))>500 THEN RAISE EXCEPTION 'eric_invalid_progress'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  IF p_epoch IS DISTINCT FROM (SELECT publisher_epoch FROM public.eric_app_workers WHERE workspace_id=p_workspace)
    OR p_generation IS NULL OR p_generation<(SELECT source_generation FROM public.eric_app_workers WHERE workspace_id=p_workspace) THEN RAISE EXCEPTION 'eric_stale_projection'; END IF;
  UPDATE public.eric_app_records SET payload=payload||jsonb_build_object('application_state',p_state,'message',p_message),
    source_hash=encode(sha256(convert_to((payload||jsonb_build_object('application_state',p_state,'message',p_message))::text,'UTF8')),'hex')
    WHERE workspace_id=p_workspace AND kind='task_progress' AND id=p_id AND revision=p_revision AND payload->>'application_state'='pending';
  GET DIAGNOSTICS affected=ROW_COUNT;
  RETURN affected=1;
END $$;
REVOKE ALL ON FUNCTION public.eric_app_task_progress(uuid,uuid,text,bigint,bigint,uuid,text,boolean),public.eric_app_ack_task(uuid,text,bigint,text,text,uuid,bigint) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.eric_app_task_progress(uuid,uuid,text,bigint,bigint,uuid,text,boolean),public.eric_app_ack_task(uuid,text,bigint,text,text,uuid,bigint) TO service_role;
