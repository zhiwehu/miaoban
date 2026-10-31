#!/usr/bin/env python3
"""
NVS 分区编辑器 —— 导出 / 修改 / 重生成 ESP-IDF NVS 分区镜像
=============================================================

NVS 分区是 4KB 一页的键值存储。往里加键的难点在于：不能只追加一页，
因为 NVS 的命名空间表是按页存放的，跨页追加同名命名空间不会生效。

所以这里的做法是：**完整读出 -> 改 -> 完整重生成**。

用法:
    # 1. 导出成 CSV（人可读，可再编辑）
    python3 nvs_edit.py export nvs.bin > nvs.csv

    # 2. 改 CSV（第一行是表头，namespace 行声明命名空间）
    #    wifi,namespace,,
    #    ssid,data,string,CFC-SW
    #    ota_url,data,string,https://example.com/ota/

    # 3. 由 CSV 重新生成分区镜像并自检
    python3 nvs_edit.py build nvs.csv nvs_new.bin 0x4000

    # 4. 直接改（一步到位）
    python3 nvs_edit.py set nvs.bin out.bin --ns wifi --key ota_url --value https://x/ota/

底层复用了 ESP-IDF 自带的 nvs_tool / nvs_partition_gen，格式绝对兼容固件。
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile

NVS_TOOL = os.path.expanduser(
    "~/esp/esp-idf/components/nvs_flash/nvs_partition_tool/nvs_tool.py")
GEN_PKG_DIR = os.path.expanduser(
    "~/.espressif/python_env/idf5.3_py3.9_env/lib/python3.9/site-packages")


def find_python():
    for p in [os.path.expanduser("~/.espressif/python_env/idf5.3_py3.9_env/bin/python3"),
              sys.executable]:
        if os.path.exists(p):
            return p
    return sys.executable


PY = find_python()


def dump_json(nvs_path):
    out = subprocess.run([PY, NVS_TOOL, "-f", "json", nvs_path],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("nvs_tool 失败:\n" + out.stderr)
    return json.loads(out.stdout)


def _parser():
    import nvs_parser
    return nvs_parser


def collect(nvs_path):
    """解析 NVS 镜像 -> (namespace 名列表, [(ns名, key, encoding, value)])。

    NVS 的结构要点（实测自 ESP-IDF nvs_parser）：
      * 命名空间以「默认命名空间里的一条 u8」声明：key=命名空间名, value=命名空间索引
      * 字符串/二进制：父条目存 [size(2)][crc(4)]，真正的数据在 children 里，
        每个 child 的 32 字节 raw 全是数据；值 = concat(children.raw)[:size]
      * span==1 时值直接在父条目的 data 区 (raw[26:32])
    """
    sys.path.insert(0, os.path.expanduser(
        "~/esp/esp-idf/components/nvs_flash/nvs_partition_tool"))
    nvs_parser = _parser()
    with open(nvs_path, "rb") as source:
        data = bytearray(source.read())
    nvs = nvs_parser.NVS_Partition(os.path.basename(nvs_path), data)

    decls = []      # [(name, index)]
    plain = []      # [(ns_index, key, type, value_bytes_or_int)]
    blob_groups = {}   # (ns,key) -> [bytearray]  分块存放的 blob
    for page in nvs.pages:
        for e in page.entries:
            if e.state != "Written" or e.data is None:
                continue
            ns = e.metadata["namespace"]
            t = e.metadata["type"]
            key = e.key
            if ns == 0 and t in ("uint8_t", "uint16_t", "uint32_t"):
                # 默认命名空间里的 u8 = 命名空间声明
                decls.append((key, e.data["value"]))
                continue
            if t == "blob_index":
                continue
            if t in ("string", "blob_data"):
                size = e.data["size"]
                span = e.metadata["span"]
                if span > 1:
                    val = b"".join(bytes(c.raw) for c in e.children)[:size]
                else:
                    val = bytes(e.raw[26:32])[:size]
                if t == "blob_data":
                    # 同一个 blob 可能被分成多条（多页 blob），按出现顺序拼回去
                    blob_groups.setdefault((ns, key), []).append(val)
                plain.append((ns, key, t, val))
            elif t in ("uint8_t", "int8_t", "uint16_t", "int16_t",
                       "uint32_t", "int32_t", "uint64_t", "int64_t"):
                plain.append((ns, key, t, e.data["value"]))

    # 合并分块 blob：同一个 (ns,key) 只保留一条，内容为所有块拼接
    merged = []
    seen_blob = set()
    for ns, key, t, val in plain:
        if t != "blob_data":
            merged.append((ns, key, t, val))
            continue
        k = (ns, key)
        if k in seen_blob:
            continue
        seen_blob.add(k)
        merged.append((ns, key, t, b"".join(blob_groups[k])))
    return decls, merged


TYPE_ENC = {
    "uint8_t": "u8", "int8_t": "i8",
    "uint16_t": "u16", "int16_t": "i16",
    "uint32_t": "u32", "int32_t": "i32",
    "uint64_t": "u64", "int64_t": "i64",
    "string": "string",
    "blob_data": "base64",
}


def to_csv(nvs_path):
    decls, plain = collect(nvs_path)
    idx_to_name = {idx: name for name, idx in decls}
    # 按命名空间声明顺序输出
    order = [name for name, _ in decls]
    grouped = {n: [] for n in order}
    grouped.setdefault("nvs", [])       # 默认命名空间
    for ns, key, t, val in plain:
        name = idx_to_name.get(ns, "nvs")
        enc = TYPE_ENC.get(t)
        if enc is None:
            continue
        if enc == "string":
            v = val.decode("utf-8", "replace")
            v = v.rstrip("\x00")     # 生成器自己会补结尾的 \0
        elif enc == "base64":
            v = base64.b64encode(val).decode()
        else:
            v = str(val)
        grouped[name].append((key, enc, v))

    rows = [["key", "type", "encoding", "value"]]
    for name in order + (["nvs"] if grouped.get("nvs") else []):
        rows.append([name, "namespace", "", ""])
        for key, enc, v in grouped.get(name, []):
            rows.append([key, "data", enc, v])
    import io
    import csv as _csv
    buf = io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerows(rows)
    return buf.getvalue()


def build(csv_path, out_path, size=0x4000, version=2):
    """用 ESP-IDF 的生成器造镜像，并做一次自检。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = GEN_PKG_DIR + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [PY, "-m", "esp_idf_nvs_partition_gen", "generate", csv_path, out_path,
           str(size), "--version", str(version)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("生成失败:\n" + r.stdout + r.stderr)
    # 自检
    chk = subprocess.run([PY, NVS_TOOL, "-i", "-d", "minimal", out_path],
                         capture_output=True, text=True)
    return chk.stdout + chk.stderr


def set_key(csv_path, ns, key, value, enc="string"):
    """在指定命名空间里新增/替换一个键。

    注意：nvs_partition_gen 是「按当前命名空间」归类的，所以新键必须插在
    **目标命名空间的数据块末尾**（也就是下一个 namespace 行之前）。
    """
    import csv as _csv
    with open(csv_path, newline="") as f:
        rows = list(_csv.reader(f))
    header, body = rows[0], rows[1:]

    start = end = None
    for i, r in enumerate(body):
        if len(r) >= 2 and r[1] == "namespace":
            if r[0] == ns:
                start = i + 1
                end = len(body)
                for j in range(i + 1, len(body)):
                    r2 = body[j]
                    if len(r2) >= 2 and r2[1] == "namespace":
                        end = j
                        break
                break
    if start is None:
        body.append([ns, "namespace", "", ""])
        body.append([key, "data", enc, value])
    else:
        block = [r for r in body[start:end] if not (r and r[0] == key)]
        block.append([key, "data", enc, value])
        body = body[:start] + block + body[end:]

    with open(csv_path, "w", newline="") as f:
        w = _csv.writer(f, lineterminator="\n")
        w.writerows([header] + body)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export")
    p.add_argument("nvs")

    p = sub.add_parser("build")
    p.add_argument("csv")
    p.add_argument("out")
    p.add_argument("size", nargs="?", type=lambda s: int(s, 0), default=0x4000)

    p = sub.add_parser("set")
    p.add_argument("nvs")
    p.add_argument("out")
    p.add_argument("--ns", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--value", required=True)

    args = ap.parse_args()
    if args.cmd == "export":
        print(to_csv(args.nvs), end="")
    elif args.cmd == "build":
        print(build(args.csv, args.out, args.size))
        print(f"已生成 {args.out}")
    elif args.cmd == "set":
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tf:
            csvp = tf.name
        open(csvp, "w").write(to_csv(args.nvs))
        set_key(csvp, args.ns, args.key, args.value)
        print(build(csvp, args.out))
        os.unlink(csvp)
        print(f"已生成 {args.out}  (+{args.ns}/{args.key})")


if __name__ == "__main__":
    main()
