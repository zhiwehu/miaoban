"""Identify this robot before any flash operation; never trust USB numbering."""
import json
import os
import subprocess

ESPY = os.path.expanduser('~/.espressif/python_env/idf5.3_py3.9_env/bin/python3')
HEAD_MAC = '90:e5:b1:ae:80:a0'

def find_head_port():
    code = 'import serial.tools.list_ports,json;print(json.dumps([(p.device,p.serial_number) for p in serial.tools.list_ports.comports()]))'
    ports = json.loads(subprocess.check_output([ESPY, '-c', code], text=True))
    matches = [port for port, serial in ports if (serial or '').lower() == HEAD_MAC]
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one matching EchoEar head; refusing flash')
    return matches[0]

def head_esptool(arguments, **kwargs):
    port = find_head_port()
    verify = subprocess.run([ESPY, '-m', 'esptool', '--chip', 'esp32s3', '--port', port, '--after', 'no_reset', 'chip_id'], capture_output=True, text=True, check=True)
    if HEAD_MAC not in verify.stdout.lower():
        raise RuntimeError('Head MAC verification failed; refusing flash')
    return subprocess.run([ESPY, '-m', 'esptool', '--chip', 'esp32s3', '--port', port, '--before', 'no_reset'] + list(arguments), **kwargs)
