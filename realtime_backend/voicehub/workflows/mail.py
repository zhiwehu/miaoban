"""Private SMTP configuration and immutable drafts with single-use send authorization."""
import contextlib
from email.message import EmailMessage
from email.utils import formatdate
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import ssl
import time
import uuid
from .artifacts import Artifacts,MIME
from .storage import atomic_json,read_json,record_path,locked

ADDRESS=re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}")
DEFAULT={'enabled':True,'smtp_enabled':False,'host':'','port':465,'security':'ssl','username':'','from_address':'','password_env':'VOICEHUB_SMTP_PASSWORD','password':'','contacts':[]}
def address(value):
 if not isinstance(value,str) or len(value)>254 or not ADDRESS.fullmatch(value):raise ValueError('mail_address_invalid')
 return value

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class MailSettings:
 def __init__(self,root):
  self.root=Path(root)/'workflows';self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.path=self.root/'settings.json'
 def get(self):return {**DEFAULT,**(read_json(self.path) if self.path.exists() else {})}
 def password(self,cfg=None):
  cfg=cfg or self.get();return cfg['password'] or os.environ.get(cfg['password_env'],'')
 def public(self):
  cfg=self.get();return {**{k:v for k,v in cfg.items() if k!='password'},'password_set':bool(self.password(cfg)),'smtp_ready':self.ready(cfg)}
 def ready(self,cfg=None):
  cfg=cfg or self.get();return bool(cfg['smtp_enabled'] and cfg['host'] and cfg['from_address'] and (not cfg['username'] or self.password(cfg)))
 def save(self,changes):
  if not isinstance(changes,dict) or set(changes)-set(DEFAULT)-{'clear_password'}:raise ValueError('mail_settings_invalid')
  with locked(self.root/'settings.lock'):
   old=self.get();cfg={**old,**{k:v for k,v in changes.items() if k in DEFAULT}}
   if changes.get('password')=='':cfg['password']=old['password']
   if changes.get('clear_password') is True:cfg['password']=''
   if type(cfg['enabled']) is not bool or type(cfg['smtp_enabled']) is not bool or type(cfg['port']) is not int or not 1<=cfg['port']<=65535 or cfg['security'] not in ('ssl','starttls'):raise ValueError('mail_settings_invalid')
   for key in ('host','username','from_address','password_env','password'):
    if not isinstance(cfg[key],str) or len(cfg[key])>4096 or any(ord(c)<32 for c in cfg[key]):raise ValueError('mail_settings_invalid')
   if cfg['host'] and not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',cfg['host']):raise ValueError('mail_host_invalid')
   if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}',cfg['password_env']):raise ValueError('mail_password_env_invalid')
   if cfg['from_address']:address(cfg['from_address'])
   if not isinstance(cfg['contacts'],list) or len(cfg['contacts'])>100:raise ValueError('mail_contacts_invalid')
   for contact in cfg['contacts']:
    if not isinstance(contact,dict) or set(contact)!={'name','email'} or not isinstance(contact['name'],str) or not 1<=len(contact['name'])<=80:raise ValueError('mail_contacts_invalid')
    address(contact['email'])
   atomic_json(self.path,cfg)
  return self.public()
 def contact(self,query):
  if not isinstance(query,str) or not 1<=len(query)<=254:raise ValueError('mail_contact_invalid')
  if ADDRESS.fullmatch(query):return {'email':query,'name':query}
  matches=[c for c in self.get()['contacts'] if c['name'].casefold()==query.casefold()]
  if len(matches)==1:return matches[0]
  return {'needs_input':True,'question':'请明确收件人的邮箱地址。' if not matches else '找到多个同名联系人，请说出要使用的邮箱地址。'}

