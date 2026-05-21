#!/usr/bin/env python3
# tests/test_daemon_state.py
# Daemon 状态机时序单测 (v3 - per-session _Session 对象)
#
# 跑法: 在仓库根 `python tests/test_daemon_state.py` 退出码 0 = pass。
#
# 策略: mock time.time + 替换 _send 捕获 wire, 直接驱动 _handle_envelope 与
#       _pusher_tick (从 _pusher_task 抽出的单次迭代函数), 不起真 socket。
#
# 所有 envelope 使用 session_id="s"，通过 _sess() / _wire_sess() 访问 per-session 状态。
#
# 覆盖:
#   1. tool_start → tool_done 基本 wire 演化 (sessions 数组)
#   2. task_complete 推断: 4s 静默触发一次, 不重复推
#   3. tool_error 后跨过 dizzy 也不被庆祝 (codex P2 bug 1)
#   4. task_error (StopFailure) 同上 (codex P2 bug 1 task variant)
#   5. user_prompt 清 completed pulse + 清 has_subagent
#   6. 5Hz 节流: 同 wire 不重复推
#   7. subagent_start 设置 has_subagent, completed 阈值变 8s
#   9. 并行工具: 多个 tool_use_id 同时在 tools 中
#   10. wire sessions 字段: category/error/interrupted

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
    return True


# ── per-session helpers ────────────────────────────────
def _sess():
    """返回测试用 session 's' 的 _Session 对象（envelope 发送后才存在）。"""
    return d._sessions.get("s")


def _wire_sess(wire=None):
    """从最后一条（或指定）wire 中取第一个 session 字段 dict。"""
    w = wire if wire is not None else (_sent_wires[-1] if _sent_wires else {})
    sessions = w.get("ss", [])
    return sessions[0] if sessions else {}


def _any_completed(wires=None):
    """统计 wires 中有任意 session s=="C" 的条数。"""
    ws = wires if wires is not None else _sent_wires
    return sum(1 for w in ws
               if any(s.get("s") == "C" for s in w.get("ss", [])))


# ── mock transport ─────────────────────────────────────
class _MockTransport:
    def __init__(self, online=True):
        self._connected_val = online

    def connected(self): return self._connected_val


# ── reset all daemon state ─────────────────────────────
def _reset():
    d._sessions.clear()
    d._dirty = False
    d._stub = True
    d._transport = _MockTransport(online=True)
    _sent_wires.clear()
    _set(100.0)


def _g():
    return {
        "session_id": "s", "cwd": "/x", "transcript_path": "/x.j",
        "hook_event_name": "X", "permission_mode": "auto",
    }


def _env_pre(tool, summary="", needs_approval=False, tool_use_id="t1", category="read", risk_level="normal"):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_start", "tool": tool, "summary": summary,
                      "needs_approval": needs_approval, "tool_use_id": tool_use_id,
                      "tool_category": category, "risk_level": risk_level},
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
    _assert(len(_sess().tools) == 1, f"tools should have 1 entry, got {len(_sess().tools)}")
    _assert(_wire_sess()["s"] == "W", f"wire s should be W, got {_wire_sess().get('s')}")
    _assert("Read" in _wire_sess().get("m", ""), "m missing tool name")

    _adv(0.5)
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    last = await d._pusher_tick(last)
    _assert(len(_sess().tools) == 0, "tools should be empty")
    _assert(_wire_sess()["s"] == "I", f"wire s should be I, got {_wire_sess().get('s')}")
    print("  ok  basic busy→idle, wire sessions fields correct")


