"""Versioned decision schemas; keep the original four-label adapter compatible."""
from .rex_runtime import LABELS
TOOL_LABELS={**LABELS,'购物清单工具':'shopping_list','多计时器工具':'timer','物品位置记录':'item_memory'}
ACTION_LABELS={
 'shopping_list':{'添加待购物品':'add','查看购物清单':'list','标记已经买好':'complete','移除购物条目':'remove'},
 'timer':{'新建倒计时':'start','查询剩余时间':'status','暂停计时':'pause','继续计时':'resume','取消计时':'cancel'},
 'item_memory':{'保存物品位置':'remember','查询物品位置':'recall','删除位置记录':'forget','列出位置记录':'list'}}
