#!/usr/bin/env python3
# tests/test_offline_approval.py
# 设备离线时的分层审批策略测试
#
# 跑法: python tests/test_offline_approval.py

import sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))

import asyncio
import ble_daemon as d

# 导入 hook_bridge 的风险分级函数
from hook_bridge import _classify_risk


def _assert(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)


def _reset():
    d._sessions.clear()
    d._dirty = False
    d._stub = True
    d._device_online = False  # 模拟设备离线


def _g():
    return {
        "session_id": "s", "cwd": "/x", "transcript_path": "/x.j",
        "hook_event_name": "PreToolUse", "permission_mode": "auto",
    }


def _env_pre(tool, summary="", needs_approval=True, tool_use_id="t1", category="exec", risk_level="normal"):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_start", "tool": tool, "summary": summary,
                      "needs_approval": needs_approval, "tool_use_id": tool_use_id,
                      "tool_category": category, "risk_level": risk_level},
            "generic": _g()}


# ── 风险分级测试 ──────────────────────────────────────────
def test_classify_risk_safe():
    """只读工具 → safe"""
    _assert(_classify_risk("Read", {"file_path": "/etc/hosts"}) == "safe", "Read should be safe")
    _assert(_classify_risk("Glob", {"pattern": "*.py"}) == "safe", "Glob should be safe")
    _assert(_classify_risk("Grep", {"pattern": "TODO"}) == "safe", "Grep should be safe")
    _assert(_classify_risk("WebFetch", {"url": "https://example.com"}) == "safe", "WebFetch should be safe")
    print("  ok  safe tools: Read/Glob/Grep/WebFetch")


def test_classify_risk_normal():
    """普通写操作 → normal"""
    _assert(_classify_risk("Bash", {"command": "ls -la"}) == "normal", "ls should be normal")
    _assert(_classify_risk("Write", {"file_path": "test.py", "content": "..."}) == "normal", "Write test.py should be normal")
    _assert(_classify_risk("Edit", {"file_path": "main.py", "old_string": "a", "new_string": "b"}) == "normal", "Edit main.py should be normal")
    print("  ok  normal tools: Bash(ls)/Write/Edit")


def test_classify_risk_critical_bash():
    """破坏性 Bash 命令 → critical"""
    _assert(_classify_risk("Bash", {"command": "git branch -D feature"}) == "critical", "git branch -D should be critical")
    _assert(_classify_risk("Bash", {"command": "git push --force origin main"}) == "critical", "git push --force should be critical")
    _assert(_classify_risk("Bash", {"command": "rm -rf /tmp/old"}) == "critical", "rm -rf should be critical")
    _assert(_classify_risk("Bash", {"command": "dd if=/dev/zero of=/dev/sda"}) == "critical", "dd should be critical")
    print("  ok  critical Bash: git branch -D / git push --force / rm -rf / dd")


def test_classify_risk_critical_paths():
    """关键路径写入 → critical"""
    _assert(_classify_risk("Write", {"file_path": ".git/config", "content": "..."}) == "critical", ".git/config should be critical")
    _assert(_classify_risk("Edit", {"file_path": ".env", "old_string": "a", "new_string": "b"}) == "critical", ".env should be critical")
    _assert(_classify_risk("Write", {"file_path": "credentials.json", "content": "..."}) == "critical", "credentials.json should be critical")
    print("  ok  critical paths: .git/config / .env / credentials.json")


# ── 离线审批逻辑测试 ──────────────────────────────────────
async def test_offline_safe_auto_approve():
    """设备离线 + safe 工具 → 自动批准"""
    _reset()
    d._stub = False  # 关闭 stub 模式，测试真实离线逻辑
    env = _env_pre("Read", "/etc/hosts", needs_approval=True, tool_use_id="t1", category="read", risk_level="safe")
    resp = await d._handle_envelope(env)
    _assert(resp.get("decision") == "once", f"safe tool offline should auto-approve, got {resp}")
    print("  ok  offline + safe → auto-approve")


async def test_offline_normal_auto_approve():
    """设备离线 + normal 工具 → 自动批准"""
    _reset()
    d._stub = False  # 关闭 stub 模式，测试真实离线逻辑
    env = _env_pre("Bash", "ls -la", needs_approval=True, tool_use_id="t1", category="exec", risk_level="normal")
    resp = await d._handle_envelope(env)
    _assert(resp.get("decision") == "once", f"normal tool offline should auto-approve, got {resp}")
    print("  ok  offline + normal → auto-approve")


async def test_online_approval_flow():
    """设备在线 → 走正常审批流程（stub 模式自动批准）"""
    _reset()
    d._device_online = True
    env = _env_pre("Bash", "rm -rf /tmp", needs_approval=True, tool_use_id="t1", category="exec", risk_level="critical")
    resp = await d._handle_envelope(env)
    _assert(resp.get("decision") == "once", f"online stub should auto-approve, got {resp}")
    print("  ok  online + critical → normal approval flow (stub auto-approve)")


# ── 主函数 ─────────────────────────────────────────────────
async def main():
    tests = [
        # 风险分级（同步）
        test_classify_risk_safe,
        test_classify_risk_normal,
        test_classify_risk_critical_bash,
        test_classify_risk_critical_paths,
        # 离线审批（异步）
        test_offline_safe_auto_approve,
        test_offline_normal_auto_approve,
        test_online_approval_flow,
    ]
    print(f"running {len(tests)} offline approval tests...")
    try:
        for t in tests:
            print(f"\n[{t.__name__}]")
            if asyncio.iscoroutinefunction(t):
                await t()
            else:
                t()
        print(f"\n{'='*50}\n  ALL OFFLINE APPROVAL TESTS PASSED ({len(tests)} tests)")
        return 0
    except Exception as e:
        print(f"\n{'='*50}\n  TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
