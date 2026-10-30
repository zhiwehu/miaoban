"""Convert validated source spans to bounded MCP arguments; never infer a missing city."""
import re
from .tools import validate_arguments

FIELDS={'weather':('城市','时间'),'news':('新闻来源','条数','新闻关键词'),
        'system_info':('查询项',)}
SOURCE_ALIASES={'bbc':'bbc','bbc world':'bbc','bbc世界频道':'bbc','英国广播公司':'bbc',
                '中新网':'chinanews','中国新闻网':'chinanews'}
SECTIONS={'cpu':'cpu','处理器':'cpu','CPU使用率':'cpu','CPU占用率':'cpu','CPU负载':'cpu',
          '内存':'memory','运行内存':'memory','可用内存':'memory','内存占用':'memory',
          '磁盘':'disk','硬盘':'disk','存储空间':'disk','剩余空间':'disk',
          '运行时间':'uptime','运行时长':'uptime','开机时间':'uptime','开机时长':'uptime'}
DAY={'今天':0,'现在':0,'今晚':0,'今日':0,'明天':1,'明早':1,'明晚':1,'明日':1,
     '后天':2,'后日':2,'未来三天':None,'未来3天':None}


def normalize_spans(tool,text,spans,defaults,previous=None):
    if tool not in FIELDS:raise ValueError('unknown_tool')
    values={key:[] for key in FIELDS[tool]}
    for span in spans:
        if not isinstance(span,dict) or span.get('type') not in values:
            raise ValueError('invalid_field')
        offset=span.get('offset')
        if (not isinstance(offset,list) or len(offset)!=2 or
            any(type(i) is not int for i in offset) or not 0<=offset[0]<offset[1]<=len(text)
            or text[offset[0]:offset[1]]!=span.get('span')):
            raise ValueError('span_not_in_input')
        value=span['span'].strip()
        if value and value not in values[span['type']]:values[span['type']].append(value)
    def clarification(message):
        return {'status':'clarify','tool':tool,'arguments':{},'message':message}
    if tool=='weather':
        if len(values['城市'])>1:return clarification('请一次指定一个城市，我先查哪个城市？')
        prior={}
        if previous and previous.get('tool')=='weather':
            prior=previous.get('partial_arguments') or previous.get('arguments',{})
        city=next(iter(values['城市']),None)
        if not city:city=prior.get('city')
        city=city or defaults.get('default_city','').strip()
        arguments={}
        if len(values['时间'])>1:return clarification('请指定今天、明天或后天中的一天。')
        if values['时间']:
            date=values['时间'][0]
            if date not in DAY:return clarification('目前支持今天、明天、后天或未来三天的天气，请指定其中一个。')
            if DAY[date] is not None:arguments['day_offset']=DAY[date]
        else:
            if not prior or 'day_offset' in prior:arguments['day_offset']=prior.get('day_offset',0)
        if not values['时间'] and re.search(r'昨天|前天|上周|大后天|下周|下个月|\d+月\d+[日号]',text):
            return clarification('目前支持今天、明天、后天或未来三天的天气，请指定其中一个。')
        if not city:
            result=clarification('你想查哪个城市的天气？')
            result['partial_arguments']=arguments
            return result
        arguments={'city':city,**arguments}
    elif tool=='news':
        arguments={'source':defaults.get('news_source','bbc'),'limit':defaults.get('news_limit',3)}
        if len(values['新闻来源'])>1:return clarification('请先选一个新闻来源：BBC 或中新网。')
        if values['新闻来源']:
            source=SOURCE_ALIASES.get(values['新闻来源'][0].lower())
            if not source:return clarification('目前新闻来源支持 BBC 和中新网，你想用哪个？')
            arguments['source']=source
        if len(values['条数'])>1:return clarification('你想听几条新闻？目前支持一到五条。')
        if not values['条数'] and re.search(r'[一二两三四五六七八九十\d]+\s*[条则篇]',text):
            return clarification('请确认要读几条新闻，目前支持一到五条。')
        if not values['新闻来源'] and any(source in text.lower() for source in SOURCE_ALIASES):
            return clarification('请确认新闻来源：BBC 还是中新网？')
        if values['条数']:
            number=values['条数'][0]
            number=re.sub(r'[条则篇个\s]','',number)
            count={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5}.get(number)
            if count is None and re.fullmatch(r'\d{1,2}',number):count=int(number)
            if count is None or not 1<=count<=5:return clarification('目前每次可读一到五条新闻，你想听几条？')
            arguments['limit']=count
        if len(values['新闻关键词'])>1:return clarification('请先给一个新闻关键词。')
        if values['新闻关键词']:arguments['keyword']=values['新闻关键词'][0]
    else:
        sections=[]
        for value in values['查询项']:
            section=SECTIONS.get(value) or SECTIONS.get(value.lower())
            if not section:return clarification('目前能查询 CPU、内存、磁盘和系统运行时长，请指定其中一项。')
            if section not in sections:sections.append(section)
        arguments={'sections':sections} if sections else {}
    validate_arguments(tool,arguments)
    return {'status':'ready','tool':tool,'arguments':arguments}
