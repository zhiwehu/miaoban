#!/usr/bin/env python3
"""Generate private Mosquitto ACLs/password hashes and per-device provisioning secrets."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess


def create_config(destination, host, devices, broker_port=1883, udp_port=8884):
    address=ipaddress.IPv4Address(host)
    if not address.is_private or address.is_unspecified or address.is_multicast:
        raise ValueError('Use a specific private IPv4 interface')
    if not 1 <= len(devices) <= 8 or len(set(devices)) != len(devices):
        raise ValueError('Use 1 to 8 unique device IDs')
    if any(not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',d) for d in devices):
        raise ValueError('Invalid device ID')
    if not 1 <= broker_port <= 65535 or not 0 <= udp_port <= 65535:
        raise ValueError('Invalid port')
    passwd=shutil.which('mosquitto_passwd')
    if not passwd:raise RuntimeError('Install Mosquitto first')
    destination=Path(destination).resolve()
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    cfg=dict(broker_host=host,broker_port=broker_port,udp_host=host,udp_port=udp_port,
        username='voicehub_backend',password=secrets.token_urlsafe(32),devices={})
    for device in devices:
        cfg['devices'][device]=dict(username='d_'+hashlib.sha256(device.encode()).hexdigest()[:16],
            password=secrets.token_urlsafe(32),provision_token=secrets.token_urlsafe(32),enabled=True)
    def private(name,content):
        path=destination/name
        path.touch(mode=0o600,exist_ok=False)
        path.write_text(content)
        return path
    credentials=[(cfg['username'],cfg['password'])]+[(d['username'],d['password']) for d in cfg['devices'].values()]
    password_file=private('passwords', ''.join(u+':'+p+'\n' for u,p in credentials))
    # -U replaces the private plaintext file with hashes; no passwords in process arguments.
    result=subprocess.run([passwd,'-U',str(password_file)],capture_output=True)
    if result.returncode:
        password_file.unlink(missing_ok=True)
        raise RuntimeError('MQTT password hashing failed')
    acl='user '+cfg['username']+'\ntopic readwrite voicehub/#\n'
    for device,item in cfg['devices'].items():
        acl+='\nuser '+item['username']+'\ntopic write voicehub/'+device+'/up\ntopic read voicehub/'+device+'/down\n'
    acl_file=private('acl',acl)
    private('mosquitto.conf',f'''listener {broker_port} {host}
allow_anonymous false
password_file {password_file}
acl_file {acl_file}
allow_zero_length_clientid false
max_packet_size 32768
max_queued_messages 128
max_inflight_messages 20
persistence false
connection_messages true
log_type error
log_type warning
''')
    private('config.json',json.dumps(cfg,indent=2)+'\n')
    return cfg


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True)
    parser.add_argument('--device',action='append',required=True)
    parser.add_argument('--out',type=Path,default=Path(__file__).resolve().parents[1]/'data/mqtt')
    parser.add_argument('--broker-port',type=int,default=1883)
    parser.add_argument('--udp-port',type=int,default=8884)
    args=parser.parse_args()
    create_config(args.out,args.host,args.device,args.broker_port,args.udp_port)
    print('Created private MQTT configuration. Keep config.json and provisioning URLs out of Git/logs.')


if __name__=='__main__':main()
