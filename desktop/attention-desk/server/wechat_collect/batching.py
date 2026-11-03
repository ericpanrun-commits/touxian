"""Bounded ordered context. Previously analysed messages only appear as context."""
from dataclasses import dataclass
from datetime import datetime
import re

from .filtering import DEPENDENT


@dataclass(frozen=True)
class BatchPlan:
    primary: tuple[str, ...]
    context: tuple[str, ...]
    wait_for_context: bool
    context_truncated: bool = False


def build_batches(messages, primary_ids, *, max_messages=40, max_chars=12000):
    primary_ids = set(primary_ids)
    plans, primary, context, count = [], [], [], 0
    context_truncated=False
    preceding = []
    previous_time = None
    def flush():
        nonlocal primary, context, count, context_truncated
        if primary:
            tail = next(m for m in reversed(messages) if m['id'] == primary[-1])
            waiting = bool(DEPENDENT.search(tail['raw_text']) or tail['message_type'] == 'image')
            if re.search(r'紧急|立刻|马上|停课|调课',tail['raw_text']):
                waiting=False
            plans.append(BatchPlan(tuple(primary), tuple(context), waiting,context_truncated))
        primary, context, count = [], [], 0
        context_truncated=False
    for message in messages:
        current_time = None
        if message.get('source_time') and message.get('time_reliability') == 'reliable':
            try:
                current_time = datetime.fromisoformat(message['source_time'])
            except ValueError:
                pass
        gap = current_time and previous_time and (current_time - previous_time).total_seconds() > 300
        if primary and (len(primary) + len(context) >= max_messages or count + len(message['raw_text']) > max_chars or gap):
            flush()
        if message['id'] in primary_ids:
            if not primary:
                context = [m['id'] for m in preceding[-8:] if m['id'] not in primary_ids]
                count = sum(len(m['raw_text']) for m in preceding[-8:] if m['id'] in context)
                while context and count+len(message['raw_text'])>max_chars:
                    removed=context.pop(0)
                    count-=len(next(m for m in preceding[-8:] if m['id']==removed)['raw_text'])
                    context_truncated=True
            primary.append(message['id'])
            count += len(message['raw_text'])
        elif primary and len(context) < 10:
            context.append(message['id'])
            count += len(message['raw_text'])
        preceding.append(message)
        previous_time = current_time or previous_time
    flush()
    return plans
