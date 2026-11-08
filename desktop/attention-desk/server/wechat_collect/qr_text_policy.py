"""Text-only scan instructions. Never inspect media, URLs or QR contents.

The release cutoff is intentionally later than the new user instruction. It
does not amend content grants, retry held jobs, or authorize historical replay.
"""
from datetime import datetime
import re

EFFECTIVE_FROM = '2026-11-08T13:52:00+00:00'
SCAN = re.compile(r'扫码|扫描\s*(?:二维)?码|二维码')
# Remove only a bounded media reference from the dependency check, never from
# stored or transmitted prose. Other "see attachment" instructions remain.
QR_MEDIA_REFERENCE = re.compile(r'二维码(?:请)?(?:详见|见|在)(?:群内|群里|原群|下方|上方|所附|附带|的|中|\s)*(?:图片|上图|附件)(?:中|里)?')
HINT = '需扫码，二维码请返回原微信群查看'
WARNING = {'code': 'qr_in_original_chat', 'message': HINT}
INSTRUCTION = '''
扫码正文规则：当前输入只包含获授权的通知正文，不含图片、附件、OCR或二维码内容。
正文已经说明事项、适用对象和操作时，“扫码”“扫描二维码”“扫码登记”“扫码填写”是操作方式；不能仅因二维码图片未提供就认定通知不完整或等待附件。
保留正文中的事项、截止时间、地点、扫码要求及必要操作信息；不得解析、猜测或重建二维码、网址及附件内容。
若正文仅说“具体事项见图片”，仍应 information_complete=false、await_more_context=true。姓名名单、学号、手机号等隐私规则和其他安全检查保持不变。
'''


def scan_notice(messages):
    """All selected text must be new at both publication and local capture."""
    if not isinstance(messages, list) or not messages or any(not isinstance(m, dict) for m in messages):
        return False
    lower = datetime.fromisoformat(EFFECTIVE_FROM)
    try:
        for message in messages:
            if message.get('message_type') != 'text' or message.get('time_reliability') != 'reliable':
                return False
            for key in ('source_time', 'captured_at'):
                stamp = datetime.fromisoformat(message[key])
                if stamp.tzinfo is None or stamp < lower:
                    return False
        return any(SCAN.search(message['raw_text']) for message in messages)
    except (KeyError, TypeError, ValueError):
        return False


def document_warnings(messages):
    warnings = [{'code': 'redacted_excerpt', 'message': '这是经最小化的文字节选，完整原文仅保留在本机。'}]
    if scan_notice(messages):
        warnings.append(dict(WARNING))
    return warnings


def dependency_text(text, messages, item):
    if (scan_notice(messages) and item.get('information_complete') is True
            and item.get('await_more_context') is False):
        return QR_MEDIA_REFERENCE.sub('', text)
    return text
