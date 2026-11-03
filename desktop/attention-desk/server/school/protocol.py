"""Evidence selection, fact-bound composition and one scoped repair.

Math is rendered from immutable source passages. The model chooses facts and
explains their use; it never has to reproduce a formula or a citation string.
"""
import copy
import json
import re
from typing import Any, Literal
from pydantic import Field, ValidationError
from ..ai_transport import strict_json
from ..extraction_errors import ExtractionFailure
from .models import Strict
from .validation import validate_review
from .profiles import MATH as MATH_PROFILE, profile_for, source_scope

EVIDENCE_PROMPT_VERSION = MATH_PROFILE.evidence
COMPOSITION_PROMPT_VERSION = MATH_PROFILE.composition
REPAIR_PROMPT_VERSION = MATH_PROFILE.repair
CATEGORIES = ('notes_facts', 'textbook_facts', 'formulas', 'conditions', 'examples', 'uncertain_items')
LOW_QUALITY = {'ocr_unverified', 'vision_unverified', 'vision_uncertain', 'unverified', 'unknown'}
SLOT = re.compile(r'\[\[(F\d{2})\]\]')
MATH = re.compile(r'[A-Za-z0-9α-ωΑ-Ω=<>≤≥→∞√∑∏^/\\]|(?:等于|结果[为是]|趋于|收敛到|极限[为是])\s*[零一二三四五六七八九十百千万]')
TEACHER = re.compile(r'(?:老师|教师|授课者|teacher|lecturer).{0,18}(?:讲|教|漏|未|没|taught|covered|omitted)|(?:课堂|本节课|这堂课).{0,10}(?:讲了|讲过|没讲|漏讲|未讲)', re.I)
FORMULA = re.compile(r'\blim\b|[=≤≥→∞√∑∏^]|[A-Za-z]\([^)]{1,60}\)')
CONDITION = re.compile(r'若|设|当|只要|满足|条件|前提|趋于|趋向|非零|有定义|→|∀|∃')
GENERAL_CONDITION = re.compile(CONDITION.pattern + r'|\b(?:if|when|unless|provided|requires?|only\s+when|preconditions?)\b', re.I)


class ProtocolFailure(ExtractionFailure):
    def __init__(self, stage, issues, raw_output=None):
        super().__init__('review_validation', '证据或复盘有具体项目未通过校验，需核对；不会无限重试。', raw_output=raw_output)
        self.stage, self.issues = stage, issues


def problem(path, code, message, **extra):
    return {'path': path, 'code': code, 'message': message, **extra}


