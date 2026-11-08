"""Fail-closed text projection for an explicit ongoing DeepSeek content grant.

No grant is inferred from a configured key, collection allowlist or paid-AI switch.
The grant file is local evidence of a separately recorded human authorization.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..cloud_sync.wechat_file_lease import immutable_evidence
from ..extraction_errors import ExtractionFailure
from .deepseek_projection import prepare_reviewed_projection

POLICY = 'deepseek-new-text-v1'
FILENAME = 'deepseek-content-authorization.json'


class ApprovedText(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    original_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    text: str = Field(min_length=1, max_length=12000)
    source_time: str | None
    time_reliability: str | None


class ContentGrant(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: int = 1
    enabled: bool
    authorization_id: str = Field(min_length=1, max_length=160)
    destination: str
    policy: str
    not_before: str
    mode: Literal['continuous', 'reviewed'] = 'continuous'
    reviewed_samples: list[ApprovedText] = Field(default_factory=list, max_length=100)
    # Local logical chat ID -> ingestion watermark at activation. Never exported.
    chat_watermarks: dict[str, int] = Field(min_length=1, max_length=30)
    source_bindings: dict[str, str] = Field(default_factory=dict, max_length=30)

    @field_validator('not_before')
    @classmethod
    def aware(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError('timezone_required')
        return value


def deny(code):
    raise ExtractionFailure(code, '内容授权或隐私检查未通过；原文保留本机，未发送。', attempts=0)


def read_grant(raw):
    try:
        # Reject duplicate keys, including conflicting enabled/scope values.
        from ..ai_transport import strict_json
        grant = ContentGrant.model_validate(strict_json(raw.decode('utf8')))
    except (ValidationError, ValueError, UnicodeError, ExtractionFailure):
        deny('content_authorization_invalid')
    if (grant.version != 1 or not grant.enabled or grant.destination != 'https://api.deepseek.com'
            or grant.policy != POLICY or any(value < 0 for value in grant.chat_watermarks.values())
            or (grant.mode == 'reviewed') != bool(grant.reviewed_samples)
            or grant.mode=='continuous' and (set(grant.source_bindings)!=set(grant.chat_watermarks)
                or any(not re.fullmatch(r'[0-9a-f]{64}',value) for value in grant.source_bindings.values()))):
        deny('content_authorization_required')
    return grant


def binding_digest(source_id, binding_json):
    binding=json.loads(binding_json or '{}')
    # Pin the account and actual chat, not volatile collector version metadata.
    value=[source_id,binding.get('account_scope'),binding.get('internal_chat_id')]
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


@contextmanager
def leased_grant(personal):
    """Reuse the existing Windows evidence lease; no edits to send-safety code.

    Hold through request completion. Revocation/replacement while in flight must
    report sharing failure/pending, never pretend the in-flight call was stopped.
    """
    try:
        with immutable_evidence(personal.path.parent/FILENAME, max_bytes=16384) as raw:
            yield read_grant(raw)
    except OSError:
        deny('content_authorization_unavailable')


CONTACT = re.compile(r'^\s*(?:(?:\[(?:太阳|爱心|玫瑰|握手|拥抱)\]|[☀♥❤🌞🌟✨])\s*)?(?:(?:[一二三四五六七八九十百0-9]+[、.．)）]|[-*•])\s*)?(?:(?:活动|咨询|负责|项目)?联系人|联系电话|联系方式|咨询电话|邮箱|电子邮箱|微信号|QQ群|QQ群号|联系老师)\s*[:：]?')
ACTION = re.compile(r'截止|提交|报名|集合|考试|作业|务必|必须|仅限|面向|(?:\d{1,2}[月:：点时])')
IDENTIFIER = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|https?://|wxid_|(?<!\d)(?:\+?86[\s-]*)?1[3-9](?:[\s-]*\d){9}(?!\d)|(?<!\d)(?:\d[\s-]*){6,20}[Xx]?(?!\d)', re.I)
SENSITIVE = re.compile(r'身份证号|银行卡|病历|诊断|家庭住址|详细住址|政治面貌|党籍|处分名单|资助名单|成绩单|成绩明细|心理咨询记录|手机号|学号\s*[:：]|准考证号|(?:姓名|我叫)\s*[:：]?|(?:成绩|分数|绩点).{0,12}\d')
ENCODED = re.compile(r'\\u[0-9a-f]{4}|%[0-9a-f]{2}|&#\w+;|(?:base64|data:)\s*|[零〇一二三四五六七八九幺]{7,}|(?<![A-Za-z0-9])[A-Za-z0-9+/]{32,}={0,2}', re.I)
MENTION = re.compile(r'@(?!\s*(?:所有人|全体成员)(?:[\s，,。！!：:]|$))')
PERSON = re.compile(r'(?:^|[\s，,；;：:、])(?:\d+[.、]\s*)?[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦马苗方俞任袁柳唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路江童颜郭梅盛林钟徐邱高夏蔡田樊胡霍万卢莫房裘缪干解应宗丁宣邓单杭洪包左石崔吉龚程嵇邢裴陆荣翁荀羊甄曲封芮储靳段富巫乌焦巴牧隗山谷车侯全班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束龙叶幸司韶黎乔赖卓蔺屠蒙池门燕农温庄晏柴瞿阎连习容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广阙东殴殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾沙养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公][\u4e00-\u9fff]{1,2}(?:同学|老师|先生|女士|\s*[:：]\s*\d|\s+\d)')
GENERIC_NAMES = {'老师','教师','辅导员','班长','团支书','学习委员','同学','同学们','全体同学','全班同学','所有同学','大家','所有人','管理员','群主'}


def minimize_notice(text, known_names=()):
    """Remove only whole contact lines; uncertain/private prose is held locally.

    This conservative recognizer is not a proof that arbitrary Chinese prose
    contains no personal data. Novel names and obfuscation remain a limitation;
    source-scoped permission and explicit review holds are also required.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 12000:
        deny('privacy_review_required')
    if any(unicodedata.category(char) == 'Cf' for char in text):
        deny('privacy_review_required')
    retained = []
    contact_section = False
    removed = False
    lines=text.splitlines()
    for index,line in enumerate(lines):
        normalized = unicodedata.normalize('NFKC', line)
        contact = bool(CONTACT.search(normalized))
        continuation = contact_section and bool(IDENTIFIER.search(normalized))
        if contact or continuation:
            bare_header=contact and not CONTACT.sub('', normalized).strip()
            following=next((unicodedata.normalize('NFKC',part) for part in lines[index+1:] if part.strip()),'') if bare_header else ''
            if ACTION.search(normalized) or (bare_header and (not IDENTIFIER.search(following) or ACTION.search(following))):
                deny('privacy_scope_would_change')
            removed = True
            contact_section = True
            continue
        if contact_section and re.fullmatch(r'\s*[\u4e00-\u9fff]{2,4}\s*',normalized) and not ACTION.search(normalized):
            deny('privacy_review_required')
        contact_section = False
        audience_safe=normalized
        for audience in ('全体同学','全班同学','所有同学','各位同学','同学们'):
            audience_safe=audience_safe.replace(audience,' ')
        if (IDENTIFIER.search(normalized) or SENSITIVE.search(normalized) or ENCODED.search(normalized)
                or MENTION.search(normalized) or PERSON.search(audience_safe)):
            deny('privacy_review_required')
        if not ACTION.search(normalized) and (re.fullmatch(r'\s*\d+[.、]\s*[\u4e00-\u9fff]{2,4}\s*',normalized)
                or re.fullmatch(r'\s*[\u4e00-\u9fff]{2,4}(?:[，,、][\u4e00-\u9fff]{2,4})+\s*',normalized)):
            deny('privacy_review_required')
        for name in known_names:
            normalized_name = unicodedata.normalize('NFKC', name).strip()
            if 2 <= len(normalized_name) <= 40 and normalized_name not in GENERIC_NAMES and normalized_name in normalized:
                deny('privacy_review_required')
        retained.append(line)
    reviewed = '\n'.join(retained) if removed else text
    if not reviewed.strip():
        deny('privacy_review_required')
    return reviewed


