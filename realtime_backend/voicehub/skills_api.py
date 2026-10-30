"""Protected skill definitions and visible background-operation state."""
import asyncio
import contextlib
import uuid
from aiohttp import web
async def skills_api(request):
 from .app import HUB
 hub=request.app[HUB];identity=request.match_info.get('identity')
 try:
  if request.path=='/api/operations':return web.json_response({'operations':hub.operations.recent()})
  if request.path.startswith('/api/operations/'):
   await hub.operations.cancel(identity);return web.json_response({'operation':hub.operations.get(identity)})
  if request.path=='/api/skills':
   if request.method=='GET':return web.json_response({'skills':hub.skills.list()})
   return web.json_response({'skill':hub.skills.save(await request.json())},status=201)
  if request.path.endswith('/run'):
   body=await request.json()
   if not isinstance(body,dict) or set(body)-{'arguments'}:raise ValueError('skill_arguments_invalid')
   scope='ui-'+uuid.uuid4().hex;hub.operations.check_capacity(scope);task=hub.skills.instantiate(identity,body.get('arguments',{}),scope)
   async def source():
    pipeline=hub.factory(hub.config.get(),hub.http,hub.local);pipeline.session_id=scope
    async with contextlib.aclosing(hub.workflows.run(task['id'],pipeline)) as stream:
     async for item in stream:yield item
   gate=asyncio.Event();record=hub.operations.start(scope,task['id'],task['goal'],source(),gate);gate.set()
   return web.json_response({'task':task,'operation':record},status=202)
  body=await request.json()
  if not isinstance(body,dict) or body.get('id')!=identity:raise ValueError('skill_id_invalid')
  return web.json_response({'skill':hub.skills.save(body)})
 except FileNotFoundError:raise web.HTTPNotFound()
 except (ValueError,TypeError,KeyError) as exc:
  return web.json_response({'error':{'code':'skill_request_invalid','message':'配置或参数未通过校验，请检查技能、输入参数和工具是否启用。'}},status=400)