def pointer(parts):
    return '/' + '/'.join(str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def parsed(model, raw, stage):
    try:
        return model.model_validate(strict_json(raw)).model_dump()
    except ValidationError as error:
        raise ProtocolFailure(stage, [problem(pointer(e['loc']), 'schema', e['msg']) for e in error.errors()], raw) from None
    except ExtractionFailure as error:
        raise ProtocolFailure(stage, [problem('/', 'json', '本次结果不是可定位项目的有效 JSON。')], raw) from error


class EvidenceSelection(Strict):
    notes_facts: list[str] = Field(max_length=14)
    textbook_facts: list[str] = Field(max_length=14)
    formulas: list[str] = Field(max_length=14)
    conditions: list[str] = Field(max_length=14)
    examples: list[str] = Field(max_length=14)
    uncertain_items: list[str] = Field(max_length=14)


def catalog(context):
    """Source passages are the indivisible condition-preserving evidence units."""
    values = []
    condition = CONDITION if profile_for(context).requires_textbook else GENERAL_CONDITION
    for source in context['sources']:
        if source['kind'] != 'text' or not source['text'].strip():
            continue
        values.append({'fact_id': f'F{len(values)+1:02}', 'source_id': source['source_id'],
                       'source_kind': source['source_kind'], 'quality': source['quality'],
                       'text': source['text'], 'uncertain': source['quality'] in LOW_QUALITY,
                       'math_bearing': bool(FORMULA.search(source['text'])),
                       'condition_bearing': bool(condition.search(source['text']))})
    return values


def extract_evidence(raw, context):
    selection = parsed(EvidenceSelection, raw, 'evidence')
    available = {f['fact_id']: f for f in catalog(context)}
    issues, safety_notes = [], []
    for category in CATEGORIES:
        values = selection[category]
        if len(values) != len(set(values)):
            issues.append(problem('/' + category, 'duplicate_fact', '同一分类不要重复同一事实标识。'))
        for i, fid in enumerate(values):
            fact = available.get(fid)
            if not fact:
                issues.append(problem(f'/{category}/{i}', 'unknown_fact', '事实标识不在本次来源中。'))
                continue
            if category == 'notes_facts' and fact['source_kind'] not in {'notes', 'recent_notes'}:
                issues.append(problem(f'/{category}/{i}', 'source_role', '笔记事实只能选择笔记来源。'))
            if category == 'textbook_facts' and fact['source_kind'] != 'textbook':
                issues.append(problem(f'/{category}/{i}', 'source_role', '教材事实只能选择教材来源。'))
            if category == 'formulas' and not fact['math_bearing']:
                issues.append(problem(f'/{category}/{i}', 'formula_evidence', '该来源没有可定位的数学表达。'))
            if category == 'conditions' and not fact['condition_bearing']:
                issues.append(problem(f'/{category}/{i}', 'condition_evidence', '该来源没有可定位的条件或趋向。'))
    for fid, fact in available.items():
        if fact['uncertain'] or fid in selection['uncertain_items']:
            fact['uncertain'] = True
            # The source quality, never the model's confidence, controls downgrade.
            if fid not in selection['uncertain_items']:
                selection['uncertain_items'].append(fid)
                safety_notes.append({'fact_id': fid, 'rule': 'source_uncertainty_preserved'})
            for group in ('notes_facts', 'textbook_facts'):
                if fid in selection[group]:
                    selection[group].remove(fid)
                    safety_notes.append({'fact_id': fid, 'rule': 'untrusted_fact_downgraded'})
        else:
            group = 'notes_facts' if fact['source_kind'] in {'notes', 'recent_notes'} else 'textbook_facts'
            substantive = fact['source_kind'] == 'notes' or fact['math_bearing'] or len(fact['text']) >= 100
            if substantive and fid not in selection[group]:
                issues.append(problem('/' + group, 'evidence_coverage', '遗漏本次已提供的实质证据。', required_fact_id=fid))
    if issues:
        raise ProtocolFailure('evidence', issues, raw)
    selected = set().union(*(set(selection[c]) for c in CATEGORIES))
    facts = [f for fid, f in available.items() if fid in selected]
    return {'categories': selection, 'facts': facts,
            'protected_facts': [f['fact_id'] for f in facts if f['math_bearing'] and not f['uncertain']],
            'uncertain_fact_ids': [f['fact_id'] for f in facts if f['uncertain']],
            'safety_notes': safety_notes}


class DraftClaim(Strict):
    text: str = Field(min_length=1, max_length=600)
    provenance: Literal['notes', 'textbook', 'inference', 'uncertain']
    fact_ids: list[str] = Field(min_length=1, max_length=4)


class DraftConcept(Strict):
    name: str = Field(min_length=1, max_length=100)
    explanation: DraftClaim


class DraftMethod(Strict):
    signal: str = Field(min_length=1, max_length=300)
    method: str = Field(min_length=1, max_length=600)
    conditions: str = Field(min_length=1, max_length=600)
    common_misuse: str = Field(min_length=1, max_length=400)
    fact_ids: list[str] = Field(min_length=1, max_length=4)


class DraftTest(Strict):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=600)
    fact_ids: list[str] = Field(min_length=1, max_length=4)


class DraftAction(Strict):
    text: str = Field(min_length=1, max_length=300)
    duration_minutes: int = Field(ge=1, le=60)
    rationale: DraftClaim


class ReviewDraft(Strict):
    core_concepts: list[DraftConcept] = Field(min_length=3, max_length=7)
    notes_taught: list[DraftClaim] = Field(min_length=1, max_length=12)
    must_master: list[DraftClaim] = Field(min_length=1, max_length=12)
    method_cards: list[DraftMethod] = Field(min_length=1, max_length=6)
    textbook_supplement: list[DraftClaim] = Field(max_length=8)
    understanding_gaps: list[DraftClaim] = Field(max_length=5)
    connections: list[DraftClaim] = Field(max_length=5)
    self_tests: list[DraftTest] = Field(min_length=3, max_length=5)
    suggested_actions: list[DraftAction] = Field(max_length=3)


class CompositionCandidate(ReviewDraft):
    # An omitted current-note item must not hide independent citation/math
    # failures until after the one permitted repair has already been spent.
    # Only this collection can be empty while diagnosing; output validation
    # still requires at least one fact-bound current-note item.
    notes_taught: list[DraftClaim] = Field(max_length=12)


class GeneralReviewDraft(ReviewDraft):
    core_concepts: list[DraftConcept] = Field(max_length=7)
    must_master: list[DraftClaim] = Field(max_length=12)
    method_cards: list[DraftMethod] = Field(max_length=6)
    self_tests: list[DraftTest] = Field(max_length=5)


