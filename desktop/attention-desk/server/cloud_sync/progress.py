"""Mirror accepted task fields with the canonical guards and an atomic receipt."""
from __future__ import annotations
from .commands import TransactionView
from ..personal_store import PersonalError,now


def rebase_task_versions(connection,event_id,expected,current):
    """Only our recorded task applications may account for a stale parent version."""
    while expected<current:
        row=connection.execute('SELECT to_version FROM cloud_task_application WHERE event_id=? AND from_version=? AND to_version>? ORDER BY to_version LIMIT 1',
            (event_id,expected,expected)).fetchone()
        if not row:return None
        expected=row['to_version']
    return current if expected==current else None


def apply_task_progress(personal,row):
    value=row['payload'];revision=row['revision'];identity=row['id'];event_id=value['event_id']
    desired='done' if value['completed'] else 'todo'
    with personal.connection(write=True) as c:
        if c.execute('SELECT 1 FROM cloud_task_application WHERE cloud_revision=?',(revision,)).fetchone():return
        event=c.execute('SELECT * FROM event WHERE id=?',(event_id,)).fetchone()
        task=c.execute('SELECT * FROM task WHERE id=? AND event_id=?',(identity,event_id)).fetchone()
        if event and event['status'] in ('done','cancelled'):
            raise PersonalError('这个事项已结束。请先恢复事项，再核对任务状态。','parent_closed',409)
        if not task or not event or task['title']!=value['source_title']:
            raise PersonalError('任务在另一处发生了变化，请核对最新内容后确认完成状态。','task_changed',409)
        # A prior cloud application is proof only when no intervening Event edit occurred.
        own=c.execute('SELECT * FROM cloud_task_application WHERE task_id=? ORDER BY cloud_revision DESC LIMIT 1',(identity,)).fetchone()
        unchanged=task['status']==value['source_status']
        own_status=own and own['status']==task['status'] and rebase_task_versions(c,event_id,own['to_version'],event['version']) is not None
        if not unchanged and not own_status and task['status']!=desired:
            raise PersonalError('此任务已在另一处修改，请核对后再确认。','task_changed',409)
        before=event['version']
        if task['status']!=desired:
            result=TransactionView(personal,c).edit_task(event_id,identity,before,{'status':desired});after=result['version']
        else:after=before
        c.execute('INSERT INTO cloud_task_application VALUES(?,?,?,?,?,?,?,?)',(revision,identity,event_id,before,after,task['title'],desired,now()))
