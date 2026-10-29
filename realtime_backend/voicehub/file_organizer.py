"""Previewed, journalled moves within one admin-selected directory; no content reads."""
from collections import Counter
from contextlib import contextmanager
import ctypes
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unicodedata
import uuid

CATEGORIES={'文档':'.pdf .doc .docx .xls .xlsx .csv .ppt .pptx .txt .md .rtf .odt .ods .epub',
 '图片':'.jpg .jpeg .png .gif .webp .svg .heic .bmp .tif .tiff',
 '音频':'.mp3 .wav .m4a .aac .flac .ogg .aiff .aif',
 '视频':'.mp4 .mov .mkv .avi .webm .m4v',
 '压缩包':'.zip .rar .7z .gz .bz2 .xz .tar',
 '代码':'.py .js .mjs .ts .tsx .jsx .html .css .go .rs .c .cpp .h .java .sh .json .yaml .yml'}
EXTENSIONS={suffix:group for group,suffixes in CATEGORIES.items() for suffix in suffixes.split()}
GROUPS=set(CATEGORIES)|{'其他'}
FLAGS=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW

def fingerprint(info):return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]
def safe_name(name):
 return isinstance(name,str) and 0<len(name)<=255 and not name.startswith('.') and not any(c in name for c in ('/','\\','\0')) and not any(ord(c)<32 or ord(c)==127 for c in name)
def rename_exclusive(source_fd,source,target_fd,target):
 libc=ctypes.CDLL(None,use_errno=True)
 function=getattr(libc,'renameatx_np' if sys.platform=='darwin' else 'renameat2',None)
 if function is None:raise ValueError('file_atomic_move_unavailable')
 function.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];function.restype=ctypes.c_int
 if function(source_fd,os.fsencode(source),target_fd,os.fsencode(target),4 if sys.platform=='darwin' else 1):
  code=ctypes.get_errno();raise OSError(code,os.strerror(code))

