"""Read-only navigation guards and observed result-layout parsing, isolated only."""
import pytest
from server.learning.browser_parse import grade_page,is_ended_exercise,submitted_result
from server.learning.browser_capture import EdgeReader,CaptureStopped,safe_location,safe_text

def preview():
    rows=['隔离布局，不是真实学生记录','无穷小比较定义测试题','题量: 2','满分: 20.0','本次成绩','10.0']
    for n,answer,icon,score in [(1,'A','\ue902','10.0'),(2,'BD','\ue901','0.0')]:
        rows.extend([str(n),'【多选题】',f'隔离无穷小比较题{n}','A.','sin x','B.','tan x','C.','ln(1+x)','D.','1-cos x','我的答案：',answer,icon,score,'分'])
    return rows

def test_graded_preview_keeps_every_real_answer_including_wrong_without_solving_keys():
    value=grade_page(preview(),'preview')
    assert [q['result'] for q in value['questions']]==['correct','incorrect']
    assert all(not q['correct_answer'] for q in value['questions'])
    assert value['result_origin']=='platform_graded_score'
    assert all(q['submitted_at'] is None for q in value['questions'])

def test_publication_conflict_never_uses_keys_or_integral_as_verified_results():
    rows=['已提交，待教师公布正确答案','获得积分:','6','1.','[单选题]','隔离题：选择条件','我的答案：','A','正确答案：','A','答案解析：','示例解析','2','积分']
    value=grade_page(rows,'classroom')
    assert value['publication_conflict'] and value['questions'][0]['result']=='unknown'
    assert value['questions'][0]['correct_answer']=='A'

def test_classroom_key_and_literal_explanation_are_platform_evidence_not_our_solving():
    rows=['已提交','1.','[单选题]','隔离题：比值条件','我的答案：','B','正确答案：','答案解析：','平台明确结论：故选 B。','0','积分']
    q=grade_page(rows,'classroom')['questions'][0]
    assert q['result']=='correct' and q['key_origin']=='platform_explanation_explicit_conclusion'
    assert q['result_quote']=='平台明确结论：故选 B。'

@pytest.mark.parametrize('labels',[
 ['随堂练习','结束时间：2020-01-03 10:00','开始答题'],
 ['视频','结束时间：01-03 10:00'],['二维码签到','结束时间：01-03 10:00'],
 ['随堂练习','结束时间：01-03 10:00','结束时间：01-03 11:00'],
 ['随堂练习','结束时间：01-04 10:00'],
])
def test_ambiguous_active_nonexercise_and_other_day_entries_are_never_opened(labels):
    assert not is_ended_exercise(labels,'2020-01-03')

def test_navigation_cannot_invoke_an_answer_control_or_submit_button():
    reader=EdgeReader.__new__(EdgeReader);reader.bound=lambda:None;reader.location=lambda:'https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu';reader.actions=[]
    class Forbidden:
        Name='A';ControlTypeName='RadioButtonControl'
        def GetInvokePattern(self):raise AssertionError('Must never request an answer action')
    for operation in ['menu','question_navigation','graded_quiz','ended_exercise']:
        with pytest.raises(CaptureStopped):reader.invoke(Forbidden(),operation,['已提交','我的答案：'],'2020-01-03')
    Forbidden.Name='提交答案';Forbidden.ControlTypeName='ButtonControl'
    with pytest.raises(CaptureStopped):reader.invoke(Forbidden(),'question_navigation',['已提交','我的答案：'])

@pytest.mark.parametrize('url',['https://chaoxing.com.evil.test/result','http://mooc1.chaoxing.com/result','https://x:y@mooc1.chaoxing.com/result','https://mooc1.chaoxing.com:8443/result'])
def test_unapproved_address_never_becomes_a_learning_source(url):
    with pytest.raises(CaptureStopped):safe_location(url)

def test_exported_location_and_text_do_not_keep_url_queries_or_credentials():
    assert safe_location('https://mooc1.chaoxing.com/result?enc=private#private')=='https://mooc1.chaoxing.com/result'
    value=safe_text('来源 https://mooc1.chaoxing.com/result?token=private\nCookie: private-session')
    assert 'private' not in value and '[omitted]' in value

def test_only_past_ended_exercises_for_the_requested_day_are_opened():
    assert is_ended_exercise(['随堂练习','结束时间：01-03 10:00'],'2020-01-03')
    assert not is_ended_exercise(['随堂练习','结束时间：01-03 10:00'],'2099-01-03')


def test_grouped_native_question_navigation_stays_before_the_question_body():
    class Node:
        IsPassword=False
        def __init__(self,kind,name='',children=(),rid=0):self.ControlTypeName,self.Name,self.children,self.rid=kind,name,list(children),rid
        def GetChildren(self):return self.children
        def GetRuntimeId(self):return [self.rid]
    def numbers():return [Node('GroupControl',children=[Node('TextControl',str(n))],rid=n) for n in range(1,5)]
    result=Node('DocumentControl',children=[Node('GroupControl',children=numbers()),Node('TextControl','[单选题]'),Node('TextControl','已提交'),Node('TextControl','我的答案：')])
    reader=EdgeReader.__new__(EdgeReader)
    assert [n for n,_ in reader.question_navigation(result)]==[1,2,3,4]
    answer_groups=Node('DocumentControl',children=[Node('TextControl','[单选题]'),Node('GroupControl',children=numbers()),Node('TextControl','已提交'),Node('TextControl','我的答案：')])
    assert not reader.question_navigation(answer_groups)