async def test_task_complete_one_shot():
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", tool_use_id="t1"))
    _adv(0.3)
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    last = await d._pusher_tick(last)

    # v5 current contract: completion is explicit (Stop/SessionEnd), not inferred
    # from a quiet period after tool_done.
    _adv(4.1)
    last = await d._pusher_tick(last)
    n = _any_completed()
    _assert(n == 0, f"quiet period alone should not complete, got {n}")

    await d._handle_envelope({"type": "event", "v": 2,
                              "event": {"kind": "stop", "stop_reason": "end_turn"},
                              "generic": _g()})
    last = await d._pusher_tick(last)
    n = _any_completed()
    _assert(n == 1, f"explicit stop should produce 1 completed pulse, got {n}")

    # 再多 tick, last_activity_ts 没变, 不应再推
    for _ in range(20):
        _adv(0.4)
        last = await d._pusher_tick(last)
    n2 = _any_completed()
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
    _assert(_wire_sess()["s"] == "E", f"s should be E after tool_error, got {_wire_sess().get('s')}")
    _assert(_sess().current_error == "boom", "current_error should be 'boom'")

    # 跨 DIZZY (3s) + QUIET (4s) = 7s, 旧 bug 在此时推 completed
    for _ in range(20):
        _adv(0.4)
        last = await d._pusher_tick(last)
    n = _any_completed()
    _assert(n == 0, f"tool_error 后不应庆祝, 推了 {n} 次")
    print("  ok  tool_error 跨 dizzy 8s 后无 completed (codex P2 bug 1)")


async def test_task_error_no_celebrate():
    """⚡ codex P2 bug 1 task variant"""
    _reset()
    last = None
    await d._handle_envelope(_env_task_error())
    last = await d._pusher_tick(last)
    _assert(_wire_sess()["s"] == "E", f"s should be E after task_error, got {_wire_sess().get('s')}")
    _assert(_sess().current_error == "API timeout", "current_error should be set")

    for _ in range(20):
        _adv(0.4)
        last = await d._pusher_tick(last)
    n = _any_completed()
    _assert(n == 0, f"task_error 后不应庆祝, 推了 {n} 次")
    print("  ok  task_error 跨 dizzy 8s 后无 completed (codex P2 bug 1 task)")


async def test_user_prompt_clears_completed():
    """用 explicit stop 进 C；验证新 contract：user_prompt 保留 completed_until，
    has_subagent 清零；首个 tool_start 才清 completed_until（避免 tool_done 后 C 闪回）。

    历史：原断言 "user_prompt 清零 completed_until"。upstream 3fcba73 删了静默期推断
    后，前置的 4.1s 路径已不再生效；本测试改用显式 stop。配合 codex P2 review，
    contract 升级为"user_prompt 不清 C，新 turn 首次真实工作清"。
    """
    _reset()
    last = None
    # 用显式 stop 进 C（不再依赖已废除的静默期推断）
    await d._handle_envelope(_env_pre("Read", tool_use_id="t1"))
    _adv(0.3)
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    await d._handle_envelope({"type": "event", "v": 2,
                              "event": {"kind": "stop", "stop_reason": "end_turn"},
                              "generic": _g()})
    saved = _sess().completed_until
    _assert(saved > _clock[0], "stop should set completed_until")

    # user_prompt 保留 completed_until（修上一轮 C 被新 prompt 秒杀的 bug）
    _sess().has_subagent = True
    await d._handle_envelope(_env_user_prompt())
    _assert(_sess().completed_until == saved,
            f"user_prompt 应保留 completed_until, got {_sess().completed_until} (saved={saved})")
    _assert(_sess().has_subagent is False, "user_prompt 应清 has_subagent")

    # 新 turn 首工具开始 → 抛弃旧 C（防 tool_done 后 C 闪回）
    await d._handle_envelope(_env_pre("Read", tool_use_id="t2"))
    _assert(_sess().completed_until == 0.0,
            f"新 turn tool_start 应清旧 completed_until, got {_sess().completed_until}")
    print("  ok  user_prompt preserves C; first tool_start clears C; has_subagent reset")


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
    """subagent_start is display state only; Stop still completes explicitly."""
    _reset()
    last = None
    await d._handle_envelope(_env_subagent_start())
    _assert(_sess().has_subagent is True, "has_subagent should be True")

    await d._handle_envelope(_env_pre("Read", tool_use_id="t1"))
    _adv(0.3)
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    last = await d._pusher_tick(last)

    # No quiet-period completion, even with a subagent marker.
    _adv(4.1)
    last = await d._pusher_tick(last)
    n1 = _any_completed()
    _assert(n1 == 0, f"quiet period should not complete, got {n1}")

    _adv(4.0)
    last = await d._pusher_tick(last)
    n2 = _any_completed()
    _assert(n2 == 0, f"quiet period should not complete after 8.1s, got {n2}")

    await d._handle_envelope({"type": "event", "v": 2,
                              "event": {"kind": "stop", "stop_reason": "end_turn"},
                              "generic": _g()})
    last = await d._pusher_tick(last)
    n3 = _any_completed()
    _assert(n3 == 1, f"explicit stop should complete, got {n3}")
    print("  ok  subagent_start does not infer completion; explicit Stop → C")


