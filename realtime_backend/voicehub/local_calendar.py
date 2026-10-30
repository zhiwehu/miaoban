"""Answer explicit local calendar questions from the host clock, without model routing."""
import re
from datetime import datetime, timedelta


def calendar_answer(text, now=None):
    # Full matching intentionally leaves mixed requests, quotations, translations
    # and foreign time zones to the normal conversation path.
    text=re.sub(r'[\s，。？！,.?!]','',text)
    prefix=r'(?:(?:请问|请|麻烦|告诉我|帮我看看|你知道|你能告诉我))*'
    question=r'(?:是)?(?:星期几|周几|礼拜几|几月几[号日]|几[号日])'
    match=re.fullmatch(prefix+r'(?:本地|上海|北京|北京时间)?(今天|今日|明天|昨天)'+question+r'(?:吗|呢|啊|呀|来着)*',text)
    if not match:return None
    day=match.group(1)
    now=now if now is not None else datetime.now().astimezone()
    date=now.date()+timedelta(days={'今天':0,'今日':0,'明天':1,'昨天':-1}[day])
    weekday='一二三四五六日'[date.weekday()]
    return f'{day}是{date.year}年{date.month}月{date.day}日，星期{weekday}。'
