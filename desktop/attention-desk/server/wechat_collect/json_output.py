"""One local envelope repair; never alter JSON values or request another model call."""
import json
import re

from ..ai_transport import strict_json
from ..extraction_errors import ExtractionFailure


def parse_output(raw):
    try:
        return strict_json(raw), False
    except ExtractionFailure:
        pass
    original=raw
    candidate=raw.strip().lstrip('\ufeff')
    fence=re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```',candidate,re.I)
    if fence:
        candidate=fence[1].strip()
    # raw_decode finds a single complete object. Only harmless framing outside it
    # is discarded; a second object/array or duplicate keys remains an error.
    start=candidate.find('{')
    if start<0 or start>200 or any(ch in candidate[:start] for ch in '[}]'):
        raise ExtractionFailure('invalid_json','模型 JSON 无法在本地修复，原文已保留。',raw_output=original)
    try:
        _,end=json.JSONDecoder().raw_decode(candidate[start:])
        end+=start
        trailing=candidate[end:].strip()
        if len(trailing)>2000 or any(ch in trailing for ch in '{}[]'):
            raise ValueError('ambiguous additional data')
        parsed=strict_json(candidate[start:end])
        if not isinstance(parsed,dict):
            raise ValueError('object required')
        return parsed, True
    except (ValueError,ExtractionFailure):
        raise ExtractionFailure('invalid_json','模型 JSON 本地修复失败，原文已保留，可手动整理。',raw_output=original) from None
