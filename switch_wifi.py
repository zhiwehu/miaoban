#!/usr/bin/env python3
"""
把猫头切到和 Mac 同一个 WiFi，并让它直连本机后端（不再用隧道）
================================================================

做的事：
  1. 读 Mac 当前 WiFi 的 SSID（从 ipconfig 读，不打印）
  2. 用**原厂 NVS 备份**作基底，写 wifi/ssid + wifi/password + wifi/ota_url
  3. 刷进猫头并重启
  4. 以 0.0.0.0 起后端，猫头直连 http://<Mac局域网IP>:8099

注意：ESP32-S3 只支持 2.4GHz。如果 Mac 连的 SSID 是 5GHz 专用，
猫头会连不上 —— 那种情况需要换一个双频同名的 SSID。

用法：
    python3 switch_wifi.py --password '你的WiFi密码'
    python3 switch_wifi.py --password-file /tmp/wifi_pw.txt
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import nvs_edit  # noqa: E402

BACKUP = os.path.join(HERE, "backup")
ESPY = os.path.expanduser("~/.espressif/python_env/idf5.3_py3.9_env/bin/python3")
from device_ports import head_esptool
PORT = 8099


def mac_ssid() -> str:
    """读 Mac 当前 WiFi 的 SSID。"""
    out = subprocess.run(["ipconfig", "getsummary", "en0"],
                         capture_output=True, text=True).stdout
    m = re.search(r"SSID\s*:\s*(.+)", out)
    if not m:
        raise RuntimeError("读不到 Mac 的 SSID（WiFi 没连？）")
    return m.group(1).strip()


def lan_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def build_and_flash(ssid: str, password: str, ota_url: str, flash: bool = True) -> str:
    base = os.path.join(BACKUP, "nvs_head_s3.bin")
    if not os.path.exists(base):
        raise RuntimeError(f"缺少原厂备份 {base}")
    csv = nvs_edit.to_csv(base)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(csv)
        csv_path = tf.name
    out = "/tmp/nvs_head_lan.bin"
    nvs_edit.set_key(csv_path, "wifi", "ssid", ssid)
    nvs_edit.set_key(csv_path, "wifi", "password", password)
    nvs_edit.set_key(csv_path, "wifi", "ota_url", ota_url)
    check = nvs_edit.build(csv_path, out, 0x4000)
    os.unlink(csv_path)
    if "ota_url" not in check:
        raise RuntimeError("生成的 NVS 里没找到 ota_url:\n" + check)
    print("[switch] NVS 已生成（ssid/password/ota_url 三项就位）")
    if not flash:
        print(f"[switch] --no-flash：只生成不刷写，文件在 {out}")
        return out
    r = head_esptool(["write_flash", "0x9000", out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("刷写失败:\n" + r.stdout + r.stderr)
    print("[switch] 已写入猫头 NVS")
    return out


def reboot_head():
    head_esptool(["chip_id"],
                   capture_output=True)
    print("[switch] 猫头已重启")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--password")
    ap.add_argument("--password-file")
    ap.add_argument("--ssid", default=None, help="默认用 Mac 当前的 SSID")
    ap.add_argument("--no-flash", action="store_true")
    args = ap.parse_args()

    pw = args.password
    if not pw and args.password_file:
        pw = open(args.password_file).read().strip()
    if not pw:
        print("必须给 --password 或 --password-file", file=sys.stderr)
        return 1

    ssid = args.ssid or mac_ssid()
    ip = lan_ip()
    ota = f"http://{ip}:{PORT}/xiaozhi/ota/"
    print(f"[switch] 目标 SSID 长度={len(ssid)}  本机 IP={ip}")
    print(f"[switch] ota_url = {ota}")

    build_and_flash(ssid, pw, ota)
    if not args.no_flash:
        reboot_head()
    print("\n下一步：起后端（不带 --public-host 就自动用本机 IP）")
    print(f"  ~/ai/mac_voice_agent/.venv/bin/python xiaozhi_server.py --bind 0.0.0.0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
