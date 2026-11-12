"""Local-only urgency. Candidate scores cannot create P0 in the personal domain."""
from .planning.time import state


def personal_priority(event, *, at=None):
    dates={}
    for value in event['deadlines']:
        confirmed=bool((value['confirmed_by_user'] or value.get('verified_automatically')) and not value.get('uncertain'))
        # Only this trusted read model accepts automatic verification; storage
        # and the public confirmation contract retain the true human flag.
        view={**value,'confirmed_by_user':confirmed}
        from zoneinfo import ZoneInfo
        dates[value['id']]={**state(view,at=at,display_zone=ZoneInfo('Asia/Shanghai') if at else None),'confirmed':confirmed}
    if event["status"] in {"done", "cancelled"}:
        return {"level": None, "reason": "事项已完成或已取消。", "deadlines": dates}
    pending = [task for task in event["tasks"] if task["status"] != "done"]
    urgent = [dates[task["deadline_id"]] for task in pending if task["deadline_id"] in dates and dates[task["deadline_id"]]["confirmed"] and dates[task["deadline_id"]]["days_remaining"] is not None and dates[task["deadline_id"]]["days_remaining"] <= 3]
    return {"level": "P1" if urgent else "P2", "reason": "已验证的任务存在逾期或未来 3 天内的截止时间。" if urgent else "按已核实的行动与日期处理；待确认内容保留。",
            "deadlines": dates}
