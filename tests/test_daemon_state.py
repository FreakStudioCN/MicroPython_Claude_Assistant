#!/usr/bin/env python3
# tests/test_daemon_state.py
# Daemon 状态机时序单测 (v2 - 基于 _tools 字典)
#
# 跑法: 在仓库根 `python tests/test_daemon_state.py` 退出码 0 = pass。
#
# 策略: mock time.time + 替换 _send 捕获 wire, 直接驱动 _handle_envelope 与
#       _pusher_tick (从 _pusher_task 抽出的单次迭代函数), 不起真 socket。
#
# 覆盖:
#   1. tool_start → tool_done 基本 wire 演化 (9 字段)
#   2. task_complete 推断: 4s 静默触发一次, 不重复推
#   3. tool_error 后跨过 dizzy 也不被庆祝 (codex P2 bug 1)
#   4. task_error (StopFailure) 同上 (codex P2 bug 1 task variant)
#   5. approval deny / timeout 后不被立刻庆祝 (codex P2 bug 2)
#   6. user_prompt 清 completed pulse + 清 _has_subagent
#   7. 5Hz 节流: 同 wire 不重复推
#   8. subagent_start 设置 _has_subagent, completed 阈值变 8s
#   9. 并行工具: 多个 tool_use_id 同时在 _tools 中
#   10. v2 wire 9 字段: category/error/interrupted

import asyncio
import os
import sys
import time as real_time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))
import ble_daemon as d  # noqa: E402


# ── mock time ──────────────────────────────────────────
_clock = [100.0]


class _MockTime:
    @staticmethod
    def time():
        return _clock[0]


def _set(t): _clock[0] = t
def _adv(dt): _clock[0] += dt


# ── capture _send ───────────────────────────────────────
_sent_wires = []


async def _capture_send(payload):
    _sent_wires.append(dict(payload))


# ── reset all daemon globals ───────────────────────────
def _reset():
    d._tools.clear()
    d._approval_queue.clear()
    d._has_subagent = False
    d._current_error = ""
    d._current_interrupted = False
    d._dirty = False
    d._last_activity_ts = 0.0
    d._completed_until = 0.0
    d._completed_inferred_for_ts = 0.0
    d._dizzy_until = 0.0
    d._approval_in_progress = False
    d._decision_value = None
    d._decision_event = asyncio.Event()  # 新 loop 上的新 event
    d._stub = True  # 默认 stub, 个别 test override
    _sent_wires.clear()
    _set(100.0)


def _g():
    return {
        "session_id": "s", "cwd": "/x", "transcript_path": "/x.j",
        "hook_event_name": "X", "permission_mode": "auto",
    }


def _env_pre(tool, summary="", needs_approval=False, tool_use_id="t1", category="read"):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_start", "tool": tool, "summary": summary,
                      "needs_approval": needs_approval, "tool_use_id": tool_use_id,
                      "tool_category": category},
            "generic": _g()}


def _env_post(tool, tool_use_id="t1", interrupted=False):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_done", "tool": tool, "duration_ms": 10,
                      "tool_use_id": tool_use_id, "interrupted": interrupted},
            "generic": _g()}


def _env_post_fail(tool, tool_use_id="t1", is_interrupt=False):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_error", "tool": tool, "error_msg": "boom",
                      "is_interrupt": is_interrupt, "duration_ms": 10, "tool_use_id": tool_use_id},
            "generic": _g()}


def _env_task_error():
    return {"type": "event", "v": 2,
            "event": {"kind": "task_error", "error": "API timeout",
                      "last_assistant_message": ""},
            "generic": _g()}


def _env_user_prompt():
    return {"type": "event", "v": 2,
            "event": {"kind": "user_prompt", "prompt": "go"},
            "generic": _g()}


def _env_subagent_start():
    return {"type": "event", "v": 2,
            "event": {"kind": "subagent_start", "agent_id": "a1", "agent_type": "Explore"},
            "generic": _g()}


def _assert(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)


# ── tests ─────────────────────────────────────────────
async def test_basic_busy_idle():
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", "/etc/hosts", tool_use_id="t1", category="read"))
    last = await d._pusher_tick(last)
    _assert(len(d._tools) == 1, f"_tools should have 1 entry, got {len(d._tools)}")
    _assert(_sent_wires[-1]["running"] == 1, "wire running != 1")
    _assert(_sent_wires[-1]["category"] == "read", "wire category != read")
    _assert("Read" in _sent_wires[-1]["msg"], "msg missing tool name")

    _adv(0.5)
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    last = await d._pusher_tick(last)
    _assert(len(d._tools) == 0, "tools should be empty")
    _assert(_sent_wires[-1]["running"] == 0, "wire running != 0")
    print("  ok  basic busy→idle, wire 9 fields correct")


