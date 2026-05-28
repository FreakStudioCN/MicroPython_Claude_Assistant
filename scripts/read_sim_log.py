#!/usr/bin/env python3
"""读取 PC 端 sim_device 循环日志（按时间顺序）

用法：
    python scripts/read_sim_log.py              # 读取所有日志
    python scripts/read_sim_log.py --tail 50    # 只显示最后 50 行
"""

import os
import sys
import argparse
from typing import Optional


def get_file_mtime(path: str) -> Optional[int]:
    """获取日志文件的 mtime，文件不存在返回 None"""
    try:
        return int(os.stat(path).st_mtime)
    except OSError:
        return None


def read_file_content(path: str) -> str:
    """读取日志文件内容"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def main():
    parser = argparse.ArgumentParser(description="读取 PC 端 sim_device 循环日志")
    parser.add_argument("--tail", type=int, help="只显示最后 N 行")
    args = parser.parse_args()

    # 日志目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, "sim_device", "logs")

    if not os.path.isdir(log_dir):
        print(f"[错误] 日志目录不存在: {log_dir}")
        sys.exit(1)

    # 获取所有日志文件的 mtime
    files = []
    for i in range(8):  # 最多支持 8 个文件
        path = os.path.join(log_dir, f"sim_device_{i}.log")
        mtime = get_file_mtime(path)
        if mtime is not None:
            files.append((i, mtime, path))

    if not files:
        print("[错误] 未找到任何日志文件")
        sys.exit(1)

    # 按 mtime 排序（从旧到新）
    files.sort(key=lambda x: x[1])

    print(f"[info] 找到 {len(files)} 个日志文件，按时间顺序读取...", file=sys.stderr)

    # 按顺序读取并合并
    all_lines = []
    for i, mtime, path in files:
        content = read_file_content(path)
        if content:
            lines = content.splitlines()
            all_lines.extend(lines)
            print(f"[info] sim_device_{i}.log: {len(lines)} 行", file=sys.stderr)

    # 输出
    if args.tail:
        all_lines = all_lines[-args.tail :]

    for line in all_lines:
        print(line)

    print(f"\n[info] 总计 {len(all_lines)} 行", file=sys.stderr)


if __name__ == "__main__":
    main()
