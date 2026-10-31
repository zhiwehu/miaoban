"""Small text/metadata boundary between the local LLM and robot protocol."""
import re

EMOTIONS = ('neutral', 'happy', 'sad', 'sleepy', 'thinking', 'confused')
EXPRESSION_PROMPT = (
    '\n回复第一行必须是 [emotion:标签]，第二行开始是要说的话。'
    '标签只能选 neutral、happy、sad、sleepy、thinking、confused。'
    '根据你的回复语气选择：普通解释 neutral；庆祝、玩笑 happy；安慰、难过 sad；'
    '晚安、困倦 sleepy；推敲 thinking；没听懂 confused。'
    '安慰伤心的人不要嬉笑；不要在正文解释标签，也不要机械地每次都 happy。'
)

def parse_reply(raw):
    text = (raw or '').strip()
    match = re.match(r'^\[emotion:([^\]\r\n]*)\]\s*', text, re.IGNORECASE)
    if not match:
        match = re.match(r'^\[(' + '|'.join(EMOTIONS) + r')\]\s*', text, re.IGNORECASE)
        if not match:
            return text, 'neutral'
    emotion = match.group(1).strip().lower()
    return text[match.end():].strip(), emotion if emotion in EMOTIONS else 'neutral'
