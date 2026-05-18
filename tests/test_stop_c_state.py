#!/usr/bin/env python3
# tests/test_stop_c_state.py
#
# 验证 stop 事件触发 C（完成）状态的完整行为，重点覆盖：
#
#   1. stop 基本行为：立即推 C
#   2. [BUG] 长时间思考后 stop → C 丢失
#      根本原因：_pusher_tick 中 session cleanup 不检查 completed_until，
#      stop 把 turn_active 置 False 后，若 elapsed > SESSION_CLEANUP_S(10s)，
#      cleanup 在推送前把 session 删掉，C 状态永远推不出去。
#   3. turn_active=True 在思考期间保护 session 不被清理
#   4. turn_active=True 无工具时也推 W（思考中指示灯）
#   5. notification/permission_prompt → P 状态
#   6. stop 后 1s 内的 notification 被忽略，不覆盖 C
#   7. C 状态到期后恢复 I
#   8. stop 清 waiting，覆盖 P → C
#
# 跑法: python tests/test_stop_c_state.py
# 退出码: 0 = 全部通过, 1 = 有失败（标 [BUG] 的用例修复前预期失败）
#
# 策略与 test_daemon_state.py 相同:
#   mock time.time + 替换 _send 捕获 wire, 直接驱动 _handle_envelope /
#   _pusher_tick, 不起真 socket.

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))
import ble_daemon as d  # noqa: E402

# ── mock time ──────────────────────────────────────────────────────────────
_clock = [100.0]


class _MockTime:
    @staticmethod
    def time():
        return _clock[0]


def _set(t): _clock[0] = t
def _adv(dt): _clock[0] += dt


# ── capture _send ──────────────────────────────────────────────────────────
_sent_wires = []


async def _capture_send(payload):
    _sent_wires.append(dict(payload))
    return True


# ── mock transport ─────────────────────────────────────────────────────────
class _MockTransport:
    def connected(self): return True


# ── helpers ────────────────────────────────────────────────────────────────
def _reset():
    d._sessions.clear()
    d._dirty = False
    d._stub = True
    d._transport = _MockTransport()
    _sent_wires.clear()
    _set(100.0)


def _sess():
    return d._sessions.get("s")


def _wire_states(wire=None):
    """返回最后一条（或指定）wire 中所有 session 的 s 字段列表。"""
    w = wire if wire is not None else (_sent_wires[-1] if _sent_wires else {})
    return [s.get("s") for s in w.get("ss", [])]


def _any_state(state, wires=None):
    """统计 wires 中推送过指定状态的 wire 条数。"""
    ws = wires if wires is not None else _sent_wires
    return sum(1 for w in ws if any(s.get("s") == state for s in w.get("ss", [])))


def _g(sid="s"):
    return {
        "session_id": sid, "cwd": "/home/user/project",
        "transcript_path": "/x.j", "hook_event_name": "X",
        "permission_mode": "auto",
    }


def _ev(kind, extra=None, sid="s"):
    evt = {"kind": kind}
    if extra:
        evt.update(extra)
    return {"type": "event", "v": 2, "event": evt, "generic": _g(sid)}


def _env_user_prompt(sid="s"):
    return _ev("user_prompt", {"prompt": "继续"}, sid=sid)


def _env_stop(sid="s"):
    return _ev("stop", {"stop_reason": "end_turn"}, sid=sid)


def _env_pre(tool="Read", summary="file.py", tid="t1", sid="s"):
    return _ev("tool_start", {
        "tool": tool, "summary": summary,
        "tool_use_id": tid, "tool_category": "read",
        "needs_approval": False,
    }, sid=sid)


def _env_post(tool="Read", tid="t1", sid="s"):
    return _ev("tool_done", {
        "tool": tool, "tool_use_id": tid,
        "duration_ms": 100, "interrupted": False,
    }, sid=sid)


def _env_notification(ntype="permission_prompt", sid="s"):
    return _ev("notification", {
        "notification_type": ntype,
        "message": "Claude needs your attention",
    }, sid=sid)


def _assert(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)


# ── tests ──────────────────────────────────────────────────────────────────

async def test_stop_c_basic():
    """stop 在 tool_done 后立即到来 → C 状态被推送。"""
    _reset()
    last = None

    await d._handle_envelope(_env_user_prompt())
    await d._handle_envelope(_env_pre())
    _adv(0.5)
    await d._handle_envelope(_env_post())
    await d._handle_envelope(_env_stop())

    _adv(0.2)
    last = await d._pusher_tick(last)

    n = _any_state("C")
    _assert(n > 0, f"stop 后应推 C 状态，实际 C={n}")
    _assert(_sess() is not None, "session 不应被清理")
    _assert(_sess().completed_until > _clock[0], "completed_until 应在未来")
    print("  ok  stop 在 tool_done 后立即到来 → C 状态正确推送")


