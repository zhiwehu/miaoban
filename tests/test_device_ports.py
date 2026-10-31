import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import device_ports as ports

class PortTests(unittest.TestCase):
    def test_identifies_head_when_usb_numbers_are_swapped(self):
        data = json.dumps([['/dev/cu.usbmodem101', '10:51:DB:BA:A1:FC'],
                           ['/dev/cu.usbmodem1101', ports.HEAD_MAC.upper()]])
        with patch.object(ports.subprocess, 'check_output', return_value=data):
            self.assertEqual(ports.find_head_port(), '/dev/cu.usbmodem1101')
    def test_missing_head_refuses_any_flash(self):
        with patch.object(ports.subprocess, 'check_output', return_value='[]'):
            with self.assertRaises(RuntimeError): ports.find_head_port()
    def test_wrong_identity_refuses_write(self):
        with patch.object(ports, 'find_head_port', return_value='/dev/mock'), patch.object(ports.subprocess, 'run', return_value=SimpleNamespace(stdout='MAC: 10:51:DB:BA:A1:FC')) as run:
            with self.assertRaises(RuntimeError): ports.head_esptool(['write_flash', '0x9000', 'nvs.bin'])
            self.assertEqual(run.call_count, 1)
