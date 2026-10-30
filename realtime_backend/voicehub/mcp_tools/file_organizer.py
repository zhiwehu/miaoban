"""File metadata summary/preview only; moves require the separate admin confirmation UI."""
import asyncio
import json
import os
from pathlib import Path
from ..file_organizer import FileOrganizer
SCHEMA=('统计管理员指定目录第一层文件，或生成按类型分类的整理预览。不会移动文件；必须到 MCP 页确认后才执行。',{
 'action':{'type':'string','enum':['scan','preview'],'description':'scan统计；preview生成待确认整理计划。不能传目录、移动或删除操作。'}})
REQUIRED=['action']
ACKNOWLEDGEMENT='我先查看指定目录，整理方案生成后请到页面确认。'
READ_ONLY=True
ERROR_MESSAGES={
 'file_disabled':'文件整理尚未启用，请在 MCP 页设置指定目录并启用。',
 'file_directory_invalid':'请设置一个存在的绝对目录，不能使用符号链接作为整理目录。',
 'file_directory_forbidden':'不能整理系统根目录、用户主目录或包含本平台运行数据的目录。',
 'file_directory_changed':'整理目录发生变化，请重新生成预览。',
 'file_directory_unavailable':'整理目录不存在或当前没有访问权限。',
 'file_confirmation_required':'请先在页面确认本次操作。',
 'file_plan_invalid':'整理计划不存在或已经处理，请刷新或重新生成预览。',
 'file_plan_expired':'预览已超过十分钟，请重新生成预览。',
 'file_destination_collision':'分类目录中已有同名文件，请先处理冲突后重新预览。',
 'file_destination_unsafe':'分类目标不是普通目录或包含符号链接，已拒绝操作。',
 'file_source_changed':'文件已变化，请重新生成预览。',
 'file_scan_limit':'目录超过扫描上限（2000项、1000个文件），请缩小整理范围。',
 'file_recovery_required':'上次操作未完整结束，请先查看并撤销上次操作。',
 'file_journal_invalid':'整理记录无法验证，请保留文件并检查后端记录。',
 'file_move_interrupted':'部分文件未能移动，请查看记录并按需撤销。',
 'file_undo_conflict':'撤销遇到文件变化或同名冲突，未覆盖现有文件。',
 'file_atomic_move_unavailable':'当前系统不支持禁止覆盖的原子移动，已拒绝执行。',
}
async def execute(http,action):
 root=Path(os.environ.get('VOICEHUB_DATA_ROOT',Path(__file__).resolve().parents[2]/'data'))
 config=json.loads((root/'settings.json').read_text())['tools']
 if not config.get('file_organizer'):raise ValueError('file_disabled')
 organizer=FileOrganizer(root,config.get('file_directory',''))
 try:result=await asyncio.to_thread(organizer.scan if action=='scan' else organizer.preview)
 except OSError:raise ValueError('file_directory_unavailable') from None
 if action=='scan':return {**result,'status':'scanned','message':'只统计文件类型和数量，没有读取内容或移动文件。'}
 return {'status':'pending_confirmation','plan_id':result['id'],**{k:result[k] for k in ('count','categories','skipped')},
         'requires_confirmation':True,'message':'尚未移动任何文件。请到 MCP 工具页查看源文件与目标列表，再确认执行。'}


def spoken_summary(result):
 """Keep file and skipped-item counts exact without an extra model generation."""
 value=json.loads(result['content'][0]['text'])
 count=value['count'];skipped=value['skipped'];groups=value['categories']
 if type(count) is not int or type(skipped) is not int or not isinstance(groups,dict):raise ValueError('invalid_file_summary')
 kinds='、'.join(str(k)+str(v)+'个' for k,v in sorted(groups.items()))
 if count==0:return '指定目录第一层没有可整理的普通文件。'+('跳过'+str(skipped)+'项。' if skipped else '')
 response=('已生成'+str(count)+'个文件的分类预览' if value['status']=='pending_confirmation' else '指定目录第一层有'+str(count)+'个可整理文件')
 if kinds:response+='，其中'+kinds
 response+='。'
 if skipped:response+='跳过'+str(skipped)+'项，包括子目录、隐藏文件或链接等。'
 return response+('文件尚未移动，请到 MCP 工具页核对清单并确认执行。' if value['status']=='pending_confirmation' else '没有读取文件内容或移动文件。')
