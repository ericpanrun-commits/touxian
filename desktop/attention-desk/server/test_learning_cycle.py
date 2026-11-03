"""Isolated fixtures for real-page edge cases; never the formal learner database."""
import json
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
import pytest
from server.extraction_models import RawInput
from server.review_store import ReviewStore
from server.personal_store import PersonalError
from server.learning.models import CapturedPage,CapturedQuestion
from server.learning.activities import before_lecture,lecture_cycle
from server.learning.api import audit
from server.learning.local_questions import retest,retest_family
from server.test_learning import store,quiz
from server.test_preview_learning import add_lecture

QUESTION='隔离题：当 x→0 时，下列无穷小中与 x 等价的有哪一个？'
OPTIONS={'A':'sin x','B':'1−cos x','C':'x²','D':'√x'}

def lesson(store,monkeypatch,*,covered=True):
    import server.learning.activities as module
    lid=add_lecture(store,day='2020-01-03',title='等价无穷小')
    # The SQL note-quality reader is exercised by test_preview_learning; this
    # fixture supplies its reliable-body output to test the new comparison rule.
    monkeypatch.setattr(module,'lectures',lambda c,course:[{'id':lid,'course_id':course,'lecture_date':'2020-01-03',
        'title':'等价无穷小','notes_available':True,'notes_evidence_text':'当 x→0 时的等价无穷小。' if covered else '无关的学习安排。','accepted_artifact_id':None}])
    return lid

def save(store,lid,*,kind='preview',answer='B',result='incorrect',identity='fixture-preview',independent=False,
         confirmed=True,pending=False,question=QUESTION,origin='platform_graded_score',extra=''):
    quote='本题已批阅得分：'+('2分' if result=='correct' else '0分')
    text=f'隔离学习通来源，不是真实学生证据\n{question}\n我的答案：\n{answer}\n{quote}\n{extra}'
    bundle=ReviewStore(store.personal).collect(RawInput(original_text=text),str(uuid4()))
    ctx={'course_id':'course-math-ii','lecture_id':lid,'lecture_basis':'user_confirmed_cycle','quality':'text_extracted',
        'activity_type':kind,'activity_id':identity,'capture_method':'readonly_verified_capture','result_origin':origin,
        'independent':independent,'independence_source':'explicit_user_chat','independence_statement':'隔离用户确认独立完成',
        'answer_key_status':'publication_conflict' if pending else 'not_shown'}
    if kind=='preview':ctx.update(preview_session_id='fixture-preview-session',preview_title='等价无穷小',target_lecture_date='2020-01-03',
        temporal_relation='before_lecture' if confirmed else None,relation_source='explicit_user_chat' if confirmed else None)
    cap=store.record_capture(bundle['snapshot']['raw_item_id'],bundle['snapshot']['id'],text,ctx)
    q=CapturedQuestion(question=question,options=OPTIONS,my_answer=answer,result=result,result_quote=quote)
    ids=store.save_page(cap,CapturedPage(questions=[q]),trusted=True)
    return cap,ids

def test_independent_classroom_with_missing_submit_time_keeps_real_lecture_and_no_fake_timestamp(store,monkeypatch):
    lid=lesson(store,monkeypatch)
    save(store,lid,kind='classroom',answer='A',result='correct',identity='class-1',independent=True)
    view=store.overview()
    with store.personal.connection() as c:
        a=store.active_attempts(c)[0]
        assert a['lecture_id']==lid and a['lecture_link_basis']=='user_confirmed_cycle'
        assert a['occurred_at'] is None and a['independent']==1
    assert view['received_without_date']['correct']==1
    assert view['topics'][0]['learning_state']['label']=='正在练'

def test_repeated_classroom_question_reuses_exercise_but_activity_identity_retains_distinct_attempts(store,monkeypatch):
    lid=lesson(store,monkeypatch)
    for identity in ['class-1','class-2','class-1']:
        save(store,lid,kind='classroom',answer='A',result='correct',identity=identity)
    with store.personal.connection() as c:
        assert c.execute('SELECT count(*) FROM learning_exercise').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==2

def test_pending_publication_never_uses_points_or_computed_result_as_a_grade(store,monkeypatch):
    lid=lesson(store,monkeypatch)
    save(store,lid,kind='classroom',answer='A',result='correct',identity='pending-class',pending=True,extra='获得积分：2')
    with store.personal.connection() as c:assert store.active_attempts(c)[0]['result']=='unknown'
    assert lecture_cycle(store.personal,lid)['during_lecture']['counts']['unknown']==1

def test_published_result_appends_correction_to_same_activity_without_mutating_original(store,monkeypatch):
    lid=lesson(store,monkeypatch)
    _,old=save(store,lid,kind='classroom',answer='A',result='correct',identity='same-class',pending=True)
    save(store,lid,kind='classroom',answer='A',result='correct',identity='same-class')
    with store.personal.connection() as c:
        active=store.active_attempts(c)
        assert len(active)==1 and active[0]['result']=='correct' and active[0]['supersedes_id']==old[0]
        assert c.execute('SELECT result FROM learning_attempt WHERE id=?',(old[0],)).fetchone()[0]=='unknown'

