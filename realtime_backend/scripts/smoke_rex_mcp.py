"""Real local decisions -> parameters -> read-only MCP calls, using synthetic requests."""
import asyncio
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from voicehub.config import DEFAULT
from voicehub.rex_planner import RexPlanner
from voicehub.mcp_client import CommonToolsClient


async def main():
    planner=RexPlanner();cfg=copy.deepcopy(DEFAULT['tools'])
    records=[]
    try:
        for text in ['请查询上海明天的天气','从中新网读两条新闻','查询本机内存','从BBC读两条新闻',
                     '明天天气怎么样','你会查天气吗','读十条新闻']:
            plan=await planner.plan([{'role':'user','content':text}],cfg)
            record={'text':text,'plan':plan}
            if plan['status']=='ready':
                async with CommonToolsClient(timeout=25) as client:
                    result=await client.call(plan['tool'],plan['arguments'])
                record['mcp_result']=result
            records.append(record)
            print(json.dumps(record,ensure_ascii=False),flush=True)
        pending=await planner.plan([{'role':'user','content':'明天天气怎么样'}],cfg)
        followup=await planner.plan([{'role':'user','content':'明天天气怎么样'},
                                    {'role':'user','content':'上海'}],cfg,pending)
        records.append({'text':'补充城市：上海','plan':followup})
        assert followup['status']=='ready' and followup['arguments']=={'city':'上海','day_offset':1}
        output=Path('../diagnostics/2026-10-28/rex-slots-v2');output.mkdir(parents=True,exist_ok=True)
        (output/'real-mcp-smoke.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
        required=('请查询上海明天的天气','从中新网读两条新闻','查询本机内存')
        failures=[r for r in records if r['text'] in required and
                  (r['plan']['status']!='ready' or r.get('mcp_result',{}).get('isError',True))]
        assert not failures,failures
    finally:await planner.close()


if __name__=='__main__':asyncio.run(main())
