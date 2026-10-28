"""Label native Rex spans plus exact, independently specified MCP plans."""
import hashlib
import itertools
import json
import random
import re
import string
from pathlib import Path
from benchmark_laya_router import CASES

KINDS={'city':'城市','time':'时间','source':'新闻来源','number':'条数','keyword':'新闻关键词',
       'section':'查询项','section2':'查询项'}
TEMPLATES={
 'train':{
  'weather':['替我查询{city}{time}的天气','我想获取{city}{time}气象预报',
             '{city}{time}要不要带伞，请实际查询','麻烦读取{city}{time}的温度',
             '帮忙看看{city}{time}会不会有雨','查询{city}天气','{time}天气怎么样'],
  'news':['从{source}读{number}条{keyword}新闻','获取{number}则{keyword}消息，来源用{source}',
          '帮忙播报{source}最新的{number}条消息','给我{number}条关于{keyword}的新闻',
          '读取{source}关于{keyword}的最新消息','给我播报新闻'],
  'system_info':['请读取当前{section}','看看这台电脑的{section}情况',
                 '获取本机{section}和{section2}','查询服务器{section}指标','我想检查电脑状态']},
 'validation':{
  'weather':['获取一份{city}{time}的气象结果','气象服务里{city}{time}预报如何',
             '现在想查询{city}的天气状况','请查询{time}的气象预报'],
  'news':['挑出{source}发布的{number}则{keyword}新闻','向我播送{number}条{keyword}新报道',
          '取一下{source}的新消息','看看有什么新闻'],
  'system_info':['检查当前主机{section}的数值','我需要本机{section}与{section2}的数据',
                 '给我系统的资源概况']},
 'test':{
  'weather':['请为我调取{city}{time}天气信息','我打算出门，请核实{city}{time}天气',
             '能查询{city}此刻的气象情况吗','我现在想查{time}的天气'],
  'news':['把{source}的{keyword}新报道读{number}条给我','请查询{number}则{keyword}最新资讯',
          '我需要听{source}刚更新的消息','新闻简报来一份'],
  'system_info':['核查这部电脑目前的{section}','向系统获取{section}以及{section2}的状态',
                 '请实际查询机器资源']}}


def norm(text):return re.sub(r'[\W_]+','',text).lower()


def render(template,variables):
    text='';slots=[]
    for literal,key,_,_ in string.Formatter().parse(template):
        text+=literal
        if key:
            value=variables[key];start=len(text);text+=value
            slots.append({'type':KINDS[key],'span':value,'offset':[start,len(text)]})
    return text,slots


