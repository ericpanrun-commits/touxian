-- Transport metadata only. Local Eric Inbox remains the source of learning facts.
DO $$
BEGIN
  IF to_regclass('public.eric_notes_devices') IS NOT NULL AND
     obj_description(to_regclass('public.eric_notes_devices'),'pg_class') IS DISTINCT FROM 'eric-os-anywhere-notes-v1' THEN
    RAISE EXCEPTION 'Unowned notes table; migration stopped';
  END IF;
  IF to_regclass('public.eric_notes_submissions') IS NOT NULL AND
     obj_description(to_regclass('public.eric_notes_submissions'),'pg_class') IS DISTINCT FROM 'eric-os-anywhere-notes-v1' THEN
    RAISE EXCEPTION 'Unowned notes table; migration stopped';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.eric_notes_devices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  installation_id uuid NOT NULL,
  role text NOT NULL CHECK (role IN ('upload','sync')),
  label text NOT NULL DEFAULT '' CHECK (length(label)<=80),
  key_hash text NOT NULL UNIQUE CHECK (key_hash ~ '^[a-f0-9]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  last_seen_at timestamptz,
  rate_window timestamptz NOT NULL DEFAULT now(),
  rate_count integer NOT NULL DEFAULT 0
);
COMMENT ON TABLE public.eric_notes_devices IS 'eric-os-anywhere-notes-v1';
CREATE TABLE IF NOT EXISTS public.eric_notes_submissions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  installation_id uuid NOT NULL,
  device_id uuid NOT NULL REFERENCES public.eric_notes_devices(id),
  sha256 text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  bytes bigint NOT NULL CHECK (bytes BETWEEN 6 AND 50000000),
  filename text NOT NULL CHECK (length(filename)<=180),
  object_path text NOT NULL,
  state text NOT NULL DEFAULT 'uploading' CHECK (state IN ('uploading','pending','acked')),
  shared_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  received_at timestamptz,
  acked_at timestamptz,
  local_receipt_id uuid,
  payload_deleted_at timestamptz,
  UNIQUE (installation_id,sha256)
);
COMMENT ON TABLE public.eric_notes_submissions IS 'eric-os-anywhere-notes-v1';
CREATE INDEX IF NOT EXISTS eric_notes_pending ON public.eric_notes_submissions(installation_id,state,created_at);
ALTER TABLE public.eric_notes_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.eric_notes_submissions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.eric_notes_devices,public.eric_notes_submissions FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.eric_notes_devices,public.eric_notes_submissions TO service_role;

CREATE OR REPLACE FUNCTION public.eric_notes_authorize(p_hash text,p_role text)
RETURNS TABLE(id uuid,installation_id uuid)
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE d public.eric_notes_devices;
BEGIN
  UPDATE public.eric_notes_devices AS device
  SET rate_count=CASE WHEN device.rate_window < now()-interval '1 minute' THEN 1 ELSE device.rate_count+1 END,
      rate_window=CASE WHEN device.rate_window < now()-interval '1 minute' THEN now() ELSE device.rate_window END,
      last_seen_at=now()
  WHERE device.key_hash=p_hash AND device.role=p_role AND device.revoked_at IS NULL
  RETURNING device.* INTO d;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='28000',MESSAGE='notes_unauthorized'; END IF;
  IF d.rate_count>30 THEN RAISE EXCEPTION 'notes_rate_limited'; END IF;
  RETURN QUERY SELECT d.id,d.installation_id;
END $$;

CREATE OR REPLACE FUNCTION public.eric_notes_prepare(
  p_installation uuid,p_device uuid,p_sha256 text,p_bytes bigint,p_filename text,p_shared_at timestamptz
) RETURNS SETOF public.eric_notes_submissions
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE old public.eric_notes_submissions;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('eric-notes:'||p_installation::text,0));
  IF NOT EXISTS(SELECT 1 FROM public.eric_notes_devices WHERE id=p_device AND installation_id=p_installation AND role='upload' AND revoked_at IS NULL) THEN
    RAISE EXCEPTION USING ERRCODE='28000',MESSAGE='notes_unauthorized';
  END IF;
  SELECT * INTO old FROM public.eric_notes_submissions WHERE installation_id=p_installation AND sha256=p_sha256;
  IF FOUND THEN
    IF old.bytes<>p_bytes THEN RAISE EXCEPTION 'notes_hash_conflict'; END IF;
    RETURN NEXT old; RETURN;
  END IF;
  IF (SELECT count(*) FROM public.eric_notes_submissions WHERE installation_id=p_installation AND created_at>now()-interval '1 day')>=100 THEN
    RAISE EXCEPTION 'notes_daily_limit';
  END IF;
  IF (SELECT coalesce(sum(bytes),0) FROM public.eric_notes_submissions WHERE installation_id=p_installation AND payload_deleted_at IS NULL)+p_bytes>512000000 THEN
    RAISE EXCEPTION 'notes_pending_limit';
  END IF;
  RETURN QUERY INSERT INTO public.eric_notes_submissions(installation_id,device_id,sha256,bytes,filename,object_path,shared_at)
    VALUES(p_installation,p_device,p_sha256,p_bytes,p_filename,p_installation::text||'/'||p_sha256||'.pdf',p_shared_at) RETURNING *;
END $$;
REVOKE ALL ON FUNCTION public.eric_notes_authorize(text,text) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.eric_notes_prepare(uuid,uuid,text,bigint,text,timestamptz) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.eric_notes_authorize(text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.eric_notes_prepare(uuid,uuid,text,bigint,text,timestamptz) TO service_role;

DO $$
BEGIN
  IF EXISTS(SELECT 1 FROM storage.buckets WHERE id='eric-notes-transit' AND (public OR name<>'eric-notes-transit')) THEN
    RAISE EXCEPTION 'Unexpected existing notes bucket; migration stopped';
  END IF;
END $$;
INSERT INTO storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
VALUES('eric-notes-transit','eric-notes-transit',false,50000000,ARRAY['application/pdf']) ON CONFLICT(id) DO NOTHING;

