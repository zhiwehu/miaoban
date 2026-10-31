import unittest
from unittest.mock import patch
import xiaozhi_server as server

class BackendAddressTests(unittest.TestCase):
    def setUp(self):
        self.old = server.STATE.copy()
        server.STATE.update(tunnel_mode=False, advertise_host=None, port=8099,
                            ws_path='/xiaozhi/v1/')
    def tearDown(self):
        server.STATE.clear()
        server.STATE.update(self.old)
    def test_explicit_lan_host_keeps_ws_and_port(self):
        server.STATE['advertise_host'] = '192.168.1.20'
        with patch.object(server, 'lan_ip', return_value='10.0.0.3'):
            self.assertEqual(server.ws_url(), 'ws://192.168.1.20:8099/xiaozhi/v1/')
    def test_auto_host_tracks_current_lan(self):
        with patch.object(server, 'lan_ip', return_value='192.168.1.30'):
            self.assertEqual(server.ws_url(), 'ws://192.168.1.30:8099/xiaozhi/v1/')
    def test_tunnel_uses_tls_without_local_port(self):
        server.STATE.update(tunnel_mode=True, public_host='robot.example.test')
        self.assertEqual(server.ws_url(), 'wss://robot.example.test/xiaozhi/v1/')

    def test_ota_never_persists_a_loopback_address_to_robot(self):
        with patch.object(server, 'lan_ip', return_value='127.0.0.1'):
            with self.assertRaises(server.web.HTTPServiceUnavailable):
                server.ota_response()

    def test_status_reports_current_ota_host_and_port(self):
        server.STATE['public_host'] = '10.0.0.99'
        server.STATE['ota_path'] = '/xiaozhi/ota/'
        handler = object.__new__(server.ControlHandler)
        handler.path = '/api/status'
        with patch.object(server, 'lan_ip', return_value='172.20.10.3'), patch.object(handler, '_json') as response:
            handler.do_GET()
        self.assertEqual(response.call_args.args[0]['ota_url'], 'http://172.20.10.3:8099/xiaozhi/ota/')

if __name__ == '__main__':
    unittest.main()
