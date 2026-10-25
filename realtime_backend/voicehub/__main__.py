import argparse
import json
from pathlib import Path
from aiohttp import web
from .app import create_app, HUB


def main():
    parser = argparse.ArgumentParser(description='VoiceHub independent realtime voice server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--device-host', help='Specific private IP for an authenticated device-only listener')
    parser.add_argument('--device-port', type=int, default=8766)
    parser.add_argument('--mqtt-config', help='Private per-device MQTT/UDP config; disabled by default')
    parser.add_argument('--data', default=str(Path(__file__).resolve().parents[1]/'data'))
    parser.add_argument('--print-device-token', action='store_true')
    parser.add_argument('--print-admin-token', action='store_true')
    args = parser.parse_args()
    app = create_app(args.data, device_host=args.device_host, device_port=args.device_port, mqtt_config=args.mqtt_config)
    if args.print_device_token or args.print_admin_token:
        print(app[HUB].auth['device_token' if args.print_device_token else 'admin_token'])
        app[HUB].store.close()
        return
    print(f'VoiceHub console: http://127.0.0.1:{args.port} (bind {args.host})')
    if args.device_host:
        print(f'Device ingress: {args.device_host}:{args.device_port} (token required; no management routes)')
    print('Model loading is lazy. Settings apply to new connections. Ctrl+C stops this server.')
    web.run_app(app, host=args.host, port=args.port, access_log=None, shutdown_timeout=5)


if __name__ == '__main__':
    main()
