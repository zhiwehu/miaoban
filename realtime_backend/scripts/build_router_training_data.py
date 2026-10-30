"""Synthetic, labelled request families; split families before expanding entities."""
import hashlib
import itertools
import json
import random
import re
from pathlib import Path
from benchmark_laya_router import CASES

FAMILIES = {
 'train': {
  'weather': [
   '{city}{day}的气象预报帮我调出来', '我准备{day}去{city}，查查会遇到什么天气',
   '想了解{city}{day}的气温区间，请获取最新预报', '{city}{day}可能有雨吗，查气象数据告诉我',
   '替我确认一下{city}{day}的室外温度', '出行前请查询{city}{day}的降水情况',
   '请向天气服务查询{city}{day}的预报', '我要一份{city}{day}的天气简报',
   '用实时气象信息看看{city}{day}要不要打伞', '先停止播新闻，改为获取{city}{day}气象情况',
   '历史用户：帮我读新闻。当前用户：现在请查询{city}{day}天气。',
   '历史用户：{city}{day}会下雨吗？历史助手：你想查哪个城市？当前用户：{city}。'],
  'news': [
   '替我获取一份最新的新闻简报', '打开新闻源，挑{n}则新报道给我',
   '我想听听此刻的国际要闻，读{n}条', '查阅RSS，播报刚发布的{n}个标题',
   '查询新闻源中关于{topic}的最新报道', '现在请从BBC读取{n}条报道',
   '能帮我搜一下中新网最近的{topic}消息吗', '今天先听新闻，请获取{n}条资讯',
   '不用查看电脑了，给我获取{topic}新闻', '历史用户：解释一下CPU。当前用户：接下来读取最新报道。'],
  'system_info': [
   '查询正在运行声桥的电脑{metric}', '从系统读取一下当前{metric}',
   '帮我采集这台主机的{metric}', '我想确认后端服务器此刻的{metric}',
   '打开本机监测，告诉我{metric}', '用工具看一下当前机器的{metric}',
   '不要讲原理，请实际读取本机{metric}', '现在给我一份这台电脑的资源使用概况',
   '读取系统接口，查看机器启动至今的时间', '历史用户：读一下新闻。当前用户：改为查询后端主机{metric}。'],
  'null': [
   '早上好，很高兴见到你', '我们随便聊聊，不必执行任何查询', '我今天有点累，陪我说说话',
   '讲一则适合小朋友的睡前故事', '把这句话换一种说法', '你喜欢什么颜色',
   '请用通俗的话介绍{topic}这个概念', '{city}是个怎样的城市？只讲城市常识',
   '“{city}{day}天气预报”这几个字如何翻译成英文', '我的朋友刚说“查一下{city}天气”，你觉得这句话礼貌吗',
   '别获取{city}{day}气象数据了，我们聊别的', '不要获取{topic}新闻，换个话题吧',
   '请解释{metric}是什么意思，不要实际查询', '学习电脑知识时为什么要关注{metric}',
   '你有没有读取{metric}的能力？我只想了解功能', '举例说明如何向机器人询问{city}天气，不用真的查',
   '{city}{day}的天气让朋友自己查，我们讲故事', '写一个关于雨天的比喻，不需要天气预报',
   '如果将来要查询{topic}新闻，需要有哪些步骤？这里只讨论方案',
   '描述一个机器人查看{metric}的虚构场景', '先别打开新闻源，我没有要听新闻',
   '历史用户：查询{city}天气。当前用户：算了，取消，陪我聊天。',
   '历史用户：查询本机{metric}。当前用户：不查了，只解释这个术语。',
   '历史用户：获取{topic}新闻。当前用户：刚才只是举例，不需要执行。',
   '帮我播放音乐', '关闭卧室的灯', '把屏幕调亮', '请帮我删除一个文件',
   '现在不需要工具，简单回答就好', '联网查股票价格吧', '告诉我查询工具与聊天的区别',
   '请先停下来，不要继续上一项操作', '新闻播报应当遵守哪些基本原则',
   '假设你正在读新闻，请写一段模拟开场白', '所谓CPU占用率高通常有哪些原因'],
 },
 'validation': {
  'weather':['帮我拿到{city}{day}气象台预报', '在{city}{day}晾衣服合适吗？依据最新天气判断',
             '{day}要去{city}，给我实际查一下雨量概率', '查询{city}{day}的体感气温'],
  'news':['读取最近发布的{topic}资讯，给我{n}则', '此刻有哪些新报道？获取{n}条看看',
          '请从BBC的订阅源拿{n}个最新标题', '从中新网挑{n}则新消息给我听'],
  'system_info':['查一下本机实时监控中的{metric}', '我想获取当前后端电脑的{metric}',
                '实际检测一下这台电脑当前资源情况', '能查询一下这部主机的{metric}吗'],
  'null':['讲解一下什么叫{metric}，只要解释', '我不打算查询{city}的天气，别联网',
          '获取{topic}新闻这个动作一般是怎么实现的', '“帮我查看{metric}”这句话请改成英文',
          '有办法查天气吗？这里只问你支持哪些能力', '天气不错，我们一起想个故事吧',
          '用户前面问过{city}天气，但现在说：撤销查询。', '只讨论{topic}的常识，不读新闻',
          '把声音调到一半大小', '什么样的硬盘适合存照片', '谢谢你，你可以休息了'],
 },
 'test': {
  'weather':['给我拉取{city}{day}天气的最新结果', '{city}{day}能不能户外散步？先查预报再说',
             '我要核实{city}{day}的降水风险', '拿一下{city}{day}最高最低气温的数据',
             '请检查{city}{day}当地的气象状况', '前一轮聊的是新闻；这一轮要查{city}{day}的天气'],
  'news':['现在想听{n}条刚更新的报道，帮我取一下', '查询订阅源里最新的{topic}事件报道',
          '从新闻列表中读出最近的{n}则标题', '我想了解BBC世界频道刚更新的消息',
          '请实际获取中新网最新的{n}条资讯', '天气暂时不用管，替我读{n}条最新消息'],
  'system_info':['请取出运行服务这台电脑的{metric}数据', '帮我核实当前这台机器的{metric}',
                '我需要一份主机当下资源报告，请读取系统', '检测本机资源，还有多少空余',
                '读取电脑从启动到现在的时长', '当前用户想知道实际{metric}，并非解释它的概念'],
  'null':['做个假设：某人让你查{city}天气，你会如何回复？不要真的执行',
          '把“读取{metric}”当成一句台词，续写一段对话',
          '我只是说{city}的气候好，没有让你查询天气',
          '想问你是否具备查看{metric}的功能，暂时不用查',
          '解释{topic}相关的基本常识，别拉取新闻',
          '我收回刚才获取{topic}资讯的要求，停止',
          '帮我写一个查询{city}天气的函数示例，不要发实际请求',
          '谈谈{metric}为什么值得关注，别调用系统接口',
          '历史用户：查看{city}气象数据。当前用户：不用继续，回答一道数学题。',
          '历史用户：读取{metric}。当前用户：刚刚说错了，取消吧。',
          '听到有人说“播报新闻”，请告诉我这四个字有几个汉字',
          '今天心情有点阴天，安慰我一下', '请把播放的歌曲暂停',
          '用一句话向我道晚安', '浏览器与操作系统是什么关系',
          '说明如何提出一个清晰的问题，不用做任何查询'],
 }
}


