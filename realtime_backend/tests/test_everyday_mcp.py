import json
import tempfile
import unittest
from pathlib import Path
from voicehub.config import ConfigFile
from voicehub.mcp_client import CommonToolsClient
from voicehub.tools import validate_arguments

class EverydayMCP(unittest.IsolatedAsyncioTestCase):
 async def test_real_stdio_roundtrip(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cfg=ConfigFile(root/'settings.json');value=cfg.get()
   for name in ('shopping_list','item_memory','timer'):value['tools'][name]=True
   cfg.save(value)
   async with CommonToolsClient(data_root=root,env={'VOICEHUB_DEVICE_ID':'test'}) as c:
    for tool,args in [('shopping_list',{'action':'add','items':['两盒牛奶']}),('item_memory',{'action':'remember','item':'钥匙','location':'门口抽屉'}),('timer',{'action':'start','duration_seconds':30,'name':'泡面'})]:
     result=await c.call(tool,args);self.assertFalse(result['isError'],result)
     self.assertEqual(json.loads(result['content'][0]['text'])['status'],'ok')
 def test_array_validation(self):
  for bad in ([],[''],['a']*11,[3],['x'*61]):
   with self.assertRaises(ValueError):validate_arguments('shopping_list',{'action':'add','items':bad})