async def test_parallel_tools():
    """多个 tool_use_id 同时在 tools 中, running 计数正确。"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Read", tool_use_id="t1", category="read"))
    await d._handle_envelope(_env_pre("Bash", tool_use_id="t2", category="exec"))
    await d._handle_envelope(_env_pre("Glob", tool_use_id="t3", category="read"))
    last = await d._pusher_tick(last)

    _assert(len(_sess().tools) == 3, f"should have 3 tools, got {len(_sess().tools)}")
    _assert(_wire_sess()["s"] == "W", f"s should be W, got {_wire_sess().get('s')}")

    # 完成一个
    await d._handle_envelope(_env_post("Read", tool_use_id="t1"))
    last = await d._pusher_tick(last)
    _assert(len(_sess().tools) == 2, f"should have 2 tools, got {len(_sess().tools)}")
    _assert(_wire_sess()["s"] == "W", f"s should be W, got {_wire_sess().get('s')}")

    # 完成剩余
    await d._handle_envelope(_env_post("Bash", tool_use_id="t2"))
    await d._handle_envelope(_env_post("Glob", tool_use_id="t3"))
    last = await d._pusher_tick(last)
    _assert(len(_sess().tools) == 0, "all tools should be done")
    _assert(_wire_sess()["s"] == "W",
            "fast-tool guarantee should keep W briefly after final tool_done")
    _adv(0.5)
    last = await d._pusher_tick(last)
    _assert(_wire_sess()["s"] == "I", "s should be I after fast-tool window")
    print("  ok  parallel tools: 3 tools → W, brief W hold, then I")


async def test_wire_sessions_fields():
    """验证 wire sessions 字段: v4 s/m 字段。"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Bash", "ls -la", tool_use_id="t1", category="exec"))
    last = await d._pusher_tick(last)
    ws = _wire_sess()
    _assert("s" in ws, "wire session missing s")
    _assert(ws["s"] == "W", f"s should be W, got {ws.get('s')}")
    _assert("m" in ws, "wire session missing m")

    # tool_error with is_interrupt=True → dizzy_until=0 → IDLE
    await d._handle_envelope(_env_post_fail("Bash", tool_use_id="t1", is_interrupt=True))
    last = await d._pusher_tick(last)
    ws2 = _wire_sess()
    _assert(ws2["s"] == "I", f"interrupted error → IDLE, got {ws2.get('s')}")
    print("  ok  wire sessions fields: v4 s/m 字段正确")


