"""Admin calendar configuration and local event management."""
import uuid
from aiohttp import web
from .mcp_tools.calendar import ERROR_MESSAGES
async def calendar_api(request):
 from .app import HUB
 hub=request.app[HUB];store=hub.calendar.store
 try:
  if request.path.endswith('/settings'):
   settings=store.save_settings(await request.json()) if request.method=='PUT' else store.settings()
   return web.json_response({'settings':settings})
  if request.path.endswith('/cancel'):
   event=store.cancel(request.match_info['identity']);hub.calendar.cancel_notification(event['event_id'])
   hub.publish({'type':'calendar.changed','event_id':event['event_id'],'status':'cancelled'});return web.json_response({'event':event})
  if request.method=='GET':return web.json_response(store.query(request.query.get('start'),request.query.get('end')))
  body=await request.json()
  if not isinstance(body,dict) or set(body)-{'title','start_time','end_time','remind_minutes','request_key'}:raise ValueError('calendar_arguments_invalid')
  if not hub.config.get()['tools']['calendar']:raise ValueError('calendar_disabled')
  event=store.create(**{**body,'request_key':body.get('request_key') or uuid.uuid4().hex})
  hub.publish({'type':'calendar.changed','event_id':event['event_id'],'status':'created'});return web.json_response({'event':event},status=201)
 except (ValueError,TypeError,KeyError) as exc:return web.json_response({'error':{'code':'calendar_request_invalid','message':ERROR_MESSAGES.get(str(exc),'请检查时区、时间、标题或提醒设置。')}},status=400)
