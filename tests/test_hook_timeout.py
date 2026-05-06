#!/usr/bin/env python3
"""
测试 Claude Code Hook 的超时行为

用法：
1. 备份 ~/.claude/settings.json
2. 运行此脚本：python tests/test_hook_timeout.py
3. 脚本会自动配置 hook 并启动测试
4. 观察不同阻塞时间下 Claude Code 的行为

测试场景：
- 场景 1：Hook 立即返回 {} → 工具应该执行
- 场景 2：Hook 阻塞 5 秒后返回 {} → 工具应该执行（延迟 5s）
- 场景 3：Hook 阻塞 30 秒后返回 {} → 工具应该执行（延迟 30s）
- 场景 4：Hook 阻塞 90 秒后返回 {} → 观察 Claude Code 是否超时
- 场景 5：Hook 立即返回 {"decision": "block"} → 工具应该被拒绝
"""

import json
import sys
import time
import os
from pathlib import Path

def create_test_hook(delay_seconds: int, should_block: bool = False):
    """创建一个测试 hook 脚本"""
    hook_path = Path(__file__).parent / f"_test_hook_delay_{delay_seconds}.py"

    hook_code = f'''#!/usr/bin/env python3
import json
import sys
import time

# 读取 hook 输入
raw = sys.stdin.read()
event = json.loads(raw) if raw else {{}}

hook_name = event.get("hook_event_name", "")
tool_name = event.get("tool_name", "")

# 记录日志
log_path = r"{Path(__file__).parent / f"_hook_log_delay_{delay_seconds}.txt"}"
with open(log_path, "a", encoding="utf-8") as f:
    f.write(f"{{time.time():.3f}} | {{hook_name}} | {{tool_name}} | delay={delay_seconds}s\\n")

# 模拟审批延迟
if hook_name == "PreToolUse" and tool_name == "Bash":
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"  → Sleeping {{delay_seconds}}s...\\n")

    time.sleep({delay_seconds})

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"  → Woke up at {{time.time():.3f}}\\n")

    {"print(json.dumps({'decision': 'block', 'reason': 'Test block'}))" if should_block else "print(json.dumps({}))"}
else:
    print(json.dumps({{}}))
'''

    hook_path.write_text(hook_code, encoding="utf-8")
    return hook_path


def get_settings_path():
    """获取 Claude Code settings.json 路径"""
    if sys.platform == "win32":
        base = Path(os.environ.get("USERPROFILE", "~"))
    else:
        base = Path.home()
    return base / ".claude" / "settings.json"


def backup_settings():
    """备份当前 settings.json"""
    settings_path = get_settings_path()
    if settings_path.exists():
        backup_path = settings_path.with_suffix(".json.backup")
        backup_path.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"✓ 已备份 settings.json → {backup_path}")
        return True
    return False


def install_test_hook(hook_path: Path):
    """安装测试 hook 到 settings.json"""
    settings_path = get_settings_path()

    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        settings = {}

    if "hooks" not in settings:
        settings["hooks"] = {}

    # 只 hook PreToolUse（审批场景）
    settings["hooks"]["PreToolUse"] = [{
        "hooks": [{
            "type": "command",
            "command": f"python {hook_path.absolute()}"
        }]
    }]

    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ 已安装测试 hook: {hook_path.name}")


def restore_settings():
    """恢复原始 settings.json"""
    settings_path = get_settings_path()
    backup_path = settings_path.with_suffix(".json.backup")

    if backup_path.exists():
        settings_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
        backup_path.unlink()
        print(f"✓ 已恢复 settings.json")


def run_test_scenario(delay_seconds: int, should_block: bool = False):
    """运行单个测试场景"""
    print(f"\n{'='*60}")
    print(f"测试场景：Hook 阻塞 {delay_seconds} 秒" + (" + 返回 block" if should_block else ""))
    print(f"{'='*60}")

    # 创建测试 hook
    hook_path = create_test_hook(delay_seconds, should_block)

    # 清空日志
    log_path = Path(__file__).parent / f"_hook_log_delay_{delay_seconds}.txt"
    if log_path.exists():
        log_path.unlink()

    # 安装 hook
    install_test_hook(hook_path)

    print(f"\n请在 Claude Code 中执行以下命令：")
    print(f"  ! echo 'test delay {delay_seconds}s'")
    print(f"\n观察：")
    print(f"  1. 命令是否执行？")
    print(f"  2. 延迟了多久？")
    print(f"  3. 是否有超时错误？")
    print(f"\nHook 日志将写入：{log_path}")
    print(f"\n按 Enter 继续下一个测试...")
    input()

    # 显示日志
    if log_path.exists():
        print(f"\n--- Hook 日志 ---")
        print(log_path.read_text(encoding="utf-8"))

    # 清理
    hook_path.unlink()


