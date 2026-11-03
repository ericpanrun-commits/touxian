"""Owner course choices shared by Inbox and the existing note confirmation."""
import json
from ..personal_store import encode


def choose_course(context,course_id):
    if course_id!=context.get('course_id'):
        for field in ('confirmed_note_lecture_id','lecture_id','lecture_basis','automatic_lecture_title','note_automation'):
            context.pop(field,None)
    context.update(course_id=course_id,course_basis='user_confirmation' if course_id else 'user_unlinked')
    if not context.get('confirmed_note_date'):context['note_date_requires_confirmation']=True


def inbox_course_changed(connection,raw_id,course_id,version):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_capture'").fetchone():return
    # Match the current note shown by Inbox. Older snapshot captures remain
    # historical; ordinary exercises never become confirmed student evidence.
    row=connection.execute("""SELECT * FROM learning_capture WHERE raw_item_id=?
        AND json_extract(context_json,'$.note_share')=1 ORDER BY created_at DESC,id DESC LIMIT 1""",(raw_id,)).fetchone()
    if not row:return
    context=json.loads(row['context_json']);choose_course(context,course_id)
    if course_id:
        context['note_link_update']={'inbox_version':version,'course_id':course_id}
        context['note_automation']={'status':'interrupted','message':'课程已保存，会自动继续整理这份笔记。'}
        status='received'
    else:
        context.pop('note_link_update',None)
        context['note_automation']={'status':'needs_context','message':'课程关联已移除，请选择课程后继续整理。'}
        status='needs_context'
    # The marker and course change commit together, including when the outer
    # transaction also owns a cloud-command receipt. No model runs here.
    connection.execute('UPDATE learning_capture SET context_json=?,status=?,error_json=NULL WHERE id=?',
        (encode(context),status,row['id']))
