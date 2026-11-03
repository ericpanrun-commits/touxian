"""Run only the bounded read-only probe; report never contains chat text."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.wechat_collect.spike import inspect_structure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--chat-name',help='Explicit test group; bounded exact queries for spaces/bracket variants, no message reads')
    args = parser.parse_args()
    report = inspect_structure(chat_name=args.chat_name)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'level': report['level'], 'reason': report['level_reason'],
                      'real_operations': report['real_operations'], 'report': str(args.report)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
