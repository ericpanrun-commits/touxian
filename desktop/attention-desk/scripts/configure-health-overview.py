"""Run manually with the desktop Python environment. Never prints a token."""
import getpass
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.ai_settings import protect
from server.health_overview import config_path


def main():
    if not sys.stdin.isatty():
        raise SystemExit('Run in an interactive Windows terminal so the token stays masked.')
    mode = input('Health mode (trend / summary / off): ').strip()
    if mode not in {'trend', 'summary', 'off'}:
        raise SystemExit('Invalid mode.')
    token = getpass.getpass('Desktop-only Overview token: ') if mode != 'off' else ''
    if mode != 'off' and not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise SystemExit('Use an independent 32–256 character base64url token.')
    path = config_path()
    # Encrypt before creating any file. Failure leaves existing configuration intact.
    value = {'mode': mode, 'token_dpapi': protect(token)}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)
    print('Health Overview configuration saved for the current Windows user.')


if __name__ == '__main__':
    main()
