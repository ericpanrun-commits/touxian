"""Note routing must be conservative under conflicting clocks, courses and OCR."""
import json,sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import pytest
from server.learning.note_course import infer,MATH

@pytest.fixture
def personal(tmp_path):
    path=tmp_path/'notes.sqlite3'
    c=sqlite3.connect(path)
    c.executescript('''CREATE TABLE course(id,name,normalized_name);
      CREATE TABLE course_alias(normalized_alias,course_id);
      CREATE TABLE document_section(title,textbook_id,document_id);
      CREATE TABLE textbook(id,course_id,document_id,is_fixture);
      CREATE TABLE lecture(id,course_id,lecture_date,title);
      CREATE TABLE inbox_receipt(raw_item_id,request_id,created_at);
      CREATE TABLE learning_artifact(kind,payload_json);
      INSERT INTO course VALUES('course-math-ii','高等数学 II','高等数学ii');
      INSERT INTO course_alias VALUES('高等数学ii','course-math-ii');
      INSERT INTO course_alias VALUES('高等数学','course-math-ii');
      INSERT INTO course_alias VALUES('高数','course-math-ii');''')
    c.commit();c.close()
    @contextmanager
    def connection():
        with sqlite3.connect(path) as db:db.row_factory=sqlite3.Row;yield db
    return SimpleNamespace(path=path,connection=connection)

def schedule(*sessions):
    return {'timezone':'Asia/Shanghai','term_week_one_monday':'2026-08-31','term_start_confirmed_by':'explicit_user_chat',
      'course_mappings':{'高等数学I1':MATH},'sessions':list(sessions or [
        {'title':'高等数学I1','start':'2026-10-26T14:30:00+08:00','end':'2026-10-26T16:00:00+08:00','week':9}])}

def shared(personal,at):
    with personal.connection() as c:
        c.execute("INSERT INTO inbox_receipt VALUES('raw','cloud','2026-10-30T01:00:00Z')")
        c.execute('INSERT INTO learning_artifact VALUES(?,?)',('note_transport_context',json.dumps({'submission_id':'cloud','shared_at':at,'pending_created_at':at})))

def test_unique_actual_slot_and_two_topic_families_auto_link(personal):
    shared(personal,'2026-10-26T06:45:00Z')
    result=infer(personal,'raw','课堂笔记.pdf','极限与连续，左右极限和间断点。',{},settings=schedule())
    assert result['confidence']=='high' and result['course_id']==MATH
    assert result['teaching_week']==9 and result['lecture_day']=='2026-10-26'

def test_single_keyword_and_only_registered_course_never_suffice(personal):
    shared(personal,'2026-10-26T06:45:00Z')
    result=infer(personal,'raw','Image.pdf','左右极限',{},settings=schedule())
    assert result['confidence']!='high' and result['course_id'] is None

def test_time_alone_recommends_but_does_not_assign(personal):
    shared(personal,'2026-10-26T06:45:00Z')
    result=infer(personal,'raw','Image.pdf','',{},settings=schedule())
    assert result['confidence']=='medium' and result['recommended_course_id']==MATH and result['course_id'] is None

def test_generated_pdf_creation_is_weak_not_an_independent_original_timestamp(personal):
    result=infer(personal,'raw','课堂.pdf','极限与连续',{'source_time':'2026-10-26T14:45:00+08:00'},settings=schedule())
    assert result['confidence']=='medium'

def test_offline_desktop_receive_date_does_not_replace_real_share_day(personal):
    shared(personal,'2026-10-26T06:45:00Z')
    result=infer(personal,'raw','课堂.pdf','极限与连续',{'source_time':'2026-10-26T14:45:00+08:00'},settings=schedule())
    assert result['lecture_day']=='2026-10-26'

def test_ocr_conflicts_with_other_course_title_and_time_are_conservative(personal):
    shared(personal,'2026-10-26T06:45:00Z')
    config=schedule({'title':'工程图学1','start':'2026-10-26T14:30:00+08:00','end':'2026-10-26T16:00:00+08:00','week':9})
    result=infer(personal,'raw','工程图学1.pdf','极限与连续',{},settings=config)
    assert result['confidence']=='low' and not result['course_id']

def test_overlapping_slots_or_inconsistent_teaching_week_never_auto_assign(personal):
    shared(personal,'2026-10-26T06:45:00Z')
    first=schedule()['sessions'][0]
    for config in [schedule(first,{**first,'title':'其他课程'}),schedule({**first,'week':8})]:
        result=infer(personal,'raw','Image.pdf','极限与连续',{},settings=config)
        assert result['confidence']!='high'

def test_human_course_and_same_document_associations_win_without_relabelling(personal):
    assert infer(personal,'raw','Image.pdf','',{'course_id':MATH,'course_basis':'user_confirmation'},settings=schedule())['confidence']=='high'
    result=infer(personal,'raw','Image.pdf','',{},known=[{'course_id':MATH}],settings=schedule())
    assert result['course_id']==MATH and result['basis']=='existing_note_document'

