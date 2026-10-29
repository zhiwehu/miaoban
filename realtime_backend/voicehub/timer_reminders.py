"""Timer notifications survive speech interruption and process restarts."""
import time
import uuid
from .everyday_store import EverydayStore

class TimerReminders:
 def __init__(self,root,operations,sessions,enabled):
  self.store=EverydayStore(root);self.operations=operations;self.sessions=sessions;self.enabled=enabled
 async def tick(self,now=None):
  if not self.enabled():return
  now=time.time() if now is None else now
  for timer in self.store.due(now):
   identity=uuid.uuid5(uuid.NAMESPACE_URL,'voicehub-timer:'+timer['id']+':'+str(timer['generation'])).hex
   session=next((s for s in self.sessions.values() if not s.closed and ((timer['device'] and s.device_id==timer['device']) or (not timer['device'] and s.id==timer['session']))),None)
   if session is None:
    if not timer['device'] and not timer['session']:
     self.operations.notify(identity,'','计时结束：'+timer['name'],timer['name']+'，时间到了。',kind='timer',timer_id=timer['id'],generation=timer['generation'],deadline=timer['deadline'])
    continue
   if len(session.notification_ids)>=25:continue
   self.operations.notify(identity,session.id,'计时结束：'+timer['name'],timer['name']+'，时间到了。',kind='timer',timer_id=timer['id'],generation=timer['generation'],deadline=timer['deadline'])
 def valid(self,record):
  return self.enabled() and time.time()-record['deadline']<=300 and self.store.valid_notification(record['timer_id'],record['generation'])
