"""Private bounded JSON records shared by the host and its email MCP process."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile

IDENTITY=re.compile(r'[0-9a-f]{32}')
def atomic_json(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd,name=tempfile.mkstemp(dir=path.parent,prefix='.record-')
 try:
  with os.fdopen(fd,'w') as output:json.dump(value,output,ensure_ascii=False);output.flush();os.fsync(output.fileno())
  os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)

def read_json(path,limit=2*1024*1024):
 path=Path(path)
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  with os.fdopen(fd,'rb') as f:
   raw=f.read(limit+1)
   if len(raw)>limit:raise ValueError('workflow_record_limit')
   return json.loads(raw)
 except BaseException:raise

@contextmanager
def locked(path):
 fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:fcntl.flock(fd,fcntl.LOCK_EX);yield
 finally:os.close(fd)

def record_path(root,identity):
 if not isinstance(identity,str) or not IDENTITY.fullmatch(identity):raise ValueError('workflow_id_invalid')
 return Path(root)/(identity+'.json')
