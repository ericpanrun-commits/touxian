"""Check local reference mapping for the approved seven texts; never call an API."""
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
evaluation = runpy.run_path(str(ROOT/'scripts/deepseek-reviewed-evaluation.py'))
from server.wechat_collect.deepseek_projection import prepare_reviewed_projection


def main():
    # Recheck the exact approved manifest and current read-only source hashes.
    samples = evaluation['prepare']()
    labels = json.loads((evaluation['LABELS']/'private-label-map.json').read_text(encoding='utf8'))
    settings = evaluation['AISettings'](evaluation['DATA']/'ai-settings.json')
    config = settings._read()[1].model_copy(update={'base_url':'https://api.deepseek.com', 'thinking':False})
    results = []
    with sqlite3.connect((evaluation['DATA']/'personal.sqlite3').as_uri()+'?mode=ro', uri=True) as connection:
        connection.execute('PRAGMA query_only=ON')
        for sample in samples:
            label = next(row for row in labels if row['sample'] == sample['id'])
            original = connection.execute('SELECT raw_text FROM wechat_message WHERE id=?', (label['message_id'],)).fetchone()[0]
            batch, snapshot, context = evaluation['inputs'](sample)
            request = evaluation['build_text_request'](config, batch, snapshot, context)
            fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            receipt = json.loads((evaluation['OUT']/(fingerprint+'.json')).read_text(encoding='utf8'))
            snapshot['blocks'][0]['text'] = original
            snapshot['wechat']['messages'][0]['raw_text'] = original
            projection = prepare_reviewed_projection(batch, snapshot, samples)
            parsed = json.loads(receipt['raw'])
            parsed['batch_id'] = projection.batch['id']
            restored, _ = projection.restore(parsed, context)
            results.append({'sample':sample['id'], 'status':'passed',
                            'mapped_evidence':len(restored['evidence'])})
    report = {'network_requests':0, 'production_writes':0, 'results':results}
    evaluation['save'](evaluation['OUT']/'projection-summary.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