def run_interactive_test():
    """交互式测试：手动控制审批时间"""
    print(f"\n{'='*60}")
    print(f"交互式测试：手动审批")
    print(f"{'='*60}")

    hook_path = Path(__file__).parent / "_test_hook_interactive.py"
    log_path = Path(__file__).parent / "_hook_log_interactive.txt"

    hook_code = '''#!/usr/bin/env python3
import json
import sys
import time

raw = sys.stdin.read()
event = json.loads(raw) if raw else {}

hook_name = event.get("hook_event_name", "")
tool_name = event.get("tool_name", "")

log_path = r"''' + str(log_path) + '''"
with open(log_path, "a", encoding="utf-8") as f:
    f.write(f"{time.time():.3f} | {hook_name} | {tool_name}\\n")

if hook_name == "PreToolUse" and tool_name == "Bash":
    cmd = event.get("tool_input", {}).get("command", "")

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"  Command: {cmd}\\n")
        f.write(f"  Waiting for approval...\\n")

    # 等待用户在终端输入
    print("\\n" + "="*60, file=sys.stderr)
    print(f"APPROVAL REQUIRED", file=sys.stderr)
    print(f"Command: {cmd}", file=sys.stderr)
    print("="*60, file=sys.stderr)
    print("Enter 'y' to approve, 'n' to deny: ", file=sys.stderr, end="")
    sys.stderr.flush()

    start = time.time()
    try:
        choice = input().strip().lower()
        elapsed = time.time() - start

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"  User input: {choice} (after {elapsed:.1f}s)\\n")

        if choice == 'y':
            print(json.dumps({}))
        else:
            print(json.dumps({"decision": "block", "reason": "User denied"}))
    except Exception as e:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"  Error: {e}\\n")
        print(json.dumps({}))
else:
    print(json.dumps({}))
'''

    hook_path.write_text(hook_code, encoding="utf-8")

    if log_path.exists():
        log_path.unlink()

    install_test_hook(hook_path)

    print(f"\n已安装交互式 hook")
    print(f"\n请在 Claude Code 中执行 Bash 命令，例如：")
    print(f"  ! echo 'interactive test'")
    print(f"\n当 hook 提示审批时，你可以：")
    print(f"  - 立即输入 y/n → 测试快速审批")
    print(f"  - 等待 30 秒再输入 → 测试慢速审批")
    print(f"  - 等待 90 秒再输入 → 测试超长审批")
    print(f"\nHook 日志：{log_path}")
    print(f"\n按 Enter 结束测试...")
    input()

    if log_path.exists():
        print(f"\n--- Hook 日志 ---")
        print(log_path.read_text(encoding="utf-8"))

    hook_path.unlink()


def main():
    print("Claude Code Hook 超时行为测试")
    print("="*60)

    # 检查 settings.json
    settings_path = get_settings_path()
    if not settings_path.parent.exists():
        print(f"错误：找不到 Claude Code 配置目录：{settings_path.parent}")
        print(f"请确保已安装 Claude Code")
        return

    print(f"Settings 路径：{settings_path}")

    # 备份
    if settings_path.exists():
        backup_settings()

    try:
        print("\n选择测试模式：")
        print("1. 自动测试（预设延迟：0s, 5s, 30s, 90s）")
        print("2. 交互式测试（手动控制审批时间）")
        print("3. 快速验证（仅测试 block 功能）")

        choice = input("\n请选择 (1/2/3): ").strip()

        if choice == "1":
            # 自动测试
            for delay in [0, 5, 30, 90]:
                run_test_scenario(delay)

        elif choice == "2":
            # 交互式测试
            run_interactive_test()

        elif choice == "3":
            # 快速验证
            run_test_scenario(0, should_block=False)
            run_test_scenario(0, should_block=True)

        else:
            print("无效选择")

    finally:
        # 恢复设置
        restore_settings()

        # 清理临时文件
        for f in Path(__file__).parent.glob("_test_hook_*.py"):
            f.unlink()

        print(f"\n✓ 测试完成，已恢复原始配置")


if __name__ == "__main__":
    main()
