"""Local-only, read-only HealthData adapter. No database, sync or worker jobs."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

ORIGIN = 'https://health-analyzer.healthdash26.workers.dev'
PATH = '/api/integrations/eric-os/overview'
LIMIT = 32 * 1024
METRICS = {'sleep_duration_hours': '小时', 'steps': '步', 'resting_heart_rate_bpm': 'bpm',
           'hrv_rmssd_ms': 'ms', 'active_energy_kcal': 'kcal', 'exercise_minutes': '分钟'}


def fail(status=503, code='health_unavailable', message='暂时无法读取 HealthData，请稍后刷新或打开原站。'):
    raise HTTPException(status, {'code': code, 'message': message}, headers={'Cache-Control': 'private, no-store'})


def config_path():
    from .personal_store import default_database_path
    return default_database_path().parent / 'health-overview.json'


def configuration():
    from .ai_settings import protect
    try:
        value = json.loads(config_path().read_text(encoding='utf-8'))
        token = protect(value['token_dpapi'], decrypt=True)
        mode = value.get('mode', 'off')
        if mode not in {'trend', 'summary'} or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
            fail()
        return token, mode
    except Exception as error:
        if isinstance(error, HTTPException):
            raise
        fail()


def local_access(request: Request, response: Response):
    from .runtime import RuntimeConfig
    runtime = RuntimeConfig.from_env()
    try:
        local_peer = ipaddress.ip_address(request.client.host).is_loopback
        server_port = request.scope['server'][1]
        allowed_ports = {server_port, int(os.environ.get('ATTENTION_WEB_PORT', '5173'))}
        host_ok = request.url.hostname in {'127.0.0.1', 'localhost', '::1'} and (request.url.port or 80) == server_port
        origin = request.headers.get('origin')
        parsed = urlsplit(origin) if origin else None
        origin_ok = not parsed or (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'} and
                                   not parsed.username and not parsed.password and not parsed.path and not parsed.query and
                                   not parsed.fragment and (parsed.port or 80) in allowed_ports)
    except (ValueError, AttributeError, KeyError, TypeError):
        local_peer = host_ok = origin_ok = False
    if runtime.role != 'desktop' or runtime.environment != 'development' or not local_peer or not host_ok or not origin_ok or request.headers.get('sec-fetch-site') == 'cross-site':
        fail(403, 'local_only', '请在这台 Windows 电脑的本地工作台查看 Health。')
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'


def validate_overview(value):
    """Whitelist projection; schema semantics mirror health_contract.js."""
    def require(condition):
        if not condition:
            raise ValueError('invalid_health_overview')
    def day(value):
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    def instant(value):
        if value is None:
            return True
        return isinstance(value, str) and len(value) <= 64 and 'T' in value and datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None
    def numeric(value):
        return value is None or type(value) in (int, float) and math.isfinite(value)
    require(isinstance(value, dict) and value['schema'] == 'health-overview.v1' and value['timezone'] == 'Asia/Shanghai' and value['mode'] in {'trend', 'summary'})
    for key in ('source_generated_at', 'received_at', 'fetched_at'):
        require(instant(value[key]))
    require(value['fetched_at'] is not None and (value['latest_health_date'] is None or day(value['latest_health_date'])))
    window = value['window']
    require(window['days'] == 7 and day(window['start']) and day(window['end']) and (date.fromisoformat(window['end']) - date.fromisoformat(window['start'])).days == 6)
    require(isinstance(value['metrics'], list) and len(value['metrics']) == 6)
    metrics = []
    for item, (metric, unit) in zip(value['metrics'], METRICS.items()):
        require(item['id'] == metric and item['unit'] == unit and numeric(item['latest_value']))
        require((item['latest_date'] is None) == (item['latest_value'] is None))
        if item['latest_date'] is not None:
            require(day(item['latest_date']) and 0 <= (date.fromisoformat(window['end']) - date.fromisoformat(item['latest_date'])).days < 30)
        out = {key: item[key] for key in ('id', 'unit', 'latest_value', 'latest_date')}
        if value['mode'] == 'trend':
            require(isinstance(item['days'], list) and len(item['days']) == 7)
            rows = []
            for index, row in enumerate(item['days']):
                require(day(row['date']) and (date.fromisoformat(row['date']) - date.fromisoformat(window['start'])).days == index and numeric(row['value']) and row['status'] in {'complete', 'partial', 'missing'})
                require(row['status'] != 'missing' or row['value'] is None)
                rows.append({key: row[key] for key in ('date', 'value', 'status')})
            require(item['chart']['view_box'] == [0, 0, 320, 120] and isinstance(item['chart']['points'], list) and len(item['chart']['points']) <= 7)
            points, seen = [], set()
            for point in item['chart']['points']:
                i = point['index']
                require(type(i) is int and 0 <= i < 7 and i not in seen and rows[i]['value'] is not None)
                require(type(point['x']) in (int, float) and math.isfinite(point['x']) and 0 <= point['x'] <= 320 and type(point['y']) in (int, float) and math.isfinite(point['y']) and 0 <= point['y'] <= 120)
                seen.add(i)
                points.append({key: point[key] for key in ('index', 'x', 'y')})
            require(len(seen) == sum(row['value'] is not None and row['status'] != 'missing' for row in rows))
            out.update(days=rows, chart={'view_box': [0, 0, 320, 120], 'points': points})
        metrics.append(out)
    analysis = value['analysis']
    require(analysis['state'] in {'idle', 'pending', 'analyzing', 'completed', 'failed', 'empty', 'unknown'} and type(analysis['source_matches']) is bool and instant(analysis['completed_at']))
    require(analysis['source_matches'] or analysis['state'] in {'empty', 'unknown'})
    require(analysis['state'] == 'completed' or analysis['completed_at'] is None)
    return {**{key: value[key] for key in ('schema', 'mode', 'timezone', 'source_generated_at', 'received_at', 'latest_health_date', 'fetched_at')},
            'window': {key: window[key] for key in ('start', 'end', 'days')}, 'metrics': metrics,
            'analysis': {key: analysis[key] for key in ('state', 'source_matches', 'completed_at')}}


async def read_overview():
    token, mode = configuration()
    try:
        async with asyncio.timeout(8):
            async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                async with client.stream('GET', ORIGIN + PATH, params={'view': mode}, headers={'Authorization': 'Bearer ' + token}) as upstream:
                    if upstream.status_code != 200 or 'application/json' not in upstream.headers.get('content-type', '') or int(upstream.headers.get('content-length', '0')) > LIMIT:
                        fail()
                    body = bytearray()
                    async for chunk in upstream.aiter_bytes(chunk_size=8192):
                        body.extend(chunk)
                        if len(body) > LIMIT:
                            fail()
                    result = validate_overview(json.loads(body))
                    if result['mode'] != mode:
                        fail()
                    return result
    except HTTPException:
        raise
    except Exception:
        fail()


router = APIRouter(prefix='/api/personal/health', tags=['local-health'], dependencies=[Depends(local_access)])


@router.get('/overview')
async def overview():
    return await read_overview()
