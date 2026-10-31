#!/usr/bin/env python3
"""电机演示: 直接用 JTAG 驱动底座电机 PWM，请盯着喵伴看。

底座 LEDC: ch0 = 0x60019008 (duty), 10bit 分辨率, 实测 PWM ≈ 498.5 Hz
固件的 motor_ctrl 在没收到猫头指令时是空闲的，所以直接写 duty 不会被覆盖。
"""
import subprocess
import sys
import time

OCD = "/Users/huzhiwei/.espressif/tools/openocd-esp32/v0.12.0-esp32-20241016/openocd-esp32/bin/openocd"
DUTY_CH0 = 0x60019008
DUTY_CH1 = 0x6001901C


def write_reg(addr, val):
    cmd = [OCD, "-f", "board/esp32c3-builtin.cfg", "-c", "adapter speed 4000",
           "-c", "init", "-c", "halt", "-c", f"mww 0x{addr:08x} 0x{val:x}",
           "-c", "resume", "-c", "shutdown"]
    subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def step(label, ch0, ch1, hold_ms):
    print(f"  {label:<28} ch0={ch0:<5} ch1={ch1:<5} 保持 {hold_ms} ms", flush=True)
    write_reg(DUTY_CH0, ch0)
    write_reg(DUTY_CH1, ch1)
    time.sleep(hold_ms / 1000.0)


def main():
    print("=== 喵伴底座电机演示 (JTAG 直驱 LEDC) ===", flush=True)
    step("归零", 0, 0, 600)
    for duty in (128, 256, 384, 512, 640, 768, 896, 1023):
        step(f"ch0 占空比 {duty}/1023 ({duty*100//1023}%)", duty, 0, 900)
    step("反向 ch1", 0, 512, 1200)
    step("归零", 0, 0, 600)
    print("=== 演示结束，已归零 ===", flush=True)


if __name__ == "__main__":
    main()
