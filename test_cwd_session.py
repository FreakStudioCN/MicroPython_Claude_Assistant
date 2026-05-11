#!/usr/bin/env python3
# test_cwd_session.py - 测试 cwd 能否区分 session
# 用法: 在不同目录下运行 Claude Code，触发 hook，观察 cwd 字段

import json
import sys
import os

# 模拟从 stdin 读取 hook event
if len(sys.argv) > 1:
    # 从命令行参数读取 JSON 文件（用于测试）
    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        event = json.load(f)
else:
    # 从 stdin 读取（真实 hook 场景）
    raw = sys.stdin.read().strip()
    if not raw:
        print("No input")
        sys.exit(1)
    event = json.loads(raw)

# 提取关键字段
session_id = event.get("session_id", "")
cwd = event.get("cwd", "")
hook_name = event.get("hook_event_name", "")

# 从 cwd 提取最后一段目录名
cwd_basename = os.path.basename(cwd) if cwd else ""

print(f"Hook: {hook_name}")
print(f"Session ID: {session_id}")
print(f"CWD: {cwd}")
print(f"CWD basename: {cwd_basename}")
print(f"\n可用于区分 session: {cwd_basename or session_id}")
