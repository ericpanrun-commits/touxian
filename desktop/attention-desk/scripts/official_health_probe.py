"""Read-only smoke check using the existing separately revocable worker identity."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.cloud_sync.settings import WorkerSettings, ENDPOINT
from server.personal_store import default_database_path
import httpx


def main():
    config = WorkerSettings(default_database_path().with_name('cloud-worker-settings.json')).read()
    if not config or not config['enabled']:
        raise SystemExit('existing_worker_not_enabled')
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        ready = client.get(ENDPOINT + '/health')
        denied = client.get(ENDPOINT + '/worker/official-health')
        result = client.get(ENDPOINT + '/worker/official-health', headers={'X-Eric-Worker-Key': config['worker_key']})
    value = result.json()
    if result.status_code != 200:
        print(json.dumps({'status': result.status_code, 'error': (value.get('detail') or {}).get('code'),
                          'public_health_status': ready.status_code, 'unauthenticated_status': denied.status_code}))
        return 1
    report = {'endpoint': ENDPOINT + '/worker/official-health', 'status': result.status_code,
        'public_health_status': ready.status_code, 'unauthenticated_status': denied.status_code,
        'health': value, 'source_count': len(value.get('sources', [])), 'production_data_writes': 0}
    (ROOT/'artifacts/p0-20261104/official-health-live.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('status','public_health_status','unauthenticated_status','source_count','production_data_writes')}))
    return 0 if report['source_count'] > 0 and denied.status_code == 401 and ready.status_code == 200 else 1


if __name__ == '__main__':
    raise SystemExit(main())
