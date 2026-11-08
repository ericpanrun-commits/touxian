"""Paid API evaluation using only the existing invented benchmark messages.

Never reads a message database, real labels, profile, or reviewed-inputs.json.
Reuses the isolated request journal and production request/output validators.
"""
import argparse
import ast
import asyncio
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]


def synthetic_cases():
    source = ROOT/'artifacts/p0-20261104/local-model-evaluation/benchmark_offline.py'
    tree = ast.parse(source.read_text(encoding='utf8'))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'CASES' for target in node.targets)]
    if len(assignments) != 1:
        raise ValueError('synthetic_cases_missing')
    cases = ast.literal_eval(assignments[0].value)
    if len(cases) != 20 or any(len(row) != 5 for row in cases):
        raise ValueError('synthetic_cases_changed')
    return cases


def configured_evaluator():
    module = runpy.run_path(str(ROOT/'scripts/deepseek-reviewed-evaluation.py'))
    namespace = module['main'].__globals__
    cases = synthetic_cases()
    namespace['OUT'] = ROOT/'artifacts/deepseek-synthetic-20261108'
    # Override preparation BEFORE invoking main: the database-reading function
    # is never called. Only ast.literal_eval of the fictional CASES is used.
    namespace['prepare'] = lambda: [dict(id=row[0], text=row[1],
        source_time='2026-11-08T10:00:00+08:00', time_reliability='reliable') for row in cases]
    original_inputs = namespace['inputs']
    def inputs(sample):
        batch, snapshot, context = original_inputs(sample)
        context.update(entry_year=2026, class_name='一班',
            role_assertions={role:{'value':'false','confirmed':True}
                for role in ('班长','团支书','学习委员','教师','辅导员')},
            confirmed_fields=['entry_year','class_name','role_assertions'])
        return batch, snapshot, context
    namespace['inputs'] = inputs
    return namespace


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--replay-only', action='store_true')
    args = parser.parse_args()
    namespace = configured_evaluator()
    asyncio.run(namespace['main'](args.execute or args.replay_only, replay_only=args.replay_only))
    print(json.dumps({'synthetic_only':True, 'real_messages_read':0, 'real_messages_sent':0}))
