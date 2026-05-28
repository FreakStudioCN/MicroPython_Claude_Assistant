#!/usr/bin/env python3
"""读取设备端循环日志（按时间顺序）

用法：
    python scripts/read_device_log.py              # 读取所有日志
    python scripts/read_device_log.py --tail 50    # 只显示最后 50 行
"""

import subprocess
import sys
import argparse
from typing import Optional


def get_file_mtime(index: int) -> Optional[int]:
    """获取日志文件的 mtime，文件不存在返回 None"""
    result = subprocess.run(
        ["mpremote", "exec", f"import os; print(os.stat('/log/run_{index}.log')[8])"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        try:
            return int(result.stdout.strip().split("\n")[-1])
        except ValueError:
            return None
    return None


def read_file_content(index: int) -> str:
    """读取日志文件内容"""
    result = subprocess.run(
        ["mpremote", "fs", "cat", f":/log/run_{index}.log"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout
    return ""


def main():
    parser = argparse.ArgumentParser(description="读取设备端循环日志")
    parser.add_argument("--tail", type=int, help="只显示最后 N 行")
    args = parser.parse_args()

    # 获取所有日志文件的 mtime
    files = []
    for i in range(8):  # 最多支持 8 个文件
        mtime = get_file_mtime(i)
        if mtime is not None:
            files.append((i, mtime))

    if not files:
        print("[错误] 未找到任何日志文件")
        sys.exit(1)

    # 按 mtime 排序（从旧到新）
    files.sort(key=lambda x: x[1])

    print(f"[info] 找到 {len(files)} 个日志文件，按时间顺序读取...")

    # 按顺序读取并合并
    all_lines = []
    for i, mtime in files:
        content = read_file_content(i)
        if content:
            lines = content.splitlines()
            all_lines.extend(lines)
            print(f"[info] /log/run_{i}.log: {len(lines)} 行", file=sys.stderr)

    # 输出
    if args.tail:
        all_lines = all_lines[-args.tail :]

    for line in all_lines:
        print(line)

    print(f"\n[info] 总计 {len(all_lines)} 行", file=sys.stderr)


if __name__ == "__main__":
    main()
