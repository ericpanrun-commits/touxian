"""Local, exact-content permission boundary for reviewed DeepSeek text.

This does not grant permission or claim to anonymize arbitrary chat. Only an
explicitly approved original digest + reviewed text pair may cross this boundary.
No source identifiers, sender, profile, history, attachments or warnings leave it.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from ..extraction_errors import ExtractionFailure
from ..user_context import UserContext


def denied(code='content_authorization_required'):
    raise ExtractionFailure(code, '当前文字不在已核对的数据授权范围内，保留本机待核对。', attempts=0)


def retained_positions(original: str, reviewed: str) -> list[int]:
    """Map whole retained lines; ambiguous removals and rewritten text fail closed.

    A quote crossing a removed line or normalized newline is rejected by restore,
    rather than being represented as a contiguous quotation of the original.
    """
    if original == reviewed:
        return list(range(len(original)))
    source = original.splitlines(keepends=True)
    target = reviewed.split('\n')
    if not reviewed or len(source) > 500:
        denied('projection_mapping_unavailable')
    # Count subsequences, capped at two. Repeated identical lines must not cause
    # us to guess which occurrence the human review retained.
    paths = {0: [()]}
    for index, line in enumerate(source):
        for count in sorted(list(paths), reverse=True):
            if count < len(target) and line.rstrip('\r\n') == target[count]:
                candidates = paths.get(count + 1, []) + [p + (index,) for p in paths[count]]
                paths[count + 1] = candidates[:2]
    matches = paths.get(len(target), [])
    if len(matches) != 1:
        denied('projection_mapping_unavailable')
    offsets, offset = [], 0
    for line in source:
        offsets.append(offset)
        offset += len(line)
    positions = []
    for number, index in enumerate(matches[0]):
        if number:
            previous = matches[0][number - 1]
            # Only an unchanged, adjacent LF is an original span. Removed lines
            # and CRLF normalization create a discontinuity, never a fake quote.
            positions.append(offsets[index] - 1 if index == previous + 1
                and source[previous].endswith('\n') and not source[previous].endswith('\r\n') else -1)
        positions.extend(range(offsets[index], offsets[index] + len(target[number])))
    return positions


@dataclass(frozen=True)
class ReviewedProjection:
    batch: dict
    snapshot: dict
    context: dict
    original_batch: dict
    original_snapshot: dict
    message_ids: dict
    blocks: dict

    def restore(self, parsed: dict | str, local_context: dict):
        """Validate on sent text first, restore references, then apply local facts."""
        from .analysis import validate_output
        raw=parsed if isinstance(parsed,str) else json.dumps(parsed,ensure_ascii=False)
        checked, warnings = validate_output(raw,
            self.batch, self.snapshot, local_context, require_notice=True)
        # Validation adds local attention bookkeeping, outside the wire schema.
        restored = deepcopy({key:checked[key] for key in ('schema_version','batch_id','items','evidence')})
        # Reapply local routing from the validated pre-routing fields. Feeding
        # an unresolved item's derived "ignore" level back as model intent would
        # incorrectly convert a waiting-for-context item into a filtered item.
        restored['items'] = [deepcopy(checked['_attention_originals'][item['id']]) for item in checked['items']]
        restored['batch_id'] = self.original_batch['id']
        for evidence in restored['evidence']:
            local_id, original, positions = self.blocks[evidence['block_id']]
            span = positions[evidence['start']:evidence['end']]
            if (not span or span[0] < 0 or span != list(range(span[0], span[0] + len(span)))
                    or original[span[0]:span[-1]+1] != evidence['quote']):
                denied('projection_quote_crosses_removed_content')
            evidence.update(block_id=local_id, message_id=self.message_ids[evidence['message_id']],
                            start=span[0], end=span[-1]+1)
        for item in restored['items']:
            item['evidence_message_ids'] = [self.message_ids[mid] for mid in item['evidence_message_ids']]
            times = [d['time'] for d in item['deadlines']]
            times += [item[key] for key in ('start_time','end_time','deadline') if item[key]]
            for value in times:
                value['anchor_message_id'] = self.message_ids[value['anchor_message_id']]
        result, local_warnings = validate_output(json.dumps(restored, ensure_ascii=False),
            self.original_batch, self.original_snapshot, local_context, require_notice=True)
        # Local bookkeeping only: omitted attachments must never be marked as
        # analyzed when the text response is committed or recovered.
        result['_analyzed_message_ids'] = list(self.message_ids.values())
        if set(result['_analyzed_message_ids']) != set(self.original_batch['primary_ids']):
            local_warnings.append('text_only_media_held')
        return result, sorted(set(warnings + local_warnings))


def prepare_reviewed_projection(batch, snapshot, approved_samples, *, text_only=False):
    """Pure preparation. Caller must supply the separately approved manifest.

    Explicitly excludes context-only and media blocks. No environment fallback,
    credential lookup, database mutation or HTTP occurs here.
    """
    approvals = {}
    for sample in approved_samples:
        digest, text = sample['original_sha256'], sample['text']
        if digest in approvals and approvals[digest] != text:
            denied('conflicting_content_approval')
        approvals[digest] = text
    original_blocks = {b['block_id']: b for b in snapshot['blocks']}
    originals = {m['message_id']: m for m in snapshot['wechat']['messages']}
    messages, blocks, ids, mappings = [], [], {}, {}
    primary = batch['primary_ids']
    if not primary or len(primary) != len(set(primary)):
        denied()
    for local_mid in primary:
        message = originals.get(local_mid)
        if text_only and message and message.get('message_type') in {'image', 'file'}:
            continue
        if not message or message.get('message_type') != 'text':
            denied()
        original = message.get('raw_text', '')
        block = original_blocks.get(message['block_id'])
        if not block or block.get('source_type') != 'text' or block.get('text') != original:
            denied('projection_source_mismatch')
        digest = hashlib.sha256(original.encode()).hexdigest()
        reviewed = approvals.get(digest)
        # Time metadata was also approved for the seven-message evaluation.
        if reviewed is None or not any(s['original_sha256'] == digest
                and s.get('source_time') == message.get('source_time')
                and s.get('time_reliability') == message.get('time_reliability') for s in approved_samples):
            denied()
        mid, bid = f'm{len(messages)+1}', f'b{len(messages)+1}'
        positions = retained_positions(original, reviewed)
        ids[mid] = local_mid
        mappings[bid] = (block['block_id'], original, positions)
        messages.append({'message_id':mid, 'block_id':bid, 'raw_text':reviewed,
            'sender':None, 'message_type':'text', 'source_time':message.get('source_time'),
            'time_reliability':message.get('time_reliability'),
            'captured_at':message.get('captured_at')})  # Local validation only; build_messages omits this field.
        blocks.append({'block_id':bid, 'text':reviewed, 'source_type':'text', 'needs_review':False})
    if not messages:
        denied('content_outside_authorized_scope')
    return ReviewedProjection({'id':'batch', 'primary_ids':list(ids)},
        {'blocks':blocks, 'wechat':{'messages':messages, 'warnings':[]}},
        UserContext().model_dump(), deepcopy(batch), deepcopy(snapshot), ids, mappings)