async def test_stop_c_after_long_thinking():
    """[BUG] 长时间思考后 stop → C 状态丢失。

    场景：user_prompt → tool_done → 等待 12s（Claude 生成回复）→ stop
    预期：stop 触发 C 状态
    当前行为（有 bug）：stop 把 turn_active 置 False 后，cleanup 发现
        elapsed(12s) > SESSION_CLEANUP_S(10s) → 删除 session → C 状态丢失

    修复方向：cleanup 需额外检查 completed_until <= now（C 状态期间不清理）
    """
    _reset()
    last = None

    # T=100: user_prompt
    await d._handle_envelope(_env_user_prompt())

    # T=100.5: 一次工具执行完成
    await d._handle_envelope(_env_pre())
    _adv(0.5)
    await d._handle_envelope(_env_post())
    # last_activity_ts = 100.5

    # T=100.5→T=112.5: Claude 思考 12s（turn_active=True 期间 session 受保护）
    _adv(12.0)
    last = await d._pusher_tick(last)
    _assert(_sess() is not None,
            "思考期间（turn_active=True）session 不应被清理")

    # T=112.5: stop 到来（turn_active → False，completed_until = 114.5）
    await d._handle_envelope(_env_stop())

    # T=112.7: 下一个 pusher tick（200ms 后）
    # Bug 触发点：cleanup 检查 not tools(T) & not turn_active(T) & elapsed=12.2s>10s
    #             → 删除 session，completed_until 信息丢失
    _adv(0.2)
    last = await d._pusher_tick(last)

    n = _any_state("C")
    _assert(n > 0,
            f"stop 后（长思考场景）应推 C 状态，实际 C={n}\n"
            f"    BUG: _pusher_tick cleanup 在推送前删除了 session\n"
            f"    fix: cleanup 条件补充 and s.completed_until <= now")
    print("  ok  长时间思考后 stop → C 状态正确推送（BUG 修复验证）")


async def test_cleanup_respects_completed_until():
    """[BUG] cleanup 应保留 completed_until > now 的 session。

    直接验证 cleanup 逻辑：stop 后即使 elapsed > SESSION_CLEANUP_S，
    只要 completed_until 未到期，session 就不应被删除。
    """
    _reset()
    last = None

    await d._handle_envelope(_env_user_prompt())
    await d._handle_envelope(_env_pre())
    _adv(0.5)
    await d._handle_envelope(_env_post())
    # last_activity_ts = 100.5

    # 推进到超过 SESSION_CLEANUP_S
    _adv(12.0)  # T=112.5，elapsed=12s > 10s

    # stop：turn_active=False, completed_until=114.5
    await d._handle_envelope(_env_stop())

    # 直接调用 pusher_tick（不等 200ms，clock 仍在 112.5）
    # 此时 completed_until=114.5 > now=112.5 → session 不应被删
    _adv(0.1)
    last = await d._pusher_tick(last)

    _assert(_sess() is not None,
            f"completed_until 未到期时 session 不应被 cleanup 删除\n"
            f"    BUG: cleanup 未检查 completed_until")
    _assert(_sess().completed_until > _clock[0],
            "completed_until 应仍在未来")
    print("  ok  cleanup 保留 completed_until 未到期的 session")


async def test_turn_active_prevents_cleanup():
    """turn_active=True 期间（user_prompt→stop），即使无工具且 elapsed>10s，session 不被清理。"""
    _reset()
    last = None

    await d._handle_envelope(_env_user_prompt())
    await d._handle_envelope(_env_pre())
    _adv(0.5)
    await d._handle_envelope(_env_post())
    # turn_active=True，tools={}，last_activity=100.5

    # 推进 15s（远超 SESSION_CLEANUP_S）
    _adv(15.0)
    last = await d._pusher_tick(last)

    # turn_active=True 保护，session 不应被删
    _assert(_sess() is not None,
            "turn_active=True 期间（思考中）session 不应被 cleanup")
    print("  ok  turn_active=True 防止思考期间 session 被 cleanup（15s）")


