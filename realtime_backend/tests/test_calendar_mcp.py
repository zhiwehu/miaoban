from datetime import datetime,timedelta,timezone
import json
import tempfile
import unittest
from voicehub.mcp_client import CommonToolsClient
class CalendarMcpTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_stdio_create_query_cancel_and_host_owned_device_context(self):
  with tempfile.TemporaryDirectory() as root:
   start=(datetime.now(timezone.utc)+timedelta(days=1)).replace(microsecond=0)
   async with CommonToolsClient(data_root=root,env={'VOICEHUB_DEVICE_ID':'fixture-robot','VOICEHUB_SESSION_ID':'fixture-session','VOICEHUB_REQUEST_ID':'test-request'}) as client:
    tools=await client.list_tools();self.assertIn('calendar',{t['name'] for t in tools})
    args={'action':'create','title':'本地协议测试','start_time':start.isoformat(),'remind_minutes':0}
    first=await client.call('calendar',args);self.assertFalse(first['isError']);event=json.loads(first['content'][0]['text']);self.assertEqual(event['device_id'],'fixture-robot')
    second=await client.call('calendar',args);self.assertEqual(json.loads(second['content'][0]['text'])['event_id'],event['event_id'])
    query=await client.call('calendar',{'action':'query','range_start':(start-timedelta(hours=1)).isoformat(),'range_end':(start+timedelta(hours=2)).isoformat()});self.assertEqual(json.loads(query['content'][0]['text'])['count'],1)
    forged=await client.call('calendar',{**args,'device_id':'other'});self.assertTrue(forged['isError'])
    cancelled=await client.call('calendar',{'action':'cancel','event_id':event['event_id']});self.assertEqual(json.loads(cancelled['content'][0]['text'])['status'],'cancelled')
