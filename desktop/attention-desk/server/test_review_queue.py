"""The daily reading list is derived; history and learner state stay intact."""
import json

from server.personal_store import PersonalStore, now
from server.school.artifacts import ReviewArtifacts


def seed(tmp_path, classes):
    store = PersonalStore(tmp_path/'personal.sqlite3')
    store.initialize()
    with store.connection(write=True) as c:
        for identity, date, versions, adopted in classes:
            c.execute('''INSERT INTO lecture(id,course_id,lecture_date,title,idempotency_key,
                request_hash,created_at,updated_at) VALUES (?,'course-math-ii',?,?,?,?,?,?)''',
                (identity,date,'Synthetic '+identity,identity,identity,now(),now()))
            for number, content, status in versions:
                c.execute('''INSERT INTO review_artifact(id,lecture_id,version,content_json,provider,model,
                    prompt_version,input_versions_json,source_evidence_json,input_hash,status,created_at)
                    VALUES (?,?,?,?, 'fixture','fixture','fixture','{}','[]',?,?,?)''',
                    (identity+'-v'+str(number),identity,number,json.dumps(content) if content is not None else None,
                     identity+'-'+str(number),status,now()))
            if adopted:
                c.execute('UPDATE lecture SET accepted_artifact_id=? WHERE id=?',(identity+'-v'+str(adopted),identity))
    return store


def snapshot(store):
    with store.connection() as c:
        return {name:[tuple(row) for row in c.execute('SELECT * FROM '+name+' ORDER BY id')]
                for name in ('lecture','review_artifact','review_feedback','task')}


def test_daily_reading_selects_newest_content_without_adopting_or_overwriting_history(tmp_path):
    store = seed(tmp_path,[
        ('oct28','2026-10-28',[(1,{'text':'old'},'accepted'),(2,None,'failed'),
            (3,None,'needs_review'),(4,{'text':'source checked'},'needs_review')],1),
        ('oct26','2026-10-26',[(i,{'text':str(i)} if i in (4,7,8,9) else None,
            'failed' if i<4 else 'needs_review') for i in range(1,10)],None),
    ])
    before = snapshot(store)
    queue = ReviewArtifacts(store).daily_queue()
    assert queue['schema_version'] == 1
    assert [member['review_id'] for member in queue['members']] == ['oct28-v4','oct26-v9']
    assert all(member['has_content'] for member in queue['members'])
    assert queue['members'][0]['newer_than_adopted'] is True
    assert queue['members'][0]['adopted_review_id'] == 'oct28-v1'
    assert snapshot(store) == before
    assert ReviewArtifacts(store).daily_queue() == queue


def test_incomplete_newer_candidate_does_not_hide_readable_or_resurrect_rejected_content(tmp_path):
    store = seed(tmp_path,[
        ('readable','2026-10-28',[(1,{'text':'read'},'needs_review'),(2,None,'processing'),
            (3,{'text':'no'},'rejected'),(4,{'text':'old'},'superseded')],None),
        ('empty','2026-10-29',[(1,None,'failed'),(2,None,'needs_review')],None),
        ('terminal','2026-10-30',[(1,{'text':'no'},'rejected')],None),
    ])
    members = ReviewArtifacts(store).daily_queue()['members']
    assert [member['review_id'] for member in members] == ['empty-v2','readable-v1']
    assert members[0]['has_content'] is False
    assert members[1]['newer_unfinished_version'] == 2
    assert members[1]['newer_than_adopted'] is False


def test_many_history_versions_do_not_consume_daily_classroom_slots(tmp_path):
    store = seed(tmp_path,[
        ('recent','2026-10-29',[(i,{'text':str(i)},'needs_review') for i in range(1,132)],None),
        ('earlier','2026-10-28',[(1,{'text':'earlier'},'needs_review')],None),
    ])
    assert [member['review_id'] for member in ReviewArtifacts(store).daily_queue()['members']] == ['recent-v131','earlier-v1']
