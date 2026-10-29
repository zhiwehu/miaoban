"""Bounded local shopping, item memory and device-scoped timers; process-safe writes."""
import time
import uuid
from pathlib import Path
from .workflows.storage import atomic_json, read_json, locked


def clean(value,limit):
 if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError('everyday_arguments_invalid')
 return value.strip()


class EverydayStore:
 def __init__(self,root):
  self.root=Path(root);self.folder=self.root/'everyday';self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
 def transaction(self,kind,change):
  path=self.folder/(kind+'.json')
  with locked(self.folder/(kind+'.lock')):
   rows=read_json(path) if path.exists() else []
   result=change(rows)
   atomic_json(path,rows)
   return result
 def shopping(self,action,items=None,list_name='购物清单'):
  list_name=clean(list_name,40)
  if action not in ('add','list','complete','remove'):raise ValueError('everyday_arguments_invalid')
  if action!='list' and (not isinstance(items,list) or not 1<=len(items)<=10):raise ValueError('everyday_arguments_invalid')
  names=[clean(x,60) for x in (items or [])]
  def apply(rows):
   current=[r for r in rows if r['list_name']==list_name and r['state']!='removed']
   if action=='list':return {'status':'ok','items':current}
   if action=='add':
    rows[:]=[r for r in rows if r['state']!='removed']
    if len(rows)+len(names)>500:raise ValueError('everyday_limit')
    for name in dict.fromkeys(names):
     if not any(r['name']==name and r['state']=='pending' for r in current):
      row={'id':uuid.uuid4().hex,'list_name':list_name,'name':name,'state':'pending'}
      rows.append(row);current.append(row)
    return {'status':'ok','items':current}
   selected=[]
   for name in names:
    candidates=[r for r in current if r['state']=='pending'] if action=='complete' and any(r['state']=='pending' and name in r['name'] for r in current) else current
    matches=[r for r in candidates if r['name']==name] or [r for r in candidates if name in r['name']]
    if len(matches)!=1:return {'status':'needs_selection' if matches else 'not_found','items':matches}
    selected.extend(matches)
   for row in selected:row['state']='bought' if action=='complete' else 'removed'
   return {'status':'ok','items':selected}
  return self.transaction('shopping',apply)
 def memory(self,action,item=None,location=None):
  if action not in ('remember','recall','forget','list'):raise ValueError('everyday_arguments_invalid')
  if action!='list':item=clean(item,60)
  if action=='remember':location=clean(location,300)
  def apply(rows):
   if action=='list':return {'status':'ok','items':[r for r in rows if not r.get('deleted')]}
   matches=[r for r in rows if r['item']==item]
   if action=='remember':
    rows[:]=[r for r in rows if not r.get('deleted') or r['item']==item]
    if matches:row=matches[0];row.update(location=location,deleted=False,updated_at=time.time())
    else:
     if len(rows)>=500:raise ValueError('everyday_limit')
     row={'item':item,'location':location,'updated_at':time.time()};rows.append(row)
    return {'status':'ok','items':[row]}
   matches=[r for r in matches if not r.get('deleted')] or [r for r in rows if not r.get('deleted') and item in r['item']]
   if len(matches)!=1:return {'status':'needs_selection' if matches else 'not_found','items':matches}
   if action=='forget':matches[0].update(deleted=True,location='')
   return {'status':'ok','items':matches}
  return self.transaction('memory',apply)
 def timer(self,action,name=None,duration_seconds=None,device='',session='',request='',now=None):
  now=time.time() if now is None else now
  if action not in ('start','status','pause','resume','cancel'):raise ValueError('everyday_arguments_invalid')
  if name is not None:name=clean(name,60)
  if action=='start':
   name=name or '计时器'
   if type(duration_seconds)is not int or not 1<=duration_seconds<=86400:raise ValueError('everyday_arguments_invalid')
  def apply(rows):
   active=[r for r in rows if r['device']==device and r['state']!='cancelled']
   def output(selected):
    return {'status':'ok','items':[{**r,'remaining_seconds':max(0,round(r['deadline']-now)) if r['state']=='running' else r['remaining_seconds']} for r in selected]}
   if action=='start':
    if len(rows)>=1000:
     removable=[r for r in rows if r['state']=='cancelled' or (r['state']=='running' and r['deadline']<now-300)]
     if removable:rows.remove(min(removable,key=lambda r:r['deadline']))
    same=[r for r in rows if request and r.get('request')==request and r['device']==device]
    if same:return output(same)
    if len(rows)>=1000 or len([r for r in active if r['state']=='paused' or r['deadline']>now])>=20:raise ValueError('everyday_limit')
    if any(r['name']==name and (r['state']=='paused' or r['deadline']>now) for r in active):return {'status':'needs_selection','items':[]}
    row={'id':uuid.uuid4().hex,'name':name,'device':device,'session':session,'request':request,'state':'running','deadline':now+duration_seconds,'remaining_seconds':duration_seconds,'generation':1}
    rows.append(row);return output([row])
   selected=[r for r in active if r['name']==name] if name else active
   if action=='status':return output(selected)
   # Completed timers remain queryable but must not make an unnamed active timer ambiguous.
   if not name:selected=[r for r in selected if r['state']=='paused' or r['deadline']>now]
   if len(selected)!=1:return {'status':'needs_selection' if selected else 'not_found','items':selected}
   row=selected[0]
   if action=='pause' and row['state']=='running':row.update(remaining_seconds=max(0,round(row['deadline']-now)),state='paused',generation=row['generation']+1)
   if action=='resume' and row['state']=='paused' and row['remaining_seconds']>0:row.update(deadline=now+row['remaining_seconds'],state='running',generation=row['generation']+1,session=session or row['session'])
   if action=='cancel':row.update(state='cancelled',generation=row['generation']+1)
   return output([row])
  return self.transaction('timers',apply)
 def due(self,now=None):
  now=time.time() if now is None else now
  return self.transaction('timers',lambda rows:[r.copy() for r in rows if r['state']=='running' and 0<=now-r['deadline']<=300])
 def valid_notification(self,identity,generation):
  return self.transaction('timers',lambda rows:any(r['id']==identity and r['generation']==generation and r['state']=='running' for r in rows))
 def running(self,device):
  return self.transaction('timers',lambda rows:any(r['device']==device and r['state']=='running' and r['deadline']>time.time() for r in rows))
