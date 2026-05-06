#!/usr/bin/env python3
"""
简单的 Hook 超时测试

这个脚本会创建一个测试 hook，然后你可以在 Claude Code 中执行 Bash 命令来观察行为。
"""

import json
import sys
import time
from pathlib import Path

def create_simple_test():
    """创建一个简单的测试场景"""

    test_dir = Path(__file__).parent

    # 创建 3 个测试 hook
    hooks = {
        "instant": 0,      # 立即返回
        "delay_5s": 5,     # 延迟 5 秒
        "delay_30s": 30,   # 延迟 30 秒
    }

    for name, delay in hooks.items():
        hook_path = test_dir / f"_hook_{name}.py"
        log_path = test_dir / f"_hook_{name}.log"

        # 清空日志
        if log_path.exists():
            log_path.unlink()

        hook_code = f'''#!/usr/bin/env python3
import json
import sys
import time

# 读取输入
raw = sys.stdin.read()
event = json.loads(raw) if raw else {{}}

hook_name = event.get("hook_event_name", "")
tool_name = event.get("tool_name", "")
cmd = event.get("tool_input", {{}}).get("command", "")

# 记录日志
log_path = r"{log_path}"
with open(log_path, "a", encoding="utf-8") as f:
    f.write(f"{{time.time():.3f}} START | {{hook_name}} | {{tool_name}}\\n")
    if cmd:
        f.write(f"  Command: {{cmd}}\\n")

# 如果是 PreToolUse + Bash，延迟后返回
if hook_name == "PreToolUse" and tool_name == "Bash":
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"  Sleeping {delay}s...\\n")

    time.sleep({delay})

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{{time.time():.3f}} DONE (after {delay}s)\\n")

# 返回空（允许执行）
print(json.dumps({{}}))
'''

        hook_path.write_text(hook_code, encoding="utf-8")
        print(f"✓ 创建测试 hook: {hook_path.name} (延迟 {delay}s)")

    print("\n" + "="*60)
    print("测试步骤：")
    print("="*60)

    for name, delay in hooks.items():
        hook_path = test_dir / f"_hook_{name}.py"
        log_path = test_dir / f"_hook_{name}.log"

        print(f"\n{name.upper()} (延迟 {delay}s):")
        print(f"1. 手动编辑 ~/.claude/settings.json，添加：")
        print(f'''
   "hooks": {{
     "PreToolUse": [{{
       "hooks": [{{
         "type": "command",
         "command": "python {hook_path.absolute()}"
       }}]
     }}]
   }}
''')
        print(f"2. 在 Claude Code 中执行：! echo 'test {name}'")
        print(f"3. 观察命令是否执行，以及延迟时间")
        print(f"4. 查看日志：{log_path}")


def test_current_hook():
    """测试当前项目的 hook_bridge.py"""

    print("\n" + "="*60)
    print("测试当前项目的 hook_bridge.py")
    print("="*60)

    # 模拟一个 PreToolUse 事件
    test_event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_use_id": "test_123",
        "tool_input": {
            "command": "echo 'test command'",
            "description": "Test command"
        },
        "session_id": "test_session",
        "cwd": "/test",
        "transcript_path": "/test/transcript.jsonl",
        "permission_mode": "default"
    }

    print("\n发送测试事件到 hook_bridge.py...")
    print(json.dumps(test_event, indent=2))

    # 检查 daemon 是否运行
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 57320), timeout=1) as s:
            print("\n✓ ble_daemon 正在运行")

            # 发送测试事件
            s.sendall(json.dumps({
                "type": "event",
                "v": 2,
                "event": {
                    "kind": "tool_start",
                    "tool": "Bash",
                    "tool_category": "exec",
                    "summary": "echo 'test'",
                    "needs_approval": True,
                    "tool_use_id": "test_123",
                    "risk_level": "normal"
                },
                "generic": {
                    "session_id": "test_session",
                    "cwd": "/test",
                    "hook_event_name": "PreToolUse",
                    "transcript_path": "",
                    "permission_mode": "default"
                }
            }).encode("utf-8"))
            s.shutdown(socket.SHUT_WR)

            print("\n等待 daemon 响应...")
            start = time.time()

            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk

            elapsed = time.time() - start

            if buf:
                resp = json.loads(buf.decode("utf-8"))
                print(f"\n✓ 收到响应 (耗时 {elapsed:.1f}s):")
                print(json.dumps(resp, indent=2))
            else:
                print(f"\n✗ 无响应 (超时 {elapsed:.1f}s)")

    except ConnectionRefusedError:
        print("\n✗ ble_daemon 未运行")
        print("请先启动：python daemon/ble_daemon.py --stub")
    except socket.timeout:
        print("\n✗ 连接超时")


def main():
    print("Claude Code Hook 超时测试")
    print("="*60)

    print("\n选择测试模式：")
    print("1. 创建测试 hook 文件（需要手动配置 settings.json）")
    print("2. 测试当前项目的 hook_bridge.py（需要 daemon 运行）")

    choice = input("\n请选择 (1/2): ").strip()

    if choice == "1":
        create_simple_test()
    elif choice == "2":
        test_current_hook()
    else:
        print("无效选择")


if __name__ == "__main__":
    main()
