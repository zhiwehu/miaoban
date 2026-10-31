#!/usr/bin/env python3
"""
喵伴 (ESP-VoCat / EchoEar) 旋转底座 —— 工具箱
================================================

你插上的这块板子是 **喵伴的旋转底座** (rotating_base)：

    MCU      ESP32-C3 (RISC-V, 单核 160MHz, 4MB flash, 无 PSRAM)
    固件     ESP-IDF v5.4.1, project name = "rotating_base", board = "EchoEar"
    外设     ILI9341 SPI 小屏(LVGL) / 舵机(LEDC PWM) / 旋钮(ADC) / 2 个按键 / ESP-NOW / UART
    接口     原生 USB (USB Serial/JTAG, VID 0x303a PID 0x1001) -> /dev/cu.usbmodem1101
    MAC      10:51:DB:BA:A1:FC

底座是「卫星」：真正带麦克风/扬声器/主屏的猫头是另一块 ESP32-S3，
通过 UART 帧协议跟底座说话；底座也能被 ESP-NOW 控制。

用法:
    python3 miaoban.py info              # 芯片 / flash 信息
    python3 miaoban.py log 10            # 抓 10 秒串口控制台
    python3 miaoban.py backup            # 备份整片 4MB flash
    python3 miaoban.py restore           # 从备份恢复整片 flash
    python3 miaoban.py reg 0x60019000 16 # JTAG 读寄存器
    python3 miaoban.py write 0xADDR 0xVAL# JTAG 写寄存器
    python3 miaoban.py jtag "..." "..."   # 任意 openocd 命令
    python3 miaoban.py hw                # 底座外设寄存器快照 (LEDC/GPIO/ADC)
    python3 miaoban.py motor ch0 512 2000# 直接驱动电机 PWM 占空比
    python3 miaoban.py scan              # 枚举所有 USB 串口并逐个识别板子
    python3 miaoban.py net               # 扫描局域网找设备
"""

import glob
import os
import re
import shutil
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, "backup")

# ---------------------------------------------------------------- 工具链发现

def _first(paths, pattern):
    for p in paths:
        hits = sorted(glob.glob(os.path.join(p, pattern)))
        if hits:
            return hits[-1]
    return None


def find_esptool_python():
    env = os.path.expanduser("~/.espressif/python_env")
    for pat in ("idf*_py3.9_env/bin/python3", "idf*_py3.12_env/bin/python3"):
        hit = _first([env], pat)
        if hit:
            try:
                r = subprocess.run([hit, "-c", "import esptool,serial"], capture_output=True)
                if r.returncode == 0:
                    return hit
            except OSError:
                pass
    for name in ("esptool.py", "esptool"):
        w = shutil.which(name)
        if w:
            return sys.executable
    return None


def find_openocd():
    root = os.path.expanduser("~/.espressif/tools/openocd-esp32")
    hit = _first([root], "*/openocd-esp32/bin/openocd")
    if hit:
        return hit
    return shutil.which("openocd")


def find_port():
    """优先用系统里出现的 Espressif USB 设备。"""
    cands = []
    for p in sorted(glob.glob("/dev/cu.usbmodem*")) + sorted(glob.glob("/dev/cu.usbserial*")) \
            + sorted(glob.glob("/dev/cu.SLAB_USBtoUART*")) + sorted(glob.glob("/dev/cu.wchusbserial*")) \
            + sorted(glob.glob("/dev/cu.SLAB_*")):
        if p not in cands:
            cands.append(p)
    return cands[0] if cands else "/dev/cu.usbmodem1101"


PORT = find_port()
ESPTOOL_PY = find_esptool_python()
OPENOCD = find_openocd()


# ---------------------------------------------------------------- 底层封装

def esptool(args, timeout=900):
    if not ESPTOOL_PY:
        print("!! 找不到可用的 esptool (ESP-IDF python 环境)", file=sys.stderr)
        return 1
    cmd = [ESPTOOL_PY, "-m", "esptool", "--port", PORT] + args
    print("$", " ".join(cmd), file=sys.stderr)
    return subprocess.call(cmd)


