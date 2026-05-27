#!/usr/bin/env python3
"""扫描项目所有 PC 端 .py 文件的第三方 import，更新 pyproject.toml [dependencies]。"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# MicroPython 专属模块（设备端，不算 PC 依赖）
_MICROPYTHON_MODULES = {
    "machine", "micropython", "aioble", "bluetooth", "network",
    "uasyncio", "ujson", "utime", "uos", "ubinascii", "ustruct",
    "lcd_bus", "lvgl", "lv", "esp32", "neopixel",
}

# 标准库模块（Python 3.11+）
_STDLIB = {
    "os", "sys", "re", "time", "json", "asyncio", "subprocess", "tempfile",
    "argparse", "pathlib", "typing", "threading", "socket", "struct",
    "hashlib", "base64", "io", "abc", "enum", "math", "random", "copy",
    "functools", "itertools", "collections", "contextlib", "dataclasses",
    "datetime", "logging", "signal", "shutil", "glob", "stat", "traceback",
    "unittest", "http", "urllib", "email", "html", "xml", "csv", "sqlite3",
    "queue", "weakref", "gc", "platform", "inspect", "textwrap", "string",
    "codecs", "locale", "getpass", "pprint", "warnings", "builtins", "types",
    "operator", "heapq", "bisect", "array", "pickle", "zipfile", "tarfile",
    "gzip", "bz2", "lzma", "zlib", "hmac", "secrets", "ssl", "select",
    "selectors", "errno", "ctypes", "multiprocessing", "concurrent",
    "tkinter", "unittest", "importlib", "tokenize", "ast", "dis",
    "uuid", "wave", "webbrowser", "winsound",
}

# 项目内部模块（不是第三方包）
_LOCAL_MODULES = {
    "ble_daemon", "hook_bridge", "protocol", "config", "state", "transport",
    "character", "risk_config",
    "char_among_us", "char_cat", "char_creeper", "char_ghost",
    "char_kirby", "char_pikachu", "char_robot",
}

# import 名 → pyproject.toml 包名 + 最低版本
_IMPORT_TO_PKG = {
    "bleak":              "bleak>=0.21",
    "serial":             "pyserial>=3.5",
    "websockets":         "websockets>=12.0",
    "PIL":                "Pillow>=10.0",
    "mpremote":           "mpremote>=1.22",
    "esptool":            "esptool>=4.7",
    "volcengine_tts_v1_ws": "volcengine-tts-v1-ws>=0.1",
}

# 扫描目录（只扫 PC 端代码，device/ 是 MicroPython 固件跳过）
_SCAN_DIRS = ["daemon", "scripts", "tests"]

# 通过命令行调用（无 import）但仍需 pip 安装的工具包
_FORCED_DEPS = [
    "mpremote>=1.22",
    "esptool>=4.7",
]


def collect_imports(root: str, scan_dirs: list) -> set:
    imports = set()
    for d in scan_dirs:
        dirpath = os.path.join(root, d)
        if not os.path.isdir(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                tree = ast.parse(open(fpath, encoding="utf-8").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
    return imports


def third_party(imports: set) -> set:
    return {m for m in imports
            if m not in _STDLIB and m not in _MICROPYTHON_MODULES
            and m not in _LOCAL_MODULES and not m.startswith("_")}


def resolve_deps(third: set) -> list:
    deps = []
    for m in sorted(third):
        if m in _IMPORT_TO_PKG:
            deps.append(_IMPORT_TO_PKG[m])
        else:
            print(f"[warn] 未知第三方模块 '{m}'，跳过（可手动加入 _IMPORT_TO_PKG）")
    return sorted(set(deps) | set(_FORCED_DEPS))


def update_pyproject(root: str, deps: list):
    path = os.path.join(root, "pyproject.toml")
    text = open(path, encoding="utf-8").read()
    new_block = "dependencies = [\n" + "".join(f'    "{d}",\n' for d in deps) + "]"
    updated = re.sub(r"dependencies\s*=\s*\[.*?\]", new_block, text, flags=re.DOTALL)
    if updated == text:
        print("[deps] pyproject.toml 无变化")
        return False
    open(path, "w", encoding="utf-8").write(updated)
    print(f"[deps] 更新 pyproject.toml dependencies ({len(deps)} 个包)")
    return True


def main():
    imports = collect_imports(ROOT, _SCAN_DIRS)
    third = third_party(imports)
    deps = resolve_deps(third)
    changed = update_pyproject(ROOT, deps)
    if changed:
        print("\n".join(f"  {d}" for d in deps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