def prepare_authorized_projection(personal, batch, snapshot, grant):
    """Check current local source scope, time and watermark before minimization."""
    lower = datetime.fromisoformat(grant.not_before)
    approvals = []
    primary = set(batch['primary_ids'])
    with personal.connection() as connection:
        names = [row[0] for row in connection.execute('SELECT DISTINCT sender FROM wechat_message WHERE sender IS NOT NULL')]
        for message in snapshot['wechat']['messages']:
            if message['message_id'] not in primary:
                continue
            current = connection.execute('SELECT m.*,ch.enabled,ch.source_id AS chat_source_id,ch.binding_json AS chat_binding FROM wechat_message m JOIN wechat_chat ch ON ch.id=m.chat_id WHERE m.id=?', (message['message_id'],)).fetchone()
            if (not current or not current['enabled'] or current['chat_id'] != batch['chat_id']
                    or current['chat_id'] not in grant.chat_watermarks
                    or current['message_type'] != 'text' or current['raw_text'] != message['raw_text']
                    or current['source_time'] != message.get('source_time')
                    or current['time_reliability'] != message.get('time_reliability')):
                deny('content_outside_authorized_scope')
            if grant.mode == 'reviewed':
                continue
            if (binding_digest(current['chat_source_id'],current['chat_binding'])!=grant.source_bindings[current['chat_id']]
                    or current['ingest_seq'] <= grant.chat_watermarks[current['chat_id']]
                    or current['time_reliability'] != 'reliable'):
                deny('content_outside_authorized_scope')
            try:
                published, captured = datetime.fromisoformat(current['source_time']), datetime.fromisoformat(current['captured_at'])
                if not published.tzinfo or not captured.tzinfo or published < lower or captured < lower:
                    deny('historical_content_not_authorized')
            except (TypeError, ValueError):
                deny('unverified_source_time')
            reviewed = minimize_notice(current['raw_text'], names)
            approvals.append({'original_sha256':hashlib.sha256(current['raw_text'].encode()).hexdigest(),
                'text':reviewed, 'source_time':current['source_time'], 'time_reliability':'reliable'})
    return prepare_reviewed_projection(batch, snapshot,
        [sample.model_dump() for sample in grant.reviewed_samples] if grant.mode == 'reviewed' else approvals)


def projection_record(projection):
    """Local recovery evidence only; never part of an API payload."""
    return {'batch':projection.batch, 'snapshot':projection.snapshot,
            'message_ids':projection.message_ids, 'blocks':projection.blocks}


def restore_record(record, batch, snapshot, raw, context):
    from .deepseek_projection import ReviewedProjection
    from ..user_context import UserContext
    projection = ReviewedProjection(record['batch'], record['snapshot'], UserContext().model_dump(),
        batch, snapshot, record['message_ids'], record['blocks'])
    return projection.restore(raw, context)