def test_different_course_number_is_not_absorbed_by_generic_math_alias(personal):
    shared(personal,'2026-10-26T06:45:00Z')
    result=infer(personal,'raw','高等数学III.pdf','极限与连续',{},settings=schedule())
    assert result['confidence']=='low'
    mapped=infer(personal,'raw','高等数学I1.pdf','极限与连续',{},settings=schedule())
    assert mapped['course_id']==MATH

def test_explicit_old_note_day_disables_current_class_time_weight(personal):
    shared(personal,'2026-10-26T06:45:00Z')
    result=infer(personal,'raw','2026-10-23 课堂.pdf','极限与连续',{},settings=schedule())
    assert result['confidence']!='high' and result.get('lecture_day') is None


def test_delayed_phone_retry_does_not_become_the_upload_day_class(personal):
    shared(personal,'2026-10-29T00:30:00Z')
    with personal.connection() as c:
        c.execute('UPDATE learning_artifact SET payload_json=?',(json.dumps({'submission_id':'cloud',
            'shared_at':'2026-10-29T00:30:00Z','pending_created_at':'2026-10-26T06:45:00Z'}),))
    result=infer(personal,'raw','课堂.pdf','极限与连续',{},settings=schedule())
    assert result['confidence']=='medium' and result['source_timestamp'].startswith('2026-10-26T14:45')
    assert '待发送PDF' in ''.join(result['reasons'])


def general_course(personal):
    with personal.connection() as c:
        c.execute("INSERT INTO course VALUES('military','军事理论','军事理论')")
        c.execute("INSERT INTO course_alias VALUES('军事理论','military')")


def test_registered_short_course_title_and_actual_slot_can_route_without_math_content(personal):
    general_course(personal);shared(personal,'2026-10-26T06:45:00Z')
    config=schedule({'title':'军事理论','start':'2026-10-26T14:30:00+08:00','end':'2026-10-26T16:00:00+08:00','week':9})
    result=infer(personal,'raw','军事理论课堂笔记.pdf','课程笔记正文。',{},settings=config)
    assert result['confidence']=='high' and result['course_id']=='military' and result['lecture_day']=='2026-10-26'
    assert result['evidence_groups']==['content','timetable']
    with personal.connection() as c:assert c.execute('SELECT count(*) FROM course').fetchone()[0]==2


@pytest.mark.parametrize('clock',['proxy','actual_share','delayed','other_slot'])
def test_trusted_general_course_does_not_turn_every_upload_clock_into_a_class_date(personal,clock):
    general_course(personal)
    config=schedule({'title':'军事理论' if clock!='other_slot' else '高等数学I1',
        'start':'2026-10-26T14:30:00+08:00','end':'2026-10-26T16:00:00+08:00','week':9})
    if clock!='proxy':shared(personal,'2026-10-26T06:45:00Z')
    if clock=='delayed':
        with personal.connection() as c:
            c.execute('UPDATE learning_artifact SET payload_json=?',(json.dumps({'submission_id':'cloud',
                'shared_at':'2026-10-29T06:45:00Z','pending_created_at':'2026-10-26T06:45:00Z'}),))
    result=infer(personal,'raw','课堂笔记.pdf','',{'course_id':'military','course_basis':'user_confirmation',
        'source_time':'2026-10-26T14:45:00+08:00'},settings=config)
    assert result['course_id']=='military' and result['confidence']=='high'
    assert result['lecture_day']==('2026-10-26' if clock=='actual_share' else None)


def test_unregistered_trusted_context_is_not_a_course_identity(personal):
    result=infer(personal,'raw','笔记.pdf','',{'course_id':'missing','course_basis':'user_confirmation'},settings=schedule())
    assert result['confidence']=='low' and result['course_id'] is None


@pytest.mark.parametrize('clock',['pending_only','delayed','actual_share','original_metadata'])
def test_strong_course_evidence_does_not_promote_a_pdf_clock_to_a_class_date(personal,clock):
    general_course(personal)
    with personal.connection() as c:
        c.execute("INSERT INTO textbook VALUES('book','military','doc',0)")
        c.execute("INSERT INTO document_section VALUES('国家安全教育','book','doc')")
    context={'source_time':'2026-10-26T14:45:00+08:00'}
    if clock in {'delayed','actual_share'}:shared(personal,'2026-10-26T06:45:00Z')
    if clock=='delayed':
        with personal.connection() as c:
            c.execute('UPDATE learning_artifact SET payload_json=?',(json.dumps({'submission_id':'cloud',
                'shared_at':'2026-10-29T06:45:00Z','pending_created_at':'2026-10-26T06:45:00Z'}),))
    if clock=='original_metadata':
        context.update(note_created_at='2026-10-26T14:45:00+08:00',note_created_origin='original_note_metadata')
    config=schedule({'title':'军事理论','start':'2026-10-26T14:30:00+08:00','end':'2026-10-26T16:00:00+08:00','week':9})
    result=infer(personal,'raw','军事理论课堂笔记.pdf','国家安全教育',context,settings=config)
    # The title, registered chapter and weak time proxy can identify a course.
    # They still do not prove the date on which the original class happened.
    assert result['confidence']=='high' and result['course_id']=='military'
    assert result['lecture_day']==('2026-10-26' if clock in {'actual_share','original_metadata'} else None)