async def test_task_complete_one_shot():
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", tool_use_id="t1"))
    _adv(0.3)
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    last = await d._pusher_tick(last)

    _adv(4.1)  # 跨 TASK_COMPLETE_QUIET_S
    last = await d._pusher_tick(last)
    n = sum(1 for w in _sent_wires if w.get("completed"))
    _assert(n == 1, f"expected 1 completed pulse, got {n}")

    # 再多 tick, _last_activity_ts 没变, 不应再推
    for _ in range(20):
        _adv(0.4)
        last = await d._pusher_tick(last)
    n2 = sum(1 for w in _sent_wires if w.get("completed"))
    _assert(n2 == 1, f"should NOT re-fire, got {n2}")
    print(f"  ok  task_complete one-shot (1 pulse over {20*0.4 + 4.1:.1f}s)")


async def test_tool_error_no_celebrate():
    """⚡ codex P2 bug 1 regression"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", tool_use_id="t1"))
    _adv(0.3)
    await d._handle_envelope(_env_post_fail("Read", tool_use_id="t1"))
    last = await d._pusher_tick(last)
    _assert("error" in _sent_wires[-1]["msg"], "should show error msg")
    _assert(_sent_wires[-1]["error"] == "boom", "error field should be 'boom'")

    # 跨 DIZZY (3s) + QUIET (4s) = 7s, 旧 bug 在此时推 completed
    for _ in range(20):
        _adv(0.4)
        last = await d._pusher_tick(last)
    n = sum(1 for w in _sent_wires if w.get("completed"))
    _assert(n == 0, f"tool_error 后不应庆祝, 推了 {n} 次")
    print("  ok  tool_error 跨 dizzy 8s 后无 completed (codex P2 bug 1)")


async def test_task_error_no_celebrate():
    """⚡ codex P2 bug 1 task variant"""
    _reset()
    last = None
    await d._handle_envelope(_env_task_error())
    last = await d._pusher_tick(last)
    _assert(_sent_wires[-1]["error"] == "API timeout", "error field should be set")

    for _ in range(20):
        _adv(0.4)
        last = await d._pusher_tick(last)
    n = sum(1 for w in _sent_wires if w.get("completed"))
    _assert(n == 0, f"task_error 后不应庆祝, 推了 {n} 次")
    print("  ok  task_error 跨 dizzy 8s 后无 completed (codex P2 bug 1 task)")


async def test_approval_deny_no_celebrate():
    """⚡ codex P2 bug 2 regression. 用真 wait_for + 极短 timeout 模拟 deny。"""
    _reset()
    d._stub = False
    orig_to = d.APPROVAL_TIMEOUT_S
    d.APPROVAL_TIMEOUT_S = 0.05  # 50ms 真实 wall wait_for
    try:
        env = _env_pre("Bash", "rm -rf /", needs_approval=True, tool_use_id="t1", category="exec")
        resp = await d._handle_envelope(env)
        _assert(resp.get("decision") == "deny", f"expected deny, got {resp}")

        # _last_activity_ts 应被刷到 wait 之后的 mock clock 值 (仍 100.0)
        _assert(d._last_activity_ts == 100.0,
                f"_last_activity_ts not refreshed: {d._last_activity_ts}")
        # _completed_inferred_for_ts 应锁住等于 _last_activity_ts
        _assert(d._completed_inferred_for_ts == d._last_activity_ts,
                "deny path should lock inference guard")

        # 时间快进 100s, pusher 不应推 completed
        _adv(100.0)
        last = None
        for _ in range(5):
            last = await d._pusher_tick(last)
        n = sum(1 for w in _sent_wires if w.get("completed"))
        _assert(n == 0, f"deny 后跨 100s 也不应庆祝, 推了 {n} 次")
    finally:
        d.APPROVAL_TIMEOUT_S = orig_to
    print("  ok  approval deny → no celebrate over 100s (codex P2 bug 2)")


async def test_user_prompt_clears_completed():
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", tool_use_id="t1"))
    _adv(0.3)
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    _adv(4.1)
    last = await d._pusher_tick(last)
    _assert(d._completed_until > 0, "should be in completed state")

    # 设置 subagent 标志
    d._has_subagent = True
    await d._handle_envelope(_env_user_prompt())
    _assert(d._completed_until == 0.0,
            f"user_prompt 应清 completed_until, got {d._completed_until}")
    _assert(d._has_subagent is False, "user_prompt 应清 _has_subagent")
    print("  ok  user_prompt clears completed pulse + _has_subagent")


async def test_throttle_no_dup_push():
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", "/x", tool_use_id="t1"))
    last = await d._pusher_tick(last)
    n0 = len(_sent_wires)

    # 重复 mark_dirty 但 wire 不变 → 不应重复推
    for _ in range(10):
        d._mark_dirty()
        last = await d._pusher_tick(last)
    n1 = len(_sent_wires)
    _assert(n1 == n0, f"unchanged wire 不应重复推, before={n0} after={n1}")
    print(f"  ok  throttle: 10 dirty marks, wire 不变 → 0 重复 push")


async def test_subagent_threshold():
    """subagent_start 设置 _has_subagent, completed 阈值从 4s 变 8s。"""
    _reset()
    last = None
    await d._handle_envelope(_env_subagent_start())
    _assert(d._has_subagent is True, "_has_subagent should be True")

    await d._handle_envelope(_env_pre("Read", tool_use_id="t1"))
    _adv(0.3)
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    last = await d._pusher_tick(last)

    # 4.1s 后不应触发 (阈值是 8s)
    _adv(4.1)
    last = await d._pusher_tick(last)
    n1 = sum(1 for w in _sent_wires if w.get("completed"))
    _assert(n1 == 0, f"4.1s 后不应 completed (阈值 8s), got {n1}")

    # 再过 4s (总共 8.1s) 应触发
    _adv(4.0)
    last = await d._pusher_tick(last)
    n2 = sum(1 for w in _sent_wires if w.get("completed"))
    _assert(n2 == 1, f"8.1s 后应 completed, got {n2}")
    print("  ok  subagent_start → completed threshold 8s (not 4s)")


async def test_parallel_tools():
    """多个 tool_use_id 同时在 _tools 中, running 计数正确。"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", tool_use_id="t1", category="read"))
    await d._handle_envelope(_env_pre("Bash", tool_use_id="t2", category="exec"))
    await d._handle_envelope(_env_pre("Glob", tool_use_id="t3", category="read"))
    last = await d._pusher_tick(last)

    _assert(len(d._tools) == 3, f"should have 3 tools, got {len(d._tools)}")
    _assert(_sent_wires[-1]["running"] == 3, f"running should be 3, got {_sent_wires[-1]['running']}")

    # 完成一个
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    last = await d._pusher_tick(last)
    _assert(len(d._tools) == 2, f"should have 2 tools, got {len(d._tools)}")
    _assert(_sent_wires[-1]["running"] == 2, f"running should be 2, got {_sent_wires[-1]['running']}")

    # 完成剩余
    await d._handle_envelope(_env_post("Bash", tool_use_id="t2"))
    await d._handle_envelope(_env_post("Glob", tool_use_id="t3"))
    last = await d._pusher_tick(last)
    _assert(len(d._tools) == 0, "all tools should be done")
    _assert(_sent_wires[-1]["running"] == 0, "running should be 0")
    print("  ok  parallel tools: 3 tools → running=3, 逐个完成计数正确")


