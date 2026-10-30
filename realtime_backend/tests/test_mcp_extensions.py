import asyncio
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
import zipfile
from voicehub.mcp_extensions import ExtensionManager,checked_schema
from voicehub.mcp_client import MCPError


def example_zip(extra=None):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        root=Path(__file__).resolve().parents[1]/'examples/mcp-echo'
        for name in ('mcp.json','server.py'):archive.writestr(name,(root/name).read_bytes())
        if extra:archive.writestr(*extra)
    return output.getvalue()


class ExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.manager=ExtensionManager(self.tmp.name)
    async def asyncTearDown(self):
        await self.manager.close();self.tmp.cleanup()
    async def test_upload_requires_trust_then_real_discovery_parameters_and_disable(self):
        record=self.manager.import_bytes(example_zip())
        self.assertFalse(record['enabled']);self.assertEqual(self.manager.catalogue(),[])
        with self.assertRaises(ValueError):await self.manager.enable('local-echo',False)
        await self.manager.enable('local-echo',True)
        tools=self.manager.catalogue();self.assertEqual(len(tools),1)
        name=tools[0]['name'];self.assertNotEqual(name,'echo')
        with self.assertRaises(ValueError):await self.manager.call(name,{'text':7})
        result=await self.manager.call(name,{'text':'用户上传 MCP 参数验证'})
        self.assertEqual(json.loads(result['content'][0]['text'])['echo'],'用户上传 MCP 参数验证')
        restored=ExtensionManager(self.tmp.name)
        self.assertTrue(restored.public_servers()[0]['enabled'])
        await self.manager.disable('local-echo')
        with self.assertRaises(MCPError):await self.manager.call(name,{'text':'test'})
        await self.manager.disable('local-echo',delete=True)
        self.assertFalse((self.manager.root/'local-echo').exists())
    async def test_upload_does_not_execute_and_rejects_path_traversal(self):
        marker=Path(self.tmp.name)/'executed'
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:
            z.writestr('mcp.json',json.dumps({'id':'marker','name':'marker','entrypoint':'server.py'}))
            z.writestr('server.py',f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        self.manager.import_bytes(raw.getvalue());self.assertFalse(marker.exists())
        with self.assertRaises(ValueError):self.manager.import_bytes(example_zip(('../escape.py','bad')))
        self.assertFalse((self.manager.root.parent/'escape.py').exists())
    def test_property_names_are_not_schema_keywords(self):
        from voicehub.mcp_extensions import validate_schema_arguments
        schema={'type':'object','properties':{'pattern':{'type':'string'},'path':{'type':'string'}},'required':['pattern']}
        self.assertEqual(checked_schema(schema),schema)
        validate_schema_arguments(schema,{'pattern':'*.txt'})
        with self.assertRaises(ValueError):validate_schema_arguments(schema,{'pattern':1})
        with self.assertRaises(ValueError):checked_schema({'type':'object','properties':{'pattern':{'type':'string','pattern':'(a+)+$'}}})
        with self.assertRaises(ValueError):checked_schema({'type':'object','properties':{'x':{'$ref':'https://example.com/schema'}}})

    async def test_schema_references_and_command_substitution_not_allowed(self):
        with self.assertRaises(ValueError):checked_schema({'type':'object','properties':{'x':{'$ref':'https://example.com'}}})
        with self.assertRaises(ValueError):
            self.manager.import_bytes(json.dumps({'mcpServers':{'demo':{'command':'python server.py; echo bad'}}}).encode())

    async def test_json_server_configuration_is_callable_and_environment_values_are_redacted(self):
        script=Path(__file__).resolve().parents[1]/'examples/mcp-echo/server.py'
        self.manager.import_bytes(json.dumps({'mcpServers':{'json-echo':{
            'type':'stdio','command':sys.executable,'args':[str(script)],'env':{'EXAMPLE_TOKEN':'test-value-not-public'}}}}).encode())
        self.assertNotIn('test-value-not-public',str(self.manager.public_servers()))
        await self.manager.enable('json-echo',True)
        name=self.manager.catalogue()[0]['name']
        result=await self.manager.call(name,{'text':'JSON 配置验证'})
        self.assertIn('JSON 配置验证',str(result))

    async def test_disabling_closes_an_inflight_server_process(self):
        root=Path(__file__).resolve().parents[1]/'examples/mcp-echo'
        source=(root/'server.py').read_text().replace("arguments=request.get('params',{}).get('arguments',{})",
            "import time; time.sleep(30)\n        arguments=request.get('params',{}).get('arguments',{})")
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            archive.writestr('mcp.json',(root/'mcp.json').read_bytes());archive.writestr('server.py',source)
        self.manager.import_bytes(raw.getvalue());await self.manager.enable('local-echo',True)
        task=asyncio.create_task(self.manager.call(self.manager.catalogue()[0]['name'],{'text':'wait'}))
        async with asyncio.timeout(3):
            while not any(c.serial>=3 for c in self.manager.active.get('local-echo',())):await asyncio.sleep(.01)
        await self.manager.disable('local-echo')
        with self.assertRaises(MCPError):await task
        self.assertFalse(self.manager.active['local-echo'])
