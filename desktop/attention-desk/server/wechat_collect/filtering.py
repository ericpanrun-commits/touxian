"""Conservative triage. Rejections are reversible and carry an explicit reason."""
import re
from dataclasses import dataclass

RULE_VERSION = 'wechat-local-4'
IMPORTANT = re.compile(r'通知|重要|体测|签到|截止|提交|报名|作业|考试|调课|停课|课程|教室|会议|集合|学习通|CET|四六级|四、六级|实验|竞赛|比赛|招新|面试|问卷|表格|缴费|奖学金|实习|时间|地点|附件|二维码|务必|带好|携带|领取|打印|签字|请.{0,20}(?:准备|带|交|填写|到场)|https?://', re.I)
FORCE = re.compile(r'@所有人|@全体成员|截止|考试时间|调课|停课|延期|取消|改为')
DEPENDENT = re.compile(r'上面|下面|这个|刚才|补充|二维码|填一下|发个通知|有(?:一)?个(?:重要)?通知|稍后|看图')
ACK = re.compile(r'^(?:收到|好的|好|嗯|嗯嗯|OK|ok|谢谢|哈哈哈+|[\U0001F300-\U0001FAFF\u2600-\u27ff\ufe0f\u200d\s]+)[!！。~～]*$')
TIME_HINT = re.compile(r'今天|明天|后天|今晚|明早|今早|上午|下午|晚上|周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}[:：]\d{2}|\d{1,2}[月./-]\d{1,2}|\d{1,2}[点时]')
CHATTER = re.compile(r'吃(?:饭|啥|什么)|干饭|食堂|打(?:游戏|球)|开黑|睡(?:觉|了)|早安|晚安|笑死|笑麻|哈哈|[好很]好笑|好吃|好看|今天天气|下雨了|天气不错|在干嘛|你们在干什么|我也是|确实|绷不住|离谱|卧槽|无语|刷视频|看电影')
STICKER = re.compile(r'^\[(?:动画表情|表情|贴纸|微笑|呲牙|捂脸|旺柴|笑脸|流泪|强|赞|图片表情)\]$')


@dataclass(frozen=True)
class FilterDecision:
    level: str
    reason: str


def classify(message, *, aliases=(), trusted_senders=(), nearby_candidate=False, priority=None):
    text = message.get('raw_text', message.get('text', '')).strip()
    kind = message.get('message_type', 'unknown')
    if priority:
        from .quality import waiting_text,confirmed,NOTICE,HELP,relay_progress,ordinary_reply
        if relay_progress(text): return FilterDecision('fast_reject','relay_participation_update')
        if ordinary_reply(text): return FilterDecision('fast_reject','ordinary_reply_without_notice')
        if HELP.search(text) and re.search(r'(?:怎么办|怎么做)[?？。!！]*$',text) and not confirmed(text):
            return FilterDecision('fast_reject','ordinary_help_question')
        if waiting_text(text):
            if HELP.search(text) or not NOTICE.search(text): return FilterDecision('fast_reject','question_or_ordinary_help')
            return FilterDecision('await_context','unconfirmed_notice_or_question')
        if confirmed(text) and NOTICE.search(text): return FilterDecision('force_analyze','confirmed_followup')
        if text in {'二田','操场','教室','那边'}: return FilterDecision('await_context','fragment_without_subject')
    if FORCE.search(text) or any('@' + a in text for a in aliases if a):
        return FilterDecision('force_analyze', 'mention_or_explicit_change')
    if message.get('sender') in trusted_senders and (IMPORTANT.search(text) or len(text) >= 40):
        return FilterDecision('force_analyze', 'trusted_notice')
    if priority and (kind=='sticker' or ACK.fullmatch(text) or STICKER.fullmatch(text)):
        return FilterDecision('fast_reject','standalone_ack_or_sticker')
    if priority and kind=='system' and '撤回了一条消息' in text:
        return FilterDecision('fast_reject','withdrawal_has_no_notice_content')
    if kind in {'image', 'file', 'link', 'unknown'} or IMPORTANT.search(text) or DEPENDENT.search(text):
        return FilterDecision('candidate', 'notice_media_or_context')
    if len(text) >= 80:
        return FilterDecision('candidate', 'long_text_preserved')
    if priority and CHATTER.search(text):
        return FilterDecision('fast_reject','ordinary_chatter_without_notice_signal')
    if priority and TIME_HINT.search(text):
        return FilterDecision('candidate','possible_date_preserved')
    if nearby_candidate:
        return FilterDecision('candidate', 'nearby_context')
    if kind == 'sticker' or text and ACK.fullmatch(text):
        return FilterDecision('fast_reject', 'standalone_ack_or_sticker')
    if priority in {'medium','low'} and len(text)<(35 if priority=='medium' else 60):
        return FilterDecision('fast_reject','short_conversation_without_notice_signal')
    return FilterDecision('candidate', 'uncertain_text_preserved')
