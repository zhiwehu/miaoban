"""Train-only paraphrase expansion after validation error analysis; frozen tests untouched."""
import hashlib,json,random,re,sys,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.build_everyday_router_data import ENTITIES,norm
PATTERNS={
'shopping_list':{
'add':['购物列表记上{item}','待购清单增加{item}','采购计划添个{item}','还得采购{item}，帮我列进去','将{item}登记到购物列表','把{item}作为待采购条目保存','准备购买{item}，记到清单上','清单上还少{item}这一项','待买的东西加上{item}','帮忙新增待购买的{item}','把{item}写进超市采购列表','新增购物条目{item}'],
'list':['我的采购列表都有什么','待买事项读给我听听','给我看看未购买商品','把购物列表报一下','还有哪些商品没买齐','想知道购物清单里全部内容','超市采购的单子拿出来看看','我都列了哪些要买的东西','帮我核对待购买物品列表','显示当前购物条目','列举尚待购入的商品','待买清单念一遍'],
'complete':['在清单上把{item}标为购入','已经购入{item}，请更新清单','待买的{item}现在买完了','采购到{item}了，勾选已购','把{item}的购买状态改为完成','{item}已买到手，清单打勾','{item}采购完成，请标记','待购条目{item}划成已买','今天买到了{item}，给清单打勾','购物列表{item}这项可以勾选完成','为{item}标记购买已完成','买好的{item}不用再采购了，更新状态'],
'remove':['把{item}从待购物品里撤销','不打算购买{item}了，移出清单','采购列表删去{item}这一项','撤销清单上{item}的采购计划','待买列表不再保留{item}','从购物单中剔除{item}','清单上关于{item}这条不要了','把{item}的待购条目取消','{item}从待购物品中撤掉','购物计划中的{item}先移除','删去商品{item}，没有买只是不要了','购物清单移走{item}这个条目']},
 'timer':{
 'start':['倒计时{duration}','帮忙计时{duration}','启动一个{duration}计时器','开个{duration}的倒数','我要一个{duration}倒计时','创建计时任务，{name}，{duration}','设{name}计时{duration}，现在开始','{name}从现在计{duration}','给{name}新建{duration}的定时倒数','{name}的计时设成{duration}并启动','开始计算{name}，计{duration}','我要给{name}计{duration}的时间'],
 'status':['查查{name}还剩几秒钟','{name}的计时余额是多少','给我看看全部计时任务','{name}倒计时还有多少没走完','查{name}当前计时进度','目前所有倒数的剩余时长','正在倒数的{name}到什么进度','{name}离倒计时结束还有多长','计时任务{name}剩几分几秒','我开着的计时器还剩多少秒','查询{name}倒计时剩余时长','看看正在计时的{name}'],
 'pause':['把{name}计时临时停住','{name}计时停一停但不要清掉','让{name}的倒计时保持暂停','将{name}计时器转为暂停状态','我要暂停{name}，稍后继续','{name}计时先按住不走','把{name}倒计时暂时冻结','暂停{name}的时间计算','{name}计时停止走动保留余量','先把{name}倒计时挂起','请暂停{name}而不是取消','{name}的计时中途暂停一下'],
 'resume':['让{name}从暂停状态重新计时','把{name}暂停的时间接着算','{name}从停住的地方继续倒数','重新继续{name}已经暂停的计时器','将暂停的{name}计时器恢复运行','{name}结束暂停，继续计时','{name}的计时可以重新走了','按继续键恢复{name}倒计时','接着计算{name}暂停前剩下的时间','把暂停解除，{name}继续倒数','恢复已挂起的{name}计时任务','{name}不要再暂停了，接着计时'],
 'cancel':['{name}的计时彻底撤销','{name}不用了，把计时器关了','移除{name}这个计时任务','删除{name}的定时倒数','停止并删除{name}计时器','{name}倒计时取消，不要再报时','将{name}倒计时关闭并作废','{name}的计时不需要保留，取消吧','把{name}计时记录作废','不必再为{name}倒数了，撤销计时','{name}计时结束，不要响铃','放弃{name}倒计时任务']},
 'item_memory':{
 'remember':['帮忙保存{object}，它在{place}','更新{object}的位置为{place}','登记一条存放记录：{object}在{place}','以后要记得{object}收在{place}','把{object}在{place}这件事记下来','{object}的存放地改成{place}，帮我存好','记录一下{object}的存放位置','记住{object}的位置','物品备忘录加一项，{object}在{place}','请保存{object}的新位置{place}','{object}换地方了，现在在{place}，记着','{object}存放信息更新：{place}'],
 'recall':['我以前记的{object}位置在哪里','找出{object}曾经保存的位置','查{object}的存放处','记忆中{object}应该放在哪里','{object}当时收在了什么地方','你记下的{object}位置说一下','保存过的{object}位置告诉我','给我看看{object}的存放备忘','我存过{object}在哪儿，翻出来','查找关于{object}的存放记录','我找不到{object}，看看位置记录','物品备忘里{object}放哪'],
 'forget':['关于{object}的存放记忆撤销','从备忘录移除{object}那条位置','不再保留已存的{object}位置','把{object}之前登记的位置删除','已记录的{object}地点请忘记','忘了{object}那条存放信息吧','删除保存过的{object}在哪里的信息','{object}的位置备忘请清掉','把{object}的旧位置从记忆中移走','清除已存的{object}位置记录','以前那条{object}存放信息作废','{object}位置登记撤销掉'],
 'list':['把物品存放备忘录全部列出','已存了哪些物品的所在位置','所有位置记忆都有哪些','登记过位置的东西帮我汇总','列出物品位置备忘的全部条目','有哪些物品的位置让你记住了','查查我以前登记过的所有物品地点','存放备忘录里有什么东西','把已经保存的地点记录念一下','我让你记住的全部物品位置呢','所有存放位置列表打开看看','保存的东西放在哪里全部说一遍']}}
