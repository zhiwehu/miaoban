"""Local single-event calendar with atomic, cross-process state and UTC ordering."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import re
import time
import uuid
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from .workflows.storage import atomic_json,read_json,locked
from .workflows.mail import digest

DEFAULT={'timezone':'Asia/Shanghai','remind_minutes':10,'duration_minutes':30,'late_minutes':60,'device_id':''}
def stamp(value,zone):
 if not isinstance(value,str) or len(value)>64 or not re.search(r'[T ]\d{2}:\d{2}',value):raise ValueError('calendar_time_required')
 try:date=datetime.fromisoformat(value)
 except ValueError:raise ValueError('calendar_time_invalid') from None
 try:
  if date.tzinfo is None:
   tz=ZoneInfo(zone);first=date.replace(tzinfo=tz,fold=0);second=date.replace(tzinfo=tz,fold=1)
   if first.utcoffset()!=second.utcoffset() or first.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)!=date:raise ValueError('calendar_time_ambiguous')
   date=first
  return date.timestamp()
 except (OverflowError,OSError):raise ValueError('calendar_time_invalid') from None
def iso(value,zone):return datetime.fromtimestamp(value,ZoneInfo(zone)).isoformat(timespec='seconds')
class CalendarStore:
 def __init__(self,root):
  self.root=Path(root)/'calendar';self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.path=self.root/'state.json'
 def load(self):return read_json(self.path) if self.path.exists() else {'settings':dict(DEFAULT),'events':{}}
 def settings(self):return self.load()['settings']
 def save_settings(self,changes):
  if not isinstance(changes,dict) or set(changes)-set(DEFAULT):raise ValueError('calendar_settings_invalid')
  with locked(self.root/'lock'):
   state=self.load();cfg={**state['settings'],**changes}
   if not isinstance(cfg['timezone'],str) or len(cfg['timezone'])>80:raise ValueError('calendar_settings_invalid')
   try:ZoneInfo(cfg['timezone'])
   except (ValueError,ZoneInfoNotFoundError):raise ValueError('calendar_timezone_invalid') from None
   for key,low,high in [('remind_minutes',0,10080),('duration_minutes',1,1440),('late_minutes',1,1440)]:
    if type(cfg[key]) is not int or not low<=cfg[key]<=high:raise ValueError('calendar_settings_invalid')
   if not isinstance(cfg['device_id'],str) or len(cfg['device_id'])>80 or any(ord(c)<32 for c in cfg['device_id']):raise ValueError('calendar_device_invalid')
   state['settings']=cfg;atomic_json(self.path,state);return cfg
 def public(self,event):
  return {k:event[k] for k in ('event_id','title','timezone','remind_minutes','status','reminder_status','device_id')} | {k:iso(event[k],event['timezone']) for k in ('start_time','end_time','remind_at')}
 def create(self,title,start_time,end_time=None,remind_minutes=None,*,device_id=None,session_id='',request_key='',now=None):
  now=time.time() if now is None else now
  if not isinstance(title,str) or not 1<=len(title.strip())<=100 or any(ord(c)<32 for c in title):raise ValueError('calendar_title_invalid')
  with locked(self.root/'lock'):
   state=self.load();cfg=state['settings'];start=stamp(start_time,cfg['timezone']);end=stamp(end_time,cfg['timezone']) if end_time else start+cfg['duration_minutes']*60
   minutes=cfg['remind_minutes'] if remind_minutes is None else remind_minutes
   if type(minutes) is not int or not 0<=minutes<=10080:raise ValueError('calendar_remind_invalid')
   if not isinstance(session_id,str) or len(session_id)>80 or not isinstance(request_key,str) or len(request_key)>128:raise ValueError('calendar_context_invalid')
   target=cfg['device_id'] if device_id is None else device_id
   if not isinstance(target,str) or len(target)>80 or any(ord(c)<32 for c in target):raise ValueError('calendar_device_invalid')
   signature=digest({'title':title.strip(),'start':start,'end':end,'minutes':minutes,'device':target})
   if request_key:
    existing=next((event for event in state['events'].values() if event['request_key']==request_key),None)
    if existing:
     if existing['signature']!=signature:raise ValueError('calendar_request_changed')
     return self.public(existing)
   if not now<start<=now+366*86400 or not start<end<=start+86400:raise ValueError('calendar_time_range')
   if len(state['events'])>=1000:raise ValueError('calendar_limit')
   identity=uuid.uuid4().hex;event={'event_id':identity,'title':title.strip(),'start_time':start,'end_time':end,'timezone':cfg['timezone'],'remind_minutes':minutes,'remind_at':max(now,start-minutes*60),'device_id':target,'session_id':session_id,'status':'active','reminder_status':'pending','request_key':request_key,'signature':signature,'created':now}
   state['events'][identity]=event;atomic_json(self.path,state);return self.public(event)
 def query(self,range_start=None,range_end=None,*,now=None,include_cancelled=False):
  state=self.load();cfg=state['settings'];local=datetime.fromtimestamp(time.time() if now is None else now,ZoneInfo(cfg['timezone']))
  start=stamp(range_start,cfg['timezone']) if range_start else local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
  end=stamp(range_end,cfg['timezone']) if range_end else (datetime.fromtimestamp(start,ZoneInfo(cfg['timezone']))+timedelta(days=7)).timestamp()
  if not start<end<=start+366*86400:raise ValueError('calendar_query_range')
  events=sorted((e for e in state['events'].values() if (include_cancelled or e['status']=='active') and e['start_time']<end and e['end_time']>start),key=lambda e:e['start_time'])
  return {'timezone':cfg['timezone'],'count':len(events),'truncated':len(events)>20,'events':[self.public(e) for e in events[:20]]}
 def get(self,identity):
  if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{32}',identity):raise ValueError('calendar_id_invalid')
  event=self.load()['events'].get(identity)
  if event is None:raise ValueError('calendar_event_missing')
  return event
 def cancel(self,identity):
  with locked(self.root/'lock'):
   self.get(identity);state=self.load();event=state['events'][identity];event.update(status='cancelled',reminder_status='cancelled');atomic_json(self.path,state);return self.public(event)
 def mark(self,identity,status):
  with locked(self.root/'lock'):
   state=self.load();event=state['events'][identity]
   if event['status']=='active' and event['reminder_status']!=status:event['reminder_status']=status;atomic_json(self.path,state)
 def due(self,now=None):
  now=time.time() if now is None else now
  return sorted([e for e in self.load()['events'].values() if e['status']=='active' and e['remind_at']<=now and e['reminder_status'] in ('pending','due','queued')],key=lambda e:e['remind_at'])