async def test_turn_active_w_state_no_tools():
    """user_prompt 后无工具时也推 W（思考指示灯）。"""
    _reset()
    last = None

    await d._handle_envelope(_env_user_prompt())
    # 无 tool_start，直接推一次 tick
    last = await d._pusher_tick(last)

    states = _wire_states()
    _assert("W" in states,
            f"user_prompt 后无工具也应推 W（思考中），实际={states}")
    _assert(_sess().turn_active is True, "turn_active 应为 True")
    print("  ok  user_prompt 后无工具 → W（思考指示灯）")


async def test_turn_active_w_between_tools():
    """tool_done 之后、stop 之前：turn_active=True 保持 W 状态（思考中）。"""
    _reset()
    last = None

    await d._handle_envelope(_env_user_prompt())
    await d._handle_envelope(_env_pre())
    _adv(0.5)
    await d._handle_envelope(_env_post())
    # tools={}, turn_active=True
    last = await d._pusher_tick(last)

    states = _wire_states()
    _assert("W" in states,
            f"tool_done 后 turn_active=True → 应推 W，实际={states}")
    _assert("I" not in states,
            f"tool_done 后 turn_active=True → 不应推 I，实际={states}")
    print("  ok  tool_done 后 turn_active=True → W（处理结果中，非 I）")


async def test_notification_p_state():
    """notification/permission_prompt → P 状态。"""
    _reset()
    last = None

    await d._handle_envelope(_env_user_prompt())
    await d._handle_envelope(_env_notification("permission_prompt"))

    _assert(_sess().waiting == 1, f"waiting 应为 1，实际={_sess().waiting}")
    last = await d._pusher_tick(last)

    states = _wire_states()
    _assert("P" in states, f"notification 后应推 P，实际={states}")
    print("  ok  notification/permission_prompt → P 状态")


async def test_stop_clears_waiting():
    """stop 把 waiting 清零：P → C。"""
    _reset()
    last = None

    await d._handle_envelope(_env_user_prompt())
    await d._handle_envelope(_env_notification("permission_prompt"))
    _assert(_sess().waiting == 1, "waiting 应为 1")

    await d._handle_envelope(_env_stop())
    _assert(_sess().waiting == 0, f"stop 后 waiting 应为 0，实际={_sess().waiting}")

    _adv(0.2)
    last = await d._pusher_tick(last)
    states = _wire_states()
    _assert("C" in states, f"stop 后应推 C（不是 P），实际={states}")
    print("  ok  stop 清 waiting=0 → C（P 状态不阻断完成）")


async def test_notification_after_stop_within_1s_ignored():
    """stop 后 1s 内的 notification 被忽略，不把 C 覆盖为 P。"""
    _reset()
    last = None

    await d._handle_envelope(_env_user_prompt())
    await d._handle_envelope(_env_pre())
    _adv(0.5)
    await d._handle_envelope(_env_post())
    await d._handle_envelope(_env_stop())

    # 0.3s 后来了 notification（在 1s 过滤窗口内）
    _adv(0.3)
    await d._handle_envelope(_env_notification("permission_prompt"))
    last = await d._pusher_tick(last)

    _assert(_sess().waiting == 0,
            f"stop 后 1s 内 notification 应被忽略，waiting={_sess().waiting}")
    states = _wire_states()
    _assert("C" in states,
            f"stop 后 1s 内 notification 不应覆盖 C，实际={states}")
    print("  ok  stop 后 1s 内 notification 被忽略，C 状态不被覆盖")


async def test_c_state_expires_to_idle():
    """C 状态在 COMPLETED_HOLD_S 后到期，session 恢复 I。"""
    _reset()
    last = None

    await d._handle_envelope(_env_user_prompt())
    await d._handle_envelope(_env_pre())
    _adv(0.5)
    await d._handle_envelope(_env_post())
    await d._handle_envelope(_env_stop())

    # C 状态期间
    _adv(0.2)
    last = await d._pusher_tick(last)
    _assert("C" in _wire_states(), "C 状态应存在")

    # 跨过 COMPLETED_HOLD_S（2s）
    _adv(d.COMPLETED_HOLD_S + 0.5)
    last = await d._pusher_tick(last)
    states = _wire_states()
    # C 到期后 session 应推 I（或不在 wire 中，因为不再活跃）
    _assert("C" not in states,
            f"COMPLETED_HOLD_S 到期后不应再推 C，实际={states}")
    print("  ok  C 状态到期后恢复 I（不再庆祝）")


