"""Admin-only preview, explicit confirmation and undo. No file mutation tool for the LLM."""
import asyncio
from aiohttp import web
from .file_organizer import FileOrganizer
from .mcp_tools.file_organizer import ERROR_MESSAGES

async def files_api(request):
 from .app import HUB
 hub=request.app[HUB];cfg=hub.config.get()['tools'];action=request.match_info.get('action')
 try:
  organizer=FileOrganizer(hub.root,cfg.get('file_directory',''))
  if not action:return web.json_response({'enabled':cfg['file_organizer'],'directory':cfg['file_directory'],**await asyncio.to_thread(organizer.status)})
  if not cfg['file_organizer']:raise ValueError('file_disabled')
  body=await request.json()
  if not isinstance(body,dict) or set(body)-{'plan_id','confirm'}:raise ValueError('file_plan_invalid')
  if action=='preview':result=await asyncio.to_thread(organizer.preview)
  else:
   identity=body.get('plan_id')
   if not isinstance(identity,str) or len(identity)!=32:raise ValueError('file_plan_invalid')
   result=await asyncio.to_thread(getattr(organizer,action),identity,body.get('confirm'))
  hub.store.event(dict(type='files.'+action,plan_id=result['id'],status=result['status'],count=result['count']))
  return web.json_response({'result':result})
 except (ValueError,OSError) as exc:
  code=str(exc) if isinstance(exc,ValueError) and str(exc) in ERROR_MESSAGES else 'file_directory_unavailable'
  return web.json_response({'error':{'code':code,'message':ERROR_MESSAGES[code]}},status=400)