async def test_wire_v2_fields():
    """验证 v2 wire 9 字段: category/error/interrupted。"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Bash", "ls -la", tool_use_id="t1", category="exec"))
    last = await d._pusher_tick(last)
    w = _sent_wires[-1]
    _assert("category" in w, "wire missing category")
    _assert(w["category"] == "exec", f"category should be exec, got {w['category']}")
    _assert("error" in w, "wire missing error")
    _assert(w["error"] == "", "error should be empty")
    _assert("interrupted" in w, "wire missing interrupted")
    _assert(w["interrupted"] is False, "interrupted should be False")

    # tool_error 设置 error
    await d._handle_envelope(_env_post_fail("Bash", tool_use_id="t1", is_interrupt=True))
    last = await d._pusher_tick(last)
    w2 = _sent_wires[-1]
    _assert(w2["error"] == "boom", f"error should be 'boom', got {w2['error']}")
    _assert(w2["interrupted"] is True, f"interrupted should be True, got {w2['interrupted']}")
    print("  ok  v2 wire 9 fields: category/error/interrupted 正确")


async def test_interrupted_skips_error_state():
    """⚡ Bug fix 2: interrupted=True 时不触发 DIZZY，直接回 IDLE。"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Bash", "rm -rf /", tool_use_id="t1", category="exec"))
    last = await d._pusher_tick(last)

    # tool_error with is_interrupt=True
    await d._handle_envelope(_env_post_fail("Bash", tool_use_id="t1", is_interrupt=True))
    last = await d._pusher_tick(last)

    # 验证：_dizzy_until 应该是 0（不触发 DIZZY）
    _assert(d._dizzy_until == 0.0, f"_dizzy_until should be 0, got {d._dizzy_until}")
    # wire 中 msg 不应该是 "error"
    _assert(_sent_wires[-1]["msg"] != "error", f"msg should not be 'error', got {_sent_wires[-1]['msg']}")
    # interrupted 应该是 True
    _assert(_sent_wires[-1]["interrupted"] is True, "interrupted should be True")
    # error 字段应该有值
    _assert(_sent_wires[-1]["error"] == "boom", "error field should be 'boom'")
    print("  ok  interrupted=True 跳过 DIZZY 状态 (bug fix 2)")


