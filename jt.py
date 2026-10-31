#!/usr/bin/env python3
"""JTAG 工具: 流式执行 openocd 命令并给每条输出打时间戳。"""
import re
import subprocess
import sys
import time

OCD = "/Users/huzhiwei/.espressif/tools/openocd-esp32/v0.12.0-esp32-20241016/openocd-esp32/bin/openocd"
BOARD = "board/esp32c3-builtin.cfg"
# 底座 ESP32-C3 的 USB-JTAG 序列号。两块板都插着时 openocd 会自动挑第一个，
# 必须按序列号锁定，否则指令会打到猫头(S3)上并静默失败。
BASE_SERIAL = "10:51:DB:BA:A1:FC"


def stream(cmds, timeout=180, halt=True):
    """逐条执行 openocd 命令，返回 [(monotonic_time, text)]。"""
    seq = ["init"] + (["halt"] if halt else []) + list(cmds) + ["shutdown"]
    cmd = [OCD, "-f", BOARD, "-c", f"adapter serial {BASE_SERIAL}",
           "-c", "adapter speed 4000"]
    for c in seq:
        cmd += ["-c", c]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    out = []
    t0 = time.monotonic()
    try:
        for line in p.stdout:
            out.append((time.monotonic() - t0, line.rstrip()))
    except Exception:
        pass
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()
    return out


def sample_count(addr="0x600190a4", n=120, gap_ms=20):
    """采样定时器计数值并计算真实频率。

    gap_ms=0 时不加 sleep，靠 mdw 往返时间做间隔；此时相邻样本间隔远小于
    一个 PWM 周期，不会漏掉回绕，频率估计才准确。
    """
    cmds = []
    for _ in range(n):
        cmds.append("mdw %s 1" % addr)
        if gap_ms:
            cmds.append("sleep %d" % gap_ms)
    samples = []
    for t, line in stream(cmds):
        m = re.match(r"^(0x[0-9a-fA-F]+):\s+([0-9a-fA-F]+)", line)
        if m and m.group(1).lower() == addr.lower():
            samples.append((t, int(m.group(2), 16)))
    return samples


if __name__ == "__main__":
    if sys.argv[1:2] == ["count"]:
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 120
        s = sample_count(n=n)
        print("samples:", len(s))
        if len(s) > 3:
            # 用相邻样本的 (计数增量, 时间差) 做最小二乘，抗抖动
            pts = []
            for (t0, c0), (t1, c1) in zip(s, s[1:]):
                d = c1 - c0
                if d < 0:
                    d += 1024
                dt = t1 - t0
                if dt > 0:
                    pts.append((dt, d))
            rate = sum(d for _, d in pts) / sum(dt for dt, _ in pts)
            print("rate  : %.1f counts/s" % rate)
            print("period: %.3f ms  -> f = %.3f Hz" % (1024 / rate * 1000, rate / 1024))
            print("first 12:", [c for _, c in s[:12]])
    else:
        for t, line in stream(sys.argv[1:]):
            print("[%7.3fs] %s" % (t, line))
