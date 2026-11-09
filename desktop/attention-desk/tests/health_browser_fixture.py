"""Synthetic-only local HTTP fixture. Does not import server.main or start jobs."""
import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse

from server import health_overview as health

app = FastAPI()
app.include_router(health.router)
fixture = json.loads((Path(__file__).parent / 'health-overview.synthetic.json').read_text(encoding='utf-8-sig'))
today = datetime.now(timezone(timedelta(hours=8))).date()
offset = today - datetime.fromisoformat(fixture['window']['end']).date()
fixture['window']['start'] = (datetime.fromisoformat(fixture['window']['start']).date() + offset).isoformat()
fixture['window']['end'] = today.isoformat()
fixture['latest_health_date'] = today.isoformat()
for key in ('source_generated_at', 'received_at', 'fetched_at'):
    fixture[key] = datetime.now(timezone.utc).isoformat()
for metric in fixture['metrics']:
    metric['latest_date'] = (datetime.fromisoformat(metric['latest_date']).date() + offset).isoformat()
    for row in metric['days']:
        row['date'] = (datetime.fromisoformat(row['date']).date() + offset).isoformat()

state = {'mode': 'trend', 'reads': 0, 'run_id': os.environ.get('ERIC_HEALTH_TEST_RUN', '')}
native = httpx.AsyncClient


def upstream(request):
    # The real desktop adapter runs, with only its remote network transport replaced.
    state['reads'] += 1
    assert str(request.url).startswith(health.ORIGIN + health.PATH)
    value = copy.deepcopy(fixture)
    if state['mode'] == 'failure':
        return httpx.Response(503, json={'error': 'synthetic_failure'})
    if state['mode'] == 'summary':
        value['mode'] = 'summary'
        for metric in value['metrics']:
            metric.pop('days')
            metric.pop('chart')
    return httpx.Response(200, json=value)


health.configuration = lambda: ('D' * 43, 'summary' if state['mode'] == 'summary' else 'trend')
health.httpx.AsyncClient = lambda **kwargs: native(**kwargs, transport=httpx.MockTransport(upstream))


@app.post('/__test__/mode/{mode}')
def mode(mode: str):
    assert mode in {'trend', 'summary', 'failure'}
    state['mode'] = mode
    return {'ok': True}


@app.get('/__test__/state')
def status():
    return state


@app.get('/api/{path:path}')
def unrelated(path: str):
    return {'status': 'ok', 'items': [], 'sources': [], 'total': 0}


dist = Path(__file__).resolve().parents[1] / 'dist'
if os.environ.get('HEALTH_TEST_PWA') == '1':
    dist = dist / 'client'


@app.get('/{path:path}')
def frontend(path: str):
    file = (dist / path).resolve()
    if not file.is_relative_to(dist) or not file.is_file():
        file = dist / 'index.html'
    return FileResponse(file)
