-- Full processing is independent of the existing notes-only Shortcuts capabilities.
CREATE TABLE public.eric_app_processors (
  id uuid PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES public.eric_app_workspaces(id),
  label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
  key_hash text NOT NULL UNIQUE CHECK (key_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz,
  revoked_at timestamptz
);
CREATE UNIQUE INDEX eric_app_one_processor ON public.eric_app_processors(workspace_id) WHERE revoked_at IS NULL;
ALTER TABLE public.eric_app_processors ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.eric_app_processors FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.eric_app_processors TO service_role;