async def test_user_prompt_resets_completed():
    """新一轮 user_prompt 清除上一轮的 completed_until。"""
    _reset()
    last = None

    # 第一轮完成
    await d._handle_envelope(_env_user_prompt())
    await d._handle_envelope(_env_pre())
    _adv(0.5)
    await d._handle_envelope(_env_post())
    await d._handle_envelope(_env_stop())
    _assert(_sess().completed_until > _clock[0], "completed_until 应被设置")

    # 第二轮开始
    await d._handle_envelope(_env_user_prompt())
    _assert(_sess().completed_until == 0.0,
            f"user_prompt 应清零 completed_until，实际={_sess().completed_until}")
    _assert(_sess().turn_active is True, "turn_active 应为 True")
    print("  ok  user_prompt 清除 completed_until，turn_active=True")


async def test_stop_without_prior_error():
    """stop 在无 error 时触发 C；stop 在 error 状态（dizzy_until>now）时不触发 C。"""
    _reset()
    last = None

    # 先触发 dizzy
    await d._handle_envelope(_env_user_prompt())
    err_env = _ev("tool_error", {
        "tool": "Bash", "tool_use_id": "t1",
        "error_msg": "boom", "is_interrupt": False, "duration_ms": 0,
    })
    await d._handle_envelope(err_env)
    _assert(_sess().dizzy_until > _clock[0], "dizzy_until 应在未来")

    # stop 在 dizzy 期间到来
    await d._handle_envelope(_env_stop())
    _adv(0.2)
    last = await d._pusher_tick(last)
    # dizzy_until > now → stop 不设 completed_until → 不推 C
    _assert("C" not in _wire_states(),
            f"dizzy 期间 stop 不应推 C，实际={_wire_states()}")

    # dizzy 到期后（无事件不会自动标 dirty，手动触发推送）
    _adv(d.DIZZY_HOLD_S + 0.5)
    d._mark_dirty()
    last = await d._pusher_tick(last)
    _assert("E" not in _wire_states(),
            f"dizzy 到期后不应再推 E，实际={_wire_states()}")
    print("  ok  dizzy 期间 stop 不触发 C；dizzy 到期后不再推 E")


# ── runner ─────────────────────────────────────────────────────────────────

async def main():
    orig_time = d.time
    orig_send = d._send
    d.time = _MockTime()
    d._send = _capture_send

    tests = [
        ("test_stop_c_basic",                        test_stop_c_basic),
        ("test_turn_active_prevents_cleanup",         test_turn_active_prevents_cleanup),
        ("test_turn_active_w_state_no_tools",         test_turn_active_w_state_no_tools),
        ("test_turn_active_w_between_tools",          test_turn_active_w_between_tools),
        ("test_notification_p_state",                 test_notification_p_state),
        ("test_stop_clears_waiting",                  test_stop_clears_waiting),
        ("test_notification_after_stop_within_1s_ignored",
                                                      test_notification_after_stop_within_1s_ignored),
        ("test_c_state_expires_to_idle",              test_c_state_expires_to_idle),
        ("test_user_prompt_resets_completed",         test_user_prompt_resets_completed),
        ("test_stop_without_prior_error",             test_stop_without_prior_error),
        # 以下两个用例在 bug 修复前预期失败
        ("[BUG] test_cleanup_respects_completed_until",  test_cleanup_respects_completed_until),
        ("[BUG] test_stop_c_after_long_thinking",     test_stop_c_after_long_thinking),
    ]

    print(f"running {len(tests)} stop/C-state tests...\n")

    passed = 0
    failed = 0
    bugs_hit = 0

    try:
        for name, fn in tests:
            print(f"[{name}]")
            is_bug = name.startswith("[BUG]")
            try:
                await fn()
                passed += 1
                if is_bug:
                    print("  NOTE: [BUG] 用例通过 → bug 已修复 [OK]")
            except AssertionError as e:
                if is_bug:
                    print(f"  EXPECTED FAILURE (bug 未修复): {e}")
                    bugs_hit += 1
                else:
                    print(f"  UNEXPECTED FAILURE: {e}")
                    failed += 1
            print()
    finally:
        d.time = orig_time
        d._send = orig_send

    print("=" * 55)
    print(f"  passed:          {passed}")
    print(f"  failed:          {failed}  ← 非预期失败")
    print(f"  expected [BUG]:  {bugs_hit}  ← 已知 bug，修复后应为 0")
    print("=" * 55)

    if failed > 0:
        print("\n有非预期失败，退出码 1")
        return 1
    if bugs_hit > 0:
        print(f"\n{bugs_hit} 个已知 bug 待修复（用例已记录，修复后重跑验证）")
    else:
        print("\nALL TESTS PASSED（含 bug 修复验证）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
