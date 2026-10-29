"""购物清单; private local storage, with validated bounded arguments."""
import asyncio
import os
from pathlib import Path
from ..everyday_store import EverydayStore
from ..config import ConfigFile
SCHEMA=('添加、查询、标记已买或移除购物清单条目。商品名保留数量。',{'action': {'type': 'string', 'enum': ['add', 'list', 'complete', 'remove']}, 'items': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'uniqueItems': True, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 60}}, 'list_name': {'type': 'string', 'minLength': 1, 'maxLength': 40}})
REQUIRED=['action'];READ_ONLY=False
ACKNOWLEDGEMENT='收到，我来处理购物清单。'
ERROR_MESSAGES={'everyday_arguments_invalid':'参数还不明确，请重新说明。','everyday_limit':'本地记录已达上限，请先移除不需要的记录。','everyday_disabled':'这个工具尚未启用。'}
async def execute(http,action,**arguments):
 root=Path(os.environ.get('VOICEHUB_DATA_ROOT',Path(__file__).resolve().parents[2]/'data'))
 if not ConfigFile(root/'settings.json').get()['tools'].get('shopping_list'):raise ValueError('everyday_disabled')
 allowed={'add': ['items', 'list_name'], 'list': ['list_name'], 'complete': ['items', 'list_name'], 'remove': ['items', 'list_name']}
 if action not in allowed or set(arguments)-set(allowed[action]):raise ValueError('everyday_arguments_invalid')
 store=EverydayStore(root)

 return await asyncio.to_thread(store.shopping,action,**arguments)
