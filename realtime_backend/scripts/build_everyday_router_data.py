"""Family-separated synthetic decisions; test is frozen before adapter training."""
import hashlib,json,random,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from training.everyday_schema import TOOL_LABELS,ACTION_LABELS

def norm(value):return re.sub(r'[\W_]+','',value).lower()
PATTERNS={
 'shopping_list':{
 'add':[['把{item}加到购物清单','购物清单里添上{item}','我要买{item}，记进采购单','帮我把{item}列入要买的东西'],['采购单请补一项{item}','将{item}登记成待购买'],['购物单漏了{item}，补进去','下次去超市得买{item}，给我写到购物列表']],
 'list':[['看看购物清单','购物单上还缺什么','念一下没买的东西','我还需要买哪些东西'],['采购清单现在有哪几项','请列出待采购的物品'],['出门买东西前帮我看看还缺啥','购物列表上没勾掉的都有哪些']],
 'complete':[['{item}已经买好了','把购物单里的{item}勾掉','{item}买到了，标记一下','采购清单的{item}已购买'],['将{item}改成已买到','购物单中{item}这一项已完成购买'],['刚刚买到了{item}，购物单上打个勾','{item}不用再买了，我已经买了']],
 'remove':[['从购物清单移除{item}','购物单里的{item}删掉','{item}不买了，从清单拿掉','采购单不要{item}这一项了'],['请删除待购的{item}条目','取消采购{item}，清单移除'],['买东西的列表中去掉{item}','采购计划有变，{item}从列表划掉别买']]},
 'timer':{
 'start':[['给{name}计时{duration}','开一个{duration}倒计时叫{name}','{name}需要{duration}，开始计时','启动{name}的{duration}计时器'],['为{name}新开{duration}的计时','开启一个叫{name}的倒数，时长{duration}'],['{name}这件事给我倒数{duration}','现在开始算{name}的时间，过{duration}叫我']],
 'status':[['{name}计时器还剩多久','看看{name}倒计时','现在有哪些计时器','查一下{name}的剩余时间'],['{name}倒数还要多长时间','列一下正在运行的倒计时'],['{name}那个倒数走到哪了','我设的几个计时现在各剩多长']],
 'pause':[['暂停{name}计时器','先停一下{name}的倒计时','{name}暂时暂停计时','把{name}倒数按暂停'],['{name}的计时先暂停保留剩余时间','暂停正在跑的{name}计时'],['{name}先别往下倒数了，保留剩下的','帮{name}按下暂停键，待会接着计时']],
 'resume':[['继续{name}计时器','恢复{name}的倒计时','{name}接着计时','把暂停的{name}倒计时继续'],['将{name}计时从暂停恢复','让{name}的倒数接着运行'],['{name}可以接着倒数了','刚才暂停的{name}现在继续跑']],
 'cancel':[['取消{name}计时器','删除{name}倒计时','{name}不用计时了','结束{name}这个计时器'],['把{name}的倒计时作废','彻底取消{name}计时不再提醒'],['{name}那个倒数不要了','别再给{name}计时，到点也不用叫我']]},
 'item_memory':{
 'remember':[['记住，{object}放在{place}','帮我记一下{object}的位置，在{place}','{object}现在放到{place}了，保存位置','记录{object}在{place}'],['替我登记{object}的存放处是{place}','{object}换到{place}了，更新物品位置'],['以后问{object}在哪里，就按{place}这个位置记','帮忙存个物品位置：{object}，{place}']],
 'recall':[['我把{object}放哪了','{object}在哪里','查一下{object}的位置记录','还记得{object}放哪吗'],['替我找找记过的{object}存放位置','我存的{object}位置是什么'],['之前让你记的{object}到底收哪儿了','帮我翻翻{object}那条存放记录']],
 'forget':[['删除{object}的位置记录','忘掉{object}放在哪里的记录','{object}的存放位置不要记了','清除{object}那条位置记录'],['请移除已经保存的{object}存放信息','撤掉关于{object}的位置备忘'],['有关{object}放哪里的那条记忆删掉','我不想保留{object}的存放记录了']],
 'list':[['我记过哪些物品位置','列出所有物品位置记录','看看保存的物品位置','有哪些东西的位置存过了'],['请列举已登记位置的物品','我让你保存过哪些存放处'],['那些物品位置备忘都给我看看','我以前让你记了哪些东西的存放信息']]} }
NEG=[
 ['不要把{item}加入购物清单','解释购物清单有什么用','不用启动计时器，说个笑话','不要保存{object}的位置','倒计时是什么意思','记住这个成语怎么解释','{item}有什么营养','计时器是怎么工作的','今天星期几','明天下午三点提醒我开会','五分钟后提醒我喝水','不要查天气，我只是聊天'],
 ['我没让你添加{item}，只想了解价格','先别记录{object}的位置','暂停计时的原理是什么','你支持购物清单吗，现在不操作','我记性差是不是正常','查看天气这个说法是什么含义','我的日程明天有哪些','以后再说，先不用任何工具'],
 ['拿“添加{item}到购物单”造个句子，不要真加','我只是提到{object}，没有让你存位置','别给我开任何倒计时','你能记物品位置吗，只问功能','购物单这个词用英语怎么说','查询剩余时间是指什么','我不是要删除记录，讲个故事','后天早上提醒我出门']]