class GeneralCompositionCandidate(GeneralReviewDraft):
    notes_taught: list[DraftClaim] = Field(max_length=12)


def draft_model(context):
    return ReviewDraft if profile_for(context).requires_textbook else GeneralReviewDraft


def compose_review(raw, evidence, context):
    profile = profile_for(context)
    draft = parsed(CompositionCandidate if profile.requires_textbook else GeneralCompositionCandidate, raw, 'composition')
    facts = {f['fact_id']: f for f in evidence['facts']}
    original = {s['source_id']: s for s in context['sources']}
    issues, used, safety_notes = [], set(), []
    if not draft['notes_taught']:
        issues.append(problem('/notes_taught/0', 'missing_note_item',
            '补一个引用当前笔记的条目；识别未核验时只能保留待核对，不得用教材填充。'))

    def check_text(text, path, ids=()):
        slots = set(SLOT.findall(text))
        if not slots <= set(ids) or any(fid not in facts for fid in slots):
            issues.append(problem(path, 'uncited_slot', '公式或条件占位必须属于本项 fact_ids。', allowed_fact_ids=list(ids)))
        residue = SLOT.sub('', text).replace('AI', '')
        if '[[' in residue or ']]' in residue or MATH.search(residue):
            fragments = list(dict.fromkeys(m.group() for m in MATH.finditer(residue)))[:12]
            issues.append(problem(path, 'unbound_math', '数学表达、变量、趋向或数值须用事实占位，不能自行重抄。' if profile.requires_textbook else '原文术语、代码、数字和表达式须用事实占位，不能自行补写。',
                                  allowed_fact_ids=list(ids), unbound_fragments=fragments))
        if TEACHER.search(residue):
            issues.append(problem(path, 'teacher_claim', '不得据教材或笔记缺失推断教师讲授情况。'))
        if re.search(r'(?:PDF|教材|笔记).{0,4}第.{1,8}页', residue, re.I):
            issues.append(problem(path, 'model_page', '页码由真实来源登记，正文不自行编排。'))
        if not context['mistakes']['available'] and re.search(r'(?:你|您).{0,5}(?:容易|经常|总是).{0,8}(?:错|忘|混淆)', residue):
            issues.append(problem(path, 'invented_history', '没有历史错误证据，不能编造个人易错点。'))
        return slots

    def bindings(item, path):
        ids = item['fact_ids']
        if len(ids) != len(set(ids)) or any(fid not in facts for fid in ids):
            issues.append(problem(path + '/fact_ids', 'unknown_fact', '必须引用本次已验证事实，且不能重复。'))
        values = [facts[fid] for fid in ids if fid in facts]
        for key, text in item.items():
            if key in {'text', 'signal', 'method', 'conditions', 'common_misuse', 'question', 'answer'}:
                check_text(text, path + '/' + key, ids)
                if re.search(r'笔记.{0,8}(?:记录|写|出现|记载)', text) and not any(f['source_kind'] == 'notes' for f in values):
                    issues.append(problem(path + '/' + key, 'notes_evidence', '笔记记录的陈述必须有当前 Notes 事实。'))
                if re.search(r'教材.{0,8}(?:指出|说明|给出|列|定义|记载)', text) and not any(f['source_kind'] == 'textbook' for f in values):
                    issues.append(problem(path + '/' + key, 'textbook_evidence', '教材陈述必须有 Textbook 事实。'))
        return values

    def claim(item, path, required=None):
        values = bindings(item, path)
        role = item['provenance']
        if required and role not in required:
            issues.append(problem(path + '/provenance', 'source_role', '本部分的陈述角色不正确。', allowed_provenance=list(required)))
        if role in {'notes', 'textbook'} and any(f['source_kind'] != role for f in values):
            issues.append(problem(path + '/fact_ids', 'source_role', '陈述角色必须与事实来源一致。'))
        if path.startswith('/notes_taught') and any(f['source_kind'] != 'notes' for f in values):
            issues.append(problem(path + '/fact_ids', 'notes_only', '当前笔记内容不能用教材或旧笔记填充。'))
        if any(f['uncertain'] for f in values) and role != 'uncertain':
            # A quality downgrade is a safety rule, not an attempt to fix math.
            role = 'uncertain'
            safety_notes.append({'path': path, 'rule': 'claim_downgraded_from_source_quality'})
        return {'text': item['text'], 'provenance': role, 'citations': citations(values)}

    def citations(values):
        result, seen = [], set()
        for f in values:
            if f['source_id'] in seen:
                continue
            seen.add(f['source_id']); used.add(f['source_id'])
            result.append({'source_id': f['source_id'], 'quote': f['text']})
        return result

    content = {'schema_version': 'lecture-review-v1' if profile.requires_textbook else 'lecture-review-general-v1', 'core_concepts': []}
    for i, item in enumerate(draft['core_concepts']):
        check_text(item['name'], f'/core_concepts/{i}/name')
        content['core_concepts'].append({'name': item['name'], 'explanation': claim(item['explanation'], f'/core_concepts/{i}/explanation')})
    for key in ('notes_taught', 'must_master', 'textbook_supplement', 'understanding_gaps', 'connections'):
        allowed = {'notes', 'uncertain'} if key == 'notes_taught' else {'textbook'} if key == 'textbook_supplement' else None
        content[key] = [claim(item, f'/{key}/{i}', allowed) for i, item in enumerate(draft[key])]
        if key == 'textbook_supplement':
            for item in content[key]:
                item['notes_presence'] = 'not_found_in_provided_evidence'
    content['method_cards'] = []
    for i, item in enumerate(draft['method_cards']):
        path = f'/method_cards/{i}'
        values = bindings(item, path)
        if any(f['uncertain'] for f in values) or not values:
            issues.append(problem(path + '/fact_ids', 'unreliable_method', '方法卡仅使用可靠文字事实；不确定例题留给核对区。'))
        if any(f['math_bearing'] if profile.requires_textbook else f['condition_bearing'] for f in values):
            conditional = {f['fact_id'] for f in values if f['condition_bearing'] and not f['uncertain']}
            if not set(SLOT.findall(item['conditions'])) & conditional:
                issues.append(problem(path + '/conditions', 'conditions_required', '适用条件必须绑定本项的完整条件证据。', allowed_fact_ids=sorted(conditional)))
        content['method_cards'].append({**{k: item[k] for k in ('signal', 'method', 'conditions', 'common_misuse')}, 'citations': citations(values)})
    content['self_tests'] = []
    for i, item in enumerate(draft['self_tests']):
        values = bindings(item, f'/self_tests/{i}')
        if any(f['uncertain'] for f in values) or not values:
            issues.append(problem(f'/self_tests/{i}/fact_ids', 'unreliable_answer', '参考答案必须由可靠文字事实支撑，不确定原式不得直接作答案。'))
        content['self_tests'].append({'question': item['question'], 'answer': item['answer'], 'citations': citations(values)})
    content['suggested_actions'] = []
    for i, item in enumerate(draft['suggested_actions']):
        check_text(item['text'], f'/suggested_actions/{i}/text')
        if re.search(r'截止|截至|必须于|明天|下周', item['text']):
            issues.append(problem(f'/suggested_actions/{i}/text', 'invented_schedule', '建议不擅自设定日期或截止义务。'))
        content['suggested_actions'].append({'text': item['text'], 'duration_minutes': item['duration_minutes'],
            'rationale': claim(item['rationale'], f'/suggested_actions/{i}/rationale')})
    if issues:
        raise ProtocolFailure('composition', issues, raw)

    def render(value):
        if isinstance(value, str):
            return SLOT.sub(lambda m: facts[m[1]]['text'], value)
        if isinstance(value, list):
            return [render(v) for v in value]
        if isinstance(value, dict):
            # Source quotes are already original text, never interpreted as slots.
            return {k: v if k in {'quote', 'source_id'} else render(v) for k, v in value.items()}
        return value

    # One pass over model fields only. Source passages appended below are data.
    content = render(content)

    # Ensure that finite selection cannot silently erase a known formula/condition
    # or a known uncertainty. These are copied facts, never lesson-specific answers.
    covered = set()
    for group in ('core_concepts', 'must_master', 'method_cards'):
        for item in draft[group]:
            covered.update((item.get('explanation') or item).get('fact_ids', []))
    for fid in evidence['protected_facts']:
        if fid not in covered:
            f = facts[fid]
            content['must_master'].append({'text': ('核对原文中的公式、变量趋向与条件：' if profile.requires_textbook else '核对原文中的关键表达与适用前提：') + f['text'],
                'provenance': f['source_kind'] if f['source_kind'] in {'notes', 'textbook'} else 'inference', 'citations': citations([f])})
            safety_notes.append({'fact_id': fid, 'rule': 'source_formula_condition_coverage_preserved'})
    uncertain_covered = {fid for item in draft['understanding_gaps'] for fid in item['fact_ids']}
    for fid in evidence['uncertain_fact_ids']:
        if fid not in uncertain_covered:
            f = facts[fid]
            content['understanding_gaps'].append({'text': '这段原页表达仍需核对，不作为确认结论：' + f['text'],
                'provenance': 'uncertain', 'citations': citations([f])})
            safety_notes.append({'fact_id': fid, 'rule': 'uncertainty_warning_preserved'})

    content['mistakes'] = {'history_available': False, 'message': '当前没有足够历史错误数据', 'items': []}
    if context['mistakes']['available']:
        content['mistakes'] = {'history_available': True, 'message': '历史线索仅限已提供的真实记录；本次未自动推断个人易错点。', 'items': []}
    if context.get('before_lecture'):content['before_lecture']=copy.deepcopy(context['before_lecture'])
    if not profile.requires_textbook:
        content['source_scope'] = source_scope([original[sid] for sid in used])
    if len(json.dumps(content, ensure_ascii=False)) > 65000:
        raise ProtocolFailure('composition', [problem('/must_master', 'output_budget', '复盘证据过长，需精简重复表述并保留条件。')], raw)
    try:
        final, sources = validate_review(json.dumps(content, ensure_ascii=False), context)
    except ExtractionFailure as error:
        raise ProtocolFailure('composition', [problem('/', error.code, str(error))], raw) from error
    return final, sources, {'safety_notes': safety_notes, 'uncertain_fact_ids': evidence['uncertain_fact_ids'],
                            'needs_review': True, 'protected_facts': evidence['protected_facts']}


