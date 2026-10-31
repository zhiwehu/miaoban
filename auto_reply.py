#!/usr/bin/env python3
"""等猫头连上后，立刻用本地 TTS 让它开口说话。"""
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:8098/api"
TEXT = sys.argv[1] if len(sys.argv) > 1 else "喵伴在这儿呢，我听到你了。"
USER_TEXT = sys.argv[2] if len(sys.argv) > 2 else "你好喵伴"
EMOTION = sys.argv[3] if len(sys.argv) > 3 else "happy"


def get(path):
    with urllib.request.urlopen(API + path, timeout=8) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(API + path,
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


print(f"等待猫头连入…  将让它说: {TEXT!r}", flush=True)
deadline = time.time() + 300
while time.time() < deadline:
    try:
        if get("/status")["device_connected"]:
            print("猫头已连上！等 2 秒让它进入聆听状态…", flush=True)
            time.sleep(2)
            res = post("/say", {"text": TEXT, "user_text": USER_TEXT,
                                "emotion": EMOTION})
            print("发送结果:", json.dumps(res, ensure_ascii=False), flush=True)
            break
    except Exception as e:
        pass
    time.sleep(1)
else:
    print("超时：猫头没连上来", flush=True)
