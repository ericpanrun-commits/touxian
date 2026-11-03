"""An explicit bridge into the existing draft / batch-confirm flow."""
from ..personal_store import PersonalError, encode, now
from ..extraction_models import RawInput, ReviewEvent, ReviewAction
from ..review_store import ReviewStore
from .models import COURSE
from .store import SchoolStore


def stage_proposals(service, artifact_id, expected_version):
    artifact = service.artifacts.get(artifact_id)
    if not artifact['content']:
        raise PersonalError('请先生成并核对复盘。', 'review_incomplete', 409)
    actions = artifact['content']['suggested_actions']
    if not actions: raise PersonalError('这个版本没有复习建议。', 'no_proposals', 409)
    with service.personal.connection(write=True) as c:
        lecture = SchoolStore._lecture(c, artifact['lecture_id'], expected_version)
        # One batch per lecture. A newer AI revision must not duplicate confirmed tasks.
        if lecture['proposal_snapshot_id']:
            return {'snapshot_id': lecture['proposal_snapshot_id'], 'reused': True}
        text = '\n'.join([f"AI 复习建议（不是教师通知）\n课次：{lecture['title']}\n复盘版本：{artifact['version']}\nReviewArtifact：{artifact_id}",
                          *(f"{a['text']}（建议 {a['duration_minutes']} 分钟）" for a in actions)])
        bundle = ReviewStore(service.personal).collect(RawInput(original_text=text, title_hint='复习建议 · ' + lecture['title'][:160]),
                    'school-proposal-' + lecture['id'], source_id='school_review', source_kind='school_review',
                    source_name='课堂复盘建议 · AI 派生', connection=c)
        event = ReviewEvent(id='review-actions', title='复习 · ' + lecture['title'][:180], course_id=COURSE,
                    summary=f"来自课堂复盘第 {artifact['version']} 版。请自行决定是否采纳，尚未创建任务。",
                    category='学习', relevance='yes', participation='later', notice_kind='information',
                    actions=[ReviewAction(id=f'review-action-{i+1}', title=a['text'], kind='suggested') for i,a in enumerate(actions)],
                    uncertainties=['AI 复习建议需人工核对；不属于教师明确要求。'])
        c.execute('UPDATE raw_review SET draft_json=?,version=version+1,updated_at=? WHERE raw_snapshot_id=?',
                  (encode({'acknowledged_warnings':False,'events':[event.model_dump()]}), now(), bundle['snapshot']['id']))
        c.execute('UPDATE lecture SET proposal_snapshot_id=?,version=version+1,updated_at=? WHERE id=?',
                  (bundle['snapshot']['id'], now(), lecture['id']))
        return {'snapshot_id':bundle['snapshot']['id'], 'reused':False}
