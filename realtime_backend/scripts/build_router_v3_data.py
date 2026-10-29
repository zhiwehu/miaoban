"""Create evaluation-separated hard negatives and slot examples for the next adapter."""
import hashlib
import itertools
import json
from pathlib import Path
import random
import re
from build_router_slot_data import render

def norm(text):return re.sub(r'[\W_]+','',text).lower()

def build(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    regression=Path('../diagnostics/2026-10-29/router-stress-v1/cases.jsonl')
    if not regression.exists():raise ValueError('Frozen stress regression dataset required')
    forbidden={norm(json.loads(line)['text']) for line in regression.read_text().splitlines()}
    for split in ('test',):
        forbidden.update(norm(json.loads(line)['text']) for line in Path('data/training/router-v1',split+'.jsonl').read_text().splitlines())
    seen=set(forbidden);manifest={};rng=random.Random(20261029)
    topics=['相对湿度','降水概率','天气预报','气象','CPU','内存','硬盘','新闻','开机时间','RSS','处理器','雨量','体感温度','系统状态']
    negative={
      'train':['我只是想理解{topic}这个词，并没有让你查询','不要执行任何查询，讲解{topic}的原理',
        '前面说错了，不用获取{topic}，说个故事吧','取消刚才的{topic}请求，不要继续调用',
        '把“查询{topic}”当成例句进行语法分析','你支持{topic}这个功能吗，现在不要执行',
        '{topic}在百科里的定义是什么','教我怎么表达{topic}的查询意图，别真的查',
        '帮我确认今天到底是星期几','能不能告诉我今日是几月几日','我忘了现在是周几了',
        '现在的时间是几点钟','此时的日期是什么','请说一下明天是礼拜几','what is the current date',
        'which day of the week is today','昨晚和今天的日期相差几天'],
      'validation':['只讲{topic}的定义，不要获取实时数据','暂且停止{topic}查询，我换个问题',
        '能否用通俗的话说明{topic}的含义','询问{topic}应当怎么措辞，我只要示例',
        '请告诉我今天对应哪个星期','现在日期是多少，麻烦说一下','what weekday is it now',
        '今日的月份和日号请告诉我','不用联网查，今天是周几呢'],
      'test':['我没要执行{topic}，我是在问它的意思','别再向服务请求{topic}了，解释概念即可',
        '用{topic}造句，不要把句子当成命令','你能提供{topic}查询吗？只问是否支持',
        '问一下今天在一周里是第几天','能说说此刻是哪年哪月哪日吗','tell me today s weekday',
        '昨天对应的星期你知道吗','不查询外面的数据，告诉我今日日期']}
    positive={
      'train':{'weather':['不是问原理，请实际获取{city}{time}天气','重新发起{city}{time}的气象查询'],
        'news':['不要解释新闻，请读取{source}的{number}条{keyword}新闻','我现在要实际听{source}的{number}条新闻'],
        'system_info':['我是在请求读取本机{section}，请实际查询','调取电脑的{section}和{section2}指标']},
      'validation':{'weather':['此刻执行{city}{time}气象查询给我'],
        'news':['请实际向{source}索取{number}条{keyword}资讯'],
        'system_info':['请从这台机器实际取得{section}数值']},
      'test':{'weather':['把{city}{time}气象服务结果拿来看看'],
        'news':['我需要{source}新发布的{number}条{keyword}报道，请现在读取'],
        'system_info':['现在调用工具获取主机的{section}']}}
    contexts=['读取上海明天的天气','请播报BBC新闻','查询电脑内存','']
    for split in ('train','validation','test'):
        rows=[]
        if split in ('train','validation'):
            for row in map(json.loads,Path('data/training/router-slots-v2',split+'.jsonl').read_text().splitlines()):
                if norm(row['text']) not in seen:rows.append(row);seen.add(norm(row['text']))
        def add(row):
            if norm(row['text']) in seen:return
            seen.add(norm(row['text']));row['synthetic']=True
            row['id']=hashlib.sha256(row['text'].encode()).hexdigest()[:16]
            previous=contexts[len(rows)%len(contexts)]
            if previous:row['classification_text']='历史用户：'+previous+'\n当前用户：'+row['text']
            rows.append(row)
        prefixes=('','请你帮忙，','劳驾，','嗯，','我换一个问题，') if split=='train' else ('','想请教你，','请帮我确认，')
        for i,template in enumerate(negative[split]):
            for topic,prefix,suffix in itertools.product(topics if '{topic}' in template else [''],prefixes,('','，拜托了')):
                add({'text':prefix+template.format(topic=topic)+suffix,'label':'null','slots':[],
                     'family':f'v3.{split}.null.{i}','expected':{'status':'chat','tool':None,'arguments':{}}})
        values={'city':{'train':['南京','武汉','苏州','天津'],'validation':['合肥','宁波'],'test':['兰州','贵阳','厦门']}[split],
                'time':['今天','明天','后天'],'source':['BBC','中新网'],'number':['一','两','三'],
                'keyword':['人工智能','体育','科技'],'section':['CPU','内存','磁盘','运行时长','GPU'],
                'section2':['CPU','内存','磁盘']}
        for tool,templates in positive[split].items():
            for index,template in enumerate(templates):
                keys=[k for k in values if '{'+k+'}' in template]
                for combo in itertools.product(*(values[k] for k in keys)):
                    variables=dict(zip(keys,combo))
                    if 'section' in variables and variables.get('section')==variables.get('section2'):continue
                    text,slots=render(template,variables)
                    status='ready'
                    if tool=='weather':args={'city':variables['city'],'day_offset':{'今天':0,'明天':1,'后天':2}[variables['time']]}
                    elif tool=='news':
                        args={'source':{'BBC':'bbc','中新网':'chinanews'}[variables['source']],
                              'limit':{'一':1,'两':2,'三':3}[variables['number']]}
                        if 'keyword' in variables:args['keyword']=variables['keyword']
                    else:
                        if variables['section']=='GPU':status='clarify';args={}
                        else:
                            mapping={'CPU':'cpu','内存':'memory','磁盘':'disk','运行时长':'uptime'}
                            args={'sections':list(dict.fromkeys(mapping[variables[k]] for k in ('section','section2') if k in variables))}
                    add({'text':text,'label':tool,'slots':slots,'family':f'v3.{split}.{tool}.{index}',
                         'expected':{'status':status,'tool':tool,'arguments':args}})
        rng.shuffle(rows)
        content=''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows)
        (output/(split+'.jsonl')).write_text(content)
        manifest[split]={'count':len(rows),'labels':dict(__import__('collections').Counter(r['label'] for r in rows)),
                         'families':len({r['family'] for r in rows}),'sha256':hashlib.sha256(content.encode()).hexdigest()}
    manifest['regression_sha256']=hashlib.sha256(regression.read_bytes()).hexdigest()
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    build(parser.parse_args().output)
