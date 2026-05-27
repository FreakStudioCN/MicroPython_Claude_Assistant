#!/usr/bin/env python3
# scripts/flash_device.py —— ESP32 固件烧录工具
#
# 功能：
#   1. [可选] 用 esptool 烧录 MicroPython 底层固件（--flash-firmware）
#   2. 读取设备 MAC 地址，生成唯一 BLE_NAME
#   3. 编译并上传 device/*.py 到设备（支持 mpy-cross 字节码编译）
#   4. 安装 aioble 依赖库
#   5. 重启设备
#
# 用法：
#   python scripts/flash_device.py --variant clock                    # 仅上传代码
#   python scripts/flash_device.py --variant panel --flash-firmware   # 首次安装：烧固件+上传代码
#   python scripts/flash_device.py --variant clock --wipe             # 清空文件系统后上传

import subprocess
import sys
import os
import re
import time
import tempfile
import argparse
import serial.tools.list_ports
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE_DIR = os.path.join(ROOT, "device")
FIRMWARE_DIR = os.path.join(ROOT, "firmware")

# variant → 固件文件名前缀（匹配 firmware/ 目录下的 .bin）
_FIRMWARE_PREFIX = {
    "clock": "claude-buddy-clock-",
    "panel": "claude-buddy-panel-",
}


