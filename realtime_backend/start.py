"""Start / restart / stop the VoiceHub backend as a detached background process.

用法（脚本 wrapper 为同目录 start.sh）:
    ./start.sh              后台启动；已在运行则直接复用并打印地址
    ./start.sh --restart    先停掉本目录的后台进程再启动
    ./start.sh --stop       停止后台进程
    ./start.sh --status     查看是否在运行及监听地址
    ./start.sh --foreground 前台运行，Ctrl+C 停止（等价 ./run.sh）

控制台固定为 127.0.0.1:8765；设备通道由 data/connections.json 里的
device_enabled / device_host / device_port 决定（没有该文件则不开放设备通道）。
PID 写入 data/server.pid，日志追加到 data/server.log。
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
DATA = ROOT/'data'
PIDFILE = DATA/'server.pid'
LOGFILE = DATA/'server.log'
PREFS = DATA/'connections.json'
CONSOLE_PORT = 8765
LSOF = '/usr/sbin/lsof'


def python_bin():
    """与 run.sh 相同的优先级：VOICEHUB_PYTHON > .venv > 本机已有模型环境 > python3。"""
    env = os.environ.get('VOICEHUB_PYTHON')
    if env:
        return env
    for candidate in (ROOT/'.venv/bin/python', Path('/Users/huzhiwei/ai/mac_voice_agent/.venv/bin/python')):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable or 'python3'


def listening_pid(port):
    """返回监听该端口的 PID；端口空闲返回 None。用 lsof 而不是 ps。"""
    try:
        out = subprocess.run([LSOF, '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t'],
                             capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return int(out[0]) if out else None


def process_cwd(pid):
    try:
        out = subprocess.run([LSOF, '-a', '-p', str(pid), '-d', 'cwd', '-Fn'],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith('n'):
            return line[1:]
    return None


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def our_pid():
    """data/server.pid 指向的本目录进程；不一致时返回 None。"""
    if not PIDFILE.exists():
        return None
    try:
        pid = int(PIDFILE.read_text().strip())
    except ValueError:
        return None
    if not alive(pid) or process_cwd(pid) != str(ROOT):
        return None
    return pid


def device_config():
    if not PREFS.exists():
        return None
    try:
        saved = json.loads(PREFS.read_text())
    except (OSError, ValueError):
        return None
    if saved.get('device_enabled') is not True or not saved.get('device_host'):
        return None
    return saved['device_host'], saved.get('device_port', 8766)


def lan_ip():
    """当前 Wi-Fi/Ethernet 地址，用来判断 connections.json 里的设备地址是否过期。"""
    for iface in ('en0', 'en1', 'en2'):
        try:
            out = subprocess.run(['/usr/sbin/ipconfig', 'getifaddr', iface],
                                 capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        value = out.stdout.strip()
        if out.returncode == 0 and value.count('.') == 3:
            return value
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(('192.168.255.255', 1))
        return probe.getsockname()[0]
    except OSError:
        return None


def wait_ready(timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f'http://127.0.0.1:{CONSOLE_PORT}/api/status', timeout=2) as response:
                return json.load(response)
        except (URLError, OSError, ValueError):
            time.sleep(0.3)
    return None


def describe(pid, status):
    print(f'声桥 VoiceHub 运行中 (pid {pid})')
    print(f'  控制台    http://127.0.0.1:{CONSOLE_PORT}/')
    device = device_config()
    if device:
        host, port = device
        print(f'  设备通道  ws://{host}:{port}/xiaozhi/v1/')
        current = lan_ip()
        if current and current != host:
            print(f'  ⚠️  connections.json 里的设备地址是 {host}，本机当前私网地址是 {current}；'
                  f'地址变了就在控制台「设备接入」里改成 {current} 再重启。')
    else:
        print('  设备通道  未开放（控制台「设备接入」里可开启）')
    if isinstance(status, dict) and 'online' in status:
        print(f'  在线连接  {status["online"]}')
    print(f'  日志      {LOGFILE}')
    print(f'  停止      ./start.sh --stop')


def stop(quiet=False):
    pid = our_pid()
    if pid is None:
        if not quiet:
            print('没有本目录启动的 VoiceHub 后台进程。')
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if not alive(pid):
            break
        time.sleep(0.1)
    else:
        print(f'进程 {pid} 10 秒内没有退出，请手动检查（kill -9 {pid}）。', file=sys.stderr)
        return 1
    if PIDFILE.exists():
        PIDFILE.unlink()
    if not quiet:
        print(f'已停止 VoiceHub (pid {pid})。')
    return 0


def start(args):
    owner = listening_pid(CONSOLE_PORT)
    if owner is not None:
        if process_cwd(owner) == str(ROOT):
            if PIDFILE.exists() and PIDFILE.read_text().strip() != str(owner):
                PIDFILE.write_text(str(owner))
            describe(owner, wait_ready(timeout=5))
            return 0
        print(f'端口 {CONSOLE_PORT} 已被其它进程占用 (pid {owner}, cwd {process_cwd(owner)})，'
              f'先在控制台或活动监视器里处理它。', file=sys.stderr)
        return 1

    interpreter = python_bin()
    usable = Path(interpreter).is_file() if os.sep in interpreter else shutil.which(interpreter)
    if not usable:
        print(f'找不到可用的 Python：{interpreter}', file=sys.stderr)
        return 1

    DATA.mkdir(exist_ok=True)
    command = [python_bin(), '-u', '-m', 'voicehub', '--host', '127.0.0.1', '--port', str(CONSOLE_PORT)]
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    with LOGFILE.open('ab') as log:
        log.write(f'\n--- start {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n'.encode())
        log.flush()
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    PIDFILE.write_text(str(process.pid))

    status = wait_ready()
    if status is None:
        print(f'启动后 25 秒内没有就绪，看日志：{LOGFILE}', file=sys.stderr)
        print(subprocess.run(['/usr/bin/tail', '-n', '30', str(LOGFILE)],
                             capture_output=True, text=True).stdout, file=sys.stderr)
        return 1
    describe(process.pid, status)
    return 0


def main():
    parser = argparse.ArgumentParser(description='声桥 VoiceHub 后台启动器')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--restart', action='store_true', help='先停后启')
    group.add_argument('--stop', action='store_true', help='停止后台进程')
    group.add_argument('--status', action='store_true', help='查看运行状态')
    group.add_argument('--foreground', action='store_true', help='前台运行（Ctrl+C 停止）')
    args = parser.parse_args()

    if args.foreground:
        os.chdir(ROOT)
        os.execv(python_bin(), [python_bin(), '-u', '-m', 'voicehub',
                                '--host', '127.0.0.1', '--port', str(CONSOLE_PORT)])
    if args.stop:
        return stop()
    if args.status:
        pid = our_pid() or listening_pid(CONSOLE_PORT)
        if pid is None:
            print('未运行。启动：./start.sh')
            return 1
        describe(pid, wait_ready(timeout=5))
        return 0
    if args.restart:
        stop(quiet=True)
        return start(args)
    return start(args)


if __name__ == '__main__':
    sys.exit(main())
