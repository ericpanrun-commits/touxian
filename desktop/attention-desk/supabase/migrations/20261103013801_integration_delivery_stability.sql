-- Shared Integration is maintained separately from the Eric App schema.
-- Preserve standalone Eric installs; replace only when the shared queue exists.
-- Reuse the existing max_attempts, exponential backoff, error and lease fields.
DO $migration$
BEGIN
  IF to_regclass('integration.deliveries') IS NOT NULL THEN
    EXECUTE $definition$
CREATE OR REPLACE FUNCTION integration.fail_delivery(p_delivery_id uuid, p_claim_token uuid, p_http_status integer DEFAULT NULL::integer, p_error text DEFAULT NULL::text)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_updated integer;
begin
  update integration.deliveries d
  set status = case
        when d.attempt_count >= d.max_attempts or (p_http_status between 300 and 499 and p_http_status not in (408,429)) then 'failed'
        else 'retrying'
      end,
      next_attempt_at = case
        when d.attempt_count >= d.max_attempts or (p_http_status between 300 and 499 and p_http_status not in (408,429)) then null
        else pg_catalog.now() + make_interval(
          secs => least(
            900,
            (30 * power(2, least(d.attempt_count, 8) - 1))::integer
          )
        )
      end,
      http_status = p_http_status,
      last_error = left(
        coalesce(nullif(btrim(p_error), ''), 'delivery failed'),
        500
      ),
      lease_until = null,
      claim_token = null
  where d.id = p_delivery_id
    and d.claim_token = p_claim_token
    and d.status = 'processing';

  get diagnostics v_updated = row_count;
  return v_updated = 1;
end;
$function$;
$definition$;
    REVOKE ALL ON FUNCTION integration.fail_delivery(uuid,uuid,integer,text) FROM PUBLIC,anon,authenticated;
    GRANT EXECUTE ON FUNCTION integration.fail_delivery(uuid,uuid,integer,text) TO service_role;
  END IF;
END $migration$;

-- Source and outbox must commit together. A publish failure is a failed write,
-- so the existing IELTS client retains its local record for retry.
DO $migration$
BEGIN
  IF to_regprocedure('integration.tg_ielts_reading_session_completed()') IS NOT NULL THEN
    EXECUTE $definition$
CREATE OR REPLACE FUNCTION integration.tg_ielts_reading_session_completed()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_summary text;
begin
    if new.deleted_at is not null then
      return new;
    end if;

    v_summary := left(concat_ws(
      ' ',
      'correct=' || coalesce(new.correct::text, ''),
      'total=' || coalesce(new.total::text, ''),
      'duration_sec=' || coalesce(new.duration_sec::text, ''),
      'duration_min=' || coalesce(new.duration_min::text, ''),
      'passage=' || coalesce(new.passage::text, ''),
      'test=' || coalesce(new.test::text, '')
    ), 500);

    perform integration.publish_event(
      'ielts.reading.session_completed',
      'ielts-workspace',
      'ielts_reading_sessions',
      new.id::text,
      coalesce(new.completed_at, new.created_at, pg_catalog.now()),
      v_summary,
      '1',
      'ielts_reading_sessions:' || new.user_id::text || ':' || new.record_key || ':' || coalesce(new.fingerprint, '')
    );

  return new;
end;
$function$;
$definition$;
  END IF;
  IF to_regprocedure('integration.tg_ielts_speaking_result_saved()') IS NOT NULL THEN
    EXECUTE $definition$
CREATE OR REPLACE FUNCTION integration.tg_ielts_speaking_result_saved()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_summary text;
begin
    if new.deleted_at is not null then
      return new;
    end if;

    -- Counts and scores only. raw_result is intentionally not read.
    v_summary := left(concat_ws(
      ' ',
      'overall_band=' || coalesce(new.overall_band::text, ''),
      'fluency_coherence=' || coalesce(new.fluency_coherence::text, ''),
      'lexical_resource=' || coalesce(new.lexical_resource::text, ''),
      'grammatical_range_accuracy=' || coalesce(new.grammatical_range_accuracy::text, ''),
      'pronunciation=' || coalesce(new.pronunciation::text, ''),
      'duration_min=' || coalesce(new.duration_min::text, ''),
      'topic_count=' || coalesce(cardinality(new.topics)::text, '0')
    ), 500);

    perform integration.publish_event(
      'ielts.speaking.result_saved',
      'ielts-workspace',
      'ielts_speaking_results',
      new.id::text,
      coalesce(new.imported_at, new.created_at, pg_catalog.now()),
      v_summary,
      '1',
      'ielts_speaking_results:' || new.user_id::text || ':' || new.record_key || ':' || coalesce(new.fingerprint, '')
    );

  return new;
end;
$function$;
$definition$;
  END IF;
END $migration$;
