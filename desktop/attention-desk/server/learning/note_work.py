"""One source-bound note operation; newer owner choices always take priority."""
from contextlib import nullcontext
import re

from ..personal_store import PersonalError,encode,now
from .models import CapturedPage
from .store import capture_projection

UNCHANGED=object()

class NoteWork:
    FIELDS=('id','raw_item_id','snapshot_id','context_json','status','parsed_json','error_json')
    WHERE=' AND '.join(field+' IS ?' for field in FIELDS)

    def __init__(self,service,cid):
        self.service=service
        self.personal=service.personal
        with self.personal.connection() as c:
            row=c.execute('SELECT * FROM learning_capture WHERE id=?',(cid,)).fetchone()
            if not row:raise PersonalError('学习输入不存在。','not_found',404)
            self.token=tuple(row[field] for field in self.FIELDS)
            self.course=c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(row['raw_item_id'],)).fetchone()[0]
            self.capture=capture_projection(c,row)
            self.source=c.execute('''SELECT s.original_text,r.title_hint FROM raw_snapshot s
                JOIN raw_review r ON r.raw_snapshot_id=s.id
                WHERE s.id=? AND s.raw_item_id=?''',(row['snapshot_id'],row['raw_item_id'])).fetchone()

    def current(self,connection=None,*,assign_course=UNCHANGED):
        with nullcontext(connection) if connection is not None else self.personal.connection() as c:
            if not c.execute('SELECT 1 FROM learning_capture WHERE '+self.WHERE,self.token).fetchone():
                raise PersonalError('笔记的确认内容已有更新，已保留最新选择。','note_context_changed',409)
            raw=c.execute('SELECT inbox_course_id FROM raw_item WHERE id=?',(self.capture['raw_item_id'],)).fetchone()
            if not raw or raw[0]!=self.course:
                raise PersonalError('笔记的课程关联已有更新，已保留最新选择。','note_context_changed',409)
            if assign_course is not UNCHANGED:
                # Called only in the transaction assigning this note's course.
                self.course=assign_course

    def save(self,context,*,initial=False):
        context.pop('note_link_update',None)
        state=context.get('note_automation',{}).get('status')
        completed=state in {'local_ready','completed'} if initial else state=='completed'
        parsed=encode(CapturedPage().model_dump()) if completed or not initial else None
        error=None if completed else encode({'message':context.get('note_automation',{}).get('message','笔记原件已保存，请核对进度。')})
        with self.personal.connection(write=True) as c:
            saved=c.execute('UPDATE learning_capture SET context_json=?,status=?,parsed_json=COALESCE(?,parsed_json),error_json=? WHERE '+self.WHERE+
                ' AND EXISTS(SELECT 1 FROM raw_item WHERE id=? AND inbox_course_id IS ?)',
                (encode(context),'completed' if completed else 'needs_context',parsed,error,*self.token,self.capture['raw_item_id'],self.course))
            if not saved.rowcount:return False
            # Preserve the original ingest's exposure semantics in the same
            # transaction. Material received never proves reading or mastery.
            text=self.source[0]+'\n'+(self.source[1] or '')
            if initial and completed and context.get('course_id') and re.search(r'重要极限|夹逼准则|函数极限|数列极限|等价无穷小|连续性',text):
                store=self.service.store
                tid=store.topic_for(c,context['course_id'],text)
                topic=store._topic(c,tid)
                if not any(item.get('capture_id')==self.capture['id'] for item in topic['exposure']):
                    exposure={'kind':'material_received','capture_id':self.capture['id'],'date':now(),
                        'description':'收到相关学习材料；不能证明已读完或独立会做'}
                    c.execute('UPDATE learning_topic SET exposure_json=?,updated_at=? WHERE id=?',
                        (encode([*topic['exposure'],exposure]),now(),tid))
        return True