async def test_tool_done_interrupted_propagates():
    """⚡ Bug fix 1: tool_done 的 interrupted 值应该传递到 _current_interrupted。"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Bash", "sleep 10", tool_use_id="t1", category="exec"))
    last = await d._pusher_tick(last)

    # tool_done with interrupted=True
    await d._handle_envelope(_env_post("Bash", tool_use_id="t1", interrupted=True))
    last = await d._pusher_tick(last)

    # 验证：_current_interrupted 应该是 True（不是写死的 False）
    _assert(d._current_interrupted is True, f"_current_interrupted should be True, got {d._current_interrupted}")
    # wire 中 interrupted 应该是 True
    _assert(_sent_wires[-1]["interrupted"] is True, "wire interrupted should be True")
    print("  ok  tool_done interrupted=True 正确传递 (bug fix 1)")


async def test_user_prompt_clears_error():
    """⚡ Bug fix 3: user_prompt 应该清除旧 turn 的错误状态。"""
    _reset()
    last = None

    # 先触发一个 tool_error
    await d._handle_envelope(_env_pre("Read", "/nonexist", tool_use_id="t1", category="read"))
    await d._handle_envelope(_env_post_fail("Read", tool_use_id="t1", is_interrupt=False))
    last = await d._pusher_tick(last)

    # 验证错误状态已设置
    _assert(d._current_error == "boom", "error should be set")
    _assert(d._current_interrupted is False, "interrupted should be False")

    # 新 turn 开始：user_prompt
    await d._handle_envelope(_env_user_prompt())
    last = await d._pusher_tick(last)

    # 验证：错误状态应该被清除
    _assert(d._current_error == "", f"_current_error should be cleared, got {d._current_error!r}")
    _assert(d._current_interrupted is False, "_current_interrupted should be False")
    # wire 中 error 应该是空
    _assert(_sent_wires[-1]["error"] == "", "wire error should be empty")
    print("  ok  user_prompt 清除旧错误状态 (bug fix 3)")


async def main():
    # 替换 time + _send 全程 mock
    orig_time = d.time
    orig_send = d._send
    d.time = _MockTime()
    d._send = _capture_send

    tests = [
        test_basic_busy_idle,
        test_task_complete_one_shot,
        test_tool_error_no_celebrate,
        test_task_error_no_celebrate,
        test_approval_deny_no_celebrate,
        test_user_prompt_clears_completed,
        test_throttle_no_dup_push,
        test_subagent_threshold,
        test_parallel_tools,
        test_wire_v2_fields,
        test_interrupted_skips_error_state,
        test_tool_done_interrupted_propagates,
        test_user_prompt_clears_error,
    ]
    print(f"running {len(tests)} daemon state tests (v2)...")
    try:
        for t in tests:
            print(f"\n[{t.__name__}]")
            await t()
        print(f"\n{'='*50}\n  ALL DAEMON TESTS PASSED ({len(tests)} groups)")
        return 0
    finally:
        d.time = orig_time
        d._send = orig_send


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