def flash_micropython_firmware(variant: str, port: str, step: str):
    """用 esptool 烧录 MicroPython 底层固件（.bin）。"""
    print(f"[{step}] 烧录 MicroPython 固件...")
    prefix = _FIRMWARE_PREFIX[variant]
    candidates = [f for f in os.listdir(FIRMWARE_DIR)
                  if f.startswith(prefix) and f.endswith(".bin")]
    if not candidates:
        print(f"[错误] firmware/ 目录下未找到 {prefix}*.bin")
        sys.exit(1)
    bin_path = os.path.join(FIRMWARE_DIR, sorted(candidates)[-1])  # 取版本最新的
    print(f"  → 固件文件: {os.path.basename(bin_path)}")

    # clock(ESP32-C3) 用默认参数，panel(ESP32-S3 PSRAM) 需指定 flash 模式/大小/频率
    if variant == "panel":
        cmd = [
            sys.executable, "-m", "esptool",
            "--chip", "esp32s3",
            "--port", port,
            "--baud", "460800",
            "--before", "default_reset",
            "--after", "hard_reset",
            "write_flash",
            "--flash_mode", "dio",
            "--flash_size", "16MB",
            "--flash_freq", "80m",
            "--erase-all",
            "0x0", bin_path,
        ]
    else:
        cmd = [
            sys.executable, "-m", "esptool",
            "--chip", "esp32c3",
            "--port", port,
            "--baud", "460800",
            "write_flash", "--erase-all", "-z", "0x0", bin_path,
        ]
    print(f"  → 执行: {' '.join(cmd[3:])}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("[错误] esptool 烧录失败")
        sys.exit(1)
    print(f"  → 固件烧录完成，请按下设备 RST 键重启，确认串口重新连接后按回车继续...")
    input()
    # 重新扫描串口（固件烧录后 USB 复位，端口号可能变化）
    new_port = select_com_port()
    print(f"  → 使用新串口: {new_port}")
    return new_port
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


def run_mpremote(cmd: list[str], timeout: int = 30) -> str:
    result = subprocess.run(
        ["mpremote", "connect", _COM_PORT] + cmd,
        capture_output=True, text=True, encoding="utf-8",
        timeout=timeout,
    )
    if result.returncode != 0:
        print(f"[错误] mpremote 命令失败: {' '.join(cmd)}")
        print(result.stderr)
        sys.exit(1)
    return result.stdout.strip()


def run_mpremote_safe(cmd: list[str]) -> Optional[str]:
    """运行 mpremote 命令，失败时返回 None 而不退出。"""
    try:
        result = subprocess.run(
            ["mpremote", "connect", _COM_PORT] + cmd,
            capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception as e:
        print(f"[警告] mpremote 执行异常: {e}")
        return None


def get_remote_filenames(remote_dir: str) -> set[str]:
    """获取设备端指定目录的文件名集合，目录不存在或出错返回空集合。"""
    output = run_mpremote_safe(["fs", "ls", f":{remote_dir}"])
    if output is None:
        return set()
    filenames = set()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        # 跳过 mpremote 输出的标题行，如 "ls :assets/" 或 "ls :"
        if line.startswith("ls "):
            continue
        # 输出格式: "      1234 filename.ext"，取最后一个字段
        parts = line.split()
        if len(parts) >= 2:
            filenames.add(parts[-1])
        elif len(parts) == 1:
            filenames.add(parts[0])
    return filenames


def assets_in_sync(local_assets_dir: str) -> bool:
    """比对本地和设备端 assets 文件名集合，完全一致返回 True。"""
    try:
        local_files = set(
            f for f in os.listdir(local_assets_dir)
            if os.path.isfile(os.path.join(local_assets_dir, f))
        )
    except Exception as e:
        print(f"[警告] 读取本地 assets 目录失败: {e}")
        return False

    remote_files = get_remote_filenames("assets")
    in_sync = local_files == remote_files

    if in_sync:
        print(f"  → 设备端 assets 与本地一致（{len(local_files)} 个文件），跳过上传")
    else:
        added = local_files - remote_files
        removed = remote_files - local_files
        if added:
            print(f"  → 本地新增: {', '.join(sorted(added))}")
        if removed:
            print(f"  → 设备端多余: {', '.join(sorted(removed))}")
    return in_sync


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
    except FileNotFoundError:
        print(f"[警告] mpy-cross 未找到，跳过编译: {src_path}")
        return False


def wipe_device(step: str):
    print(f"[{step}] 清空设备文件系统...")
    # ESP32 不允许 rm -r :/ ，改为先 ls 根目录再逐项删除
    try:
        result = subprocess.run(
            ["mpremote", "connect", _COM_PORT, "fs", "ls", ":"],
            capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode != 0:
            print(f"[警告] 获取根目录列表失败: {result.stderr.strip()}")
            return
        entries = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("ls "):
                continue
            parts = line.split()
            if parts:
                entries.append(parts[-1].rstrip("/"))
        if not entries:
            print("  → 设备文件系统已为空，跳过清空")
            return
        for entry in entries:
            print(f"  → 删除 :{entry} ...")
            r = subprocess.run(
                ["mpremote", "connect", _COM_PORT, "fs", "rm", "-r", f":{entry}"],
                capture_output=True, text=True, encoding="utf-8",
            )
            if r.returncode != 0:
                print(f"  [警告] 删除 :{entry} 失败: {r.stderr.strip()}")
            else:
                print(f"  ✓ 已删除 :{entry}")
        print("  ✓ 清空完成")
    except Exception as e:
        print(f"[错误] 清空设备失败: {e}")
        sys.exit(1)
    time.sleep(1)


def get_mac_address(step: str) -> str:
    print(f"[{step}] 读取设备 MAC 地址...")
    code = (
        "import bluetooth\n"
        "ble = bluetooth.BLE()\n"
        "ble.active(True)\n"
        "mac = ble.config('mac')[1]\n"
        "print(''.join(f'{b:02X}' for b in mac[-2:]))\n"
    )
    try:
        output = run_mpremote(["exec", code])
    except SystemExit:
        print("[错误] 读取 MAC 地址失败，请检查设备连接")
        sys.exit(1)
    mac_suffix = output.strip().split("\n")[-1]
    if len(mac_suffix) != 4 or not all(c in "0123456789ABCDEF" for c in mac_suffix):
        print(f"[错误] MAC 地址格式异常: {mac_suffix!r}")
        sys.exit(1)
    print(f"  → MAC 后4位: {mac_suffix}")
    return mac_suffix


def generate_config(mac_suffix: str, variant: str, character: str, step: str) -> str:
    print(f"[{step}] 生成 config.py...")
    ble_name = f"Claude-Buddy-{mac_suffix}"
    src = os.path.join(DEVICE_DIR, "config.py")
    try:
        with open(src, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"[错误] 找不到 config.py: {src}")
        sys.exit(1)
    except Exception as e:
        print(f"[错误] 读取 config.py 失败: {e}")
        sys.exit(1)
    content = re.sub(r'^BLE_NAME\s*=.*$', f'BLE_NAME = "{ble_name}"', content, flags=re.MULTILINE)
    content = re.sub(r'^VARIANT\s*=.*$', f'VARIANT = "{variant}"', content, flags=re.MULTILINE)
    content = re.sub(r'^CHARACTER\s*=.*$', f'CHARACTER = "{character}"', content, flags=re.MULTILINE)
    print(f"  → BLE_NAME = {ble_name!r}, VARIANT = {variant!r}, CHARACTER = {character!r}")
    return content


def install_libs(step: str):
    print(f"[{step}] 安装依赖库（aioble）...")
    try:
        run_mpremote(["mip", "install", "aioble"])
        print("  ✓ aioble 安装完成")
    except SystemExit:
        print("[错误] aioble 安装失败，请检查设备网络连接")
        sys.exit(1)


def upload_firmware(config_content: str, step: str, wiped: bool = False, character: str = "claude"):
    print(f"[{step}] 编译并上传固件文件...")
    use_mpy = check_mpy_cross()
    print(f"  {'✓ mpy-cross 可用' if use_mpy else '⚠ mpy-cross 未安装，上传源码'}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            upload_list = []

            # config.py
            config_py = os.path.join(tmpdir, "config.py")
            try:
                with open(config_py, "w", encoding="utf-8") as f:
                    f.write(config_content)
            except Exception as e:
                print(f"[错误] 写入临时 config.py 失败: {e}")
                sys.exit(1)

            if use_mpy:
                config_mpy = os.path.join(tmpdir, "config.mpy")
                if compile_to_mpy(config_py, config_mpy):
                    upload_list.append((config_mpy, "config.mpy"))
                else:
                    upload_list.append((config_py, "config.py"))
            else:
                upload_list.append((config_py, "config.py"))

            # device/*.py（char_*.py 只上传选中的那个）
            needed_char = f"char_{character}.py"
            try:
                py_files = [
                    f for f in os.listdir(DEVICE_DIR)
                    if f.endswith(".py") and f != "config.py"
                    and os.path.isfile(os.path.join(DEVICE_DIR, f))
                    and (not f.startswith("char_") or f == needed_char)
                ]
            except Exception as e:
                print(f"[错误] 读取设备目录失败: {e}")
                sys.exit(1)

            for fname in py_files:
                src = os.path.join(DEVICE_DIR, fname)
                if fname == ENTRY_FILE:
                    upload_list.append((src, fname))
                    continue
                if use_mpy:
                    mpy_name = fname.replace(".py", ".mpy")
                    mpy_path = os.path.join(tmpdir, mpy_name)
                    if compile_to_mpy(src, mpy_path):
                        upload_list.append((mpy_path, mpy_name))
                    else:
                        upload_list.append((src, fname))
                else:
                    upload_list.append((src, fname))

            for local_path, remote_name in upload_list:
                try:
                    run_mpremote(["cp", local_path, f":{remote_name}"])
                    print(f"  ✓ {remote_name} ({os.path.getsize(local_path)/1024:.1f} KB)")
                except SystemExit:
                    print(f"[错误] 上传失败: {remote_name}")
                    sys.exit(1)

            # lib/
            lib_dir = os.path.join(DEVICE_DIR, "lib")
            if os.path.isdir(lib_dir):
                subprocess.run(["mpremote", "connect", _COM_PORT, "fs", "mkdir", ":lib"], capture_output=True)
                try:
                    lib_files = sorted(
                        f for f in os.listdir(lib_dir)
                        if os.path.isfile(os.path.join(lib_dir, f)) and f.endswith(".py")
                    )
                except Exception as e:
                    print(f"[警告] 读取 lib 目录失败: {e}")
                    lib_files = []

                for fname in lib_files:
                    src = os.path.join(lib_dir, fname)
                    if use_mpy:
                        mpy_name = fname.replace(".py", ".mpy")
                        mpy_path = os.path.join(tmpdir, mpy_name)
                        if compile_to_mpy(src, mpy_path):
                            local, remote = mpy_path, f"lib/{mpy_name}"
                        else:
                            local, remote = src, f"lib/{fname}"
                    else:
                        local, remote = src, f"lib/{fname}"
                    try:
                        run_mpremote(["cp", local, f":{remote}"])
                        print(f"  ✓ {remote} ({os.path.getsize(local)/1024:.1f} KB)")
                    except SystemExit:
                        print(f"[错误] 上传失败: {remote}")
                        sys.exit(1)

            # assets/
            assets_dir = os.path.join(DEVICE_DIR, "assets")
            if os.path.isdir(assets_dir):
                print("  检查 assets 目录...")
                if not wiped and assets_in_sync(assets_dir):
                    pass  # 已在 assets_in_sync 内打印跳过提示
                else:
                    print("  → 开始上传 assets...")
                    subprocess.run(
                        ["mpremote", "connect", _COM_PORT, "fs", "mkdir", ":assets"],
                        capture_output=True,
                    )
                    try:
                        asset_files = sorted(
                            f for f in os.listdir(assets_dir)
                            if os.path.isfile(os.path.join(assets_dir, f))
                        )
                    except Exception as e:
                        print(f"[错误] 读取本地 assets 目录失败: {e}")
                        sys.exit(1)

                    # 逐文件上传
                    for fname in asset_files:
                        src = os.path.join(assets_dir, fname)
                        remote = f"assets/{fname}"
                        size_kb = os.path.getsize(src) / 1024
                        cp_timeout = max(120, int(size_kb / 5) + 30)
                        try:
                            run_mpremote(["cp", src, f":{remote}"], timeout=cp_timeout)
                            print(f"  ✓ {remote} ({size_kb:.1f} KB)")
                        except subprocess.TimeoutExpired:
                            print(f"[错误] 上传超时: {fname}（{size_kb:.1f} KB，超过 {cp_timeout}s）")
                            sys.exit(1)
                        except SystemExit:
                            print(f"[错误] 上传失败: {fname}")
                            sys.exit(1)

    except Exception as e:
        print(f"[错误] 上传固件过程中发生未预期异常: {e}")
        sys.exit(1)


def reset_device(step: str):
    print(f"[{step}] 重启设备...")
    try:
        subprocess.run(
            ["mpremote", "connect", _COM_PORT, "reset"],
            capture_output=True, text=True, encoding="utf-8",
        )
        print("  ✓ 设备已重启")
    except Exception as e:
        print(f"[警告] 重启设备失败: {e}")


def main():
    global _COM_PORT
    print("=" * 50)
    print("ESP32 固件烧录工具（MAC 自动命名）")
    print("=" * 50)

    parser = argparse.ArgumentParser(
        prog="flash_device.py",
        description="将 MicroPython 固件烧录到 ESP32 设备（panel 面板版 / clock 闹钟版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例：
  python scripts/flash_device.py                                    # 烧录面板版代码（默认）
  python scripts/flash_device.py --variant clock                    # 烧录闹钟版代码
  python scripts/flash_device.py --variant panel --wipe             # 面板版，先清空文件系统
  python scripts/flash_device.py --variant clock --flash-firmware   # 首次安装：先烧固件再上传代码

切换面板角色：
  1. 修改 device/config.py 中的 CHARACTER 字段：
       CHARACTER = "kirby"   # claude/cat/robot/ghost/among_us/creeper/kirby/pikachu
  2. 重新运行烧录命令，脚本自动读取并只上传对应角色文件

切换语音音色（clock 形态）：
  1. 运行 python scripts/gen_voice_assets.py 生成新 PCM 文件
  2. 文件自动保存到 device/assets/，重新烧录时一并上传

预览面板角色（无需设备）：
  pip install Pillow
  python scripts/preview_character.py                        # 生成所有角色预览图
  python scripts/preview_character.py --char kirby pikachu   # 只预览指定角色

参数说明：
  --variant          目标设备型号：panel（ESP32-S3 + 屏幕）| clock（ESP32-C3 + 灯光）
  --wipe             烧录前清空设备文件系统（首次烧录或切换 variant 时建议使用，不可恢复）
  --flash-firmware   首次安装时使用：先用 esptool 烧录 MicroPython 底层固件，再上传代码
        """,
    )
    parser.add_argument("--variant", choices=["panel", "clock"], default="panel",
                        help="目标型号：panel（面板版）| clock（闹钟版），默认 panel")
    parser.add_argument("--wipe", action="store_true",
                        help="烧录前清空设备文件系统（危险：不可恢复）")
    parser.add_argument("--flash-firmware", action="store_true",
                        help="首次安装：先用 esptool 烧录 MicroPython 底层固件，再上传代码")
    args = parser.parse_args()

    # 从本地 config.py 读取 CHARACTER
    config_src = os.path.join(DEVICE_DIR, "config.py")
    with open(config_src, "r", encoding="utf-8") as f:
        _cfg_text = f.read()
    _m = re.search(r'^CHARACTER\s*=\s*["\']?(\w+)["\']?', _cfg_text, re.MULTILINE)
    character = _m.group(1) if _m else "claude"
    print(f"  → 角色: {character}（来自 config.py）")

    try:
        _COM_PORT = select_com_port()
    except Exception as e:
        print(f"[错误] 串口选择失败: {e}")
        sys.exit(1)
    print(f"  → 使用串口: {_COM_PORT}")

    step = 0

    if args.flash_firmware:
        _COM_PORT = flash_micropython_firmware(args.variant, _COM_PORT, str(step))
        step += 1

    if args.wipe:
        wipe_device(str(step))
        step += 1

    mac_suffix = get_mac_address(str(step)); step += 1
    config_content = generate_config(mac_suffix, args.variant, character, str(step)); step += 1
    install_libs(str(step)); step += 1
    upload_firmware(config_content, str(step), wiped=args.wipe, character=character); step += 1
    reset_device(str(step))

    print("\n" + "=" * 50)
    print(f"✓ 烧录完成！设备名称: Claude-Buddy-{mac_suffix}  型号: {args.variant}  角色: {character}")
    print("=" * 50)


if __name__ == "__main__":
    main()
