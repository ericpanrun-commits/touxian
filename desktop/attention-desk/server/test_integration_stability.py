"""Changed shared-queue failure transition, using its actual deployed column contract.

The shared foundation is outside this checkout. This fixture contains only the
table fields used by fail_delivery; it does not emulate the function under test.
"""
from pathlib import Path
from uuid import uuid4

import pytest

from server.test_cloud_sync_sql import cluster
import psycopg

MIGRATION=next((Path(__file__).resolve().parents[1]/'supabase/migrations').glob('*_integration_delivery_stability.sql'))


@pytest.fixture(scope='module')
def shared(cluster):
    with psycopg.connect(**cluster,autocommit=True) as c:
        c.execute('CREATE SCHEMA integration')
        c.execute('''CREATE TABLE integration.deliveries (
            id uuid PRIMARY KEY, status text NOT NULL, attempt_count integer NOT NULL,
            max_attempts integer NOT NULL, next_attempt_at timestamptz, lease_until timestamptz,
            claim_token uuid, last_error text, http_status integer)''')
        c.execute(MIGRATION.read_text(encoding='utf-8'))
    return cluster


@pytest.mark.parametrize('http_status',[None,408,429,500,502,503,504])
def test_temporary_delivery_errors_keep_bounded_backoff(shared,http_status):
    identity,token=uuid4(),uuid4()
    with psycopg.connect(**shared,autocommit=True) as c:
        c.execute("INSERT INTO integration.deliveries VALUES(%s,'processing',1,3,NULL,now()+interval '1 minute',%s,NULL,NULL)",(identity,token))
        assert c.execute('SELECT integration.fail_delivery(%s,%s,%s,%s)',(identity,token,http_status,'temporary_error')).fetchone()[0]
        row=c.execute('SELECT status,next_attempt_at>now(),claim_token,lease_until,last_error FROM integration.deliveries WHERE id=%s',(identity,)).fetchone()
        assert row==('retrying',True,None,None,'temporary_error')


@pytest.mark.parametrize('http_status',[301,400,401,403,404,409,422])
def test_permanent_delivery_errors_fail_without_rescheduling(shared,http_status):
    identity,token=uuid4(),uuid4()
    with psycopg.connect(**shared,autocommit=True) as c:
        c.execute("INSERT INTO integration.deliveries VALUES(%s,'processing',1,6,NULL,now()+interval '1 minute',%s,NULL,NULL)",(identity,token))
        assert c.execute('SELECT integration.fail_delivery(%s,%s,%s,%s)',(identity,token,http_status,'permanent_error')).fetchone()[0]
        assert c.execute('SELECT status,next_attempt_at FROM integration.deliveries WHERE id=%s',(identity,)).fetchone()==('failed',None)


def test_retry_exhaustion_is_failed_and_a_stale_or_duplicate_ack_is_ignored(shared):
    identity,token=uuid4(),uuid4()
    with psycopg.connect(**shared,autocommit=True) as c:
        c.execute("INSERT INTO integration.deliveries VALUES(%s,'processing',3,3,NULL,now()+interval '1 minute',%s,NULL,NULL)",(identity,token))
        assert not c.execute("SELECT integration.fail_delivery(%s,%s,503,'old owner')",(identity,uuid4())).fetchone()[0]
        assert c.execute("SELECT integration.fail_delivery(%s,%s,503,'provider_unavailable')",(identity,token)).fetchone()[0]
        assert not c.execute("SELECT integration.fail_delivery(%s,%s,503,'repeated ack')",(identity,token)).fetchone()[0]
        assert c.execute('SELECT status,next_attempt_at,last_error FROM integration.deliveries WHERE id=%s',(identity,)).fetchone()==('failed',None,'provider_unavailable')


def test_shared_function_remains_inaccessible_to_public_clients(shared):
    with psycopg.connect(**shared,autocommit=True) as c:
        for role in ['anon','authenticated']:
            assert not c.execute("SELECT has_function_privilege(%s,'integration.fail_delivery(uuid,uuid,integer,text)','EXECUTE')",(role,)).fetchone()[0]
        assert c.execute("SELECT has_function_privilege('service_role','integration.fail_delivery(uuid,uuid,integer,text)','EXECUTE')").fetchone()[0]


@pytest.fixture(scope='module')
def ielts(shared):
    with psycopg.connect(**shared,autocommit=True) as c:
        # Fault injection replaces only the dependency. The real production
        # trigger bodies below are installed by the migration under test.
        c.execute('''CREATE FUNCTION integration.publish_event(text,text,text,text,timestamptz,text,text,text)
            RETURNS uuid LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic outbox write failure'; END $$''')
        for table,trigger in [('ielts_reading_sessions','tg_ielts_reading_session_completed'),('ielts_speaking_results','tg_ielts_speaking_result_saved')]:
            c.execute(psycopg.sql.SQL('''CREATE TABLE public.{} (
                id uuid PRIMARY KEY,user_id uuid,record_key text,fingerprint text,deleted_at timestamptz,
                created_at timestamptz DEFAULT now(),completed_at timestamptz,imported_at timestamptz,
                correct integer,total integer,duration_sec integer,duration_min integer,passage integer,test integer,
                overall_band numeric,fluency_coherence numeric,lexical_resource numeric,grammatical_range_accuracy numeric,
                pronunciation numeric,topics text[])''').format(psycopg.sql.Identifier(table)))
            # The conditional shared migration replaces pre-existing functions.
            c.execute(psycopg.sql.SQL('CREATE FUNCTION integration.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$').format(psycopg.sql.Identifier(trigger)))
            c.execute(psycopg.sql.SQL('CREATE TRIGGER outbox AFTER INSERT ON public.{} FOR EACH ROW EXECUTE FUNCTION integration.{}()').format(psycopg.sql.Identifier(table),psycopg.sql.Identifier(trigger)))
        c.execute(MIGRATION.read_text(encoding='utf-8'))
    return shared


@pytest.mark.parametrize('table',['ielts_reading_sessions','ielts_speaking_results'])
def test_ielts_source_write_rolls_back_when_outbox_write_fails(ielts,table):
    with psycopg.connect(**ielts,autocommit=True) as c:
        identity=uuid4()
        with pytest.raises(psycopg.errors.RaiseException,match='synthetic outbox write failure'):
            c.execute(psycopg.sql.SQL('INSERT INTO public.{}(id,user_id,record_key,fingerprint) VALUES(%s,%s,%s,%s)').format(psycopg.sql.Identifier(table)),(identity,uuid4(),'synthetic-key','synthetic-fingerprint'))
        assert c.execute(psycopg.sql.SQL('SELECT count(*) FROM public.{} WHERE id=%s').format(psycopg.sql.Identifier(table)),(identity,)).fetchone()[0]==0
