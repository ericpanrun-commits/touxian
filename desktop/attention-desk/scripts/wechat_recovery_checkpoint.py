"""Read-only pre-activation backup and formal-domain fingerprints; no credentials."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.personal_store import default_database_path


def main():
    directory = ROOT / 'artifacts/p0-20261104' / ('checkpoint-' + datetime.now(timezone.utc).strftime('%H%M%S'))
    directory.mkdir()
    source = default_database_path()
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as incoming:
        incoming.row_factory = sqlite3.Row
        incoming.execute('PRAGMA query_only=ON')
        with closing(sqlite3.connect(directory / 'personal.sqlite3')) as target:
            incoming.backup(target)
        hashes = {}
        counts = {}
        for table in ('event','task','course','review_artifact','raw_item','raw_snapshot','wechat_message','wechat_llm_attempt'):
            rows = [dict(r) for r in incoming.execute('SELECT * FROM ' + table + ' ORDER BY id')]
            counts[table] = len(rows)
            hashes[table] = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    certificate = ROOT / 'artifacts/wechat-db0/results/report.json'
    shutil.copyfile(certificate, directory / 'report-before.json')
    shutil.copyfile(source.with_name('wechat.yaml'), directory / 'wechat-before.yaml')
    result = {'at': datetime.now(timezone.utc).isoformat(), 'directory': str(directory),
              'counts': counts, 'sha256': hashes,
              'certificate_before': json.loads(certificate.read_text(encoding='utf-8'))['wechat_version']}
    (directory / 'checkpoint.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    (ROOT / 'artifacts/p0-20261104/checkpoint.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
