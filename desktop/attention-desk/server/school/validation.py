"""Reject invented locations, unsupported source roles and personal error history."""
import re
from pydantic import ValidationError
from ..ai_transport import strict_json
from ..extraction_errors import ExtractionFailure
from .profiles import profile_for, review_model, source_scope


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values(): yield from objects(child)
    elif isinstance(value, list):
        for child in value: yield from objects(child)


def validate_review(raw, context):
    profile = profile_for(context)
    try: result = review_model(context).model_validate(strict_json(raw)).model_dump()
    except ValidationError:
        raise ExtractionFailure('review_schema', '复盘未满足固定结构或数量要求，未把不完整结果当成成功。', raw_output=raw) from None
    if result.get('before_lecture') is None:result.pop('before_lecture',None)
    if result.get('before_lecture')!=context.get('before_lecture'):
        raise ExtractionFailure('preview_evidence_changed','课前预习表现必须保留真实记录，不能由模型补写或改变。',raw_output=raw)
    sources = {s['source_id']: s for s in context['sources']}
    used = set()
    for item in objects({k:v for k,v in result.items() if k!='before_lecture'}):
        if 'source_id' in item:
            source = sources.get(item['source_id'])
            if source is None: raise ExtractionFailure('unknown_source', '复盘引用了本次上下文没有的来源。', raw_output=raw)
            quote = item.get('quote')
            if source['kind'] != 'page_image' and (not quote or quote not in source['text']):
                raise ExtractionFailure('invalid_quote', '复盘引用不在提供的原文中，请核对来源。', raw_output=raw)
            if source['kind'] == 'page_image' and quote:
                raise ExtractionFailure('image_quote_unverified', '未转写图像不能提供凭空生成的文字引用。', raw_output=raw)
            used.add(item['source_id'])
        if 'provenance' in item:
            cited = [sources.get(c['source_id']) for c in item['citations']]
            if any(s is None for s in cited): continue
            if item['provenance'] in {'notes', 'textbook'} and any(s['source_kind'] != item['provenance'] or s['kind'] == 'page_image' for s in cited):
                raise ExtractionFailure('source_role', '笔记内容、教材内容和推断没有正确区分。', raw_output=raw)
            if item['provenance'] in {'notes', 'textbook'} and any('uncertain' in s['quality'] or s['quality'] == 'ocr_unverified' or
                    not profile.requires_textbook and s['quality'] in {'vision_unverified', 'unverified', 'unknown'} for s in cited):
                raise ExtractionFailure('uncertain_formula', '不可靠识别不能作为明确知识结论。', raw_output=raw)
        for key, text in item.items():
            if key in {'quote', 'source_id'} or not isinstance(text, str): continue
            if re.search(r'(?:老师|教师|课堂|这堂课|本节课).{0,12}(?:漏讲|没讲|没有讲|未讲|讲了|讲过|未提到|遗漏|未涉及)|(?:teacher|lecturer).{0,18}(?:taught|covered|omitted|did not teach)', text, re.I):
                raise ExtractionFailure('teacher_claim', '不能把笔记缺失说成老师漏讲，也不能凭教材补全授课事实。', raw_output=raw)
            if re.search(r'(?:PDF\s*第?\s*\d+\s*页|教材第\s*\d+\s*页|笔记第\s*\d+\s*页)', text, re.I):
                raise ExtractionFailure('model_page_label', '页码与链接必须由后端来源生成。', raw_output=raw)
            if not context['mistakes']['available'] and re.search(r'(?:你|您).{0,5}(?:容易|经常|总是).{0,8}(?:错|忘|混淆)', text):
                raise ExtractionFailure('invented_history', '没有历史错误证据，不能编造个人易错点。', raw_output=raw)
    for claim in result['notes_taught']:
        if claim['provenance'] not in {'notes', 'uncertain'} or any(sources[c['source_id']]['source_kind'] != 'notes' for c in claim['citations']):
            raise ExtractionFailure('notes_only', '课堂笔记讲了什么只能依据当前笔记。', raw_output=raw)
    for claim in result['textbook_supplement']:
        if claim['provenance'] != 'textbook':
            raise ExtractionFailure('textbook_only', '教材补充必须引用教材，且不能当作授课记录。', raw_output=raw)
    for item in [*result['method_cards'], *result['self_tests']]:
        qualities = {'ocr_unverified', 'vision_uncertain'} if profile.requires_textbook else {'ocr_unverified', 'vision_unverified', 'vision_uncertain', 'unverified', 'unknown'}
        if not any(sources[c['source_id']]['kind'] == 'text' and sources[c['source_id']]['quality'] not in qualities for c in item['citations']):
            raise ExtractionFailure('unreliable_method', '方法和自测必须有可核对的文字依据，不能仅凭未识别图像。', raw_output=raw)
    if not context['mistakes']['available']:
        if result['mistakes']['history_available'] or result['mistakes']['items'] or result['mistakes']['message'] != '当前没有足够历史错误数据':
            raise ExtractionFailure('invented_history', '当前没有足够历史错误数据，应明确保留为空。', raw_output=raw)
    if not profile.requires_textbook and result['source_scope'] != source_scope([sources[sid] for sid in used]):
        raise ExtractionFailure('source_scope', '复盘的资料范围必须与实际引用一致，不能把笔记复盘标为已核对教材。', raw_output=raw)
    return result, [sources[sid] for sid in sorted(used)]
