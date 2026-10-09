"""Conservative consolidation. Never invent evidence or silently choose a date."""
from copy import deepcopy
import re
import unicodedata
from .extraction_chunks import hash_value


def normalized(text):
    return re.sub(r'\s+|[，。；：、,.!?;:]', '', unicodedata.normalize('NFKC', text)).casefold()


def union(a, b):
    return list(dict.fromkeys([*a, *b]))


def merge_candidates(outputs, snapshot):
    evidence, evidence_ids, events = [], {}, {}
    warnings = []
    def warning(code, message):
        value = {'field_path': 'sources', 'code': code, 'message': message}
        if value not in warnings:
            warnings.append(value)
    for chunk_id, original in outputs:
        parsed = deepcopy(original)
        quotes = {}
        for quote in parsed['evidence']:
            key = (quote.get('block_id'), quote['start'], quote['end'], quote['quote'])
            if key not in evidence_ids:
                identifier = 'q_' + hash_value(key)[:24]
                evidence_ids[key] = identifier
                evidence.append({**quote, 'id': identifier})
            quotes[quote['id']] = evidence_ids[key]
        for event in parsed['events']:
            key = (normalized(event['title']), normalized(event['category']))
            event['id'] = 'e_' + hash_value(key)[:24]
            def remap(ids):
                return union([], [quotes[q] for q in ids if q in quotes])
            if event['audience']:
                event['audience']['evidence_ids'] = remap(event['audience']['evidence_ids'])
            for name in ('eligibility', 'materials', 'locations'):
                for fact in event[name]:
                    fact['evidence_ids'] = remap(fact['evidence_ids'])
            action_ids = {}
            for action in event['actions']:
                action_ids[action['id']] = 'a_' + hash_value([key, normalized(action['title']), action['kind']])[:24]
                action['id'] = action_ids[action['id']]
                action['evidence_ids'] = remap(action['evidence_ids'])
            for deadline in event['deadlines']:
                deadline.pop('date_state', None)
                deadline['related_action_ids'] = union([], [action_ids[i] for i in deadline['related_action_ids'] if i in action_ids])
                deadline['evidence_ids'] = remap(deadline['evidence_ids'])
                deadline['id'] = 'd_' + hash_value([key, normalized(deadline['role']), deadline['normalized_date'], deadline['precision'],
                    deadline['relation'], normalized(deadline['original_text']) if deadline['normalized_date'] is None else ''])[:24]
            if key not in events:
                events[key] = event
                continue
            target = events[key]
            if target['participation'] != event['participation']:
                target['participation'] = 'unclear'
                target['uncertainties'] = union(target['uncertainties'], ['不同区域的参与要求不一致，请核对。'])
            if target['notice_kind'] != event['notice_kind']:
                target['notice_kind'] = 'update' if 'update' in (target['notice_kind'], event['notice_kind']) else target['notice_kind']
                target['uncertainties'] = union(target['uncertainties'], ['不同区域的通知类型不一致，请核对变更或取消范围。'])
            if event['summary'] and event['summary'] not in target['summary']:
                summary = '\n'.join(filter(None, [target['summary'], event['summary']]))
                if len(summary) <= 4000:
                    target['summary'] = summary
                else:
                    warning('summary_limit', '摘要容量已达到上限；完整区域候选和原文仍保留。')
            target['uncertainties'] = union(target['uncertainties'], event['uncertainties'])[:30]
            if event['audience']:
                if not target['audience']:
                    target['audience'] = event['audience']
                elif normalized(target['audience']['text']) == normalized(event['audience']['text']):
                    target['audience']['evidence_ids'] = union(target['audience']['evidence_ids'], event['audience']['evidence_ids'])
                else:
                    target['eligibility'].append(event['audience'])
                    warning('audience_conflict', '不同区域的适用人群不同，已保留两处依据，请确认。')
            for name in ('eligibility', 'materials', 'locations', 'actions', 'deadlines'):
                for value in event[name]:
                    existing = next((v for v in target[name] if (v.get('id') and v['id'] == value.get('id')) or
                                     ('text' in v and normalized(v['text']) == normalized(value.get('text', '')))), None)
                    if existing:
                        existing['evidence_ids'] = union(existing['evidence_ids'], value['evidence_ids'])
                        if name == 'deadlines':
                            existing['related_action_ids'] = union(existing['related_action_ids'], value['related_action_ids'])
                            existing['uncertain'] = existing['uncertain'] or value['uncertain']
                    else:
                        target[name].append(value)
    blocks = snapshot.get('blocks') or [{'block_id': None, 'text': snapshot['original_text']}]
    block_offsets, cursor = {}, 0
    for block in blocks:
        block_offsets[block['block_id']] = cursor
        cursor += len(block['text']) + 1
    full_text = '\n'.join(b['text'] for b in blocks)
    changes = list(re.finditer(r'原(?:定|截止)[^。\n]{0,250}?(?:现|延期|延长|调整|改至)[^。\n]{0,250}', full_text))
    by_quote = {q['id']: q for q in evidence}
    def belongs_to_change(deadline, change):
        # A date elsewhere in a long document must not inherit an unrelated
        # extension merely because its month/day and generic role match.
        for identifier in deadline['evidence_ids']:
            quote = by_quote[identifier]
            offset = block_offsets.get(quote.get('block_id'))
            if offset is not None and offset + quote['start'] < change.end() and offset + quote['end'] > change.start():
                return True
        return False
    def month_day(deadline):
        value = deadline.get('normalized_date')
        if value:
            return tuple(map(int, value[5:10].split('-')))
        found = re.search(r'(\d{1,2})月(\d{1,2})日', deadline['original_text'])
        return tuple(map(int, found.groups())) if found else None
    for event in events.values():
        for deadline in event['deadlines']:
            # Reconnect only an exact role/action label, never guess a nearest date.
            if not deadline['related_action_ids']:
                role = re.sub(r'截止|期限|时间|日期', '', normalized(deadline['role']))
                matches = [a['id'] for a in event['actions'] if normalized(a['title']) == role]
                deadline['related_action_ids'] = matches
        for change in changes:
            dates = [tuple(map(int, match)) for match in re.findall(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', change.group())]
            if len(dates) != 2 or dates[0] == dates[1]:
                continue
            old = [d for d in event['deadlines'] if month_day(d) == dates[0] and belongs_to_change(d, change)]
            new = [d for d in event['deadlines'] if month_day(d) == dates[1] and belongs_to_change(d, change)]
            for previous in old:
                for current in new:
                    if normalized(previous['role']) == normalized(current['role']) or set(previous['related_action_ids']) & set(current['related_action_ids']):
                        previous['relation'], current['relation'] = 'previous', 'current'
                        event['notice_kind'] = 'update'
                        warning('superseded_date', '原截止与延期后的日期分别保留；旧日期不默认选中，依据均可核对。')
        for i, left in enumerate(event['deadlines']):
            for right in event['deadlines'][i+1:]:
                if left['relation'] == right['relation'] == 'current' and normalized(left['role']) == normalized(right['role']) and left['normalized_date'] != right['normalized_date']:
                    left['uncertain'] = right['uncertain'] = True
                    warning('date_conflict', '同一阶段存在不同日期，已保留全部候选和依据；请明确选择，不会自动覆盖。')
    # Bound consolidated editing without discarding per-region records or evidence.
    values = list(events.values())
    if len(values) > 32:
        warning('candidate_limit', '事项候选超过单次预览上限；其余候选保存在区域分析记录中，请分批整理。')
        values = values[:32]
    for event in values:
        for field in ('actions','deadlines','eligibility','materials','locations'):
            if len(event[field]) > 100:
                warning('candidate_limit', '某类候选超过单次预览上限；完整候选保存在区域分析记录中。')
                event[field] = event[field][:100]
    if len(evidence) > 2000:
        # The bounded call plan normally cannot hit this; fail safely rather than
        # removing references from retained candidates.
        from .extraction_errors import ExtractionFailure
        raise ExtractionFailure('merge_limit', '合并依据过多，区域结果保留，请分批整理。')
    return {'events': values, 'evidence': evidence}, warnings
