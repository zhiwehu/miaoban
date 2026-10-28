import asyncio
import tempfile
import time
import unittest
from aiohttp.test_utils import TestClient, TestServer
from voicehub.app import create_app
from tests.test_sessions import Pipeline


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_tool_preamble_sends_opus_tail_before_tool_starts(self):
        from voicehub.app import HUB
        from voicehub.tools import ToolNotice, ToolStatus
        ws = await self.client.ws_connect('/xiaozhi/v1/')
        await ws.send_json({'type':'hello','version':1,'device_id':'mcp-legacy',
                           'audio_params':{'format':'opus','sample_rate':16000}})
        hello = await ws.receive_json()
        session = self.app[HUB].sessions[hello['session_id']]
        async def chat(history):
            yield ToolNotice('news', '我查一下。')
            yield ToolStatus('news', 'started')
            yield '查询结果。'
        session.pipeline.chat = chat
        await ws.send_json({'type':'input.text','text':'查询新闻'})
        async with asyncio.timeout(2):
            while True:
                message = await ws.receive()
                if message.type.name == 'TEXT' and message.json().get('state') == 'stop':
                    break
        events = self.app[HUB].store.events(session.id)
        first = next(e for e in events if e['type']=='transport.first_audio')
        tool = next(e for e in events if e['type']=='tool.started')
        self.assertLess(first['created'], tool['created'])
        await ws.close()

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, pipeline_factory=lambda c,h,l: Pipeline())
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def connect(self, device='web'):
        ws = await self.client.ws_connect('/ws')
        await ws.send_json({'type':'hello', 'version':1, 'device_id':device,
                            'audio':{'format':'pcm_s16le','sample_rate':16000},
                            'capabilities':{'echo_cancellation':True}})
        hello = await ws.receive_json()
        self.assertEqual(hello['type'], 'hello')
        return ws, hello

    async def test_legacy_playback_start_has_time_to_prepare_before_binary_audio(self):
        from aiohttp import WSMsgType
        from voicehub.app import HUB
        ws = await self.client.ws_connect('/xiaozhi/v1/')
        await ws.send_json({'type':'hello', 'version':1, 'device_id':'legacy-test',
                            'audio_params':{'format':'opus', 'sample_rate':16000}})
        hello = await ws.receive_json()
        self.app[HUB].sessions[hello['session_id']].pipeline.release.set()
        await ws.send_json({'type':'input.text', 'text':'测试播放准备'})
        started = None
        async with asyncio.timeout(2):
            while True:
                message = await ws.receive()
                if message.type == WSMsgType.TEXT:
                    event = message.json()
                    if event.get('type') == 'tts' and event.get('state') == 'start':
                        started = time.monotonic()
                elif message.type == WSMsgType.BINARY:
                    self.assertIsNotNone(started)
                    self.assertGreaterEqual(time.monotonic()-started, .08)
                    self.assertTrue(message.data)
                    break
        await ws.close()

    async def test_legacy_idle_closes_channel_so_device_can_restore_wake_word(self):
        from aiohttp import WSMsgType
        from voicehub.app import HUB
        ws = await self.client.ws_connect('/xiaozhi/v1/')
        await ws.send_json({'type':'hello', 'version':1, 'device_id':'legacy-idle',
                            'audio_params':{'format':'opus', 'sample_rate':16000}})
        hello = await ws.receive_json()
        session = self.app[HUB].sessions[hello['session_id']]
        await session.activate()
        session.last_activity = time.monotonic() - session.idle_seconds - 1
        await asyncio.wait_for(session.idle(), 1)
        message = await ws.receive(timeout=.2)
        self.assertEqual(message.type, WSMsgType.CLOSE)
        await ws.close()

    async def test_native_idle_delivers_standby_without_closing_connection(self):
        from voicehub.app import HUB
        ws, hello = await self.connect('native-idle')
        session = self.app[HUB].sessions[hello['session_id']]
        await session.activate()
        session.last_activity = time.monotonic() - session.idle_seconds - 1
        await session.idle()
        async with asyncio.timeout(1):
            while (event := await ws.receive_json()).get('state') != 'standby':
                pass
        self.assertEqual(event['type'], 'session.state')
        self.assertFalse(ws.closed)
        await ws.close()

    async def ready_client(self):
        from voicehub.app import HUB
        ws = await self.client.ws_connect('/xiaozhi/v1/')
        await ws.send_json({'type':'hello', 'version':1, 'device_id':'ready-test',
                           'features':{'playback_ready':True},
                           'audio_params':{'format':'opus','sample_rate':16000}})
        hello = await ws.receive_json()
        self.app[HUB].sessions[hello['session_id']].pipeline.release.set()
        await ws.send_json({'type':'input.text','text':'测试准备确认'})
        async with asyncio.timeout(2):
            while True:
                event = await ws.receive_json()
                if event.get('type') == 'tts' and event.get('state') == 'start':
                    return ws, event

    async def test_ready_client_gets_no_audio_until_matching_prepared_ack(self):
        from aiohttp import WSMsgType
        ws, start = await self.ready_client()
        self.assertTrue(start.get('turn_id'))
        await ws.send_json({'type':'tts','state':'ready','turn_id':'stale',
                            'device_state':6,'rx_audio_packets':0,'dropped_audio_packets':0,
                            'decoded_audio_packets':0,'played_audio_packets':0,'output_enabled':True})
        with self.assertRaises(TimeoutError):
            await ws.receive(timeout=.15)
        await ws.send_json({'type':'tts','state':'ready','turn_id':start['turn_id'],
                            'device_state':6,'rx_audio_packets':3,'dropped_audio_packets':1,
                            'decoded_audio_packets':0,'played_audio_packets':0,'output_enabled':True})
        message = await ws.receive(timeout=1)
        self.assertEqual(message.type, WSMsgType.BINARY)
        await ws.close()

    async def test_first_audio_metric_records_tail_frame_after_ready_once_per_turn(self):
        from aiohttp import WSMsgType
        from voicehub.app import HUB
        ws,start=await self.ready_client()
        await ws.send_json({'type':'tts','state':'ready','turn_id':start['turn_id'],
            'device_state':6,'rx_audio_packets':0,'dropped_audio_packets':0,
            'decoded_audio_packets':0,'played_audio_packets':0,'output_enabled':True})
        async with asyncio.timeout(2):
            while True:
                message=await ws.receive()
                if message.type==WSMsgType.TEXT and message.json().get('state')=='stop':break
        events=self.app[HUB].store.events(start['session_id'])
        first=[e for e in events if e['type']=='transport.first_audio']
        ready=[e for e in events if e['type']=='device.playback_state']
        self.assertEqual(len(first),1)
        self.assertEqual(first[0]['turn_id'],start['turn_id'])
        self.assertGreaterEqual(first[0]['created'],ready[0]['created'])
        self.assertGreater(first[0]['payload']['packet_bytes'],0)
        await ws.close()

    async def test_missing_ready_ack_fails_without_sending_audio(self):
        from unittest.mock import patch
        from aiohttp import WSMsgType
        with patch('voicehub.app.PLAYBACK_READY_TIMEOUT', .15):
            ws, start = await self.ready_client()
            async with asyncio.timeout(1):
                while True:
                    msg = await ws.receive()
                    self.assertNotEqual(msg.type, WSMsgType.BINARY)
                    if msg.type == WSMsgType.TEXT and msg.json().get('type') == 'error':
                        self.assertEqual(msg.json()['code'], 'playback_not_ready')
                        self.assertIn('设备未准备好播放', msg.json()['message'])
                        break
            await ws.close()

    async def test_ready_with_output_still_disabled_does_not_send_audio(self):
        from aiohttp import WSMsgType
        ws, start = await self.ready_client()
        await ws.send_json({'type':'tts','state':'ready','turn_id':start['turn_id'],
                            'device_state':6,'rx_audio_packets':0,'dropped_audio_packets':0,
                            'decoded_audio_packets':0,'played_audio_packets':0,'output_enabled':False})
        async with asyncio.timeout(1):
            while True:
                msg = await ws.receive()
                self.assertNotEqual(msg.type, WSMsgType.BINARY)
                if msg.type == WSMsgType.TEXT and msg.json().get('type') == 'error':
                    self.assertEqual(msg.json()['code'], 'playback_not_ready')
                    break
        await ws.close()

    async def test_interrupt_cancels_ready_wait_and_late_ack_cannot_emit_audio(self):
        from voicehub.app import HUB
        ws, start = await self.ready_client()
        await ws.send_json({'type':'abort'})
        async with asyncio.timeout(1):
            while (event := await ws.receive_json()).get('state') != 'stop':
                pass
        await ws.send_json({'type':'tts','state':'ready','turn_id':start['turn_id'],
                            'device_state':6,'rx_audio_packets':0,'dropped_audio_packets':0,
                            'decoded_audio_packets':0,'played_audio_packets':0,'output_enabled':True})
        with self.assertRaises(TimeoutError):
            await ws.receive(timeout=.1)
        self.assertEqual(self.app[HUB].store.messages(start['session_id'])[-1]['status'], 'interrupted')
        await ws.close()

    async def test_device_counters_are_recorded_even_after_turn_completes(self):
        from voicehub.app import HUB
        from aiohttp import WSMsgType
        ws, start = await self.ready_client()
        fields = dict(device_state=6,rx_audio_packets=0,dropped_audio_packets=0,
                      decoded_audio_packets=0,played_audio_packets=0,output_enabled=True)
        await ws.send_json(dict(type='tts',state='ready',turn_id=start['turn_id'],**fields))
        async with asyncio.timeout(1):
            while True:
                msg = await ws.receive()
                if msg.type == WSMsgType.TEXT and msg.json().get('state') == 'stop':break
        await asyncio.sleep(.03)
        await ws.send_json(dict(type='tts',state='stopped',turn_id=start['turn_id'],**fields))
        for _ in range(30):
            rows=self.app[HUB].store.events(start['session_id'])
            if any(r['type']=='device.playback_state' and r['payload']['state']=='stopped' for r in rows):break
            await asyncio.sleep(.01)
        else:self.fail('post-turn device counters were discarded')
        await ws.close()

    async def test_real_websockets_do_not_replace_each_other(self):
        one, h1 = await self.connect('one')
        two, h2 = await self.connect('two')
        self.assertNotEqual(h1['session_id'], h2['session_id'])
        await one.send_json({'type':'input.text', 'text':'甲'})
        await two.send_json({'type':'input.text', 'text':'乙'})
        await asyncio.sleep(.05)
        response = await self.client.get('/api/sessions')
        self.assertEqual(len(await response.json()), 2)
        for hello, expected in ((h1,'甲'), (h2,'乙')):
            r = await self.client.get('/api/sessions/'+hello['session_id']+'/messages')
            self.assertEqual((await r.json())[0]['content'], expected)
        await one.close()
        await two.close()

    async def test_resume_requires_secret_and_disconnect_preserves_messages(self):
        ws, h = await self.connect()
        await ws.send_json({'type':'input.text', 'text':'保存记录'})
        await asyncio.sleep(.03)
        await ws.close()
        await asyncio.sleep(.03)
        bad = await self.client.ws_connect('/ws')
        await bad.send_json({'type':'hello','version':1,'device_id':'web',
                             'session_id':h['session_id'], 'resume_token':'wrong'})
        error = await bad.receive_json()
        self.assertEqual(error['type'], 'error')
        await bad.close()
        good = await self.client.ws_connect('/ws')
        await good.send_json({'type':'hello','version':1,'device_id':'web',
                              'session_id':h['session_id'], 'resume_token':h['resume_token']})
        reply = await good.receive_json()
        self.assertEqual(reply.get('session_id'), h['session_id'], reply.get('code'))
        await good.close()

    async def test_cross_origin_and_invalid_config_rejected(self):
        r = await self.client.put('/api/config', json={}, headers={'Origin':'https://untrusted.example'})
        self.assertEqual(r.status, 403)
        r = await self.client.put('/api/config', json={})
        self.assertEqual(r.status, 400)
        ws = await self.client.ws_connect('/ws')
        await ws.send_json({'type':'hello','version':9,'device_id':'web'})
        self.assertEqual((await ws.receive_json())['type'], 'error')
        await ws.close()

    async def test_valid_config_save_is_persisted_and_applies_to_new_connections(self):
        r = await self.client.get('/api/config')
        c = (await r.json())['config']
        c['system_prompt'] = '用于测试的新提示词'
        r = await self.client.put('/api/config', json=c)
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())['config']['system_prompt'], c['system_prompt'])
        r = await self.client.get('/api/config')
        self.assertEqual((await r.json())['config']['system_prompt'], c['system_prompt'])

    async def test_cross_site_websocket_and_rebinding_host_cannot_use_local_admin(self):
        from aiohttp import WSServerHandshakeError
        with self.assertRaises(WSServerHandshakeError):
            await self.client.ws_connect('/ws', headers={'Origin':'https://evil.example'})
        response=await self.client.get('/api/config',headers={'Host':'evil.example'})
        self.assertEqual(response.status,401)

    async def test_device_auth_is_required_outside_trusted_local_console(self):
        from unittest.mock import patch
        from voicehub.app import HUB
        with patch('voicehub.app.local_admin',return_value=False):
            bad=await self.client.ws_connect('/ws')
            await bad.send_json({'type':'hello','version':1,'device_id':'phone'})
            self.assertEqual((await bad.receive_json())['code'],'unauthorized')
            await bad.close()
            good=await self.client.ws_connect('/ws')
            await good.send_json({'type':'hello','version':1,'device_id':'phone',
                                  'token':self.app[HUB].auth['device_token']})
            self.assertEqual((await good.receive_json())['type'],'hello')
            await good.close()

    async def test_twenty_disconnect_resume_cycles_leave_no_active_sessions(self):
        from voicehub.app import HUB
        identity={}
        for _ in range(20):
            ws=await self.client.ws_connect('/ws')
            await ws.send_json(dict(type='hello',version=1,device_id='reconnect',**identity))
            hello=await ws.receive_json()
            self.assertEqual(hello['type'],'hello')
            identity={k:hello[k] for k in ('session_id','resume_token')}
            await ws.close()
            for _ in range(100):
                if not self.app[HUB].sessions and not self.app[HUB].cleanups:
                    break
                await asyncio.sleep(.005)
            self.assertFalse(self.app[HUB].sessions)
            self.assertFalse(self.app[HUB].cleanups)