class MailStore:
 def __init__(self,root,transport=None):
  self.base=Path(root);self.root=self.base/'workflow-mail';self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
  self.settings=MailSettings(root);self.artifacts=Artifacts(root);self.transport=transport or smtp_submit
 def _load(self,identity):return read_json(record_path(self.root,identity))
 def _save(self,draft):atomic_json(record_path(self.root,draft['draft_id']),draft)
 def prepare(self,to,subject,body,attachments,task_id):
  address(to)
  if not isinstance(subject,str) or not 1<=len(subject)<=200 or any(ord(c)<32 for c in subject):raise ValueError('mail_subject_invalid')
  if not isinstance(body,str) or len(body)>8000 or not isinstance(attachments,list) or len(attachments)>5 or any(not isinstance(i,str) for i in attachments) or len(set(attachments))!=len(attachments):raise ValueError('mail_body_invalid')
  files=[]
  for identity in attachments:
   meta,_=self.artifacts.read(identity,task_id);files.append({k:meta[k] for k in ('artifact_id','filename','sha256','size')})
  if sum(f['size'] for f in files)>8*1024*1024:raise ValueError('mail_attachment_limit')
  identity=uuid.uuid4().hex;draft={'draft_id':identity,'task_id':task_id,'to':to,'subject':subject,'body':body,'attachments':files,'status':'prepared','created':time.time(),'message_id':'<'+identity+'@voicehub.local>'}
  self._save(draft);return {'draft_id':identity,'to':to,'subject':subject,'attachments':files}
 def describe(self,identity,task_id=None,cfg=None):
  draft=self._load(identity)
  if task_id is not None and draft['task_id']!=task_id:raise ValueError('mail_wrong_task')
  for saved in draft['attachments']:
   meta,_=self.artifacts.read(saved['artifact_id'],draft['task_id'])
   if meta['sha256']!=saved['sha256']:raise ValueError('artifact_changed')
  cfg=cfg or self.settings.get();public={k:draft[k] for k in ('draft_id','to','subject','body','attachments','message_id')}
  public.update(from_address=cfg['from_address'],status='unknown' if draft['status']=='sending' else draft['status'])
  config={k:cfg[k] for k in ('smtp_enabled','host','port','security','username','from_address')}
  return {**public,'digest':digest({'draft':{k:v for k,v in public.items() if k!='status'},'smtp':config})}
 def grant(self,identity,task_id,scope,expected_digest=None):
  with locked(self.root/'lock'):
   draft=self._load(identity)
   if draft['task_id']!=task_id or draft['status'] not in ('prepared','failed'):raise ValueError('mail_draft_not_sendable')
   snapshot=self.describe(identity,task_id)
   if expected_digest is not None and snapshot['digest']!=expected_digest:raise ValueError('mail_draft_changed')
   token=secrets.token_urlsafe(32)
   draft['approval']={'hash':hashlib.sha256(token.encode()).hexdigest(),'digest':snapshot['digest'],'scope':scope,'expires':time.time()+600}
   self._save(draft);return token
 def send(self,identity,token,task_id):
  with locked(self.root/'lock'):
   draft=self._load(identity);approval=draft.get('approval') or {}
   if draft['task_id']!=task_id or draft['status'] not in ('prepared','failed') or not isinstance(token,str) or not secrets.compare_digest(approval.get('hash',''),hashlib.sha256(token.encode()).hexdigest()) or approval.get('expires',0)<time.time():raise ValueError('mail_approval_required')
   cfg=self.settings.get();summary=self.describe(identity,task_id,cfg)
   if summary['digest']!=approval['digest']:raise ValueError('mail_draft_changed')
   if not self.settings.ready(cfg):raise ValueError('mail_not_configured')
   msg=EmailMessage();msg['From']=cfg['from_address'];msg['To']=draft['to'];msg['Subject']=draft['subject'];msg['Message-ID']=draft['message_id'];msg['Date']=formatdate(localtime=True);msg.set_content(draft['body'])
   for file in draft['attachments']:
    meta,raw=self.artifacts.read(file['artifact_id'],task_id);maintype,subtype=MIME[meta['format']].split('/',1)
    msg.add_attachment(raw,maintype=maintype,subtype=subtype,filename=meta['filename'])
   draft.update(status='sending',approval=None);self._save(draft)
  try:result=self.transport(msg,cfg,self.settings.password(cfg))
  except BaseException:
   draft.update(status='unknown',error='mail_delivery_unknown');self._save(draft);raise
  draft.update(status=result['status'],error=result.get('code'));self._save(draft)
  return {**result,'draft_id':identity,'message_id':draft['message_id']}

def smtp_submit(message,cfg,password,*,context=None):
 smtp=None;data_started=False
 try:
  tls=context or ssl.create_default_context()
  if cfg['security']=='ssl':smtp=smtplib.SMTP_SSL(cfg['host'],cfg['port'],timeout=8,context=tls)
  else:
   smtp=smtplib.SMTP(cfg['host'],cfg['port'],timeout=8);smtp.ehlo();smtp.starttls(context=tls)
  smtp.ehlo()
  if cfg['username']:smtp.login(cfg['username'],password)
  data_started=True;smtp.send_message(message,from_addr=cfg['from_address'],to_addrs=[message['To']])
  return {'status':'submitted'}
 except smtplib.SMTPAuthenticationError:return {'status':'failed','code':'mail_auth_failed'}
 except ssl.SSLError:return {'status':'unknown' if data_started else 'failed','code':'mail_delivery_unknown' if data_started else 'mail_tls_failed'}
 except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused):return {'status':'failed','code':'mail_recipient_rejected'}
 except smtplib.SMTPDataError:return {'status':'failed','code':'mail_rejected'}
 except (OSError,smtplib.SMTPException):return {'status':'unknown' if data_started else 'failed','code':'mail_delivery_unknown' if data_started else 'mail_connection_failed'}
 finally:
  if smtp:
   with contextlib.suppress(Exception):smtp.quit()
