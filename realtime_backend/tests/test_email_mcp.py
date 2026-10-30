import json
import sys
import tempfile
import unittest
from voicehub.mcp_client import CommonToolsClient
class EmailMcpTests(unittest.IsolatedAsyncioTestCase):
 async def test_standalone_protocol_prepares_draft_but_cannot_forge_send(self):
  with tempfile.TemporaryDirectory() as root:
   async with CommonToolsClient(command=[sys.executable,'-m','voicehub.email_mcp'],data_root=root) as client:
    tools=await client.list_tools();self.assertEqual({t['name'] for t in tools},{'email_prepare','email_status','email_send'})
    r=await client.call('email_prepare',{'to':'nobody@example.com','subject':'测试草稿','body':'本地测试，不发送','attachments':[],'task_id':'task-test'})
    self.assertFalse(r['isError']);draft=json.loads(r['content'][0]['text'])
    r=await client.call('email_send',{'draft_id':draft['draft_id'],'task_id':'task-test','approval_token':'confirmed:true'})
    self.assertTrue(r['isError']);self.assertEqual(r['structuredContent']['error']['code'],'mail_approval_required')
    r=await client.call('email_status',{'draft_id':draft['draft_id'],'task_id':'task-test'})
    self.assertNotIn('approval',r['content'][0]['text']);self.assertIn('prepared',r['content'][0]['text'])
