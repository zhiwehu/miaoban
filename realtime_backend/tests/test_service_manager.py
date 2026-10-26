import asyncio
import copy
import socket
import tempfile
import unittest
from pathlib import Path
from voicehub.config import DEFAULT
from voicehub.services import ServiceManager, ServiceError


class ServiceManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.cfg=copy.deepcopy(DEFAULT)
        for kind in ['asr','llm','tts']:self.cfg[kind]['provider']='builtin'
        ports={}
        for kind in ['asr','llm','tts']:
            with socket.socket() as s:s.bind(('127.0.0.1',0));ports[kind]=s.getsockname()[1]
        self.manager=ServiceManager(Path(self.tmp.name),lambda:self.cfg,ports=ports,worker_module='tests.fake_model_worker')

    async def asyncTearDown(self):
        await self.manager.shutdown(stop_workers=True)
        self.tmp.cleanup()

    async def running(self,kind):
        async with asyncio.timeout(8):
            while True:
                status=await self.manager.status(kind)
                if status['state']=='running':return status
                if status['state']=='failed':self.fail(str(status))
                await asyncio.sleep(.03)

    async def test_start_is_idempotent_and_each_service_can_restart_independently(self):
        await asyncio.gather(self.manager.action('asr','start'),self.manager.action('asr','start'))
        first=await self.running('asr')
        await self.manager.action('llm','start');llm=await self.running('llm')
        await self.manager.action('asr','restart');second=await self.running('asr')
        self.assertNotEqual(first['pid'],second['pid'])
        self.assertEqual((await self.manager.status('llm'))['pid'],llm['pid'])
        await self.manager.action('asr','stop')
        self.assertEqual((await self.manager.status('asr'))['state'],'stopped')

    async def test_external_service_is_never_controlled_even_on_localhost(self):
        self.cfg['asr'].update(provider='http',url='http://127.0.0.1:8000/v1')
        value=await self.manager.status('asr')
        self.assertFalse(value['managed']);self.assertEqual(value['state'],'external')
        for action in ['start','stop','restart']:
            with self.assertRaises(ServiceError):await self.manager.action('asr',action)

    async def test_occupied_port_is_not_killed_or_adopted(self):
        import aiohttp.web
        app=aiohttp.web.Application();app.router.add_get('/health',lambda r:aiohttp.web.json_response({'ready':True}))
        runner=aiohttp.web.AppRunner(app);await runner.setup()
        await aiohttp.web.TCPSite(runner,'127.0.0.1',self.manager.ports['asr']).start()
        try:
            with self.assertRaises(ServiceError):await self.manager.action('asr','start')
            self.assertEqual((await self.manager.status('asr'))['state'],'blocked')
        finally:await runner.cleanup()

    async def test_worker_is_adopted_after_backend_restart_only_with_matching_identity(self):
        await self.manager.action('asr','start');first=await self.running('asr')
        other=ServiceManager(Path(self.tmp.name),lambda:self.cfg,ports=self.manager.ports,worker_module='tests.fake_model_worker')
        try:
            self.assertEqual((await other.status('asr'))['pid'],first['pid'])
            await other.action('asr','stop')
        finally:await other.shutdown()

    async def test_invalid_kind_and_action_are_rejected(self):
        for kind,action in [('broker','stop'),('asr','shell')]:
            with self.assertRaises(ServiceError):await self.manager.action(kind,action)

    async def test_model_change_requires_restart_and_crash_is_visible(self):
        await self.manager.action('llm','start')
        await self.running('llm')
        self.cfg['llm']['model']='replacement'
        self.assertTrue((await self.manager.status('llm'))['needs_restart'])
        with self.assertRaises(ServiceError): self.manager.headers('llm')
        process=self.manager.children['llm']
        process.kill()
        await process.wait()
        self.assertEqual((await self.manager.status('llm'))['state'],'failed')
        await self.manager.action('llm','start')
        self.assertEqual((await self.running('llm'))['model'],'replacement')
