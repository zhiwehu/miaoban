"""System information tool: schema, acknowledgement and host overview."""
import asyncio
import time
from .shared import fetched_at

SCHEMA = ('查询运行 VoiceHub 的电脑 CPU、内存、磁盘和运行时长。',
 {'sections': {'type': 'array',
               'minItems': 1,
               'maxItems': 4,
               'uniqueItems': True,
               'items': {'type': 'string', 'enum': ['cpu', 'memory', 'disk', 'uptime']},
               'description': '省略时返回全部概况。'}})
REQUIRED = []
ACKNOWLEDGEMENT = '我看一下这台电脑的运行情况。'


def system_info(sections=None):
    import psutil

    memory, disk = psutil.virtual_memory(), psutil.disk_usage('/')
    result = {'fetched_at':fetched_at(), 'cpu':{'logical_cores':psutil.cpu_count(),
            'usage_percent':psutil.cpu_percent(interval=.1), 'sampling':'100ms sample'},
        'memory':{'total_gb':round(memory.total/1024**3, 2), 'available_gb':round(memory.available/1024**3, 2),
                  'usage_percent':memory.percent},
        'disk':{'total_gb':round(disk.total/1024**3, 2), 'free_gb':round(disk.free/1024**3, 2),
                'usage_percent':disk.percent},
        'uptime_seconds':int(time.time()-psutil.boot_time())}
    if sections is not None:
        keys={'cpu':'cpu','memory':'memory','disk':'disk','uptime':'uptime_seconds'}
        result={key:value for key,value in result.items()
                if key=='fetched_at' or key in {keys[name] for name in sections}}
    return result



async def execute(http, sections=None):
    return await asyncio.to_thread(system_info, sections=sections)
