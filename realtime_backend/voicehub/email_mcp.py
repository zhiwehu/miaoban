"""Standalone stdio email MCP. SMTP credentials are local settings, never tool inputs."""
import asyncio
import json
import os
from pathlib import Path
import re
import sys
from jsonschema import Draft202012Validator,ValidationError
from .mcp_client import VERSION,MAX_MESSAGE
from .workflows.mail import MailStore

SCHEMAS={
 'email_prepare':{'type':'object','properties':{'to':{'type':'string','maxLength':254},'subject':{'type':'string','maxLength':200},'body':{'type':'string','maxLength':8000},'attachments':{'type':'array','items':{'type':'string'},'maxItems':5},'task_id':{'type':'string','maxLength':64}},'required':['to','subject','body','attachments','task_id'],'additionalProperties':False},
 'email_status':{'type':'object','properties':{'draft_id':{'type':'string'},'task_id':{'type':'string'}},'required':['draft_id','task_id'],'additionalProperties':False},
 'email_send':{'type':'object','properties':{'draft_id':{'type':'string'},'task_id':{'type':'string'},'approval_token':{'type':'string','maxLength':100}},'required':['draft_id','task_id','approval_token'],'additionalProperties':False}}
DESCRIPTIONS={'email_prepare':'创建带受控附件的邮件草稿，不发送。SMTP配置由本地平台管理。','email_status':'查看本任务邮件草稿与提交状态，不返回SMTP凭据或发送授权。','email_send':'提交邮件到SMTP，必须持有平台在用户确认后签发的一次性授权；模型不能生成授权。'}

def catalogue():return [{'name':name,'description':DESCRIPTIONS[name],'inputSchema':schema,'annotations':{'readOnlyHint':name=='email_status'}} for name,schema in SCHEMAS.items()]
async def call(store,name,args):
 try:
  if name not in SCHEMAS:raise ValueError('mail_tool_unknown')
  Draft202012Validator(SCHEMAS[name]).validate(args)
  if name=='email_prepare':value=await asyncio.to_thread(store.prepare,**args)
  elif name=='email_status':value=await asyncio.to_thread(store.describe,args['draft_id'],args['task_id'])
  else:value=await asyncio.to_thread(store.send,args['draft_id'],args['approval_token'],args['task_id'])
  if value.get('status') in ('failed','unknown'):
   return {'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'isError':True,'structuredContent':{'error':{'code':value.get('code','mail_delivery_unknown')}}}
  return {'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'isError':False}
 except (ValueError,KeyError,TypeError,OSError,ValidationError) as exc:
  code=str(exc)
  if not re.fullmatch(r'[a-z_]{1,80}',code):code='mail_operation_failed'
  return {'content':[{'type':'text','text':'邮件操作未完成，请检查配置、草稿或发送授权。'}],'isError':True,'structuredContent':{'error':{'code':code}}}
async def serve():
 root=Path(os.environ.get('VOICEHUB_DATA_ROOT',Path(__file__).resolve().parents[1]/'data'));store=MailStore(root)
 reader=asyncio.StreamReader(limit=MAX_MESSAGE);loop=asyncio.get_running_loop();await loop.connect_read_pipe(lambda:asyncio.StreamReaderProtocol(reader),sys.stdin.buffer)
 initialized=False;ready=False
 while True:
  try:
   raw=await reader.readline()
   if not raw:return
   request=json.loads(raw)
   if not isinstance(request,dict) or request.get('jsonrpc')!='2.0':raise ValueError('invalid_request')
   method=request.get('method');identity=request.get('id')
   if method=='notifications/initialized' and initialized:ready=True;continue
   if identity is None:continue
   if method=='initialize' and not initialized:
    initialized=True;result={'protocolVersion':VERSION,'capabilities':{'tools':{}},'serverInfo':{'name':'voicehub-email','version':'0.1'}}
   elif ready and method=='tools/list':result={'tools':catalogue()}
   elif ready and method=='tools/call':
    params=request.get('params',{})
    if not isinstance(params,dict):raise ValueError('invalid_request')
    result=await call(store,params.get('name'),params.get('arguments',{}))
   elif method=='ping':result={}
   else:
    print(json.dumps({'jsonrpc':'2.0','id':identity,'error':{'code':-32601,'message':'Unknown method'}}),flush=True);continue
   response={'jsonrpc':'2.0','id':identity,'result':result}
  except (ValueError,TypeError,KeyError):response={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid request'}}
  encoded=json.dumps(response,ensure_ascii=False)
  if len(encoded.encode())>MAX_MESSAGE:encoded=json.dumps({'jsonrpc':'2.0','id':identity,'error':{'code':-32603,'message':'Output limit'}})
  print(encoded,flush=True)
if __name__=='__main__':asyncio.run(serve())
