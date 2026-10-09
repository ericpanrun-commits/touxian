"""Shared workbench and calendar projection from structured SQLite rows only."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from .time import aware, resolve, state, system_zone, utc_now

TERMINAL = {'done', 'cancelled'}


class PlanningQuery:
    def __init__(self, store):
        self.store = store

    def deadlines(self, *, include_terminal=False, event_id=None, deadline_id=None, at=None):
        at = aware(at or utc_now())
        tz = system_zone()
        with self.store.connection() as db:
            clauses, args = [], []
            if not include_terminal:
                clauses.append("e.status NOT IN ('done','cancelled')")
            if event_id:
                clauses.append('e.id=?')
                args.append(event_id)
            if deadline_id:
                clauses.append('d.id=?')
                args.append(deadline_id)
            sql = """SELECT d.*,e.title AS event_title,e.status AS event_status,e.version AS event_version,
                e.updated_at AS event_updated_at,e.raw_item_id,e.raw_snapshot_id,e.category,
                s.name AS source_name,r.canonical_url AS source_url
                FROM deadline d JOIN event e ON e.id=d.event_id JOIN raw_item r ON r.id=e.raw_item_id
                JOIN source s ON s.id=r.source_id"""
            if clauses:
                sql += ' WHERE ' + ' AND '.join(clauses)
            rows = [dict(row) for row in db.execute(sql,args)]
            tasks = defaultdict(list)
            for row in db.execute('SELECT id,event_id,deadline_id,title,status FROM task ORDER BY sort_order,id'):
                tasks[row['event_id']].append(dict(row))
            evidence = defaultdict(list)
            for row in db.execute('SELECT id,event_id,field_path,raw_snapshot_id,page,section,source_type FROM evidence ORDER BY created_at,id'):
                evidence[(row['event_id'],row['field_path'],row['raw_snapshot_id'])].append(dict(row))
        for item in rows:
            fact = resolve(item)
            item.update(state(item,at=at,display_zone=tz))
            item['display_timezone'] = str(tz)
            item['precision_label'] = {'date':'日期','minute':'精确时间','range':'范围','unknown':'未知'}[item['precision']]
            related = [task for task in tasks[item['event_id']] if not task['deadline_id'] or task['deadline_id'] == item['id']]
            item['tasks'] = [task for task in related if task['status'] != 'done']
            item['remaining_task_count'] = sum(task['status'] != 'done' for task in tasks[item['event_id']])
            item['evidence'] = evidence[(item['event_id'],f"deadlines.{item['id']}.value",item['source_snapshot_id'] or item['raw_snapshot_id'])]
            if fact:
                first,last = fact.days(tz)
                item['start_date'], item['end_date'] = first.isoformat(),last.isoformat()
                item['start_at'] = None if fact.all_day else fact.start.astimezone(tz).isoformat()
                item['end_at'] = None if fact.all_day else fact.end.astimezone(tz).isoformat()
        return rows

    def upcoming(self, days=7, *, include_terminal=False, at=None):
        if days not in {7,14,30}:
            raise ValueError('Upcoming supports 7, 14 or 30 days')
        at = aware(at or utc_now())
        today = at.astimezone(system_zone()).date()
        end = today + timedelta(days=days)
        items = [item for item in self.deadlines(include_terminal=include_terminal,at=at)
                 if item['state'] != 'needs_confirmation' and item['state'] != 'overdue'
                 and item['start_date'] < end.isoformat() and item['end_date'] >= today.isoformat()]
        items.sort(key=lambda item:(item['start_date'],item['start_at'] or '',item['event_id'],item['id']))
        return self._result(items,at,days=days)

    def today(self, *, include_terminal=False, at=None):
        at = aware(at or utc_now())
        tz = system_zone()
        today = at.astimezone(tz).date()
        reminder_ids = set()
        with self.store.connection() as db:
            for row in db.execute("SELECT deadline_id,scheduled_at,fired_at,snoozed_until,status FROM reminder WHERE status NOT IN ('cancelled','dismissed')"):
                values = (row['snoozed_until'] or row['scheduled_at'],row['fired_at'])
                if any(value and datetime.fromisoformat(value).astimezone(tz).date() == today for value in values):
                    reminder_ids.add(row['deadline_id'])
        items = []
        for item in self.deadlines(include_terminal=include_terminal,at=at):
            if item['state'] == 'needs_confirmation':
                continue
            reasons = []
            if item['state'] == 'overdue':
                reasons.append(item['relative_label'])
            elif item['state'] == 'today':
                reasons.append('今天截止' if item['end_date'] == today.isoformat() else '今天在日期范围内')
            if item['id'] in reminder_ids:
                reasons.append('今天有提醒')
            if reasons:
                items.append({**item,'reasons':reasons})
        rank = {'overdue':0,'today':1,'upcoming':2}
        items.sort(key=lambda item:(rank[item['state']],item['end_date'],item['end_at'] or '',item['event_id'],item['id']))
        return self._result(items,at)

    def month(self, year, month, *, include_terminal=False, at=None):
        first = date(year,month,1)
        end = date(year+1,1,1) if month == 12 else date(year,month+1,1)
        items = [item for item in self.deadlines(include_terminal=include_terminal,at=at)
                 if item['state'] != 'needs_confirmation' and item['start_date'] < end.isoformat() and item['end_date'] >= first.isoformat()]
        items.sort(key=lambda item:(item['start_date'],item['start_at'] or '',item['event_id'],item['id']))
        return self._result(items,at or utc_now(),year=year,month=month)

    @staticmethod
    def _result(items,at,**extra):
        return {'items':items,'total':len(items),'timezone':str(system_zone()),
                'today':at.astimezone(system_zone()).date().isoformat(),**extra}
