"""Schedule registration shares Course IDs without inventing learner progress."""
import json
from server.courses import CourseStore,normalized_name
from server.personal_store import PersonalStore
from server.learning.note_course import reconcile_timetable
from server.cloud_sync.projection import CloudProjection


def catalogue(tmp_path):
    personal=PersonalStore(tmp_path/'personal.sqlite3');personal.initialize()
    return personal,CourseStore(personal)


def test_registration_reuses_explicit_math_and_repeated_course_titles(tmp_path):
    personal,courses=catalogue(tmp_path)
    settings={'sessions':[{'title':title} for title in ['高等数学I1','大学英语1','大学英语1','工程图学1']],
        'course_mappings':{'高等数学I1':'course-math-ii'}}
    before=courses.list()['total']
    result=courses.register_timetable(settings)
    assert len(result['created'])==2 and not result['conflicts']
    assert result['course_mappings']['高等数学I1']=='course-math-ii'
    assert courses.list()['total']==before+2
    assert courses.register_timetable(settings)['created']==[]
    assert courses.timetable(settings)['course_mappings']==result['course_mappings']
    assert courses.find('高等数学I1')['id']=='course-math-ii'
    with personal.connection() as c:
        for table in ('lecture','review_artifact','learning_attempt','document','task'):
            assert c.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


def test_read_projection_is_pure_and_does_not_register_unknown_names(tmp_path):
    personal,courses=catalogue(tmp_path)
    settings={'sessions':[{'title':'高等数学I1'},{'title':'尚未登记的课程'}],
        'course_mappings':{'高等数学I1':'course-math-ii'}}
    before=courses.list()
    result=courses.timetable(settings)
    assert result['course_mappings']=={'高等数学I1':'course-math-ii'}
    assert courses.list()==before and courses.find('高等数学I1') is None


def test_conflicting_or_unknown_explicit_mapping_never_steals_an_alias(tmp_path):
    personal,courses=catalogue(tmp_path)
    english=courses.create('大学英语1')
    settings={'sessions':[{'title':'大学英语1'},{'title':'工程图学1'}],
        'course_mappings':{'大学英语1':'course-math-ii','工程图学1':'missing-course'}}
    result=courses.register_timetable(settings)
    assert result['created']==[] and result['course_mappings']=={}
    assert {item['reason'] for item in result['conflicts']}=={'conflicting_course_alias','unknown_explicit_course'}
    assert courses.find('大学英语1')['id']==english['id']
    assert courses.find('工程图学1') is None


def test_normalized_duplicates_share_one_course_and_invalid_titles_stay_unregistered(tmp_path):
    personal,courses=catalogue(tmp_path)
    settings={'sessions':[{'title':'大学英语１'},{'title':'大学 英语1'},{'title':''},{'title':None}]}
    result=courses.register_timetable(settings)
    assert len(result['created'])==1 and len(result['conflicts'])==2
    assert len(set(result['course_mappings'].values()))==1
    assert courses.find('大学英语1')['normalized_name']==normalized_name('大学英语1')


def test_validated_startup_registers_once_and_cloud_timetable_uses_canonical_ids(tmp_path):
    personal,courses=catalogue(tmp_path)
    settings={'timezone':'Asia/Shanghai','term_week_one_monday':'2026-08-31','term_start_confirmed_by':'explicit_user_chat',
        'sessions':[{'title':'高等数学I1'},{'title':'大学英语1'},{'title':'军事理论'}],
        'course_mappings':{'高等数学I1':'course-math-ii'}}
    personal.path.with_name('notes-timetable.json').write_text(json.dumps(settings),encoding='utf-8')
    result=reconcile_timetable(personal)
    assert len(result['created'])==2 and reconcile_timetable(personal)['created']==[]
    before=courses.list(); records=CloudProjection(personal,tmp_path/'cloud').records()
    projected=next(r['payload'] for r in records if r['kind']=='facade' and r['id']=='/timetable')
    assert projected['course_mappings']==result['course_mappings'] and projected['course_conflicts']==[]
    assert projected['course_mappings']['高等数学I1']=='course-math-ii'
    assert projected['course_mappings']['大学英语1']==courses.find('大学英语1')['id']
    assert courses.list()==before
    with personal.connection() as c:
        for table in ('lecture','review_artifact','learning_attempt','task'):
            assert c.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


def test_unconfirmed_timetable_cannot_create_courses_at_startup(tmp_path):
    personal,courses=catalogue(tmp_path);before=courses.list()
    personal.path.with_name('notes-timetable.json').write_text(json.dumps({'timezone':'Asia/Shanghai',
        'term_week_one_monday':'2026-08-31','term_start_confirmed_by':'unconfirmed','sessions':[{'title':'新课程'}]}),encoding='utf-8')
    assert reconcile_timetable(personal)['created']==[] and courses.list()==before
