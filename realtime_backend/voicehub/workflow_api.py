"""Administrator task controls, private SMTP settings and managed artifact downloads."""
import asyncio
import contextlib
import json
from pathlib import Path
import sys
import uuid
from urllib.parse import quote
from aiohttp import web
from .workflows.artifacts import MIME

MESSAGES={'workflow_confirmation_changed':'草稿、附件、发件配置发生变化或确认已过期，请核对最新摘要后重新确认。',
 'workflow_wrong_session':'这项任务不属于当前会话。','workflow_not_waiting_confirmation':'任务当前没有等待确认的操作。',
 'mail_not_configured':'请先配置 SMTP 并启用邮件发送。','mail_settings_invalid':'SMTP 设置无效，请检查端口、加密方式和字段格式。',
 'mail_address_invalid':'邮箱地址格式无效。','workflow_cannot_resume':'当前任务无法继续，结果不明的发送不能直接重试。',
 'workflow_revision_changed':'任务状态已更新，请刷新后再操作。'}
async def workflow_api(request):
 from .app import HUB
 hub=request.app[HUB];manager=hub.workflows;identity=request.match_info.get('identity')
 try:
  if request.path=='/api/workflow-settings':
   if request.method=='PUT':return web.json_response({'settings':manager.settings.save(await request.json())})
   return web.json_response({'settings':manager.settings.public()})
  if request.path=='/api/workflow-settings/mcp-config':
   script=Path(__file__).resolve().parents[1]/'scripts/run_email_mcp.py'
   data={'mcpServers':{'voicehub-email':{'type':'stdio','command':sys.executable,'args':[str(script)],'env':{'VOICEHUB_DATA_ROOT':str(hub.root.resolve())}}}}
   return web.Response(text=json.dumps(data,ensure_ascii=False,indent=2),content_type='application/json',headers={'Content-Disposition':'attachment; filename="voicehub-email-mcp.json"'})
  if request.path.startswith('/api/artifacts/'):
   meta,raw=manager.artifacts.read(identity)
   return web.Response(body=raw,content_type=MIME[meta['format']],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(meta['filename'],safe='')})
  if request.path=='/api/workflows':
   if request.method=='GET':return web.json_response({'tasks':manager.recent()})
   body=await request.json()
   if not isinstance(body,dict) or set(body)!={'goal'}:raise ValueError('workflow_goal_invalid')
   task=manager.create(body['goal'],'ui-'+uuid.uuid4().hex);launch(hub,task['id']);return web.json_response({'task':task},status=202)
  task=manager.get(identity)
  if request.method=='GET':return web.json_response({'task':task})
  body=await request.json()
  if not isinstance(body,dict) or set(body)-{'action','answer','confirm','revision'}:raise ValueError('workflow_action_invalid')
  action=body.get('action')
  if body.get('revision')!=task['revision']:raise ValueError('workflow_revision_changed')
  if action=='cancel':await manager.cancel(identity,task['session_id'])
  elif action=='confirm':
   if body.get('confirm') is not True:raise ValueError('workflow_confirmation_required')
   manager.approve(identity,task['session_id']);launch(hub,identity)
  elif action=='answer':manager.answer(identity,task['session_id'],body.get('answer'));launch(hub,identity)
  elif action=='resume':manager.resume(identity,task['session_id']);launch(hub,identity)
  else:raise ValueError('workflow_action_invalid')
  return web.json_response({'task':manager.get(identity)},status=202)
 except FileNotFoundError:raise web.HTTPNotFound()
 except (ValueError,KeyError,TypeError,OSError) as exc:
  code=str(exc)
  if not code.replace('_','').isalnum() or len(code)>80:code='workflow_request_invalid'
  return web.json_response({'error':{'code':code,'message':MESSAGES.get(code,'操作未完成，请检查任务状态或配置（'+code+'）。')}},status=400)

def launch(hub,identity):
 manager=hub.workflows
 async def work():
  manager.jobs[identity]=asyncio.current_task()
  try:
   task=manager.get(identity);pipeline=hub.factory(hub.config.get(),hub.http,hub.local);pipeline.session_id=task['session_id']
   if task['status']=='planning':await manager.plan(identity,pipeline)
   async with contextlib.aclosing(manager.run(identity,pipeline)) as source:
    async for _ in source:pass
  except asyncio.CancelledError:
   task=manager._load(identity)
   if task['status']=='planning':task.update(status='interrupted',error='workflow_interrupted');manager._save(task)
   raise
  except Exception:
   task=manager._load(identity)
   if task['status'] not in ('unknown','completed','cancelled'):task.update(status='failed',error='workflow_execution_failed');manager._save(task)
 job=asyncio.create_task(work());manager.background.add(job)
 def finished(done):
  manager.background.discard(done)
  if manager.jobs.get(identity) is done:manager.jobs.pop(identity,None)
 job.add_done_callback(finished)
