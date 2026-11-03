"""Cross-scan identity uses ordered overlap, never a UUID-containing hash."""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from .models import Observation, ObservedMessage, Reconciliation


def signature(message: ObservedMessage):
    text = message.text.replace('\r\n', '\n').replace('\r', '\n')
    return hashlib.sha256(json.dumps([message.sender, message.message_type, text, message.attachment_identity], ensure_ascii=False).encode()).hexdigest()


def reconcile(previous: list[dict], observation: Observation):
    result = Reconciliation()
    if not observation.covers_tail:
        result.reason = 'tail_not_reached'
        return result
    if not previous:
        if observation.bootstrap_complete:
            result.new_indices = list(range(len(observation.messages)))
            result.complete, result.reason = True, 'bootstrap_boundary'
        else:
            result.reason = 'bootstrap_boundary_unknown'
        return result
    old = [p['content_signature'] for p in previous]
    new = [signature(m) for m in observation.messages]
    counts = Counter(old)
    choices = []
    for length in range(min(len(old), len(new)), 0, -1):
        suffix = old[-length:]
        # Need a distinctive neighbour. A run of acknowledgements cannot prove identity.
        strong = any(counts[p['content_signature']] == 1 and
                     (len(p['raw_text']) >= 8 or p.get('time_reliability') == 'reliable' or p.get('attachment_identity'))
                     for p in previous[-length:])
        if not strong:
            continue
        choices = [i for i in range(len(new) - length + 1) if new[i:i + length] == suffix and all(
            not (old_message.get('time_reliability')=='reliable' and current.time_reliability=='reliable')
            or old_message.get('source_time')==current.source_time
            for old_message,current in zip(previous[-length:],observation.messages[i:i+length]))]
        if choices:
            if len(choices) != 1:
                result.reason = 'identity_ambiguous'
                return result
            start = choices[0]
            result.matched = {start + j: p['id'] for j, p in enumerate(previous[-length:])}
            result.new_indices = list(range(start + length, len(new)))
            result.complete, result.reason = True, 'ordered_overlap'
            return result
    result.reason = 'missing_or_ambiguous_anchor'
    return result
