"""Independent synthetic regression cases. Expectations are authored, not model-generated.

Variants in one family are correlated: always report both case and family counts.
This file is evaluation-only and must not be imported by training data builders.
"""
import hashlib
import json
import re


def cases():
    seeds=[]
    def add(category,text,status,tool=None,arguments=None,**extra):
        seeds.append(dict(category=category,text=text,expected=dict(status=status,tool=tool,
                     arguments=arguments or {}),**extra))
    for text in ['今天星期几','今天是周几','你知道今天星期几吗','今天礼拜几','请问今日星期几',
                 '明天是星期几','昨天是周几','今天几号','今天是几月几号','上海今天星期几']:
        add('calendar',text,'calendar')
    for text in ['现在几点了','今天是什么节日','今年是哪一年','下周一是几月几号',
                 '一周有几天','星期几是什么意思','解释一下日期与星期的关系',
                 '今天星期几这句话翻译成英文','写一句包含今天星期几的台词']:
        add('calendar_other',text,'chat')
    for term in ['相对湿度','降水概率','体感温度','天气预报','新闻','CPU','内存','磁盘','开机时长','RSS']:
        for pattern in ['请解释什么是{}','{}是什么意思']:
            add('explanation',pattern.format(term),'chat')
    for text in ['你好','给我讲个故事','写一首春雨的诗','天气真好呀','今天心情不错',
                 '天气查询工具是怎么工作的','新闻工具有哪些用途','你会查天气吗',
                 '查询内存需要哪些步骤，只讲方法','帮我播放音乐','打开卧室的灯','调高音量']:
        add('chat_and_unsupported',text,'chat')
    for text in ['不要查天气','先别查新闻','别读取本机内存','取消查询，陪我聊天',
                 '不用查天气，只讲个笑话','别查上海天气，解释天气预报原理',
                 '不要播新闻，解释新闻是什么意思','不用查询电脑，只解释CPU是什么']:
        add('negation',text,'chat')
    for city in ['上海','北京','杭州','广州','深圳','成都','西安','哈尔滨']:
        for day,offset in [('今天',0),('明天',1),('后天',2)]:
            add('weather',f'查一下{city}{day}的天气','ready','weather',{'city':city,'day_offset':offset})
    for text in ['今天天气怎么样','明天会下雨吗','后天天气如何']:
        add('missing_city',text,'clarify','weather')
    for text in ['查上海下周天气','查北京昨天的天气','上海大后天天气怎么样']:
        add('unsupported_date',text,'clarify','weather')
    for source,spoken in [('bbc','BBC'),('chinanews','中新网')]:
        for count,number in [(1,'一'),(2,'两'),(3,'三'),(5,'五')]:
            add('news',f'从{spoken}读{number}条新闻','ready','news',{'source':source,'limit':count})
    for count in ['零','十','20']:
        add('invalid_quantity',f'从BBC读{count}条新闻','clarify','news')
    for key in ['科技','人工智能','体育']:
        add('news_keyword',f'从中新网读两条{key}新闻','ready','news',{'source':'chinanews','limit':2,'keyword':key})
    for spoken,section in [('CPU','cpu'),('内存','memory'),('磁盘','disk'),('运行时长','uptime')]:
        add('system_info',f'查询本机{spoken}','ready','system_info',{'sections':[section]})
    add('system_multi','查询CPU和内存','ready','system_info',{'sections':['cpu','memory']})
    add('unsupported_section','查询GPU使用率','clarify','system_info')
    # Exact numbered/day tokens in ASR-like and English requests, without punctuation.
    for text in ['what day is it today','今天星期几呀','我想知道今天星期几','今天周几来着',
                 '告诉我现在的日期','现在是周几','今天星期几啊','帮我看看今天星期几']:
        add('calendar_paraphrase',text,'calendar')
    rows=[]
    for index,seed in enumerate(seeds):
        # Keep metadata sufficient to recognize that these are variants, not new intents.
        for prefix in ('','麻烦你，','我想问一下，'):
            for suffix in ('','，谢谢'):
                row={**seed,'text':prefix+seed['text']+suffix,'family':f'{seed["category"]}.{index}',
                     'synthetic':True,'evaluation_only':True}
                rows.append(row)
        if seed['category'] in ('calendar','calendar_paraphrase','explanation','negation','missing_city'):
            for context in ('上海明天天气怎么样','读两条新闻','查询本机内存'):
                # No inherited parameter is supplied: this isolates contextual classification.
                rows.append({**seed,'family':f'{seed["category"]}.{index}', 'previous_user':context,
                             'synthetic':True,'evaluation_only':True})
        if seed['category'] in ('calendar','calendar_paraphrase','explanation','negation'):
            rows.append({**seed,'family':f'{seed["category"]}.{index}',
                         'defaults':{'default_city':'上海'},'synthetic':True,'evaluation_only':True})
    for row in rows:
        identity=json.dumps({k:v for k,v in row.items() if k!='id'},ensure_ascii=False,sort_keys=True)
        row['id']=hashlib.sha256(identity.encode()).hexdigest()[:16]
    return rows


def normalized(text):
    return re.sub(r'[\W_]+','',text).lower()