def build(output,classification_data):
    random.seed(20261029)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    old=Path(classification_data)
    forbidden={norm(text) for text,_ in CASES}
    for row in (json.loads(line) for line in (old/'test.jsonl').read_text().splitlines()):
        forbidden.add(norm(row['text']))
    seen=set(forbidden);manifest={}
    for split,templates in TEMPLATES.items():
        cities={'train':['南京','武汉','苏州','天津','长沙','厦门'],
                'validation':['宁波','合肥','福州'],'test':['哈尔滨','大连','南昌','多伦多']}[split]
        values={'city':cities,'time':['今天','明天','后天','现在','未来三天','下周'],
                'source':['BBC','中新网','中国新闻网','英国广播公司','路透社'],
                'number':['一','两','三','五','2','4','十'],
                'keyword':['科技','体育','人工智能','国际','经济'],
                'section':['CPU','内存','磁盘','运行时长','GPU'],
                'section2':['CPU','内存','磁盘','开机时间']}
        rows=[]
        for tool,patterns in templates.items():
            candidates=[]
            for index,template in enumerate(patterns):
                keys=[k for k in values if '{'+k+'}' in template]
                for combination in itertools.product(*(values[k] for k in keys)):
                    variable=dict(zip(keys,combination))
                    if variable.get('section')==variable.get('section2') and 'section' in variable:continue
                    text,slots=render(template,variable)
                    if norm(text) in seen:continue
                    status='ready';arguments={}
                    if tool=='weather':
                        if 'city' not in variable or variable.get('time')=='下周':status='clarify'
                        else:
                            arguments['city']=variable['city']
                            if variable.get('time')!='未来三天':arguments['day_offset']={'明天':1,'后天':2}.get(variable.get('time'),0)
                    elif tool=='news':
                        if variable.get('source')=='路透社' or variable.get('number')=='十':status='clarify'
                        else:
                            arguments={'source':'chinanews' if variable.get('source') in ('中新网','中国新闻网') else 'bbc',
                                       'limit':{'一':1,'两':2,'三':3,'五':5,'2':2,'4':4}.get(variable.get('number'),3)}
                            if 'keyword' in variable:arguments['keyword']=variable['keyword']
                    else:
                        if variable.get('section')=='GPU':status='clarify'
                        elif 'section' in variable:
                            sections=[]
                            for key in ('section','section2'):
                                if key in variable:
                                    section={'CPU':'cpu','内存':'memory','磁盘':'disk','运行时长':'uptime','开机时间':'uptime'}[variable[key]]
                                    if section not in sections:sections.append(section)
                            arguments={'sections':sections}
                    row={'text':text,'label':tool,'slots':slots,'family':f'slots.{split}.{tool}.{index}',
                         'expected':{'status':status,'tool':tool,'arguments':arguments if status=='ready' else {}},
                         'synthetic':True}
                    for prefix in ('','麻烦你，','请帮忙，'):
                        wrapped={**row,'text':prefix+text,
                                 'slots':[{**s,'offset':[x+len(prefix) for x in s['offset']]} for s in slots]}
                        if norm(wrapped['text']) not in seen:candidates.append(wrapped)
            random.shuffle(candidates)
            maximum={'train':500,'validation':80,'test':100}[split]
            for row in candidates[:maximum]:
                seen.add(norm(row['text']));rows.append(row)
        # Keep v1 training only. Positive rows without span labels are classification-only.
        if split=='train':
            for line in (old/'train.jsonl').read_text().splitlines():
                row=json.loads(line)
                if norm(row['text']) not in seen:
                    row['classification_only']=True;seen.add(norm(row['text']));rows.append(row)
        null_patterns={
          'train':['你是否具有天气查询功能','说一下相对湿度的含义，不要查实时数据',
                   '有人说“查上海天气”，请把这句话翻译成英文','新闻暂时别读，讲讲内存和CPU的区别',
                   '只是讨论如何查天气，并不是让你现在执行','请举例说明查询电脑状态的提问方法',
                   '我不想知道当前内存，只问内存这个概念','能介绍你有哪些功能吗，不需要查询',
                   'CPU占用率通常如何定义，请讲解概念','天气查询支持哪些参数？不用执行'],
          'validation':['请介绍天气查询功能，先不要执行','讲一下CPU使用率这个术语的含义',
                        '我说新闻是个例子，不要真的读新闻','说说如何向机器人提问，不要执行示例'],
          'test':['你具备查询天气的能力吗？我只想确认功能','雨量概率表示什么，请解释一下',
                  '查询内存这四个字怎么翻译？','用新闻查询写一句示例指令，不执行',
                  '本次不查系统状态，只讲讲硬盘原理']}[split]
        for index,text in enumerate(null_patterns):
            for prefix in ('','麻烦你，','请注意，','我想问一下，'):
                for suffix in ('','，谢谢','，只是了解一下'):
                    value=prefix+text+suffix
                    if norm(value) in seen:continue
                    seen.add(norm(value));rows.append({'text':value,'label':'null','slots':[],
                        'family':f'slots.{split}.null.{index}','expected':{'status':'chat','tool':None,'arguments':{}},'synthetic':True})
        # Old validation/test are not trained; new validation/test examples are separately authored.
        for i,row in enumerate(rows):
            row['id']=hashlib.sha256(row['text'].encode()).hexdigest()[:16]
            if not row.get('classification_only') and i%3==0:
                previous=['给我讲个故事','查一下上海天气','读取新闻','查询内存'][i%4]
                row['classification_text']='历史用户：'+previous+'\n当前用户：'+row['text']
        random.shuffle(rows)
        (output/(split+'.jsonl')).write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows))
        manifest[split]={'count':len(rows),'classification_only':sum(r.get('classification_only',False) for r in rows),
                         'with_slots':sum('slots' in r for r in rows),'families':len({r['family'] for r in rows})}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    parser.add_argument('--classification-data',default='data/training/router-v1')
    args=parser.parse_args();build(args.output,args.classification_data)
