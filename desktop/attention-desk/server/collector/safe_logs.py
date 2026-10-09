"""Local diagnostic locations, without exception values, URLs, tokens or bodies."""
import json
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.Lock()


def record(personal, phase, error):
    data = {'at': datetime.now(timezone.utc).isoformat(), 'phase': phase, 'exception': type(error).__name__,
            'frames': [{'file': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
                       for frame in traceback.extract_tb(error.__traceback__)[-12:]]}
    try:
        target = personal.path.parent / 'logs/mobile-collector.log'
        target.parent.mkdir(parents=True, exist_ok=True)
        with _lock, target.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(data, ensure_ascii=False) + '\n')
    except OSError:
        pass
