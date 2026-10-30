"""News tool: schema, acknowledgement and bounded RSS implementation."""
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from .shared import fetch, fetched_at

NEWS_SOURCES = {'chinanews': ('中新网', 'https://www.chinanews.com.cn/rss/scroll-news.xml'),
 'bbc': ('BBC World', 'https://feeds.bbci.co.uk/news/world/rss.xml')}

SCHEMA = ('读取新闻源最新标题，可用关键词筛选；不是全网搜索。',
 {'source': {'type': 'string', 'enum': ['chinanews', 'bbc']},
  'limit': {'type': 'integer', 'minimum': 1, 'maximum': 5},
  'keyword': {'type': 'string', 'maxLength': 80}})
REQUIRED = []
ACKNOWLEDGEMENT = '我看一下最新新闻，请稍等。'


def plain(value, limit):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', '', value or ''))).strip()[:limit]



async def execute(http, source='bbc', limit=3, keyword=''):
    label, url = NEWS_SOURCES[source]
    raw = await fetch(http, url)
    if b'<!doctype' in raw.lower() or b'<!entity' in raw.lower():
        raise ValueError('unsafe_feed')
    root = ET.fromstring(raw)
    entries = []
    for item in root.findall('.//item')[:100]:
        title = plain(item.findtext('title'), 200)
        summary = plain(item.findtext('description'), 300)
        if not title or keyword.casefold() not in (title+' '+summary).casefold():
            continue
        link = (item.findtext('link') or '').strip()
        parsed = urlsplit(link)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            link = ''
        entries.append({'title':title, 'summary':summary, 'url':link[:1000],
                        'published':plain(item.findtext('pubDate'), 100)})
        if len(entries) >= limit:
            break
    return {'source':label, 'feed_url':url, 'fetched_at':fetched_at(), 'items':entries,
            'message':'仅为该新闻源当前 RSS 内容，不代表全网搜索结果。'}
