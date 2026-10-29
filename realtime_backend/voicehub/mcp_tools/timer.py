"""多计时器; private local storage, with validated bounded arguments."""
import asyncio
import os
from pathlib import Path
from ..everyday_store import EverydayStore
from ..config import ConfigFile
SCHEMA=('创建1秒到24小时的倒计时，查询、暂停、继续或取消。多个计时器需要名字；不会因插话取消。',{'action': {'type': 'string', 'enum': ['start', 'status', 'pause', 'resume', 'cancel']}, 'name': {'type': 'string', 'minLength': 1, 'maxLength': 60}, 'duration_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 86400}})
REQUIRED=['action'];READ_ONLY=False
ACKNOWLEDGEMENT='收到，我来处理计时器。'
ERROR_MESSAGES={'everyday_arguments_invalid':'参数还不明确，请重新说明。','everyday_limit':'本地记录已达上限，请先移除不需要的记录。','everyday_disabled':'这个工具尚未启用。'}
async def execute(http,action,**arguments):
 root=Path(os.environ.get('VOICEHUB_DATA_ROOT',Path(__file__).resolve().parents[2]/'data'))
 if not ConfigFile(root/'settings.json').get()['tools'].get('timer'):raise ValueError('everyday_disabled')
 allowed={'start': ['name', 'duration_seconds'], 'status': ['name'], 'pause': ['name'], 'resume': ['name'], 'cancel': ['name']}
 if action not in allowed or set(arguments)-set(allowed[action]):raise ValueError('everyday_arguments_invalid')
 store=EverydayStore(root)
 arguments.update(device=os.environ.get('VOICEHUB_DEVICE_ID',''),session=os.environ.get('VOICEHUB_SESSION_ID',''),request=os.environ.get('VOICEHUB_REQUEST_ID',''))
 return await asyncio.to_thread(store.timer,action,**arguments)