async def test_interrupted_skips_error_state():
    """⚡ Bug fix 2: interrupted=True 时不触发 DIZZY，直接回 IDLE。"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Bash", "rm -rf /", tool_use_id="t1", category="exec"))
    last = await d._pusher_tick(last)

    # tool_error with is_interrupt=True
    await d._handle_envelope(_env_post_fail("Bash", tool_use_id="t1", is_interrupt=True))
    last = await d._pusher_tick(last)

    # 验证：dizzy_until 应该是 0（不触发 DIZZY）
    _assert(_sess().dizzy_until == 0.0, f"dizzy_until should be 0, got {_sess().dizzy_until}")
    # wire 中 s 不应该是 "E"
    _assert(_wire_sess()["s"] != "E", f"s should not be E, got {_wire_sess()['s']}")
    # interrupted 应该是 True（从 session 状态检查）
    _assert(_sess().current_interrupted is True, "current_interrupted should be True")
    # error 字段应该有值
    _assert(_sess().current_error == "boom", "current_error should be 'boom'")
    print("  ok  interrupted=True 跳过 DIZZY 状态 (bug fix 2)")


async def test_tool_done_interrupted_propagates():
    """⚡ Bug fix 1: tool_done 的 interrupted 值应该传递到 current_interrupted。"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Bash", "sleep 10", tool_use_id="t1", category="exec"))
    last = await d._pusher_tick(last)

    # tool_done with interrupted=True
    await d._handle_envelope(_env_post("Bash", tool_use_id="t1", interrupted=True))
    last = await d._pusher_tick(last)

    # 验证：current_interrupted 应该是 True（不是写死的 False）
    _assert(_sess().current_interrupted is True,
            f"current_interrupted should be True, got {_sess().current_interrupted}")
    # v4 wire 无 interrupted 字段，从 session 状态验证
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
    _assert(_sess().current_error == "boom", "error should be set")
    _assert(_sess().current_interrupted is False, "interrupted should be False")

    # 新 turn 开始：user_prompt
    await d._handle_envelope(_env_user_prompt())
    last = await d._pusher_tick(last)

    # 验证：错误状态应该被清除
    _assert(_sess().current_error == "",
            f"current_error should be cleared, got {_sess().current_error!r}")
    _assert(_sess().current_interrupted is False, "current_interrupted should be False")
    # v4 wire 无 error 字段，从 session 状态验证
    print("  ok  user_prompt 清除旧错误状态 (bug fix 3)")


async def test_multi_session_isolation():
    """v3 新增: 两个 session 状态互相隔离。"""
    _reset()
    last = None

    # session "s" 发 tool_start
    await d._handle_envelope(_env_pre("Read", tool_use_id="t1", category="read"))

    # session "s2" 发 tool_error
    env_err = {"type": "event", "v": 2,
               "event": {"kind": "tool_error", "tool": "Bash", "error_msg": "s2-err",
                         "is_interrupt": False, "duration_ms": 0, "tool_use_id": "t2"},
               "generic": {"session_id": "s2", "cwd": "/x", "transcript_path": "/x.j",
                            "hook_event_name": "X", "permission_mode": "auto"}}
    await d._handle_envelope(env_err)
    last = await d._pusher_tick(last)

    # wire 应包含两个 session
    sessions = _sent_wires[-1].get("ss", [])
    _assert(len(sessions) == 2, f"should have 2 sessions in wire, got {len(sessions)}")

    # s: WORKING, s2: ERROR（按状态区分）
    s_wire = next((s for s in sessions if s.get("s") == "W"), None)
    s2_wire = next((s for s in sessions if s.get("s") == "E"), None)
    _assert(s_wire is not None, "session 's' (W) missing from wire")
    _assert(s2_wire is not None, "session 's2' (E) missing from wire")
    print("  ok  multi-session isolation: 两个 session 状态互不干扰")


async def test_waiting_pending_wire():
    """needs_approval=True → waiting=1 → wire 返回 'P'。"""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Bash", needs_approval=True, tool_use_id="t1"))
    _assert(_sess().waiting == 1, f"waiting should be 1, got {_sess().waiting}")
    last = await d._pusher_tick(last)
    _assert(_wire_sess()["s"] == "P", f"wire s should be P, got {_wire_sess().get('s')}")
    print("  ok  needs_approval=True → waiting=1, wire s='P'")


