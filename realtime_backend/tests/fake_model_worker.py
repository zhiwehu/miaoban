import argparse,os
from aiohttp import web
parser=argparse.ArgumentParser()
for key in ['kind','model','port','service-id']:parser.add_argument('--'+key,required=True)
args=parser.parse_args()
async def health(request):
    if request.headers.get('Authorization')!='Bearer '+os.environ['VOICEHUB_WORKER_TOKEN']:raise web.HTTPForbidden()
    return web.json_response({'ready':True,'service_id':args.service_id,'model':args.model,'active':0})
app=web.Application();app.router.add_get('/health',health)
web.run_app(app,host='127.0.0.1',port=int(args.port),access_log=None,print=None)
