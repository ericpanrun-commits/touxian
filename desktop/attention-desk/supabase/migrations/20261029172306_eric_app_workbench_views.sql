CREATE INDEX eric_app_command_resource_latest ON public.eric_app_commands(workspace_id,resource_key,created_at DESC,id DESC);

CREATE FUNCTION public.eric_app_pending_changes(p_workspace uuid)
RETURNS SETOF public.eric_app_commands LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
  SELECT latest.* FROM (SELECT DISTINCT ON (resource_key) * FROM public.eric_app_commands
    WHERE workspace_id=p_workspace ORDER BY resource_key,created_at DESC,id DESC) latest
    WHERE state IN ('pending','processing','needs_confirmation','failed') ORDER BY created_at ASC,id ASC LIMIT 100;
$$;

CREATE FUNCTION public.eric_app_inbox_page(p_workspace uuid,p_status text,p_scope text,p_offset integer,p_limit integer)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result jsonb;
BEGIN
  IF p_workspace IS NULL OR p_status IS NULL OR p_status NOT IN ('all','archived','needs_review','completed','processing') OR p_scope IS NULL OR p_scope NOT IN ('all','submitted')
    OR p_offset IS NULL OR p_offset<0 OR p_offset>100000 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'eric_invalid_page'; END IF;
  WITH matching AS (
    SELECT r.*,coalesce((p.payload->>'archived')::boolean,false) archived,coalesce(p.revision,0) archive_revision
    FROM public.eric_app_records r LEFT JOIN public.eric_app_records p ON p.workspace_id=r.workspace_id AND p.kind='inbox_progress' AND p.id=r.id AND NOT p.deleted
    WHERE r.workspace_id=p_workspace AND r.kind='inbox_item' AND NOT r.deleted
      AND coalesce((p.payload->>'archived')::boolean,false)=(p_status='archived')
      AND (p_scope='all' OR r.payload->>'managed'='true')
      AND (p_status IN ('all','archived') OR p_status='needs_review' AND r.payload->>'needs_manual'='true'
        OR p_status='completed' AND r.payload->>'processing_status'='completed'
        OR p_status='processing' AND r.payload->>'processing_status' IN ('received','queued','extracting','parsing','analyzing','processing'))
  ), page AS (
    SELECT to_jsonb(m)||jsonb_build_object('payload',m.payload||jsonb_build_object('archive',jsonb_build_object('archived',m.archived,'_cloud',jsonb_build_object('revision',m.archive_revision)))) value,
      m.payload->>'created_at' stamp,m.id FROM matching m ORDER BY m.payload->>'created_at' DESC,m.id DESC OFFSET p_offset LIMIT p_limit
  )
  SELECT jsonb_build_object('items',coalesce((SELECT jsonb_agg(value ORDER BY stamp DESC,id DESC) FROM page),'[]'::jsonb),
    'total',(SELECT count(*) FROM matching),'offset',p_offset,'limit',p_limit) INTO result;
  RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.eric_app_pending_changes(uuid),public.eric_app_inbox_page(uuid,text,text,integer,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.eric_app_pending_changes(uuid),public.eric_app_inbox_page(uuid,text,text,integer,integer) TO service_role;
