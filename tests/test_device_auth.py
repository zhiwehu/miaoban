import unittest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import xiaozhi_server as server

class DeviceAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_tunnel_rejects_missing_token_before_upgrading(self):
        old = server.STATE.copy()
        try:
            server.STATE.update(tunnel_mode=True, token='test-secret')
            request = make_mocked_request('GET', '/xiaozhi/v1/')
            with self.assertRaises(web.HTTPUnauthorized):
                await server.device_handler(request)
        finally:
            server.STATE.clear(); server.STATE.update(old)

    async def test_public_ota_path_does_not_disclose_tunnel_token(self):
        old = server.STATE.copy()
        try:
            server.STATE.update(tunnel_mode=True, token='test-secret', ota_path='/xiaozhi/ota/')
            request = make_mocked_request('POST', '/xiaozhi/ota/')
            with self.assertRaises(web.HTTPNotFound):
                await server.ota_handler(request)
        finally:
            server.STATE.clear(); server.STATE.update(old)

    async def test_unknown_path_cannot_disclose_private_bootstrap(self):
        old = server.STATE.copy()
        try:
            server.STATE.update(tunnel_mode=True, token='test-secret', ota_path='/xiaozhi/ota/' + 'x' * 43 + '/')
            request = make_mocked_request('POST', '/xiaozhi/ota/')
            with self.assertRaises(web.HTTPNotFound):
                await server.ota_handler(request)
        finally:
            server.STATE.clear(); server.STATE.update(old)