ENTITIES=[
 {'item':['牛奶','鸡蛋','大米','纸巾','洗衣液','苹果','牙膏','面包','土豆','食盐','香蕉','酱油'], 'name':['煮面','蒸蛋','洗衣服','泡茶','休息','烤蛋糕','拉伸','午睡'],'duration':['五分钟','十分钟','三分钟','半小时','一小时','二十秒','十五分钟'],'object':['备用钥匙','护照','充电器','雨伞','遥控器','医保卡','眼镜','保修单'],'place':['玄关抽屉','书房的蓝色文件袋','厨房第二个柜子','床头柜','客厅收纳盒','衣柜最上层']},
 {'item':['酸奶','面粉','垃圾袋','橙子'],'name':['炖汤','散步','敷面膜'],'duration':['七分钟','十二分钟','四十五秒'],'object':['车钥匙','耳机','身份证'],'place':['门后的挂钩','书桌右侧盒子','电视柜下层']},
 {'item':['燕麦','香皂','黄瓜','保鲜膜','矿泉水'],'name':['煎牛排','煮咖啡','健身','擦地'],'duration':['九分钟','十八分钟','两分半','四十秒'],'object':['行李箱钥匙','数据线','公交卡','印章'],'place':['阳台工具箱','鞋柜左侧隔层','电脑包侧袋','抽屉里的红色袋子']}]

def build(output):
 for split in ('train','validation','test'):
  if not (Path('data/training/router-slots-v3')/(split+'.jsonl')).exists():raise FileNotFoundError('Generate the original v3 core data first; do not train a new-only replacement adapter')
 out=Path(output);out.mkdir(parents=True,exist_ok=True);seen=set();manifest={};rng=random.Random(20261030)
 forbidden=set()
 for previous in ('router-v1','router-slots-v2','router-slots-v3'):
  test=Path('data/training')/previous/'test.jsonl'
  if test.exists():forbidden.update(norm(json.loads(line)['text']) for line in test.read_text().splitlines())
 for index,split in enumerate(('train','validation','test')):
  rows=[];entities=ENTITIES[index]
  def add(text,label,action=None,family=''):
   normalized=norm(text)
   if normalized in seen or (split!='test' and normalized in forbidden):return
   seen.add(normalized);row={'text':text,'label':label,'family':family,'synthetic':True,'id':hashlib.sha256((split+text).encode()).hexdigest()[:16]}
   if action:row['action']=action
   rows.append(row)
  source=Path('data/training/router-slots-v3')/(split+'.jsonl')
  if source.exists():
   for row in map(json.loads,source.read_text().splitlines()):
    if norm(row['text']) not in seen and (split=='test' or norm(row['text']) not in forbidden):seen.add(norm(row['text']));rows.append(row)
  for tool,actions in PATTERNS.items():
   for action,families in actions.items():
    for number,template in enumerate(families[index]):
     for k in range(90 if index==0 else 35):
      values={name:rng.choice(options) for name,options in entities.items()};body=template.format(**values)
      prefix=rng.choice(['','请','麻烦你，','帮忙，','嗯，']) if index==0 else rng.choice(['','请你，','那就，']) if index==1 else rng.choice(['','劳驾，','现在，'])
      add(prefix+body,tool,action,f'{split}.{tool}.{action}.{number}')
  for number,template in enumerate(NEG[index]):
   for k in range(90 if index==0 else 25):
    values={name:rng.choice(options) for name,options in entities.items()};body=template.format(**values);prefix=rng.choice(['','请问，','嗯，'])
    add(prefix+body,'null',family=f'{split}.negative.{number}')
  # The current utterance governs routing even after an unrelated earlier tool request.
  base=list(rows)
  for row in base:
   if row.get('action') and rng.random()<.25:
    context=rng.choice(['上海明天天气如何','今天星期几','查一下新闻','我记得钥匙在门口'])
    copy={**row,'classification_text':'历史用户：'+context+'\n当前用户：'+row['text'],'id':row['id']+'c','family':row['family']+'.context'};rows.append(copy)
  followups=[('shopping_list','add','加到购物清单里',entities['item']),('timer','start','开一个倒计时',entities['duration']),('item_memory','remember','记一下物品位置',[entities['object'][0]+'在'+place for place in entities['place']])]
  for tool,action,previous,answers in followups:
   for answer in answers:
    utterance=('就是'+answer) if index==0 else ('那就'+answer) if index==1 else ('用'+answer)
    row={'text':utterance,'classification_text':'历史用户：'+previous+'\n当前用户：'+utterance,'label':tool,'action':action,'family':split+'.followup.'+tool,'synthetic':True,'id':hashlib.sha256((split+previous+utterance).encode()).hexdigest()[:16]};rows.append(row)
  rng.shuffle(rows);path=out/(split+'.jsonl');path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
  manifest[split]={'count':len(rows),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'labels':{label:sum(r['label']==label for r in rows) for label in TOOL_LABELS.values()},'action_rows':sum('action' in r for r in rows)}
 (out/'manifest.json').write_text(json.dumps({'seed':20261030,'split_policy':'separate phrase families and held-out entities; synthetic evaluation is not real-user accuracy','splits':manifest,'tool_labels':TOOL_LABELS,'action_labels':ACTION_LABELS},ensure_ascii=False,indent=2));print(json.dumps(manifest,ensure_ascii=False))
if __name__=='__main__':build('data/training/router-everyday-v4')
