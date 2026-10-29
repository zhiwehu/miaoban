"""Natural-language entry and explicit, session-bound human controls for task chains."""
import asyncio
import contextlib
import re
from ..tools import ToolNotice
from ..extension_routing import is_conversational_turn
from ..local_calendar import calendar_answer

def candidate(text):
 if re.fullmatch(r'(?:你|平台|这里)?(?:能|可以|支持|会)(?:生成文件|生成文档|发送邮件|发邮件|做复杂任务)(?:吗|么)[？?。！!]*',text):return False
 if text.startswith(('解释','介绍','如何','怎么')):return False
 if re.search(r'生成|创建|写一份|做一份|保存为|发邮件|发送邮件|邮件发送|发送到.*邮箱|发给',text):return True
 if re.search(r'然后|接着|之后|顺便|同时|并且|再|并',text):
  groups=sum(bool(re.search(p,text,re.IGNORECASE)) for p in (r'天气|气温',r'新闻|资讯',r'文件|文档|报告|纪要',r'音乐|播放',r'CPU|内存|系统信息',r'邮件|邮箱'))
  return groups>=2
 return text.startswith(('执行任务：','执行任务:','创建任务：','创建任务:'))

def route(pipeline,history):
 manager=getattr(getattr(pipeline,'local',None),'workflows',None);scope=getattr(pipeline,'session_id',None)
 if not manager or not scope or not manager.settings.get()['enabled']:return None
 text=next((m.get('content','') for m in reversed(history) if m.get('role')=='user'),'').strip()
 compact=re.sub(r'[\s，。！？,.!?]+','',text);active=manager.active(scope)
 confirmation=compact in ('确认发送','确认发送邮件','确认执行任务')
 cancel=compact in ('取消任务','取消发送','不要发送','先别发','先别发送','不用了','再见','拜拜')
 resume=compact in ('继续任务','恢复任务','重试任务')
 if confirmation or (active and (cancel or resume or compact=='查看任务')):return control(manager,pipeline,history,active,compact)
 if active and active['status']=='waiting_input' and calendar_answer(text) is None and not is_conversational_turn(text):
  if active.get('input_kind')=='contact':
   if '@' in text or text.startswith(('收件人是','邮箱是')):return answer(manager,pipeline,history,active,text)
  elif not candidate(text):return answer(manager,pipeline,history,active,text)
 if candidate(text):return start(manager,pipeline,history,text,active)
 return None

async def start(manager,pipeline,history,text,active=None):
 if active:await manager.cancel(active['id'],pipeline.session_id)
 yield ToolNotice('workflow','我先规划任务，再按步骤处理，请稍等。')
 task=manager.create(text,pipeline.session_id)
 try:
  await manager.plan(task['id'],pipeline)
  current=manager.get(task['id'])
  if current['status']=='waiting_input':yield current['question'];return
  async with contextlib.aclosing(manager.run(task['id'],pipeline,history)) as source:
   async for value in source:yield value
 except asyncio.CancelledError:
  current=manager._load(task['id'])
  if current['status']=='planning':current.update(status='interrupted',error='workflow_interrupted');manager._save(current)
  raise
 except (ValueError,TimeoutError):
  current=manager._load(task['id']);current.update(status='failed',error='workflow_planning_failed');manager._save(current)
  yield '这次还没能生成可执行的任务计划，请补充具体要求；尚未执行任务。'

async def answer(manager,pipeline,history,task,text):
 text=re.sub(r'^(?:收件人是|邮箱是)[：:\s]*','',text)
 manager.answer(task['id'],pipeline.session_id,text)
 yield ToolNotice('workflow','收到，我继续处理这项任务。')
 if manager.get(task['id'])['status']=='planning':
  await manager.plan(task['id'],pipeline)
  current=manager.get(task['id'])
  if current['status']=='waiting_input':yield current['question'];return
 async with contextlib.aclosing(manager.run(task['id'],pipeline,history)) as source:
  async for value in source:yield value

async def control(manager,pipeline,history,task,text):
 if not task:yield '当前没有等待处理或确认的任务，不会发送邮件。';return
 if text in ('取消任务','取消发送','不要发送','先别发','先别发送','不用了','再见','拜拜'):
  await manager.cancel(task['id'],pipeline.session_id);yield '未完成任务已取消。'+('再见。' if text in ('再见','拜拜') else '已生成的文件会保留。');return
 if text=='查看任务':yield '当前任务状态：'+task['status']+'。'+task.get('question','');return
 try:
  if text in ('继续任务','恢复任务','重试任务'):
   manager.resume(task['id'],pipeline.session_id)
   if manager.get(task['id'])['status']=='planning':await manager.plan(task['id'],pipeline)
  else:
   if task['status']!='waiting_confirmation':yield '任务当前没有待确认操作。请先补充信息或配置邮箱，再继续任务。';return
   mail=task['confirmation']['tool']=='email.send'
   if mail and text=='确认执行任务':yield '这一步会发送邮件，请明确说“确认发送”。';return
   if not mail and text in ('确认发送','确认发送邮件'):yield '待确认的不是邮件发送，请查看任务后说“确认执行任务”。';return
   manager.approve(task['id'],pipeline.session_id)
  async with contextlib.aclosing(manager.run(task['id'],pipeline,history)) as source:
   async for value in source:yield value
 except ValueError as exc:
  yield '草稿、附件或配置已变化，或者确认已过期。请查看最新任务摘要并重新确认。' if str(exc)=='workflow_confirmation_changed' else '当前任务无法继续，请查看任务链页面的状态。'
