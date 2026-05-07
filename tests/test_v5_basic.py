#!/usr/bin/env python3
# tests/test_v5_basic.py
# v5 架构基本功能测试：验证删除审批后的核心功能
# 跑法: python tests/test_v5_basic.py

import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))
import ble_daemon as d


# ── mock ───────────────────────────────────────────────────
_sent_wires = []

async def _capture_send(payload):
    _sent_wires.append(dict(payload))

class _MockTransport:
    def connected(self): return True

def _reset():
    d._sessions.clear()
    d._dirty = False
    d._stub = True
    d._transport = _MockTransport()
    _sent_wires.clear()

def _g():
    return {
        "session_id": "s", "cwd": "/x", "transcript_path": "/x.j",
        "hook_event_name": "X", "permission_mode": "auto",
    }

def _env_tool_start(tool, summary="", tool_use_id="t1"):
    return {
        "type": "event", "v": 2,
        "event": {
            "kind": "tool_start", "tool": tool, "tool_category": "read",
            "summary": summary, "needs_approval": False, "tool_use_id": tool_use_id,
        },
        "generic": _g(),
    }

def _env_tool_done(tool_use_id="t1"):
    return {
        "type": "event", "v": 2,
        "event": {"kind": "tool_done", "tool": "Read", "tool_category": "read",
                  "duration_ms": 100, "tool_use_id": tool_use_id, "interrupted": False},
        "generic": _g(),
    }


# ── 测试用例 ───────────────────────────────────────────────
async def test_no_approval_fields():
    """验证 _Session 不再有审批字段"""
    _reset()
    env = _env_tool_start("Read", "test.py", "t1")
    await d._handle_envelope(env)

    sess = d._sessions.get("s")
    assert sess is not None, "session should exist"
    assert not hasattr(sess, "approval_queue"), "approval_queue should be removed"
    assert not hasattr(sess, "decision_event"), "decision_event should be removed"
    assert not hasattr(sess, "decision_value"), "decision_value should be removed"
    assert not hasattr(sess, "approval_in_progress"), "approval_in_progress should be removed"
    print("  ok  _Session 无审批字段")


async def test_tool_start_immediate_return():
    """验证 tool_start 立即返回 once，不等待审批"""
    _reset()
    env = _env_tool_start("Bash", "ls", "t1")

    # 即使 needs_approval=True，也应该立即返回
    env["event"]["needs_approval"] = True
    resp = await d._handle_envelope(env)

    assert resp.get("decision") == "once", f"expected once, got {resp}"

    sess = d._sessions.get("s")
    assert sess is not None, "session should exist"
    assert len(sess.tools) == 1, "tool should be added"
    assert sess.tools["t1"]["status"] == "running", "tool should be running immediately"
    print("  ok  tool_start 立即返回 once（无审批等待）")


async def test_wire_no_pending():
    """验证 wire 不再包含 PENDING 状态"""
    _reset()
    d._send = _capture_send

    env = _env_tool_start("Read", "test.py", "t1")
    await d._handle_envelope(env)

    wire = d._to_device_wire()
    sessions = wire.get("ss", [])
    assert len(sessions) > 0, "should have sessions"

    # 检查所有 session 状态
    for s in sessions:
        state = s.get("s")
        assert state != "P", f"should not have PENDING state, got {state}"
        assert state in ["I", "W", "E", "C"], f"invalid state: {state}"

    print("  ok  wire 不包含 PENDING 状态")


async def test_basic_workflow():
    """验证基本工作流：tool_start → tool_done"""
    _reset()
    d._send = _capture_send

    # tool_start
    env1 = _env_tool_start("Read", "main.py", "t1")
    resp1 = await d._handle_envelope(env1)
    assert resp1.get("decision") == "once", "should return once"

    wire1 = d._to_device_wire()
    assert wire1["ss"][0]["s"] == "W", "should be WORKING"

    # tool_done
    env2 = _env_tool_done("t1")
    resp2 = await d._handle_envelope(env2)
    assert resp2.get("ok") is True, "should return ok"

    sess = d._sessions.get("s")
    assert len(sess.tools) == 0, "tool should be removed"

    print("  ok  基本工作流正常")


async def test_no_approval_constants():
    """验证审批相关常量已删除"""
    assert not hasattr(d, "APPROVAL_TIMEOUT_S"), "APPROVAL_TIMEOUT_S should be removed"
    assert not hasattr(d, "MIN_PENDING_RESEND_S"), "MIN_PENDING_RESEND_S should be removed"
    assert not hasattr(d, "MAX_PENDING_RESENDS"), "MAX_PENDING_RESENDS should be removed"
    assert not hasattr(d, "POST_PING_COOLDOWN_S"), "POST_PING_COOLDOWN_S should be removed"
    print("  ok  审批相关常量已删除")


# ── 主函数 ─────────────────────────────────────────────────
async def main():
    orig_send = d._send

    tests = [
        test_no_approval_constants,
        test_no_approval_fields,
        test_tool_start_immediate_return,
        test_wire_no_pending,
        test_basic_workflow,
    ]

    print(f"running {len(tests)} v5 basic tests...")
    try:
        for t in tests:
            print(f"\n[{t.__name__}]")
            await t()
        print(f"\n{'='*50}\n  ALL V5 BASIC TESTS PASSED ({len(tests)} groups)")
        return 0
    except AssertionError as e:
        print(f"\n{'='*50}\n  TEST FAILED: {e}")
        return 1
    finally:
        d._send = orig_send


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
