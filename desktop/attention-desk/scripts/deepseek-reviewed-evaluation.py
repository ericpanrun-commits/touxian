"""Explicit, isolated evaluation of the seven previously labelled/reviewed texts.

No production writes or global hold changes. A request fingerprint is consumed
before dispatch; rerunning revalidates saved responses without another API call.
This is not permission to process arbitrary messages or enable cloud uploads.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.ai_settings import AISettings
from server.ai_transport import DeepSeekTransport
from server.extraction_errors import ExtractionFailure
from server.user_context import UserContext
from server.wechat_collect.analysis import build_text_request, validate_output

LABELS = ROOT/'artifacts/p0-20261104/ai-screening-acceptance-20261108'
DATA = ROOT.parent/'data/attention-desk'
OUT = ROOT/'artifacts/deepseek-reviewed-20261108'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def verify_reviewed_inputs(samples, path):
    approved = json.loads(path.read_text(encoding='utf8'))
    if samples != approved:
        raise ValueError('approved_reviewed_inputs_changed')


def prepare():
    labels = json.loads((LABELS/'private-label-map.json').read_text(encoding='utf8'))
    assert len(labels) == 7
    samples = []
    with sqlite3.connect((DATA/'personal.sqlite3').as_uri()+'?mode=ro', uri=True) as c:
        c.execute('PRAGMA query_only=ON')
        for label in labels:
            row = c.execute('SELECT raw_text,source_time,time_reliability,message_type FROM wechat_message WHERE id=?', (label['message_id'],)).fetchone()
            if not row or digest(row[0]) != label['text_sha256_now'] or row[3] != 'text':
                raise ValueError('reviewed_sample_changed')
            text = row[0]
            # Only the inspected first sample contains contact lines. Preserve
            # all other content, including audience/role and schedule constraints.
            if label['sample'] == 'confirmed-01':
                text = '\n'.join(line for line in text.splitlines()
                    if not re.search(r'1[3-9]\d{9}|活动联系人', line))
            if re.search(r'1[3-9]\d{9}|wxid_|https?://|[\w.+-]+@[\w.-]+\.[a-z]{2,}', text, re.I):
                raise ValueError('unreviewed_contact_remaining')
            samples.append({'id':label['sample'], 'text':text, 'source_time':row[1],
                            'time_reliability':row[2], 'original_sha256':label['text_sha256_now']})
    verify_reviewed_inputs(samples, OUT/'reviewed-inputs.json')
    return samples


def inputs(sample):
    identity = sample['id']
    block = {'block_id':'b1', 'text':sample['text'], 'source_type':'text', 'needs_review':False}
    message = {'message_id':'m1', 'block_id':'b1', 'raw_text':sample['text'],
               'sender':None, 'source_time':sample['source_time'],
               'time_reliability':sample['time_reliability'], 'message_type':'text'}
    return ({'id':identity, 'primary_ids':['m1']},
            {'blocks':[block], 'wechat':{'messages':[message], 'warnings':[]}},
            UserContext().model_dump())


async def main(execute, *, replay_only=False):
    OUT.mkdir(parents=True, exist_ok=True)
    samples = prepare()
    settings = AISettings(DATA/'ai-settings.json')
    config = settings._read()[1]
    # Scope the permitted endpoint independently from the production config.
    config = config.model_copy(update={'base_url':'https://api.deepseek.com', 'thinking':False})
    save(OUT/'reviewed-inputs.json', samples)
    if not execute:
        print(json.dumps({'prepared':len(samples), 'model':config.model, 'requests':0}))
        return
    # User authorized paid testing in this goal. This process-specific control
    # does not release production queues or cloud uploads.
    controls = OUT/'evaluation-controls.json'
    save(controls, {'version':1, 'pause_paid_ai':False, 'hold_cloud_uploads':True})
    os.environ['ATTENTION_RECOVERY_CONTROL_PATH'] = str(controls)
    key = None if replay_only else settings.resolved()[1]
    results = []
    failures = 0
    for sample in samples:
        batch, snapshot, context = inputs(sample)
        body = build_text_request(config, batch, snapshot, context)
        fingerprint = digest(json.dumps(body, sort_keys=True, ensure_ascii=False))
        journal = OUT/(fingerprint+'.json')
        if journal.exists():
            result = json.loads(journal.read_text(encoding='utf8'))
        else:
            if replay_only:
                raise ValueError('saved_response_required_no_network_allowed')
            result = {'sample':sample['id'], 'fingerprint':fingerprint, 'state':'outcome_unknown'}
            # Exclusive creation also prevents concurrent runs dispatching twice.
            with journal.open('x', encoding='utf8') as f:
                json.dump(result, f)
                f.flush()
                os.fsync(f.fileno())
            try:
                response = await DeepSeekTransport().request(body, config, key, attempts=1)
                result.update(state='received', raw=response.raw_output, usage=response.usage)
            except ExtractionFailure as exc:
                result.update(error=exc.code)
            save(journal, result)
        if result.get('state') == 'received':
            for field in ('parsed','warnings','validation_error'):
                result.pop(field, None)
            try:
                parsed, warnings = validate_output(result['raw'], batch, snapshot, context, require_notice=True)
                result.update(validation='passed', parsed=parsed, warnings=warnings)
            except ExtractionFailure as exc:
                result.update(validation='failed', validation_error=exc.code)
            save(journal, result)
        summary = {k:result[k] for k in ('sample','state','usage','validation','validation_error','error','warnings') if k in result}
        summary['items'] = [{k:i[k] for k in ('is_school_notice','title','category','attention_level','action_required','relevant','start_time','deadline','actions')} for i in result.get('parsed',{}).get('items',[])]
        results.append(summary)
        print(json.dumps({k:v for k,v in summary.items() if k != 'items'}, ensure_ascii=True), flush=True)
        failures = failures+1 if result['state'] != 'received' else 0
        if failures >= 2:
            break
    save(OUT/'results.json', results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--replay-only', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.execute or args.replay_only, replay_only=args.replay_only))
