"""Durable reminder dispatch; only the bound device receives voice announcements."""
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from .calendar_store import CalendarStore

def notification_id(identity):return uuid.uuid5(uuid.NAMESPACE_URL,'voicehub-calendar:'+identity).hex
class CalendarReminders:
 def __init__(self,root,operations,sessions,publish,enabled):
  self.version=None;self.store=CalendarStore(root);self.operations=operations;self.sessions=sessions;self.publish=publish;self.enabled=enabled
 async def tick(self,now=None):
  if not self.enabled():return
  version=self.store.path.stat().st_mtime_ns if self.store.path.exists() else 0
  if version!=self.version:self.version=version;self.publish({'type':'calendar.changed','status':'updated'})
  now=time.time() if now is None else now;cfg=self.store.settings();sent=0
  for event in self.store.due(now):
   identity=event['event_id'];notice_id=notification_id(identity)
   try:record=self.operations.get(notice_id)
   except FileNotFoundError:record=None
   if record and record['delivery']=='announced':self.store.mark(identity,'announced');continue
   speaking=any(getattr(getattr(session,'turn',None),'notification_id','')==notice_id for session in self.sessions.values())
   if now-event['remind_at']>cfg['late_minutes']*60 and not speaking:
    self.cancel_notification(identity);self.store.mark(identity,'missed');self.publish({'type':'calendar.changed','event_id':identity,'status':'missed'});continue
   if event['reminder_status']=='pending':self.store.mark(identity,'due');self.publish({'type':'calendar.changed','event_id':identity,'status':'due'})
   title=event['title'];when=datetime.fromtimestamp(event['start_time'],ZoneInfo(event['timezone'])).strftime('%m月%d日%H点%M分')
   text='日程提醒：'+when+'，'+title+'。'
   if not event['device_id']:
    self.store.mark(identity,'page');self.publish({'type':'calendar.reminder','event_id':identity,'text':text});continue
   session=next((s for s in self.sessions.values() if s.device_id==event['device_id'] and not s.closed),None)
   if session is None or len(session.notification_ids)>=25 or sent>=20:continue
   self.operations.notify(notice_id,session.id,'日程提醒：'+title,text,kind='calendar',event_id=identity)
   self.store.mark(identity,'queued');sent+=1
 def cancel_notification(self,identity):
  try:record=self.operations.get(notification_id(identity))
  except FileNotFoundError:return
  if record['delivery']=='pending':record['delivery']='cancelled';self.operations.save(record)
