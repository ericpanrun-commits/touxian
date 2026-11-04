"""One existing health-monitor tick over live read-only data; no collector starts.

Default is dry-run. --notify sends at most the monitor's deduplicated Windows
incident alert, using real current failures rather than injecting a test incident.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_backlog_plan import ReadOnlyStore
from server.personal_store import default_database_path
from server.wechat_collect.config import ConfigStore
from server.wechat_collect.health import CollectionHealthMonitor
from server.cloud_sync.settings import WorkerSettings, ENDPOINT
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notify', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    personal = ReadOnlyStore(default_database_path())
    document = ConfigStore(personal.path.with_name('wechat.yaml')).read()
    monitor = CollectionHealthMonitor(SimpleNamespace(personal=personal, document=document))
    health = monitor.snapshot()
    health['sync'] = {'state': 'not_checked_by_oneoff_probe', 'pending_records': None}
    config = WorkerSettings(personal.path.with_name('cloud-worker-settings.json')).read()
    if config and config['enabled']:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.get(ENDPOINT + '/worker/official-health', headers={'X-Eric-Worker-Key': config['worker_key']})
        if response.status_code == 200:
            official = response.json()
            checked = datetime.fromisoformat(official['checked_at'])
            if official.get('state') != 'observed' or abs((datetime.now(timezone.utc) - checked).total_seconds()) > 900:
                raise SystemExit('official_health_not_current')
            health['official'] = official
        else:
            health['official'] = {'state': 'observation_failed', 'sources': []}
    sources = [*health['sources'], *health.get('official', {}).get('sources', [])]
    before_claim = json.loads(monitor.ledger.read_text(encoding='utf-8')).get('last_claim_at') if monitor.ledger.exists() else None
    if args.notify:
        monitor.deliver(health)
    ledger = json.loads(monitor.ledger.read_text(encoding='utf-8')) if monitor.ledger.exists() else None
    report = {'dry_run': not args.notify, 'health': health, 'alert_sources': sum(row['alert_required'] for row in sources),
        'delivery': ledger, 'new_delivery_claim': bool(ledger and ledger.get('last_claim_at') != before_claim),
        'business_database_writes': 0, 'model_http_requests': 0, 'collector_started': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('dry_run','alert_sources','new_delivery_claim','business_database_writes','model_http_requests','collector_started')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
