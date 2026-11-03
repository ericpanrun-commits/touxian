-- Network transport segmentation can change only before any part is accepted.
-- Original identity, bytes, content hash, device scope and completed resources stay immutable.
CREATE OR REPLACE FUNCTION public.eric_app_register_resource(p_workspace uuid,p_resource jsonb)
RETURNS SETOF public.eric_app_resources LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE old public.eric_app_resources; count_parts integer:=(p_resource->>'part_count')::integer; size bigint:=(p_resource->>'bytes')::bigint;
BEGIN
  IF p_workspace IS NULL OR p_resource IS NULL OR jsonb_typeof(p_resource)<>'object' OR count_parts IS NULL OR size IS NULL
    OR count_parts NOT IN (ceil(size::numeric/4000000)::integer,ceil(size::numeric/16000000)::integer) THEN RAISE EXCEPTION 'eric_invalid_resource'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  SELECT * INTO old FROM public.eric_app_resources WHERE workspace_id=p_workspace AND id=p_resource->>'id' FOR UPDATE;
  IF FOUND THEN
    IF old.sha256 IS DISTINCT FROM p_resource->>'sha256' OR old.bytes IS DISTINCT FROM size THEN RAISE EXCEPTION 'eric_resource_conflict'; END IF;
    IF old.part_count IS DISTINCT FROM count_parts THEN
      IF old.complete OR EXISTS(SELECT 1 FROM public.eric_app_resource_parts WHERE workspace_id=p_workspace AND resource_id=old.id) THEN RAISE EXCEPTION 'eric_resource_conflict'; END IF;
      UPDATE public.eric_app_resources SET part_count=count_parts WHERE workspace_id=p_workspace AND id=old.id RETURNING * INTO old;
    END IF;
    RETURN NEXT old;RETURN;
  END IF;
  RETURN QUERY INSERT INTO public.eric_app_resources(workspace_id,id,filename,mime,sha256,bytes,part_count)
    VALUES(p_workspace,p_resource->>'id',p_resource->>'filename',p_resource->>'mime',p_resource->>'sha256',size,count_parts) RETURNING *;
END $$;

CREATE OR REPLACE FUNCTION public.eric_app_register_part(p_workspace uuid,p_id text,p_index integer,p_sha256 text,p_bytes integer)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE resource public.eric_app_resources; old public.eric_app_resource_parts; chunk_bytes integer; expected_bytes integer;
BEGIN
  IF p_workspace IS NULL OR p_id IS NULL OR p_index IS NULL OR p_bytes IS NULL OR p_sha256 IS NULL THEN RAISE EXCEPTION 'eric_invalid_part'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-app:'||p_workspace::text,0));
  SELECT * INTO resource FROM public.eric_app_resources WHERE workspace_id=p_workspace AND id=p_id FOR UPDATE;
  IF NOT FOUND OR p_index<0 OR p_index>=resource.part_count THEN RAISE EXCEPTION 'eric_invalid_part'; END IF;
  chunk_bytes:=CASE WHEN resource.part_count=ceil(resource.bytes::numeric/4000000)::integer THEN 4000000 ELSE 16000000 END;
  expected_bytes:=CASE WHEN p_index=resource.part_count-1 THEN resource.bytes-(resource.part_count-1)::bigint*chunk_bytes ELSE chunk_bytes END;
  IF p_bytes<>expected_bytes THEN RAISE EXCEPTION 'eric_invalid_part'; END IF;
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
