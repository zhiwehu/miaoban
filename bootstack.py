#!/usr/bin/env python3
"""
一键把喵伴接上本地语音大脑
==========================

做的事（每步都可重复执行）：

  1. 起公网隧道（pinggy），拿到本次的域名
  2. 起本地小智后端（跑在 Miya 的 venv 里，带 MLX ASR/LLM/TTS）
  3. 用**原始 NVS 备份**作基底，注入新的 `wifi/ota_url` 指向本次域名
  4. 写回猫头 NVS 并重启
  5. 等猫头连上来

关键点：
  * 进程必须 `start_new_session=True` 脱离当前 shell 会话，否则命令一结束就被回收
  * 每次都从 `backup/nvs_head_s3.bin`（原厂备份）重新生成，不会累积污染
  * 猫头每次开机都会从 ota_url 拉配置并写回 NVS，所以域名变了只要重跑本脚本

用法：
    ~/ai/mac_voice_agent/.venv/bin/python bootstack.py
    ~/ai/mac_voice_agent/.venv/bin/python bootstack.py --no-voice
"""

from __future__ import annotations

import argparse
import secrets
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP = os.path.join(HERE, "backup")
VENV_PY = os.path.expanduser("~/ai/mac_voice_agent/.venv/bin/python")
ESPY = os.path.expanduser("~/.espressif/python_env/idf5.3_py3.9_env/bin/python3")
from device_ports import head_esptool
API = "http://127.0.0.1:8098"
OTA_PATH = "/xiaozhi/ota/" + secrets.token_urlsafe(32) + "/"

LOG_DIR = "/tmp"


def log(msg):
    print(f"[bootstack] {msg}", flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------- 1. 隧道

def kill_stale():
    for pat in ("xiaozhi_server.py", "pinggy.io"):
        subprocess.run(["pkill", "-f", pat], capture_output=True)
    time.sleep(1)


def start_tunnel():
    """起 pinggy 隧道，返回公网域名。"""
    logf = open(os.path.join(LOG_DIR, "tunnel.log"), "w")
    p = subprocess.Popen(
        ["ssh", "-p", "443",
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=no",
         "-R0:127.0.0.1:8099", "a.pinggy.io"],
        stdout=logf, stderr=subprocess.STDOUT,
        start_new_session=True)      # 关键：脱离会话
    log(f"隧道进程 pid={p.pid}，等域名…")
    host = None
    for _ in range(40):
        time.sleep(1)
        try:
            txt = open(os.path.join(LOG_DIR, "tunnel.log"), errors="replace").read()
        except OSError:
            continue
        txt = re.sub(r"\x1b\[[0-9;]*m", "", txt)
        # pinggy 的横幅里还有 console.pinggy.io / dashboard.pinggy.io，
        # 必须只匹配真正的隧道域名，否则会把 dashboard.pinggy.io 当成域名
        m = (re.search(r"https://([a-z0-9][a-z0-9-]*\.free\.pinggy\.net)", txt)
             or re.search(r"https://([a-z0-9][a-z0-9-]*\.run\.pinggy-free\.link)", txt))
        if m:
            host = m.group(1).strip()
            break
    if not host:
        raise RuntimeError("拿不到隧道域名，看 /tmp/tunnel.log")
    log(f"公网域名: {host}")
    return host, p.pid


# ---------------------------------------------------------------- 2. 后端

def start_server(host, voice=True):
    logf = open(os.path.join(LOG_DIR, "xz_server.log"), "w")
    cmd = [VENV_PY, os.path.join(HERE, "xiaozhi_server.py"),
           "--public-host", host, "--tunnel", "--ota-path", OTA_PATH]
    if not voice:
        cmd.append("--no-voice")
    p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                         cwd=HERE, start_new_session=True)
    log(f"后端进程 pid={p.pid}，等端口…")
    for _ in range(20):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(API + "/api/status", timeout=2) as r:
                json.load(r)
            log("后端已就绪")
            return p.pid
        except Exception:
            pass
    raise RuntimeError("后端没起来，看 /tmp/xz_server.log")


# ---------------------------------------------------------------- 3/4. NVS

def patch_and_flash(host):
    base = os.path.join(BACKUP, "nvs_head_s3.bin")
    if not os.path.exists(base):
        raise RuntimeError(f"缺少原始备份 {base}")
    out = "/tmp/nvs_head_ota.bin"
    ota = f"https://{host}{OTA_PATH}"
    r = sh([ESPY, os.path.join(HERE, "nvs_edit.py"), "set", base, out,
            "--ns", "wifi", "--key", "ota_url", "--value", ota])
    if r.returncode != 0:
        raise RuntimeError("NVS 生成失败:\n" + r.stdout + r.stderr)
    log("已生成私有后端配置地址（不记录密钥）")
    r = head_esptool(["write_flash", "0x9000", out], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("写 NVS 失败:\n" + r.stdout + r.stderr)
    log("NVS 已写入猫头")
    # 重启让新 ota_url 生效
    head_esptool(["chip_id"], capture_output=True, text=True)
    log("猫头已重启")


# ---------------------------------------------------------------- 5. 等待

def wait_device(seconds=90):
    log(f"等猫头连上来（最多 {seconds}s，需要你喊一次「你好喵伴」）…")
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(API + "/api/status", timeout=3) as r:
                d = json.load(r)
            if d.get("device_connected"):
                log("✅ 猫头已连上！")
                return True
        except Exception:
            pass
        time.sleep(2)
    log("⏳ 还没连上（可能还没唤醒，喊一声「你好喵伴」即可）")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--skip-nvs", action="store_true",
                    help="不改 NVS（域名没变时用）")
    ap.add_argument("--host", default=None, help="复用已有域名")
    args = ap.parse_args()

    kill_stale()
    host, tpid = start_tunnel()
    spid = start_server(host, voice=not args.no_voice)
    if not args.skip_nvs:
        patch_and_flash(host)
    wait_device()
    print()
    log(f"隧道 pid={tpid}  后端 pid={spid}  域名={host}")
    log(f"状态: {API}/api/status")
    log("两个进程都已脱离会话，关掉终端也不会死。")


if __name__ == "__main__":
    main()