class FileOrganizer:
 def __init__(self,data_root,directory):
  self.data=Path(data_root).resolve();self.directory=str(directory)
  self.store=self.data/'file-organizer';self.store.mkdir(parents=True,exist_ok=True,mode=0o700)
  self.path=self.store/'state.json'
 @contextmanager
 def locked(self):
  fd=os.open(self.store/'lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
  try:fcntl.flock(fd,fcntl.LOCK_EX);yield
  finally:os.close(fd)
 @contextmanager
 def root(self,plan=None):
  supplied=Path(self.directory).expanduser()
  if not self.directory or not supplied.is_absolute() or supplied.is_symlink():raise ValueError('file_directory_invalid')
  resolved=supplied.resolve(strict=True)
  if resolved in (Path('/'),Path.home().resolve(),Path('/System'),Path('/Library'),Path('/usr'),Path('/etc'),Path('/bin'),Path('/sbin'),Path('/dev')) or self.data.is_relative_to(resolved):raise ValueError('file_directory_forbidden')
  fd=os.open(resolved,FLAGS)
  try:
   identity=[os.fstat(fd).st_dev,os.fstat(fd).st_ino]
   if plan and (plan['root']!=str(resolved) or plan['root_identity']!=identity):raise ValueError('file_directory_changed')
   yield fd,str(resolved),identity
  finally:os.close(fd)
 def _load(self):
  if not self.path.exists():return {'pending':None,'operation':None}
  if self.path.is_symlink() or self.path.stat().st_size>4*1024*1024:raise ValueError('file_journal_invalid')
  return json.loads(self.path.read_text())
 def _save(self,state):
  fd,name=tempfile.mkstemp(dir=self.store,prefix='.state-')
  try:
   with os.fdopen(fd,'w') as output:json.dump(state,output,ensure_ascii=False);output.flush();os.fsync(output.fileno())
   os.replace(name,self.path)
   directory=os.open(self.store,FLAGS)
   try:os.fsync(directory)
   finally:os.close(directory)
  finally:
   if os.path.exists(name):os.unlink(name)
 def _build(self,fd,root,identity):
  items=[];skipped=0;seen=set()
  with os.scandir(fd) as entries:
   for count,entry in enumerate(entries):
    if count>=2000:raise ValueError('file_scan_limit')
    name=entry.name
    if not safe_name(name):skipped+=1;continue
    try:info=os.stat(name,dir_fd=fd,follow_symlinks=False)
    except FileNotFoundError:skipped+=1;continue
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:skipped+=1;continue
    group=EXTENSIONS.get(Path(name).suffix.lower(),'其他')
    key=(group,unicodedata.normalize('NFC',name).casefold())
    if key in seen:raise ValueError('file_destination_collision')
    seen.add(key);items.append({'source':name,'category':group,'fingerprint':fingerprint(info)})
    if len(items)>1000:raise ValueError('file_scan_limit')
  return {'id':uuid.uuid4().hex,'root':root,'root_identity':identity,'created':time.time(),'status':'pending','items':sorted(items,key=lambda i:i['source']),'skipped':skipped}
 def public(self,plan,details=True):
  if not plan:return None
  result={k:plan[k] for k in ('id','root','created','status','skipped')}
  result.update(count=len(plan['items']),categories=dict(Counter(i['category'] for i in plan['items'])),
                moved=sum(bool(i.get('moved')) and not i.get('undone') for i in plan['items']),error=plan.get('error'))
  if details:result['files']=[{'source':i['source'],'destination':i['category']+'/'+i['source'],'moved':bool(i.get('moved')),'undone':bool(i.get('undone'))} for i in plan['items']]
  return result
 def status(self):
  with self.locked():
   state=self._load();return {key:self.public(state.get(key)) for key in ('pending','operation')}
 def scan(self):
  with self.locked(),self.root() as (fd,root,identity):
   plan=self._build(fd,root,identity);return {'count':len(plan['items']),'categories':dict(Counter(i['category'] for i in plan['items'])),'skipped':plan['skipped']}
 def preview(self):
  with self.locked(),self.root() as (fd,root,identity):
   state=self._load()
   if state.get('operation') and state['operation']['status'] in ('executing','partial','undoing'):raise ValueError('file_recovery_required')
   plan=self._build(fd,root,identity);self._preflight(fd,plan)
   state['pending']=plan;self._save(state);return self.public(plan)
 def _item(self,item):
  if not safe_name(item['source']) or item['category'] not in GROUPS:raise ValueError('file_journal_invalid')
 def _source(self,fd,item):
  self._item(item);info=os.stat(item['source'],dir_fd=fd,follow_symlinks=False)
  if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or fingerprint(info)!=item['fingerprint']:raise ValueError('file_source_changed')
 def _target(self,fd,group,create=False):
  if group not in GROUPS:raise ValueError('file_journal_invalid')
  if create:
   try:os.mkdir(group,mode=0o755,dir_fd=fd)
   except FileExistsError:pass
  try:return os.open(group,FLAGS,dir_fd=fd)
  except OSError:raise ValueError('file_destination_unsafe') from None
 def _preflight(self,fd,plan):
  for item in plan['items']:
   self._source(fd,item)
   try:os.stat(item['category'],dir_fd=fd,follow_symlinks=False)
   except FileNotFoundError:continue
   target=self._target(fd,item['category'])
   try:
    try:os.stat(item['source'],dir_fd=target,follow_symlinks=False)
    except FileNotFoundError:continue
    raise ValueError('file_destination_collision')
   finally:os.close(target)
 def execute(self,identity,confirm):
  if confirm is not True:raise ValueError('file_confirmation_required')
  with self.locked():
   state=self._load();plan=state.get('pending')
   if not plan or plan['id']!=identity:raise ValueError('file_plan_invalid')
   if time.time()-plan['created']>600:raise ValueError('file_plan_expired')
   if state.get('operation') and state['operation']['status'] in ('executing','partial','undoing'):raise ValueError('file_recovery_required')
   with self.root(plan) as (fd,_,__):
    self._preflight(fd,plan)
    operation={**plan,'status':'executing','items':[{**i,'moved':False,'undone':False} for i in plan['items']]}
    state.update(pending=None,operation=operation);self._save(state)
    for item in operation['items']:
     try:
      self._source(fd,item);target=self._target(fd,item['category'],True)
      try:rename_exclusive(fd,item['source'],target,item['source']);os.fsync(target);os.fsync(fd)
      finally:os.close(target)
     except (ValueError,OSError):
      operation.update(status='partial',error='file_move_interrupted');self._save(state);return self.public(operation)
     item['moved']=True;self._save(state)
    operation['status']='completed';self._save(state);return self.public(operation)
 def undo(self,identity,confirm):
  if confirm is not True:raise ValueError('file_confirmation_required')
  with self.locked():
   state=self._load();operation=state.get('operation')
   if not operation or operation['id']!=identity or operation['status']=='reverted':raise ValueError('file_plan_invalid')
   with self.root(operation) as (fd,_,__):
    operation.update(status='undoing',error=None);self._save(state);conflicts=0
    for item in reversed(operation['items']):
     self._item(item)
     if item.get('undone'):continue
     try:
      # Reconcile a crash after rename but before its journal update.
      try:self._source(fd,item);original=True
      except FileNotFoundError:original=False
      except ValueError:original=False
      try:target=self._target(fd,item['category'])
      except ValueError:
       if original and not item.get('moved'):continue
       raise
      try:
       try:info=os.stat(item['source'],dir_fd=target,follow_symlinks=False)
       except FileNotFoundError:
        if original:item['undone']=True;continue
        raise
       if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or fingerprint(info)!=item['fingerprint']:raise ValueError('file_source_changed')
       item['moved']=True
       rename_exclusive(target,item['source'],fd,item['source']);os.fsync(target);os.fsync(fd)
      finally:os.close(target)
      item['undone']=True
     except (ValueError,OSError):conflicts+=1
     self._save(state)
    operation.update(status='partial' if conflicts else 'reverted',error='file_undo_conflict' if conflicts else None)
    self._save(state);return self.public(operation)
