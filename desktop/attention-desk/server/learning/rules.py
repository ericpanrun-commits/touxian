"""Small transparent rules. Reading and model output never demonstrate mastery."""
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')
RELIABLE = {'text_extracted', 'user_verified', 'visually_checked'}


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.replace(tzinfo=SHANGHAI) if result.tzinfo is None else result
    except (ValueError, TypeError, AttributeError):
        return None


def due_after(value, repeat=False):
    base = timestamp(value) or datetime.now(timezone.utc)
    # A real interval, then the next local 09:00. No Deadline/Task is created.
    day = (base.astimezone(SHANGHAI) + timedelta(days=1 if repeat else 2)).date()
    due=datetime.combine(day, datetime.min.time(), SHANGHAI).replace(hour=9)
    if due < base + timedelta(days=1):due += timedelta(days=1)
    return due.astimezone(timezone.utc).isoformat()


def normalized(value):
    return re.sub(r'\s+', '', value).casefold()


def result_from_quote(quote):
    wrong = re.search(r'回答错误|答题错误|回答不正确|答错|结果[：:]?\s*错误|^(?:回答|结果)?[：:]?\s*[✕×❌]\s*$', quote.strip())
    right = re.search(r'回答正确|答题正确|答对|结果[：:]?\s*正确|^(?:回答|结果)?[：:]?\s*[✓✔✅]\s*$', quote.strip())
    if bool(wrong) == bool(right):
        return 'unknown'
    return 'incorrect' if wrong else 'correct'


def state(topic, attempts, followups, current):
    known = [a for a in attempts if a['result'] != 'unknown']
    wrong = [a for a in known if a['result'] == 'incorrect']
    successes = [a for a in known if a['result'] == 'correct' and a['independent']]
    retests = [a for a in successes if a['retest_success']]
    open_plans = [f for f in followups if f['status'] != 'resolved']
    classroom_progress=[a for a in successes if a.get('classroom_improvement')]
    reasons = []
    if attempts:
        reasons.append(f"已保存 {len(attempts)} 次真实作答：正确 {sum(a['result']=='correct' for a in attempts)}，错误 {len(wrong)}，结果待核对 {sum(a['result']=='unknown' for a in attempts)}。")
    if open_plans:
        reasons.append('仍有错误未通过间隔后的独立复测。')
    if retests:
        reasons.append(f'已有 {len(retests)} 次间隔至少一天、不同题目的独立复测正确。')
    dated_successes=[moment for a in successes if (moment:=timestamp(a['occurred_at']))]
    span=(max(dated_successes)-min(dated_successes)).total_seconds() if dated_successes else 0
    if open_plans:
        label = '在进步' if classroom_progress else '不稳定' if sum(a['result']=='incorrect' for a in known[-3:]) >= 2 else '建议复习'
        if classroom_progress:reasons.append('对应题已有一次课堂独立正确，只是改善线索，仍需不同题目的延迟复测。')
    elif retests and len({a['exercise_id'] for a in successes}) >= 3 and span >= 172800:
        label = '基本稳定'
        reasons.append('这只代表已抽查的题目范围，仍不等于整章掌握。')
    elif retests:
        label = '在进步'
    elif attempts:
        label = '正在练'
        if not successes: reasons.append('已保存答题结果，独立作答证据仍不足。')
    elif topic['history_status'] == 'confirmed' or any(e.get('kind') in {'lecture_material','preview_completed','preview_material_received','material_read','material_received'} for e in topic.get('exposure',[])):
        label = '已接触'
        reasons.append('以前学过或收到过相关材料；不能据此证明已读完或独立会做。')
    else:
        label = '证据不足'
        reasons.append('尚未确认历史进度；没有数据不表示没学过。')
    return {'label': label, 'reasons': reasons, 'retest_successes': len(retests)}