class RepairPatch(Strict):
    op: Literal['replace', 'add', 'remove']
    path: str = Field(min_length=2, max_length=180)
    value: Any = None


class TargetedRepair(Strict):
    patches: list[RepairPatch] = Field(min_length=1, max_length=8)


def apply_targeted_repair(raw_patch, original_raw, failure):
    """An item/path allowlist prevents a repair from becoming full regeneration."""
    changes = parsed(TargetedRepair, raw_patch, failure.stage)
    document = copy.deepcopy(strict_json(original_raw))
    allowed = repair_roots(failure.stage, failure.issues)
    if not allowed:
        raise ProtocolFailure(failure.stage, [problem('/', 'repair_not_targetable', '错误没有可定位项目，停止自动调用。')], raw_patch)
    for patch in changes['patches']:
        path = patch['path']
        if not any(path == root or path.startswith(root + '/') for root in allowed):
            raise ProtocolFailure(failure.stage, [problem(path, 'repair_scope', '修复只能修改校验明确列出的单个条目。')], raw_patch)
        missing_note = any(issue['code'] == 'missing_note_item' and issue['path'] == '/notes_taught/0'
            for issue in failure.issues)
        if missing_note and path.startswith('/notes_taught/'):
            if path != '/notes_taught/0' or patch['op'] != 'add' or document.get('notes_taught') != []:
                raise ProtocolFailure(failure.stage, [problem(path, 'repair_scope', '缺失的笔记部分只允许补一个完整条目。')], raw_patch)
        parts = [p.replace('~1', '/').replace('~0', '~') for p in path.removeprefix('/').split('/')]
        target = document
        try:
            for key in parts[:-1]:
                target = target[int(key)] if isinstance(target, list) else target[key]
            key = parts[-1]
            if isinstance(target, list):
                index = len(target) if key == '-' else int(key)
                if patch['op'] == 'remove': target.pop(index)
                elif patch['op'] == 'add': target.insert(index, patch['value'])
                else: target[index] = patch['value']
            else:
                if patch['op'] == 'remove': del target[key]
                elif patch['op'] == 'replace':
                    if key not in target: raise KeyError(key)
                    target[key] = patch['value']
                else: target[key] = patch['value']
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProtocolFailure(failure.stage, [problem(path, 'repair_path', '修复路径与原结果不一致。')], raw_patch) from error
    return json.dumps(document, ensure_ascii=False)


def repair_roots(stage, issues):
    """An atomic item may need its text, evidence and role fixed together.

    Never allow a composition section or the document root to be regenerated.
    Evidence extraction contains only six bounded arrays, so its target is the
    affected category. Issue paths are produced by the validator, not the model.
    """
    roots = set()
    for issue in issues:
        parts = issue['path'].strip('/').split('/')
        if stage == 'evidence' and parts[0] in CATEGORIES:
            roots.add('/' + parts[0])
        elif stage == 'composition' and len(parts) >= 2 and parts[0] in ReviewDraft.model_fields and parts[1].isdigit():
            roots.add('/' + '/'.join(parts[:2]))
    return roots
