"""Dry-run the existing WeChat recovery queue, using a genuinely read-only DB."""
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.personal_store import default_database_path
from server.wechat_collect.recovery import backlog_plan


class ReadOnlyStore:
    def __init__(self, path):
        self.path = path.resolve()

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        try:
            connection.execute('BEGIN')
            yield connection
        finally:
            connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=default_database_path())
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = backlog_plan(ReadOnlyStore(args.database), at=datetime.now(timezone.utc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'jobs'}, ensure_ascii=True))
