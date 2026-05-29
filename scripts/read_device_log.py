#!/usr/bin/env python3
"""读取设备端循环日志（按时间顺序）

用法：
    python scripts/read_device_log.py              # 读取所有日志
    python scripts/read_device_log.py --tail 50    # 只显示最后 50 行
    python scripts/read_device_log.py --clear      # 删除所有日志文件
"""

import subprocess
import sys
import argparse
from typing import Optional

_PORT: str = ""


def _mpremote(cmd: list[str], **kwargs):
    """运行 mpremote，自动插入 --port 参数。"""
    full = ["mpremote"]
    if _PORT:
        full += ["connect", _PORT]
    return subprocess.run(full + cmd, **kwargs)


def get_file_mtime(index: int) -> Optional[int]:
    """获取日志文件的 mtime，文件不存在返回 None"""
    result = _mpremote(
        ["exec", f"import os; print(os.stat('/log/run_{index}.log')[8])"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        try:
            return int(result.stdout.strip().split("\n")[-1])
        except ValueError:
            return None
    return None


def read_file_content(index: int) -> str:
    """读取日志文件内容"""
    result = _mpremote(
        ["fs", "cat", f":/log/run_{index}.log"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout
    return ""


def delete_log_file(index: int) -> bool:
    """删除单个日志文件，返回是否成功"""
    result = _mpremote(
        ["exec",
         f"try:\n import os; os.remove('/log/run_{index}.log'); print('ok')\n"
         f"except Exception as e: print('err', e)"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and "ok" in result.stdout


def clear_all_logs():
    """删除所有设备端日志文件"""
    found = False
    for i in range(8):
        mtime = get_file_mtime(i)
        if mtime is not None:
            found = True
            if delete_log_file(i):
                print(f"[info] 已删除 /log/run_{i}.log")
            else:
                print(f"[error] 删除失败 /log/run_{i}.log", file=sys.stderr)
    if not found:
        print("[info] 没有日志文件需要删除")


def read_logs(port: str = "", tail: Optional[int] = None) -> str:
    """以字符串形式返回设备日志（可被 GUI 直接调用）。"""
    global _PORT
    _PORT = port

    files = []
    for i in range(8):
        mtime = get_file_mtime(i)
        if mtime is not None:
            files.append((i, mtime))

    if not files:
        return "[错误] 未找到任何日志文件"

    files.sort(key=lambda x: x[1])
    all_lines = []
    for i, mtime in files:
        content = read_file_content(i)
        if content:
            all_lines.extend(content.splitlines())

    if tail:
        all_lines = all_lines[-tail:]

    return "\n".join(all_lines)


def clear_device_logs(port: str = ""):
    """清除设备端所有日志文件。"""
    global _PORT
    _PORT = port
    clear_all_logs()


def main():
    parser = argparse.ArgumentParser(description="读取设备端循环日志")
    parser.add_argument("--port", default="", help="串口号（如 COM81），默认自动检测")
    parser.add_argument("--tail", type=int, help="只显示最后 N 行")
    parser.add_argument("--clear", action="store_true", help="删除所有设备端日志文件")
    args = parser.parse_args()

    if args.clear:
        clear_device_logs(args.port)
        return

    output = read_logs(args.port, args.tail)
    print(output)


if __name__ == "__main__":
    main()