@pytest.mark.parametrize('confirmed,covered,expected',[(True,True,True),(False,True,False),(True,False,False)])
def test_classroom_improvement_is_tentative_and_requires_prior_order_and_related_notes(store,monkeypatch,confirmed,covered,expected):
    lid=lesson(store,monkeypatch,covered=covered)
    save(store,lid,confirmed=confirmed)
    save(store,lid,kind='classroom',answer='A',result='correct',identity='class-1',independent=True)
    cycle=lecture_cycle(store.personal,lid)
    assert cycle['after_lecture']['classroom_improvements']==int(expected)
    assert cycle['after_lecture']['delayed_independent_successes']==0
    with store.personal.connection() as c:
        assert c.execute("SELECT count(*) FROM learning_followup WHERE status='pending'").fetchone()[0]==1
        assert c.execute('SELECT sum(retest_success) FROM learning_attempt').fetchone()[0]==0

def test_already_correct_preview_item_cannot_claim_improvement_of_a_different_wrong_item(store,monkeypatch):
    lid=lesson(store,monkeypatch)
    save(store,lid)
    easy=QUESTION+'（另一道隔离题）'
    save(store,lid,answer='A',result='correct',question=easy)
    save(store,lid,kind='classroom',answer='A',result='correct',question=easy,identity='class-1',independent=True)
    cycle=lecture_cycle(store.personal,lid)
    assert cycle['after_lecture']['classroom_improvements']==0
    assert cycle['after_lecture']['state']=='needs_review'

def test_user_relative_order_confirmation_does_not_invent_a_platform_submit_clock(store,monkeypatch):
    lid=lesson(store,monkeypatch)
    save(store,lid)
    before=before_lecture(store.personal,lid)
    assert before['timing_unknown']==1 and before['user_confirmed_before']==1
    assert before['attempts'][0]['submitted_at'] is None
    assert '用户确认' in before['notice']

def test_invalid_lecture_keeps_source_without_a_forced_attempt_link(store,monkeypatch):
    lesson(store,monkeypatch)
    with pytest.raises(PersonalError,match='课程不一致'):save(store,'nonexistent-lecture')
    with store.personal.connection() as c:
        assert c.execute('SELECT count(*) FROM learning_capture').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM learning_attempt').fetchone()[0]==0

def test_equivalence_retest_uses_verified_common_equivalences_and_rejects_sine_only_source():
    source={'source_id':'fixture-common','quote':'当 x→0 时，常用等价无穷小：sin x~x，tan x~x，ln(1+x)~x，1−cos x~x²/2。'}
    candidate=retest([source],QUESTION,'abcdef0123')
    assert candidate and candidate.question!=QUESTION and retest_family(candidate.question)==retest_family(QUESTION)
    assert len(set(candidate.options.values()))==4 and candidate.source_ids==['fixture-common']
    assert retest([{'source_id':'fixture-sine','quote':'当 x→0 时 sin x/x→1。'}],QUESTION,'abcdef0123') is None

def test_audit_contains_actual_cycle_sources_and_hides_unanswered_generated_answers(store,monkeypatch):
    lid=lesson(store,monkeypatch)
    save(store,lid,extra='cookie: fake-export-secret\nhttps://user:fake@portal.invalid/a?token=fake')
    save(store,lid,kind='classroom',answer='A',result='correct',identity='class-1',independent=True)
    with store.personal.connection() as c:prior=c.execute("SELECT id FROM learning_attempt WHERE result='incorrect'").fetchone()[0]
    quiz(store,prior=prior,count=1)
    with store.personal.connection(write=True) as c:
        generated=c.execute("SELECT artifact_id FROM learning_exercise WHERE kind='retest'").fetchone()[0]
        c.execute('UPDATE learning_artifact SET payload_json=? WHERE id=?',
            (json.dumps({'retest':{'question':'隔离待测题','options':OPTIONS,'correct_key':'A','explanation':'hidden-retest-explanation'}}),generated))
    response=audit(SimpleNamespace(store=store,personal=store.personal))
    with ZipFile(BytesIO(response.body)) as archive:data=json.loads(archive.read('learning-audit.json'))
    encoded=json.dumps(data)
    assert 'fake-export-secret' not in encoded and 'https://user:fake@' not in encoded
    assert data['learning_cycles'][0]['lecture_id']==lid and data['lectures'][0]['id']==lid
    assert len(data['source_snapshots'])==2
    assert data['retest_recommendations'][0]['exercise']['question']
    assert 'answer' not in data['retest_recommendations'][0]['exercise']
    exported=next(a for t in data['topics'] for a in t['artifacts'] if a['id']==generated)
    assert 'correct_key' not in exported['payload']['retest'] and 'hidden-retest-explanation' not in encoded
    assert any(s['redacted_for_export'] for s in data['source_snapshots'])