def normalize(text):
    return re.sub(r'[\W_]+','',text).lower()


def build(root):
    random.seed(20261028)
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    forbidden={normalize(text) for text,_ in CASES}
    seen=set(forbidden)
    limits={'train':{'null':650,'weather':300,'news':300,'system_info':300},
            'validation':{'null':100,'weather':60,'news':60,'system_info':60},
            'test':{'null':150,'weather':80,'news':80,'system_info':80}}
    manifest={}
    for split, labels in FAMILIES.items():
        cities=['南京','武汉','苏州','天津','长沙','厦门'] if split=='train' else (
            ['宁波','合肥','福州'] if split=='validation' else ['青岛','郑州','昆明','西安'])
        variables={'city':cities,'day':['今天','明天','后天'], 'n':['一','两','三','四','五'],
                   'topic':['科技','体育','国际','人工智能','经济','文化'],
                   'metric':['CPU使用比例','可用内存','磁盘剩余容量','内存占用率','CPU当前负载','系统运行时长']}
        rows=[]
        for label,templates in labels.items():
            candidates=[]
            for index,template in enumerate(templates):
                keys=[key for key in variables if '{'+key+'}' in template]
                variants=itertools.product(*(variables[key] for key in keys))
                for values in variants:
                    text=template.format(**dict(zip(keys,values)))
                    # All wrappers inherit their source template's split.
                    for prefix,suffix in [('', ''),('麻烦你，',''),('','，谢谢'),('请帮个忙，','。')]:
                        value=prefix+text+suffix
                        normalized=normalize(value)
                        if normalized in seen:continue
                        candidates.append({'text':value,'label':label,
                            'family':f'{split}.{label}.{index}', 'synthetic':True})
            random.shuffle(candidates)
            selected=candidates[:limits[split][label]]
            for row in selected:
                key=normalize(row['text'])
                if key in seen:continue
                seen.add(key)
                row['id']=hashlib.sha256(row['text'].encode()).hexdigest()[:16]
                rows.append(row)
        random.shuffle(rows)
        (root/(split+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
        manifest[split]={'count':len(rows),'labels':{label:sum(r['label']==label for r in rows) for label in labels},
                         'families':len({r['family'] for r in rows})}
    manifest['policy']='Synthetic template-family holdout; exact-normalized 34 regression cases excluded; no production logs.'
    (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    build(parser.parse_args().output)
