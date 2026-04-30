#!/usr/bin/env python3
# tests/test_daemon_state.py
# Daemon 状态机时序单测,捕获 codex review 抓的两个 P2 bug + 基本时序回归。
#
# 跑法: 在仓库根 `python tests/test_daemon_state.py` 退出码 0 = pass。
#
# 策略: mock time.time + 替换 _send 捕获 wire, 直接驱动 _handle_envelope 与
#       _pusher_tick (从 _pusher_task 抽出的单次迭代函数), 不起真 socket。
#
# 覆盖:
#   1. tool_start → tool_done 基本 wire 演化
#   2. task_complete 推断: 4s 静默触发一次, 不重复推
#   3. tool_error 后跨过 dizzy 也不被庆祝 (codex P2 bug 1)
#   4. task_error (StopFailure) 同上 (codex P2 bug 1 task variant)
#   5. approval deny / timeout 后不被立刻庆祝 (codex P2 bug 2)
#   6. user_prompt 清 completed pulse
#   7. 5Hz 节流: 同 wire 不重发

import asyncio
import os
import sys
import time as real_time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
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
    d._running = 0
    d._waiting = 0
    d._dirty = False
    d._last_activity_ts = 0.0
    d._completed_until = 0.0
    d._completed_inferred_for_ts = 0.0
    d._dizzy_until = 0.0
    d._session_active = False
    d._current_prompt = None
    d._current_running_msg = ""
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


def _env_pre(tool, summary="", needs_approval=False):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_start", "tool": tool, "summary": summary,
                      "needs_approval": needs_approval, "tool_use_id": "t"},
            "generic": _g()}


def _env_post(tool):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_done", "tool": tool, "duration_ms": 10,
                      "tool_use_id": "t"},
            "generic": _g()}


def _env_post_fail(tool):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_error", "tool": tool, "error_msg": "boom",
                      "is_interrupt": False, "duration_ms": 10, "tool_use_id": "t"},
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


def _assert(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)


# ── tests ─────────────────────────────────────────────
async def test_basic_busy_idle():
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", "/etc/hosts"))
    last = await d._pusher_tick(last)
    _assert(d._running == 1, f"running expected 1, got {d._running}")
    _assert(_sent_wires[-1]["running"] == 1, "wire running != 1")
    _assert("Read" in _sent_wires[-1]["msg"], "msg missing tool name")

    _adv(0.5)
    await d._handle_envelope(_env_post("Read"))
    last = await d._pusher_tick(last)
    _assert(d._running == 0, "running back to 0")
    _assert(_sent_wires[-1]["running"] == 0, "wire running != 0")
    print("  ok  basic busy→idle, wire field correct")


async def test_task_complete_one_shot():
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read"))
    _adv(0.3)
    await d._handle_envelope(_env_post("Read"))
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
    await d._handle_envelope(_env_pre("Read"))
    _adv(0.3)
    await d._handle_envelope(_env_post_fail("Read"))
    last = await d._pusher_tick(last)
    _assert("error" in _sent_wires[-1]["msg"], "should show error msg")

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
        env = _env_pre("Bash", "rm -rf /", needs_approval=True)
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
    await d._handle_envelope(_env_pre("Read"))
    _adv(0.3)
    await d._handle_envelope(_env_post("Read"))
    _adv(4.1)
    last = await d._pusher_tick(last)
    _assert(d._completed_until > 0, "should be in completed state")

    await d._handle_envelope(_env_user_prompt())
    _assert(d._completed_until == 0.0,
            f"user_prompt 应清 completed_until, got {d._completed_until}")
    print("  ok  user_prompt clears completed pulse")


async def test_throttle_no_dup_push():
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", "/x"))
    last = await d._pusher_tick(last)
    n0 = len(_sent_wires)

    # 重复 mark_dirty 但 wire 不变 → 不应重复推
    for _ in range(10):
        d._mark_dirty()
        last = await d._pusher_tick(last)
    n1 = len(_sent_wires)
    _assert(n1 == n0, f"unchanged wire 不应重复推, before={n0} after={n1}")
    print(f"  ok  throttle: 10 dirty marks, wire 不变 → 0 重复 push")


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
    ]
    print(f"running {len(tests)} daemon state tests...")
    try:
        for t in tests:
            print(f"\n[{t.__name__}]")
            await t()
        print(f"\n{'='*50}\n  ALL DAEMON TESTS PASSED ({len(tests)})")
        return 0
    finally:
        d.time = orig_time
        d._send = orig_send


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
