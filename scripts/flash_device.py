#!/usr/bin/env python3
# scripts/flash_device.py

import subprocess
import sys
import os
import re
import time
import tempfile
import argparse
import serial.tools.list_ports

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE_DIR = os.path.join(ROOT, "device")
ENTRY_FILE = "main.py"
_COM_PORT = None


def select_com_port() -> str:
    ports = [p.device for p in serial.tools.list_ports.comports()]
    if not ports:
        print("[错误] 未找到任何串口设备")
        sys.exit(1)
    print("可用串口：")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p}")
    if len(ports) == 1:
        print(f"  自动选择: {ports[0]}")
        return ports[0]
    idx = input("请选择串口编号: ").strip()
    try:
        return ports[int(idx)]
    except (ValueError, IndexError):
        print("[错误] 无效选择")
        sys.exit(1)


def run_mpremote(cmd: list[str]) -> str:
    result = subprocess.run(
        ["mpremote", "connect", _COM_PORT] + cmd,
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"[错误] mpremote 命令失败: {' '.join(cmd)}")
        print(result.stderr)
        sys.exit(1)
    return result.stdout.strip()


def check_mpy_cross() -> bool:
    try:
        return subprocess.run(["mpy-cross", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def compile_to_mpy(src_path: str, dest_path: str) -> bool:
    try:
        subprocess.run(["mpy-cross", "-o", dest_path, src_path], check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[警告] 编译失败: {src_path}")
        print(e.stderr.decode())
        return False


def wipe_device():
    print("[0/5] 清空设备文件系统...")
    subprocess.run(["mpremote", "connect", _COM_PORT, "fs", "rm", "-r", "/"], capture_output=True)
    time.sleep(1)
    print("  ✓ 清空完成")


def get_mac_address() -> str:
    print("[1/5] 读取设备 MAC 地址...")
    code = (
        "import bluetooth\n"
        "ble = bluetooth.BLE()\n"
        "ble.active(True)\n"
        "mac = ble.config('mac')[1]\n"
        "print(''.join(f'{b:02X}' for b in mac[-2:]))\n"
    )
    output = run_mpremote(["exec", code])
    mac_suffix = output.strip().split("\n")[-1]
    if len(mac_suffix) != 4 or not all(c in "0123456789ABCDEF" for c in mac_suffix):
        print(f"[错误] MAC 地址格式异常: {mac_suffix!r}")
        sys.exit(1)
    print(f"  → MAC 后4位: {mac_suffix}")
    return mac_suffix


def generate_config(mac_suffix: str, variant: str) -> str:
    print("[2/5] 生成 config.py...")
    ble_name = f"Claude-Buddy-{mac_suffix}"
    src = os.path.join(DEVICE_DIR, "config.py")
    with open(src, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(r'^BLE_NAME\s*=.*$', f'BLE_NAME = "{ble_name}"', content, flags=re.MULTILINE)
    content = re.sub(r'^VARIANT\s*=.*$', f'VARIANT = "{variant}"', content, flags=re.MULTILINE)
    print(f"  → BLE_NAME = {ble_name!r}, VARIANT = {variant!r}")
    return content


def install_libs():
    print("[3/5] 安装依赖库（aioble）...")
    run_mpremote(["mip", "install", "aioble"])
    print("  ✓ aioble 安装完成")


def upload_firmware(config_content: str):
    print("[4/5] 编译并上传固件文件...")
    use_mpy = check_mpy_cross()
    print(f"  {'✓ mpy-cross 可用' if use_mpy else '⚠ mpy-cross 未安装，上传源码'}")

    with tempfile.TemporaryDirectory() as tmpdir:
        upload_list = []

        config_py = os.path.join(tmpdir, "config.py")
        with open(config_py, "w", encoding="utf-8") as f:
            f.write(config_content)
        if use_mpy:
            config_mpy = os.path.join(tmpdir, "config.mpy")
            upload_list.append((config_mpy if compile_to_mpy(config_py, config_mpy) else config_py,
                                 "config.mpy" if os.path.exists(os.path.join(tmpdir, "config.mpy")) else "config.py"))
        else:
            upload_list.append((config_py, "config.py"))

        for fname in os.listdir(DEVICE_DIR):
            if not fname.endswith(".py") or fname == "config.py":
                continue
            src = os.path.join(DEVICE_DIR, fname)
            if not os.path.isfile(src):
                continue
            if fname == ENTRY_FILE:
                upload_list.append((src, fname))
                continue
            if use_mpy:
                mpy_name = fname.replace(".py", ".mpy")
                mpy_path = os.path.join(tmpdir, mpy_name)
                upload_list.append((mpy_path if compile_to_mpy(src, mpy_path) else src,
                                     mpy_name if os.path.exists(mpy_path) else fname))
            else:
                upload_list.append((src, fname))

        for local_path, remote_name in upload_list:
            run_mpremote(["cp", local_path, f":{remote_name}"])
            print(f"  ✓ {remote_name} ({os.path.getsize(local_path)/1024:.1f} KB)")

        assets_dir = os.path.join(DEVICE_DIR, "assets")
        if os.path.isdir(assets_dir):
            subprocess.run(["mpremote", "connect", _COM_PORT, "mkdir", ":assets"], capture_output=True)
            for fname in sorted(os.listdir(assets_dir)):
                src = os.path.join(assets_dir, fname)
                if os.path.isfile(src):
                    run_mpremote(["cp", src, f":assets/{fname}"])
                    print(f"  ✓ assets/{fname} ({os.path.getsize(src)/1024:.1f} KB)")


def reset_device():
    print("[5/5] 重启设备...")
    subprocess.run(["mpremote", "connect", _COM_PORT, "reset"], capture_output=True)
    print("  ✓ 设备已重启")


def main():
    global _COM_PORT
    print("=" * 50)
    print("ESP32 固件烧录工具（MAC 自动命名）")
    print("=" * 50)

    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["panel", "clock"], default="panel")
    args = parser.parse_args()

    _COM_PORT = select_com_port()
    print(f"  → 使用串口: {_COM_PORT}")

    wipe_device()
    mac_suffix = get_mac_address()
    config_content = generate_config(mac_suffix, args.variant)
    install_libs()
    upload_firmware(config_content)
    reset_device()

    print("\n" + "=" * 50)
    print(f"✓ 烧录完成！设备名称: Claude-Buddy-{mac_suffix}  型号: {args.variant}")
    print("=" * 50)


if __name__ == "__main__":
    main()