NEG=['只是聊聊{item}，别添加购物清单','不要记{object}放在{place}','别真执行，解释暂停计时的含义','不要把{name}计时器继续运行','现在不需要{duration}倒计时，只是讨论','别修改任何位置记录，我问个概念','“删除物品位置”只是示例文字','我买了{item}有什么好处，不要记清单','可以设置倒计时吗，先不操作','告诉我“存放位置”这个词的意思','不要启动计时，也不要查天气','先不购物，我就随便说说','不要清除{object}位置记录，保持原样','讲个购物的笑话，别动清单']
def main():
 source=Path('data/training/router-everyday-v4');out=Path('data/training/router-everyday-v4b');out.mkdir(parents=True,exist_ok=True)
 rows=[json.loads(x) for x in (source/'train.jsonl').read_text().splitlines()];original_count=len(rows);seen={norm(r['text']) for r in rows};forbidden=set()
 for path in (source/'validation.jsonl',source/'test.jsonl',Path('training/everyday_challenge.jsonl')):
  forbidden.update(norm(json.loads(x)['text']) for x in path.read_text().splitlines())
 rng=random.Random(20261031);entities=ENTITIES[0]
 def add(text,tool,action,family):
  key=norm(text)
  if key in seen or key in forbidden:return
  seen.add(key);row={'text':text,'label':tool,'family':family,'synthetic':True,'id':hashlib.sha256(text.encode()).hexdigest()[:16]}
  if action:row['action']=action
  rows.append(row)
 for tool,actions in PATTERNS.items():
  for action,templates in actions.items():
   for index,pattern in enumerate(templates):
    for repeat in range(18):
     values={k:rng.choice(v) for k,v in entities.items()};text=rng.choice(['','请','麻烦，','帮忙，'])+pattern.format(**values)
     add(text,tool,action,f'train.expanded.{tool}.{action}.{index}')
 for index,pattern in enumerate(NEG):
  for repeat in range(24):add(pattern.format(**{k:rng.choice(v) for k,v in entities.items()}),'null',None,f'train.expanded.negative.{index}')
 rng.shuffle(rows);(out/'train.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
 for name in ('validation','test'):shutil.copy2(source/(name+'.jsonl'),out/(name+'.jsonl'))
 manifest={'note':'Expanded train only; validation used for error analysis; test and manual challenge never used for fitting.','count':len(rows),'added':len(rows)-original_count,'sha256':{n:hashlib.sha256((out/(n+'.jsonl')).read_bytes()).hexdigest() for n in ('train','validation','test')}}
 (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));print(json.dumps(manifest,ensure_ascii=False))
if __name__=='__main__':main()