def jtag(cmds, timeout=120, halt=True):
    """通过 USB-JTAG 通道执行 openocd 命令 (mdw/mww/reg/...)。"""
    if not OPENOCD:
        print("!! 找不到 openocd-esp32", file=sys.stderr)
        return ""
    seq = ["init"] + (["halt"] if halt else []) + list(cmds) + ["shutdown"]
    cmd = [OPENOCD, "-f", "board/esp32c3-builtin.cfg", "-c", "adapter speed 4000"]
    for c in seq:
        cmd += ["-c", c]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        return (e.stdout or b"").decode(errors="replace") + (e.stderr or b"").decode(errors="replace")


def jtag_clean(text):
    """滤掉 openocd 的横幅，只留命令输出。"""
    out = []
    for line in text.splitlines():
        if re.match(r"^(Open On-Chip|Licensed|For bug|http://openocd|Info :|Warn |adapter speed|shutdown command|\s*$)", line):
            continue
        out.append(line)
    return "\n".join(out)


def serial_log(seconds=10, port=None, quiet=False):
    """抓取串口控制台输出。"""
    import serial
    port = port or PORT
    s = serial.Serial(port, 115200, timeout=0.5)
    s.dtr = False
    s.rts = False
    buf = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            line = s.readline()
            if line:
                buf.append(line.decode("utf-8", "replace").rstrip())
        except Exception:
            pass
    s.close()
    if quiet:
        return buf
    print("\n".join(buf))
    return buf


# ---------------------------------------------------------------- 子命令

def cmd_info(args):
    print("=== 设备 ===")
    print("串口      :", PORT)
    print("esptool   :", ESPTOOL_PY or "(缺)")
    print("openocd   :", OPENOCD or "(缺)")
    print()
    esptool(["chip_id"])
    print()
    esptool(["flash_id"])
    return 0


def cmd_log(args):
    secs = int(args[0]) if args else 10
    print(f"=== 抓取 {secs} 秒串口输出 ({PORT}) ===")
    serial_log(secs)
    return 0


def cmd_backup(args):
    outdir = args[0] if args else BACKUP_DIR
    os.makedirs(outdir, exist_ok=True)
    print(f"=== 备份整片 flash 到 {outdir} ===")
    esptool(["read_flash", "0x0", "0x400000", os.path.join(outdir, "full_flash.bin")])
    esptool(["read_flash", "0x10000", "0x200000", os.path.join(outdir, "factory_app.bin")])
    esptool(["read_flash", "0x9000", "0x6000", os.path.join(outdir, "nvs.bin")])
    esptool(["read_flash", "0x0", "0x1000", os.path.join(outdir, "bootloader.bin")])
    esptool(["read_flash", "0xE000", "0x1000", os.path.join(outdir, "partition_table.bin")])
    print("完成。")
    return 0


def cmd_restore(args):
    src = args[0] if args else os.path.join(BACKUP_DIR, "full_flash.bin")
    if not os.path.exists(src):
        print("!! 找不到备份文件:", src, file=sys.stderr)
        return 1
    print("!! 这会覆盖设备上的固件。")
    if "--yes" not in args:
        if input("确认刷回 %s ? (yes/no) " % src).strip() != "yes":
            print("已取消")
            return 1
    esptool(["write_flash", "0x0", src])
    return 0


def cmd_flash_app(args):
    if not args:
        print("用法: flash-app <app.bin>", file=sys.stderr)
        return 1
    esptool(["write_flash", "0x10000", args[0]])
    return 0


def cmd_reg(args):
    if not args:
        print("用法: reg <addr> [count]", file=sys.stderr)
        return 1
    addr = args[0]
    count = args[1] if len(args) > 1 else "4"
    out = jtag([f"mdw {addr} {count}"])
    print(jtag_clean(out))
    return 0


