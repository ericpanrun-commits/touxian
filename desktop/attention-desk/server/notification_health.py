"""Existing health observer input from worker state and its durable queue."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3


def snapshot(directory,at):
    directory=Path(directory)
    try: config=json.loads((directory/'notification-automation.json').read_text('utf-8'))
    except FileNotFoundError:return {'state':'not_enabled','alert_required':False}
    except (OSError,ValueError):return {'state':'invalid_configuration','alert_required':True}
    if config.get('enabled') is not True:return {'state':'paused','alert_required':False}
    try:
        holds=json.loads((directory/'recovery-controls.json').read_bytes())
        if holds.get('pause_paid_ai') is True or holds.get('hold_cloud_uploads') is True:
            return {'state':'globally_paused','alert_required':False}
    except (OSError,ValueError):return {'state':'invalid_global_controls','alert_required':True}
    lower=datetime.fromisoformat(config['not_before'])
    problems=[];states={}
    for name,path,key in [('analysis',directory/'deepseek-worker-state.json','last_tick'),
                          ('sync',directory/'cloud-sync/notification-automation/status.json','at')]:
        try:
            state=json.loads(path.read_text('utf-8'));states[name]=state
            age=(at-datetime.fromisoformat(state[key])).total_seconds()
            if age>300:problems.append(name+'_heartbeat_stale')
            if state.get('alert_required') or state.get('state') in {'automatic_sync_error','local_worker_error','automation_approval_required','automation_release_changed'}:
                problems.append(name+'_requires_attention')
        except (OSError,ValueError,KeyError):
            if (at-lower).total_seconds()>300:problems.append(name+'_heartbeat_missing')
    pending=0;blocked=0
    runtime=directory/'cloud-sync/notification-automation/runtime.sqlite3'
    if runtime.is_file():
        try:
            with sqlite3.connect(runtime.resolve().as_uri()+'?mode=ro',uri=True) as c:
                rows=c.execute('SELECT state,count(*),min(checked) FROM jobs GROUP BY state').fetchall()
            pending=sum(n for state,n,_ in rows if state not in {'acknowledged','held'})
            blocked=sum(n for state,n,_ in rows if state in {'blocked','delivery_attempt_limit_reached','credential_revoked','held'})
            if blocked:problems.append('sync_records_held')
            if any(state not in {'acknowledged','held'} and at.timestamp()-oldest>=900 for state,_,oldest in rows):
                problems.append('sync_backlog_stale')
        except sqlite3.Error:problems.append('sync_queue_unavailable')
    try:
        supervisor=json.loads((directory/'notification-supervisor-state.json').read_text('utf-8'))
        if any(v.get('alert_required') for v in supervisor.values()):problems.append('worker_restarts_exhausted')
    except (OSError,ValueError):pass
    return {'state':'attention_required' if problems else 'running','alert_required':bool(problems),
            'problems':problems,'pending_sync':pending,'held_sync':blocked,
            'analysis':{k:states.get('analysis',{}).get(k) for k in ('state','calls','parsed','created','pending_runnable','stalled_cycles')},
            'sync':{k:states.get('sync',{}).get(k) for k in ('state','results')}}
