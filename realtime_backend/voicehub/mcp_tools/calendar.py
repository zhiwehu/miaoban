"""Calendar MCP: local event query/create/cancel. Recipient context is host-injected."""
import asyncio
import os
from pathlib import Path
from ..calendar_store import CalendarStore
from ..config import ConfigFile
SCHEMA=('查询本地日程，创建单次日程和提醒，或取消指定日程。时间必须为ISO日期时间，按配置时区解释；不能猜缺少的日期或时刻。',{
 'action':{'type':'string','enum':['query','create','cancel']},'title':{'type':'string','maxLength':100},
 'start_time':{'type':'string','maxLength':64},'end_time':{'type':'string','maxLength':64},
 'remind_minutes':{'type':'integer','minimum':0,'maximum':10080},
 'range_start':{'type':'string','maxLength':64},'range_end':{'type':'string','maxLength':64},'event_id':{'type':'string','maxLength':32}})
REQUIRED=['action'];READ_ONLY=False
ACKNOWLEDGEMENT='收到，我来处理日程。'
ERROR_MESSAGES={key:value for key,value in [
 ('calendar_disabled','日程工具尚未启用。'),('calendar_time_required','请明确日程日期和时刻。'),('calendar_time_invalid','日期或时刻无效，请重新说明。'),
 ('calendar_time_ambiguous','这个本地时刻涉及夏令时歧义，请提供带时区偏移的完整时间。'),('calendar_time_range','日程必须在未来一年内，结束时间须在开始之后且时长不超过一天。'),
 ('calendar_query_range','查询范围必须大于零且不超过366天。'),('calendar_event_missing','没有找到这个日程，请先查询日程列表。'),
 ('calendar_id_invalid','日程标识无效。'),('calendar_request_changed','同一创建请求的内容发生变化，请重新发起。'),('calendar_title_invalid','请提供不超过100字的日程标题。'),
 ('calendar_remind_invalid','提前提醒分钟数无效。'),('calendar_limit','本地日程数量已达1000条上限。'),('calendar_arguments_invalid','请检查日程操作需要的参数。')]}
async def execute(http,action,**arguments):
 root=Path(os.environ.get('VOICEHUB_DATA_ROOT',Path(__file__).resolve().parents[2]/'data'))
 if not ConfigFile(root/'settings.json').get()['tools']['calendar']:raise ValueError('calendar_disabled')
 allowed={'query':{'range_start','range_end'},'create':{'title','start_time','end_time','remind_minutes'},'cancel':{'event_id'}}
 if action not in allowed or set(arguments)-allowed[action]:raise ValueError('calendar_arguments_invalid')
 store=CalendarStore(root)
 if action=='query':return await asyncio.to_thread(store.query,**arguments)
 if action=='cancel':
  if not arguments.get('event_id'):raise ValueError('calendar_arguments_invalid')
  return await asyncio.to_thread(store.cancel,arguments['event_id'])
 if not arguments.get('title') or not arguments.get('start_time'):raise ValueError('calendar_arguments_invalid')
 context={'session_id':os.environ.get('VOICEHUB_SESSION_ID',''),'request_key':os.environ.get('VOICEHUB_REQUEST_ID','')}
 if os.environ.get('VOICEHUB_DEVICE_ID'):context['device_id']=os.environ['VOICEHUB_DEVICE_ID']
 return await asyncio.to_thread(store.create,**arguments,**context)