# --- 喵伴底座外设寄存器 (ESP32-C3, 由 IDF soc 头文件核对) --------------------
LEDC_BASE = 0x60019000
GPIO_BASE = 0x60004000
IOMUX_BASE = 0x60009000
ADC_BASE = 0x60040000

# LEDC 通道布局: conf0 / hpoint / duty / conf1 / duty_rd, 步长 0x14
LEDC_CH = {
    0: {"conf0": LEDC_BASE + 0x00, "hpoint": LEDC_BASE + 0x04, "duty": LEDC_BASE + 0x08,
        "conf1": LEDC_BASE + 0x0C, "duty_rd": LEDC_BASE + 0x10},
    1: {"conf0": LEDC_BASE + 0x14, "hpoint": LEDC_BASE + 0x18, "duty": LEDC_BASE + 0x1C,
        "conf1": LEDC_BASE + 0x20, "duty_rd": LEDC_BASE + 0x24},
}
# 定时器组在 0xA0: timerN.conf / timerN.value, 步长 8
LEDC_TIMER = {n: LEDC_BASE + 0xA0 + n * 8 for n in range(4)}


def _candidate_ports():
    """列出所有可能是 ESP32 串口的设备节点。"""
    pats = ["/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/cu.SLAB_USBtoUART*",
            "/dev/cu.SLAB_*", "/dev/cu.wchusbserial*", "/dev/cu.usbcdc*"]
    out = []
    for p in pats:
        for hit in sorted(glob.glob(p)):
            if hit not in out:
                out.append(hit)
    return out


def _app_desc(port):
    """读 factory 分区头，解析 esp_app_desc_t (工程名/版本/编译时间)。

    esp_app_desc_t 位于 app 分区起始 + 0x20
    (esp_image_header_t 24B + 第一个 segment header 8B)。
    """
    import struct
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tf:
        path = tf.name
    try:
        subprocess.call([ESPTOOL_PY, "-m", "esptool", "--port", port,
                         "read_flash", "0x10000", "0x120", path],
                        capture_output=True, timeout=120)
        data = open(path, "rb").read()
        if len(data) < 0x20 + 256:
            return None
        base = 0x20
        magic, = struct.unpack_from("<I", data, base)
        if magic != 0xABCD5432:
            return None

        def s(off):
            return data[base + off:base + off + 32].split(b"\x00")[0].decode("utf-8", "replace")
        return {"version": s(0x10), "project": s(0x30), "time": s(0x50),
                "date": s(0x60), "idf": s(0x70)}
    except Exception:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def cmd_scan(args):
    """枚举所有 USB 串口，逐个识别是哪块板子。"""
    ports = _candidate_ports()
    if not ports:
        print("没找到任何 USB 串口设备。")
        return 1
    print(f"发现 {len(ports)} 个串口:\n")
    for p in ports:
        print("=" * 60)
        print("端口:", p)
        r = subprocess.run([ESPTOOL_PY, "-m", "esptool", "--port", p, "chip_id"],
                           capture_output=True, text=True, timeout=120)
        out = r.stdout + r.stderr
        chip = ""
        m = re.search(r"(ESP32(?:-S3|-C3|-C2|-C6)?)\b", out)
        if m:
            chip = m.group(1)
        mac = ""
        m = re.search(r"MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", out)
        if m:
            mac = m.group(1)
        if not chip:
            print("  无法连接 (可能不是 ESP32，或被占用)")
            print("  " + out.strip().splitlines()[-1] if out.strip() else "")
            continue
        print("  芯片 :", chip)
        print("  MAC  :", mac)
        d = _app_desc(p)
        if d:
            print("  工程 :", d["project"])
            print("  版本 :", d["version"])
            print("  编译 :", d["date"], d["time"], "| IDF", d["idf"])
        else:
            print("  工程 : (读不到 app 描述符)")
    print("=" * 60)
    return 0


