# setup_tool/builder.py — PyInstaller 打包构建脚本
#
# 用法: python -m setup_tool.builder
# 输出: dist/Claude_Assistant_Setup.exe

import subprocess, sys, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def esptool_data_args():
    """返回 esptool 数据文件（烧录存根 JSON）的 --add-data 参数列表。"""
    import esptool
    src = os.path.join(os.path.dirname(esptool.__file__), "targets", "stub_flasher")
    dst = os.path.join("esptool", "targets", "stub_flasher")
    sep = ";" if sys.platform == "win32" else ":"
    return ["--add-data", f"{src}{sep}{dst}"]


def main():
    entry = os.path.join(ROOT, "setup_tool", "__main__.py")
    dist = os.path.join(ROOT, "dist")
    build = os.path.join(ROOT, "build")

    sep = ";" if sys.platform == "win32" else ":"

    ico = os.path.join(ROOT, "setup_tool", "app.ico")
    if not os.path.isfile(ico):
        ico = None

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--onedir",
        "--name", "Claude_Assistant_Setup",
        *(["--icon", ico] if ico else []),
        "--distpath", dist,
        "--workpath", build,
        "--specpath", ROOT,
        # Hidden imports  (工具链)
        "--hidden-import", "esptool",
        "--hidden-import", "mpremote",
        "--hidden-import", "serial.tools.list_ports",
        "--hidden-import", "bleak",
        "--hidden-import", "PIL",
        # 子模块（原 subprocess 调用，现改为 import 方式）
        "--hidden-import", "scripts.preview_character",
        "--hidden-import", "scripts.read_device_log",
        "--hidden-import", "scripts.gen_voice_assets",
        # 数据文件 (固件 + 设备代码 + 语音)
        "--add-data", f"firmware{sep}firmware",
        "--add-data", f"device{sep}device",
        "--add-data", f"device{os.sep}lib{os.sep}aioble{sep}device{os.sep}lib{os.sep}aioble",
        "--add-data", f"device{os.sep}assets{sep}device{os.sep}assets",
        # esptool 烧录存根数据（JSON 配置文件）
        *esptool_data_args(),
        # 入口
        entry,
    ]

    print(f"构建 Claude_Assistant_Setup.exe ...")
    print(f"  PyInstaller: {sys.executable} -m PyInstaller")
    print(f"  入口: {entry}")
    print(f"  输出: {os.path.join(dist, 'Claude_Assistant_Setup.exe')}")
    print()

    result = subprocess.run(cmd, cwd=ROOT)

    if result.returncode == 0:
        print(f"\n== 构建成功！输出: {os.path.join(dist, 'Claude_Assistant_Setup.exe')} ==")
    else:
        print(f"\n== 构建失败 (code={result.returncode}) ==")
        sys.exit(1)


if __name__ == "__main__":
    main()
