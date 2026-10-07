"""Local-only urgency. Candidate scores cannot create P0 in the personal domain."""
from .extraction_validation import deadline_state


def personal_priority(event):
    dates = {value["id"]: {**deadline_state(value["value"], value["precision"]), "confirmed": value["confirmed_by_user"]} for value in event["deadlines"]}
    if event["status"] in {"done", "cancelled"}:
        return {"level": None, "reason": "事项已完成或已取消。", "deadlines": dates}
    pending = [task for task in event["tasks"] if task["status"] != "done"]
    urgent = [dates[task["deadline_id"]] for task in pending if task["deadline_id"] in dates and dates[task["deadline_id"]]["confirmed"] and dates[task["deadline_id"]]["days_remaining"] <= 3]
    return {"level": "P1" if urgent else "P2", "reason": "你已确认的任务存在逾期或未来 3 天内的截止时间。" if urgent else "等待你的确认或按计划处理；不使用模型分数。",
            "deadlines": dates}
