#!/usr/bin/env python3
"""公平地测试：固件运行中时，我能否直接驱动电机 PWM。

JTAG 访问内存需要 halt CPU，所以每一步都要保证结束时把 CPU 放回运行状态，
否则固件根本没机会覆盖我写的值。
"""
import re
import subprocess
import sys
import time

OCD = "/Users/huzhiwei/.espressif/tools/openocd-esp32/v0.12.0-esp32-20241016/openocd-esp32/bin/openocd"
BOARD = "board/esp32c3-builtin.cfg"


def sess(cmds, halt=True, resume=True, timeout=120):
    seq = ["init"]
    if halt:
        seq.append("halt")
    seq += list(cmds)
    if resume:
        seq.append("resume")
    seq.append("shutdown")
    cmd = [OCD, "-f", BOARD, "-c", "adapter speed 4000"]
    for c in seq:
        cmd += ["-c", c]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.stdout + p.stderr


def regs(addr, n):
    out = sess([f"mdw {addr} {n}"])
    return [l for l in out.splitlines() if re.match(r"^0x[0-9a-f]+:", l)]


def main():
    print("=== 1. 重启并让固件正常跑起来 ===")
    out = sess(["reset run"], halt=False, resume=False)
    if "reset" not in out.lower():
        print("openocd reset 无输出，改用 esptool 硬复位")
        subprocess.call([sys.executable, "-m", "esptool", "--port", "/dev/cu.usbmodem1101", "chip_id"],
                        capture_output=True)
    time.sleep(5)

    print("=== 2. 固件运行中的 LEDC 状态 ===")
    for l in regs("0x60019000", 1) + regs("0x60019014", 1) + regs("0x60019008", 1) + \
            regs("0x6001901c", 1) + regs("0x600190a0", 2):
        print("   ", l)

    print("=== 3. 写入 ch0 duty=0x200，CPU 继续运行 ===")
    sess(["mww 0x60019008 0x200"], halt=True, resume=True)
    time.sleep(4)

    print("=== 4. 4 秒后回读（固件有机会覆盖） ===")
    for l in regs("0x60019008", 1) + regs("0x6001901c", 1):
        print("   ", l)

    print("=== 5. 恢复 duty=0 ===")
    sess(["mww 0x60019008 0x0"], halt=True, resume=True)
    time.sleep(1)
    for l in regs("0x60019008", 1):
        print("   ", l)


if __name__ == "__main__":
    main()