async def test_tool_done_decrements_waiting():
    """tool_done decrements waiting; fast-tool W hold expires back to I."""
    _reset()
    last = None
    await d._handle_envelope(_env_pre("Bash", needs_approval=True, tool_use_id="t1"))
    await d._handle_envelope(_env_post("Bash", tool_use_id="t1"))
    _assert(_sess().waiting == 0, f"waiting should be 0 after tool_done, got {_sess().waiting}")
    last = await d._pusher_tick(last)
    _assert(_wire_sess()["s"] == "W",
            f"wire should keep brief W after tool_done, got {_wire_sess().get('s')}")
    _adv(0.5)
    last = await d._pusher_tick(last)
    _assert(_wire_sess()["s"] == "I",
            f"wire s should be I after fast-tool window, got {_wire_sess().get('s')}")
    print("  ok  tool_done decrements waiting → brief W → I")


async def test_tool_error_decrements_waiting():
    """tool_error 时 waiting 递减。"""
    _reset()
    await d._handle_envelope(_env_pre("Bash", needs_approval=True, tool_use_id="t1"))
    await d._handle_envelope(_env_post_fail("Bash", tool_use_id="t1"))
    _assert(_sess().waiting == 0, f"waiting should be 0 after tool_error, got {_sess().waiting}")
    print("  ok  tool_error decrements waiting")


async def test_display_name_from_cwd():
    """验证从 cwd 生成 display_name 并包含在 wire 的 n 字段中。"""
    _reset()
    last = None

    # 发送带 cwd 的 envelope
    env = _env_pre("Read", tool_use_id="t1", category="read")
    env["generic"]["cwd"] = "/home/user/projects/MyAwesomeProject"
    await d._handle_envelope(env)

    # 验证 session 的 display_name
    sess = _sess()
    _assert(sess.display_name == "MyAwesomePro",
            f"display_name should be 'MyAwesomePro', got {sess.display_name!r}")

    # 验证 wire 包含 n 字段
    last = await d._pusher_tick(last)
    ws = _wire_sess()
    _assert("n" in ws, "wire should contain 'n' field")
    _assert(ws["n"] == "MyAwesomePro",
            f"wire n should be 'MyAwesomePro', got {ws.get('n')!r}")
    print("  ok  display_name from cwd → wire n field")


async def test_display_name_conflict():
    """验证同 basename 不同 cwd 时加后缀区分。"""
    _reset()

    # Session 1: /home/user/project1/MyProject
    env1 = _env_pre("Read", tool_use_id="t1")
    env1["generic"]["session_id"] = "sess_abc123"
    env1["generic"]["cwd"] = "/home/user/project1/MyProject"
    await d._handle_envelope(env1)

    sess1 = d._sessions.get("sess_abc123")
    _assert(sess1.display_name == "MyProject",
            f"first session should be 'MyProject', got {sess1.display_name!r}")

    # Session 2: /home/user/project2/MyProject (同 basename)
    env2 = _env_pre("Read", tool_use_id="t2")
    env2["generic"]["session_id"] = "sess_xyz789"
    env2["generic"]["cwd"] = "/home/user/project2/MyProject"
    await d._handle_envelope(env2)

    sess2 = d._sessions.get("sess_xyz789")
    _assert(sess2.display_name.startswith("MyProje-"),
            f"second session should have suffix, got {sess2.display_name!r}")
    _assert("789" in sess2.display_name,
            f"suffix should contain session_id tail, got {sess2.display_name!r}")
    print("  ok  display_name conflict detection + suffix")


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
        test_user_prompt_clears_completed,
        test_throttle_no_dup_push,
        test_subagent_threshold,
        test_parallel_tools,
        test_wire_sessions_fields,
        test_interrupted_skips_error_state,
        test_tool_done_interrupted_propagates,
        test_user_prompt_clears_error,
        test_multi_session_isolation,
        test_waiting_pending_wire,
        test_tool_done_decrements_waiting,
        test_tool_error_decrements_waiting,
        test_display_name_from_cwd,
        test_display_name_conflict,
    ]
    print(f"running {len(tests)} daemon state tests (v3 per-session)...")
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