def cmd_motor(args):
    """直接驱动电机 PWM:  motor <ch0|ch1> <duty 0-1023> [保持毫秒]

    底座的 motor_ctrl 组件在收到猫头指令前是空闲的，所以直接写 LEDC duty
    不会被固件覆盖 —— 这是当前唯一能"动起来"的捷径。
    """
    if len(args) < 2:
        print("用法: motor <ch0|ch1> <duty 0-1023> [hold_ms]", file=sys.stderr)
        return 1
    ch = args[0].lower()
    ch = 0 if ch in ("ch0", "0", "a") else (1 if ch in ("ch1", "1", "b") else None)
    if ch is None:
        print("通道只能是 ch0 或 ch1", file=sys.stderr)
        return 1
    duty = int(args[1], 0)
    hold = int(args[2]) if len(args) > 2 else 2000
    addr = LEDC_CH[ch]["duty"]
    print(f"写入 duty={duty} (0x{duty:x}) @ 0x{addr:08x}，保持 {hold} ms")
    out = jtag([f"mww 0x{addr:08x} 0x{duty:x}", f"mdw 0x{addr:08x} 1", f"sleep {hold}",
                f"mww 0x{addr:08x} 0x0", f"mdw 0x{addr:08x} 1"])
    print(jtag_clean(out))
    return 0


def cmd_hw(args):
    """一次性打印底座关键外设寄存器快照。"""
    cmds = []
    for ch, a in LEDC_CH.items():
        for k, v in a.items():
            cmds.append(f"mdw 0x{v:08x} 1")
    for n, a in LEDC_TIMER.items():
        cmds.append(f"mdw 0x{a:08x} 2")
    out = jtag(cmds)
    print(jtag_clean(out))
    return 0


def cmd_write(args):
    if len(args) < 2:
        print("用法: write <addr> <value>", file=sys.stderr)
        return 1
    out = jtag([f"mww {args[0]} {args[1]}", f"mdw {args[0]} 1"])
    print(jtag_clean(out))
    return 0


def cmd_jtag(args):
    out = jtag(args)
    print(jtag_clean(out))
    return 0


def cmd_net(args):
    """扫描局域网，找开放 80/8080/8266 等端口的设备 (喵伴头/其它 ESP)。"""
    ports = [80, 443, 8080, 8266, 8888, 1883, 5000, 8081, 10000, 9000, 3000, 8265]
    hosts = set()
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True).stdout
        for tok in out.split():
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", tok):
                hosts.add(tok)
    except OSError:
        pass
    # 扫描 /24 网段里 ARP 已知的邻居 + 少量常见地址
    import concurrent.futures

    def probe(a):
        ip, port = a
        s = socket.socket()
        s.settimeout(0.6)
        try:
            s.connect((ip, port))
            return (ip, port)
        except Exception:
            return None
        finally:
            s.close()

    tasks = [(ip, p) for ip in sorted(hosts) for p in ports]
    with concurrent.futures.ThreadPoolExecutor(max_workers=256) as ex:
        res = [r for r in ex.map(probe, tasks) if r]
    if not res:
        print("局域网内没扫到常见服务端口。")
    for ip, p in sorted(res):
        print(f"  {ip}:{p}")
    return 0


COMMANDS = {
    "info": cmd_info,
    "log": cmd_log,
    "backup": cmd_backup,
    "restore": cmd_restore,
    "flash-app": cmd_flash_app,
    "reg": cmd_reg,
    "write": cmd_write,
    "jtag": cmd_jtag,
    "net": cmd_net,
    "motor": cmd_motor,
    "hw": cmd_hw,
    "scan": cmd_scan,
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    name = sys.argv[1]
    if name not in COMMANDS:
        print("未知命令:", name, file=sys.stderr)
        print("可用:", ", ".join(COMMANDS), file=sys.stderr)
        return 1
    return COMMANDS[name](sys.argv[2:]) or 0


if __name__ == "__main__":
    sys.exit(main())
