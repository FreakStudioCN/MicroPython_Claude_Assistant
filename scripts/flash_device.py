#!/usr/bin/env python3
# scripts/flash_device.py
# 开发者工具：读取 ESP32 MAC 地址，生成配置，烧录固件

import subprocess
import sys
import os
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE_DIR = os.path.join(ROOT, "device")

# 入口文件不编译（必须保持 .py 格式）
ENTRY_FILE = "main.py"


def run_mpremote(cmd: list[str]) -> str:
    """运行 mpremote 命令并返回输出。"""
    result = subprocess.run(
        ["mpremote"] + cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"[错误] mpremote 命令失败: {' '.join(cmd)}")
        print(result.stderr)
        sys.exit(1)
    return result.stdout.strip()


def check_mpy_cross() -> bool:
    """检查 mpy-cross 是否可用。"""
    try:
        result = subprocess.run(
            ["mpy-cross", "--version"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def compile_to_mpy(src_path: str, dest_path: str) -> bool:
    """编译 .py 文件为 .mpy 字节码。"""
    try:
        subprocess.run(
            ["mpy-cross", "-o", dest_path, src_path],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"[警告] 编译失败: {src_path}")
        print(e.stderr.decode())
        return False


def get_mac_address() -> str:
    """从设备读取 BLE MAC 地址后4位。"""
    print("[1/4] 读取设备 MAC 地址...")
    code = """
import bluetooth
ble = bluetooth.BLE()
ble.active(True)
mac = ble.config('mac')[1]
print(''.join(f'{b:02X}' for b in mac[-2:]))
"""
    output = run_mpremote(["exec", code])
    # 输出格式: "AABB\n" 或包含其他调试信息，取最后一行
    mac_suffix = output.strip().split("\n")[-1]
    if len(mac_suffix) != 4 or not all(c in "0123456789ABCDEF" for c in mac_suffix):
        print(f"[错误] MAC 地址格式异常: {mac_suffix!r}")
        sys.exit(1)
    print(f"  → MAC 后4位: {mac_suffix}")
    return mac_suffix


def generate_config(mac_suffix: str) -> str:
    """读取 device/config.py，仅替换 BLE_NAME 行。"""
    print("[2/4] 生成 config.py...")
    ble_name = f"Claude-Buddy-{mac_suffix}"
    src = os.path.join(DEVICE_DIR, "config.py")
    with open(src, "r", encoding="utf-8") as f:
        content = f.read()
    import re
    content = re.sub(r'^BLE_NAME\s*=.*$', f'BLE_NAME = "{ble_name}"', content, flags=re.MULTILINE)
    print(f"  → BLE_NAME = {ble_name!r}")
    return content


def install_libs():
    """通过 mip 安装设备端依赖库。"""
    print("[3/5] 安装依赖库（aioble）...")
    run_mpremote(["mip", "install", "aioble"])
    print("  ✓ aioble 安装完成")


def upload_firmware(config_content: str):
    """上传所有固件文件到设备。"""
    print("[4/5] 编译并上传固件文件...")

    # 检查 mpy-cross 是否可用
    use_mpy = check_mpy_cross()
    if use_mpy:
        print("  ✓ mpy-cross 可用，将编译为字节码")
    else:
        print("  ⚠ mpy-cross 未安装，将上传源码（建议: pip install mpy-cross）")

    # 创建临时目录
    with tempfile.TemporaryDirectory() as tmpdir:
        upload_list = []

        # 1. 生成并编译 config.py
        config_py = os.path.join(tmpdir, "config.py")
        with open(config_py, "w", encoding="utf-8") as f:
            f.write(config_content)

        if use_mpy:
            config_mpy = os.path.join(tmpdir, "config.mpy")
            if compile_to_mpy(config_py, config_mpy):
                upload_list.append((config_mpy, "config.mpy"))
            else:
                upload_list.append((config_py, "config.py"))
        else:
            upload_list.append((config_py, "config.py"))

        # 2. 扫描 device/ 目录下所有 .py 文件（config.py 已动态生成，跳过）
        for fname in os.listdir(DEVICE_DIR):
            if not fname.endswith(".py"):
                continue
            if fname == "config.py":
                continue

            src = os.path.join(DEVICE_DIR, fname)
            if not os.path.isfile(src):
                continue

            # main.py 不编译，直接上传
            if fname == ENTRY_FILE:
                upload_list.append((src, fname))
                continue

            # 其他 .py 文件编译为 .mpy
            if use_mpy:
                mpy_name = fname.replace(".py", ".mpy")
                mpy_path = os.path.join(tmpdir, mpy_name)
                if compile_to_mpy(src, mpy_path):
                    upload_list.append((mpy_path, mpy_name))
                else:
                    # 编译失败，回退到源码
                    upload_list.append((src, fname))
            else:
                # 直接上传源码
                upload_list.append((src, fname))

        # 3. 上传所有文件
        for local_path, remote_name in upload_list:
            run_mpremote(["cp", local_path, f":{remote_name}"])
            size_kb = os.path.getsize(local_path) / 1024
            print(f"  ✓ {remote_name} ({size_kb:.1f} KB)")


def reset_device():
    """重启设备。"""
    print("[5/5] 重启设备...")
    run_mpremote(["reset"])
    print("  ✓ 设备已重启")


def main():
    print("=" * 50)
    print("ESP32 固件烧录工具（MAC 自动命名）")
    print("=" * 50)

    mac_suffix = get_mac_address()
    config_content = generate_config(mac_suffix)
    install_libs()
    upload_firmware(config_content)
    reset_device()

    print("\n" + "=" * 50)
    print(f"✓ 烧录完成！设备名称: Claude-Buddy-{mac_suffix}")
    print("=" * 50)


if __name__ == "__main__":
    main()
