"""Small-model slot extraction; the host chooses and executes validated templates."""
import asyncio
import contextlib
import json
import re
from .tools import ToolNotice,ToolStatus

def route(pipeline,history):
 skills=getattr(getattr(pipeline,'local',None),'skills',None)
 if not skills or not getattr(pipeline,'session_id',None):return None
 text=next((m.get('content','') for m in reversed(history) if m.get('role')=='user'),'')
 if re.search(r'^(?:请)?(?:不要|先别|别执行)|怎么|如何|支持.*吗|有哪些技能',text):return None
 matches=[s for s in skills.list() if s['enabled'] and (any(t in text for t in s['triggers']) or ('执行技能' in text and s['name'] in text))]
 if not matches:return None
 return execute(pipeline,history,skills,matches)
async def execute(pipeline,history,skills,matches):
 yield ToolNotice('skill','收到，处理好后告诉你。')
 if len(matches)!=1:yield '匹配到多个技能，请明确说出要执行的技能名称。';return
 item=matches[0];args={}
 try:
  if item['parameters']['properties']:
   prompt='从用户要求提取技能参数，只输出JSON对象，不输出解释。格式为{"arguments":{}}。不得编造未提供的信息；缺失参数省略。参数只能是原始值，不能是工具引用。技能：'+json.dumps({k:item[k] for k in ('name','description','parameters')},ensure_ascii=False)
   output=''
   async with asyncio.timeout(25):
    async for delta in pipeline.raw_chat(history[-4:],system_prompt=prompt,max_tokens=600):
     output+=delta
     if len(output)>12000:raise ValueError('skill_arguments_invalid')
   data=json.loads(output)
   if not isinstance(data,dict) or set(data)!={'arguments'}:raise ValueError('skill_arguments_invalid')
   args=data['arguments']
  task=skills.instantiate(item['id'],args,pipeline.session_id)
 except (ValueError,TypeError,KeyError,TimeoutError) as exc:
  if str(exc)=='workflow_tool_unavailable':yield '这个技能需要的工具尚未启用，请先到 MCP 页启用对应工具。';return
  fields='、'.join({'title':'标题','content':'正文','to':'收件人邮箱'}.get(key,key) for key in item['parameters'].get('required',[]));yield '技能参数还不完整或格式不正确。请再说一次“'+item['name']+'”，并提供'+fields+'。';return
 yield ToolStatus('skill','started',arguments={'skill':item['id']},planner='skill')
 async with contextlib.aclosing(skills.workflows.run(task['id'],pipeline,history)) as source:
  async for value in source:yield value
 yield ToolStatus('skill','completed')
