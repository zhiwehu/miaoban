import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from voicehub.mqtt_service import MqttService, load_config


class MqttSecurityTests(unittest.TestCase):
    def config(self):
        return {'broker_host':'127.0.0.1','broker_port':1883,'udp_host':'127.0.0.1','udp_port':0,
            'username':'backend','password':'test-backend-password',
            'devices':{'cat':{'username':'cat','password':'test-cat-password',
                             'provision_token':'a'*43,'enabled':True},
                       'other':{'username':'other','password':'test-other-password',
                                'provision_token':'b'*43,'enabled':True}}}

    def test_credentials_require_unique_provision_token_and_matching_device(self):
        service=MqttService(SimpleNamespace(),self.config())
        service.ready.set()
        for device,token in [('cat',''),('cat','wrong'),('cat','b'*43),('unknown','a'*43)]:
            self.assertIsNone(service.device_config(device,token))
        value=service.device_config('cat','a'*43)
        self.assertEqual(value['username'],'cat')
        self.assertEqual(value['password'],'test-cat-password')
        service.ready.clear()
        self.assertIsNone(service.device_config('cat','a'*43))

    def test_credentials_file_rejects_public_binding_weak_or_duplicate_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            for mutation in [lambda c:c.update(udp_host='0.0.0.0'),
                lambda c:c['devices']['cat'].update(password='short'),
                lambda c:c['devices']['other'].update(provision_token='a'*43)]:
                cfg=self.config();mutation(cfg);path.write_text(json.dumps(cfg));path.chmod(0o600)
                with self.assertRaises(ValueError):load_config(path)
            path.write_text(json.dumps(self.config()));path.chmod(0o644)
            with self.assertRaises(ValueError):load_config(path)
            path.chmod(0o600)
            self.assertEqual(load_config(path)['devices']['cat']['username'],'cat')
