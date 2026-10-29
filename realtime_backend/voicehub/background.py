"""Application-owned operations: input/audio interruption never owns tool lifetime."""
import asyncio
import contextlib
from contextvars import ContextVar
import time
import uuid
from pathlib import Path
from .workflows.storage import atomic_json,read_json,record_path
from .tools import ToolStatus,ToolNotice

current_operation=ContextVar('voicehub_operation',default=None)

class Operations:
 def __init__(self,root,publish=None):
  self.path=Path(root)/'operations';self.path.mkdir(parents=True,exist_ok=True,mode=0o700)
  self.tasks={};self.listeners={};self.publish=publish or (lambda event:None)
  for path in self.path.glob('*.json'):
   record=read_json(path)
   if record['status'] in ('queued','running'):
    last=record.get('last_tool',{})
    if last.get('phase')=='started':self.publish({'type':'tool.failed','session_id':record['session_id'],'turn_id':record['turn_id'],'tool':last['name'],'background':True,'code':'operation_interrupted','timestamp':time.time()})
    record.update(status='unknown',text='应用在执行中重启，结果需要核实，不会自动重新执行。',delivery='pending');self.save(record)
 def save(self,record):
  record['updated']=time.time();atomic_json(record_path(self.path,record['id']),record)
  self.publish({'type':'operation.changed','operation_id':record['id'],'session_id':record['session_id'],'status':record['status']})
 def get(self,identity):return read_json(record_path(self.path,identity))
 def recent(self,scope=None):
  rows=[read_json(p) for p in sorted(self.path.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:100]]
  return [r for r in rows if scope is None or r['session_id']==scope][:30]
 def pending(self,scope):return list(reversed([r for r in self.recent(scope) if r['delivery']=='pending' and r['status'] not in ('running','queued')]))
 def bind(self,scope,callback):
  self.listeners[scope]=callback
  for record in self.pending(scope):callback(record)
 def unbind(self,scope,callback):
  if self.listeners.get(scope) is callback:self.listeners.pop(scope,None)
 def announced(self,identity):
  record=self.get(identity);record['delivery']='announced';self.save(record)
 def check_capacity(self,scope):
  if len(self.tasks)>=12 or sum(self.get(i)['session_id']==scope for i in self.tasks)>=3:raise ValueError('operation_capacity')
 def start(self,scope,turn_id,label,source,gate,emit=None):
  self.check_capacity(scope)
  if len(list(self.path.glob('*.json')))>=1000:
   removable=[p for p in self.path.glob('*.json') if p.stem not in self.tasks and read_json(p).get('delivery')=='announced']
   if not removable:raise ValueError('operation_history_full')
   min(removable,key=lambda p:p.stat().st_mtime).unlink()
  record={'id':uuid.uuid4().hex,'session_id':scope,'turn_id':turn_id,'label':label[:120],'status':'queued','delivery':'pending','created':time.time(),'text':''};self.save(record)
  async def work():
   token=current_operation.set(record)
   try:
    async with contextlib.aclosing(source),asyncio.timeout(900):
     await gate.wait();record['status']='running';self.save(record);text='';failed=False
     async for value in source:
      if isinstance(value,ToolStatus):
       if value.phase=='failed':failed=True
       record['last_tool']={'name':value.name,'phase':value.phase};self.save(record)
       event={'type':'tool.'+value.phase,'session_id':scope,'turn_id':turn_id,'tool':value.name,'background':True,'elapsed_ms':value.elapsed_ms,'code':value.code,'timestamp':time.time()}
       if value.arguments is not None:event['arguments']=value.arguments
       if value.planner:event['planner']=value.planner
       if emit:await emit(event)
       else:self.publish(event)
      elif isinstance(value,ToolNotice):continue
      else:
       text+=value
       if len(text)>16000:raise ValueError('operation_output_limit')
     record.update(status='waiting' if str(record.get('workflow_status','')).startswith('waiting_') else 'cancelled' if record.get('workflow_status')=='cancelled' else 'failed' if failed else 'completed',text=text.strip() or '任务处理结束，请查看任务记录。')
   except asyncio.CancelledError:record.update(status='unknown',text='已请求停止执行；正在进行的外部操作可能已经生效，请核实记录。')
   except Exception:record.update(status='failed',text='后台任务未能完成，请在技能页查看状态并检查工具配置。')
   finally:
    last=record.get('last_tool',{})
    if last.get('phase')=='started' and record['status'] in ('failed','unknown','cancelled'):
     event={'type':'tool.failed','session_id':scope,'turn_id':turn_id,'tool':last['name'],'background':True,'code':'operation_interrupted','timestamp':time.time()}
     if emit:await emit(event)
     else:self.publish(event)
    self.save(record);self.tasks.pop(record['id'],None)
    callback=self.listeners.get(scope)
    if callback:callback(record)
    current_operation.reset(token)
  job=asyncio.create_task(work());self.tasks[record['id']]=job
  # Cancellation before coroutine entry must still close its source and persist a result.
  def done(task):
   if record['id'] in self.tasks:
    self.tasks.pop(record['id'],None);record.update(status='cancelled',text='任务已取消。');self.save(record)
  job.add_done_callback(done);return record
 async def cancel(self,identity):
  await asyncio.sleep(0)
  job=self.tasks.get(identity)
  if not job:raise ValueError('operation_not_running')
  job.cancel();await asyncio.gather(job,return_exceptions=True)
 async def close(self):
  await asyncio.sleep(0)
  jobs=list(self.tasks.values())
  for job in jobs:job.cancel()
  if jobs:await asyncio.gather(*jobs,return_exceptions=True)
