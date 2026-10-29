"""Rex selects a fixed action; the local LLM extracts bounded, text-grounded slots."""
import asyncio
import contextlib
import json
import re
import time
from .tools import ToolNotice,ToolStatus,SCHEMAS,validate_arguments
from .mcp_client import CommonToolsClient,MCPError
from .background import current_operation

NAMES={'shopping_list':'购物清单','timer':'计时器','item_memory':'物品位置记录'}

def chinese_number(text):
 if text.isdigit():return int(text)
 digits={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
 value=part=0
 for ch in text:
  if ch in digits:part=digits[ch]
  elif ch in ('十','百'):value+=(part or 1)*{'十':10,'百':100}[ch];part=0
  else:raise ValueError('duration_invalid')
 return value+part

def explicit_duration(text):
 if re.search(r'负|[-－][0-9一二两三四五六七八九十]|上午|下午|晚上|明天|后天|[0-9一二两三四五六七八九十]+点|[0-9]{1,2}[:：][0-9]{2}|到|至|或者|然后|再加',text):return None
 text=re.sub(r'([0-9零一二两三四五六七八九十百]+)个半小时',r'\1小时30分钟',text)
 text=re.sub(r'([0-9零一二两三四五六七八九十百]+)小时半',r'\1小时30分钟',text)
 text=re.sub(r'([0-9零一二两三四五六七八九十百]+)分钟半',r'\1分钟30秒',text)
 text=text.replace('半小时','30分钟').replace('半分钟','30秒')
 text=re.sub(r'([0-9零一二两三四五六七八九十百]+)分半',r'\1分钟30秒',text)
 matches=re.findall(r'([0-9零一二两三四五六七八九十百]+(?:\.[0-9]+)?)\s*(小时|分钟|分|秒钟|秒)',text)
 if not matches:return None
 value=sum((float(n) if re.fullmatch(r'\d+(?:\.\d+)?',n) else chinese_number(n))*{'小时':3600,'分钟':60,'分':60,'秒钟':1,'秒':1}[unit] for n,unit in matches)
 return int(value) if value==int(value) else None

def grounded(name,args,text):
 validate_arguments(name,args)
 # Strings describing objects must be verbatim spans, never inferred personal facts.
 for key in ('item','location','name','list_name'):
  if key in args and args[key] not in text:raise ValueError('ungrounded_slot')
 for item in args.get('items',[]):
  if item not in text:raise ValueError('ungrounded_slot')
 if name=='item_memory' and args['action']=='remember':
  location=args.get('location','')
  if not location or re.fullmatch(r'(?:的|在)?(?:位置|地方|哪里|哪儿|这|那|这里|那里|这儿|那儿)',location):raise ValueError('location_required')
 if name=='timer' and args['action']=='start':
  duration=explicit_duration(text)
  if duration is None or args.get('duration_seconds')!=duration:raise ValueError('duration_required')
 return args

def summary(name,action,value):
 status=value.get('status');rows=value.get('items',[])
 if status=='needs_selection':return '有重名或多个候选，请明确'+('计时器名称，并使用不同名字新建。' if name=='timer' else '完整名称。')
 if status=='not_found':return '没有找到对应记录，请先查询列表或说明完整名称。'
 if name=='shopping_list':
  if action=='list':return '购物清单为空。' if not rows else '购物清单：'+'；'.join(r['name']+('，已买好' if r['state']=='bought' else '，待购买') for r in rows[:15])+('；还有更多条目，请在 MCP 页查看。' if len(rows)>15 else '。')
  return {'add':'已加入购物清单。','complete':'已标记为买好。','remove':'已从购物清单移除。'}[action]
 if name=='item_memory':
  if action=='remember':return '记住了，'+rows[0]['item']+'在'+rows[0]['location']+'。'
  if action=='forget':return '已忘记这条物品位置记录。'
  return '还没有物品位置记录。' if not rows else '；'.join(r['item']+'在'+r['location'] for r in rows[:10])+'。'
 if action=='cancel':return '已取消计时器。'
 if not rows:return '当前没有计时器。'
 return '；'.join(r['name']+'，'+('已暂停，' if r['state']=='paused' else '')+('时间到了' if r['remaining_seconds']==0 else '剩余'+str(r['remaining_seconds'])+'秒') for r in rows[:10])+'。'

async def execute(pipeline,history,result):
 name=result['tool'];action=result['arguments']['action']
 yield ToolNotice(name,'收到，我来处理'+NAMES[name]+'。')
 args={};started=time.monotonic()
 try:
  latest=next(m['content'] for m in reversed(history) if m.get('role')=='user')
  context=latest
  previous=getattr(pipeline,'_everyday_question',None)
  users=[m['content'] for m in history if m.get('role')=='user']
  adjacent=len(users)>1 and previous and users[-2]==previous.get('last_user')
  fresh_command=re.search(r'添加|加入|记住|记录|忘记|删除|查看|计时|购物|开启|继续|取消|暂停|恢复|列出',latest) and not latest.startswith(('就是','在','放在'))
  if adjacent and not fresh_command and time.monotonic()-previous['at']<120 and previous['tool']==name and previous['action']==action:context=previous['text']+'\n'+latest
  allowed={'shopping_list':{'add':['items','list_name'],'list':['list_name'],'complete':['items','list_name'],'remove':['items','list_name']},'item_memory':{'remember':['item','location'],'recall':['item'],'forget':['item'],'list':[]},'timer':{'start':['name'],'status':['name'],'pause':['name'],'resume':['name'],'cancel':['name']}}[name][action]
  schema={key:SCHEMAS[name][1][key] for key in allowed}
  prompt=('你是参数提取器，只输出 JSON 对象。工具和动作已由 Rex 确定，不得修改。工具='+name+'，action='+action+'。'
   '只提取用户明确提供的参数，缺少参数就省略，不能猜测。items中每个商品保留数量且必须是用户原文片段；item/location/name/list_name必须逐字来自用户原文。'
   'timer只提取name，不支持钟点提醒；没有名字可以省略name。不要将名词解释、否定或引用当作执行请求。'
   '不要输出 action、duration_seconds、question、解释或 Markdown。时长由主机计算。每个动作只输出本次允许的字段。'
   '参数模式：'+json.dumps(schema,ensure_ascii=False)+
   {'shopping_list':'例子：把牛奶标为买好了→{\"items\":[\"牛奶\"]}；添加两盒牛奶和三个鸡蛋→{\"items\":[\"两盒牛奶\",\"三个鸡蛋\"]}。商品字段必须为items数组，不是item。', 'item_memory':'例子：我的护照放哪了→{\"item\":\"护照\"}；记一下雨伞的位置→{\"item\":\"雨伞\"}，没有实际地点不能填location；记住护照在书房抽屉→{\"item\":\"护照\",\"location\":\"书房抽屉\"}。', 'timer':'例子：给泡面开个两分半倒计时→{\"name\":\"泡面\"}；泡面计时器继续→{\"name\":\"泡面\"}；暂停泡面计时器→{\"name\":\"泡面\"}。明确提到计时事项名时必须提取name。'}[name])
  raw=''
  async with asyncio.timeout(20),contextlib.aclosing(pipeline.raw_chat([{'role':'user','content':context}],system_prompt=prompt,max_tokens=256)) as source:
   async for chunk in source:
    raw+=chunk
    if len(raw)>4096:raise ValueError('slot_limit')
  args=json.loads(raw)
  if not isinstance(args,dict) or args.get('action',action)!=action:raise ValueError('action_changed')
  if set(args)-set(allowed)-{'action'}:raise ValueError('unexpected_slot')
  if name=='timer' and action=='start':args['duration_seconds']=explicit_duration(context)
  if name=='item_memory' and isinstance(args.get('item'),str):args['item']=re.sub(r'^(?:我的|我那把|我那份|我那个)','',args['item'])
  args['action']=action;grounded(name,args,context)
  required={'shopping_list':{'add':['items'],'complete':['items'],'remove':['items']},'item_memory':{'remember':['item','location'],'recall':['item'],'forget':['item']},'timer':{'start':['duration_seconds']}}
  if any(not args.get(key) for key in required.get(name,{}).get(action,[])):raise ValueError('missing_slot')
  pipeline._everyday_question=None
  yield ToolStatus(name,'started',arguments=args,planner='rex_slots_llm')
  operation=current_operation.get()
  env={'VOICEHUB_SESSION_ID':getattr(pipeline,'session_id',''),'VOICEHUB_DEVICE_ID':getattr(pipeline,'device_id',''),'VOICEHUB_REQUEST_ID':operation['id'] if operation else ''}
  async with CommonToolsClient(data_root=pipeline.local.data_root,env=env,timeout=20) as client:value=await client.call(name,args)
  if value.get('isError'):raise MCPError(value.get('structuredContent',{}).get('error',{}).get('code','tool_failed'))
  data=json.loads(value['content'][0]['text'])
 except (ValueError,KeyError,TypeError,StopIteration,TimeoutError,MCPError,OSError) as exc:
  yield ToolStatus(name,'failed',round((time.monotonic()-started)*1000),'everyday_parameters_or_tool_failed',arguments=args,planner='rex_slots_llm')
  if isinstance(exc,(ValueError,KeyError,TypeError)):
   pipeline._everyday_question={'tool':name,'action':action,'text':context[-500:],'last_user':latest,'at':time.monotonic()}
   operation=current_operation.get()
   if operation is not None:operation['everyday_question']=True
   yield {'shopping_list':'请说清要操作的商品名称，例如加入两盒牛奶。','item_memory':'请明确物品名称；记录时还需要位置，例如记住护照在书房抽屉。','timer':'请说明时长，例如倒计时五分钟；有多个计时器时请说名称。'}[name]
  else:yield '操作未成功，请在 MCP 工具页检查启用状态后重试。'
  return
 yield ToolStatus(name,'completed',round((time.monotonic()-started)*1000),arguments=args,planner='rex_slots_llm')
 yield summary(name,action,data)
