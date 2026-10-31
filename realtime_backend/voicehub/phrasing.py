"""Spoken lines that sound like a person, not a status machine.

Three rules, all enforced here instead of at the call sites:

* A long tool call gets one short, tool-specific line right away — never the
  same template every turn. ``acknowledge`` rotates through a few variants.
* A background result is introduced like something you just found out
  ("刚查到，…"), never with a fixed label such as "后台任务结果：".
* Failure and progress records are spoken as-is: adding a friendly lead to
  "任务没能完成" only makes it worse.

Rotation state is per process and per phrase family, so two consecutive turns
never repeat the same sentence, while tests can still assert on the set.
"""
import re

_ACKS = {
    'weather': ('我看眼天气。', '天气我查一下。', '这就给你看天气。', '我看看外面怎么样。'),
    'news': ('我翻一下最新消息。', '看看最近的新闻。', '我找几条新的新闻。'),
    'music': ('我找找这首歌。', '这就去放。', '我翻一下曲库。'),
    'calendar': ('我看看日程。', '日程我查一下。', '我翻一下你的安排。'),
    'timer': ('好，这就设。', '我设一下。', '好，马上设好。'),
    'shopping_list': ('我看一下清单。', '清单我查一下。', '我翻翻购物清单。'),
    'item_memory': ('我查一下放哪了。', '我找找记录。', '这个我查查。'),
    'system_info': ('我看一眼电脑状态。', '我看看这台机器。', '查下运行情况。'),
    'file_organizer': ('我先看看目录。', '我扫一眼这个目录。', '先看看有什么文件。'),
}
_DEFAULT = ('我处理一下。', '稍等，我看下。', '好，我看看。', '这个我来查。')

_LEADS = ('刚查到，', '对了，', '我查到了，', '有结果了，', '顺便说一下，')
_TAILS = ('详情我放在 MCP 页了。', '完整的在 MCP 页。', '剩下的在 MCP 页。')

_counter = {}


def _pick(key, options):
    index = _counter.get(key, 0)
    _counter[key] = index + 1
    return options[index % len(options)]


def acknowledge(tool):
    """One short line for a tool that will take a while."""
    return _pick('ack:' + str(tool), _ACKS.get(tool, _DEFAULT))


def progress(text=''):
    """The immediate line used when an operation was detached with no reply yet."""
    if re.search(r'天气|气温|下雨|降雨', text):
        return _pick('ack:weather', _ACKS['weather'])
    if re.search(r'新闻|资讯', text):
        return _pick('ack:news', _ACKS['news'])
    return _pick('progress', _DEFAULT)


_SENTENCE_END = '。！？!?；;\n'
_CLAUSE_END = '，,、'


def shorten(body, limit):
    """Cut at a sentence boundary; never leave a half clause such as「还有一」."""
    if len(body) <= limit:
        return body, False
    window = body[:limit]
    cut = max(window.rfind(mark) for mark in _SENTENCE_END)
    if cut >= 0:
        return window[:cut + 1], True
    # No full sentence fits: drop the unfinished clause instead of completing it
    # with a full stop, which would read as a truncated word.
    cut = max(window.rfind(mark) for mark in _CLAUSE_END)
    if cut > 0:
        return window[:cut].rstrip() + '。', True
    return window.rstrip() + '。', True


def announcement(text, status='completed', limit=120):
    """Introduce a finished background result the way a person would."""
    body, truncated = shorten((text or '').strip(), limit)
    # Failures and interruptions already read as complete sentences; a cheerful
    # lead-in would make them worse, so speak them unchanged.
    if status != 'completed':
        return body
    spoken = _pick('lead', _LEADS) + body
    if truncated:
        spoken += _pick('tail', _TAILS)
    return spoken
