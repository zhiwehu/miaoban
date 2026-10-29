"""物品位置记录; private local storage, with validated bounded arguments."""
import asyncio
import os
from pathlib import Path
from ..everyday_store import EverydayStore
from ..config import ConfigFile
SCHEMA=('仅按用户明确请求保存物品位置，查询记录、忘记或列出记录。查不到不能猜。',{'action': {'type': 'string', 'enum': ['remember', 'recall', 'forget', 'list']}, 'item': {'type': 'string', 'minLength': 1, 'maxLength': 60}, 'location': {'type': 'string', 'minLength': 1, 'maxLength': 300}})
REQUIRED=['action'];READ_ONLY=False
ACKNOWLEDGEMENT='收到，我来查一下物品位置记录。'
ERROR_MESSAGES={'everyday_arguments_invalid':'参数还不明确，请重新说明。','everyday_limit':'本地记录已达上限，请先移除不需要的记录。','everyday_disabled':'这个工具尚未启用。'}
async def execute(http,action,**arguments):
 root=Path(os.environ.get('VOICEHUB_DATA_ROOT',Path(__file__).resolve().parents[2]/'data'))
 if not ConfigFile(root/'settings.json').get()['tools'].get('item_memory'):raise ValueError('everyday_disabled')
 allowed={'remember': ['item', 'location'], 'recall': ['item'], 'forget': ['item'], 'list': []}
 if action not in allowed or set(arguments)-set(allowed[action]):raise ValueError('everyday_arguments_invalid')
 store=EverydayStore(root)

 return await asyncio.to_thread(store.memory,action,**arguments)
