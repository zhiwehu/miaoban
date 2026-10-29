"""Generate small documents into a managed store; accept IDs, never model paths."""
import csv
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import time
import uuid
import zipfile
from xml.sax.saxutils import escape
from .storage import atomic_json,read_json,record_path

MIME={'txt':'text/plain','md':'text/markdown','csv':'text/csv','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
class Artifacts:
 def __init__(self,root):
  self.root=Path(root)/'workflow-artifacts';self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
 def create(self,title,format,content,task_id):
  if format not in MIME or not isinstance(title,str) or not 1<=len(title)<=100 or any(ord(c)<32 or c in '/\\' for c in title):raise ValueError('artifact_title_invalid')
  if not isinstance(content,str) or not content.strip() or len(content)>12000 or any(ord(c)<32 and c not in '\n\r\t' for c in content):raise ValueError('artifact_content_invalid')
  if format=='csv':
   rows=list(csv.reader(io.StringIO(content)))
   if len(rows)>500 or any(len(row)>30 for row in rows):raise ValueError('artifact_csv_limit')
   # Prevent spreadsheet formula execution when the CSV is opened.
   out=io.StringIO(newline='');writer=csv.writer(out)
   for row in rows:writer.writerow(["'"+cell if cell.lstrip().startswith(('=','+','-','@')) else cell for cell in row])
   raw=out.getvalue().encode('utf-8-sig')
  elif format=='docx':
   output=io.BytesIO()
   paragraphs=''.join('<w:p><w:r><w:t xml:space="preserve">'+escape(line)+'</w:t></w:r></w:p>' for line in (title+'\n'+content).splitlines())
   document='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+paragraphs+'<w:sectPr/></w:body></w:document>'
   with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('[Content_Types].xml','<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    archive.writestr('_rels/.rels','<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    archive.writestr('word/document.xml',document)
   raw=output.getvalue()
  else:raw=content.encode('utf-8')
  identity=uuid.uuid4().hex;path=self.root/(identity+'.'+format)
  fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
  name=re.sub(r'[<>:"|?*]','_',title).strip(' .') or 'document'
  metadata={'artifact_id':identity,'filename':name+'.'+format,'format':format,'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'task_id':task_id,'created':time.time()}
  atomic_json(record_path(self.root,identity),metadata);return {k:v for k,v in metadata.items() if k not in ('task_id','created')}
 def read(self,identity,task_id=None):
  meta=read_json(record_path(self.root,identity))
  if task_id is not None and meta['task_id']!=task_id:raise ValueError('artifact_wrong_task')
  if meta['format'] not in MIME:raise ValueError('artifact_invalid')
  path=self.root/(identity+'.'+meta['format']);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
  with os.fdopen(fd,'rb') as f:
   info=os.fstat(f.fileno())
   if not stat.S_ISREG(info.st_mode) or info.st_size>8*1024*1024:raise ValueError('artifact_invalid')
   raw=f.read(8*1024*1024+1)
  if len(raw)!=meta['size'] or hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('artifact_changed')
  return meta,raw
