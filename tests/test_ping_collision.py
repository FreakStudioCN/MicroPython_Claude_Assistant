#!/usr/bin/env python3
# tests/test_ping_collision.py
# 验证两道防碰撞机制：
#   A. ping 后静默期（POST_PING_COOLDOWN_S=0.3）：ping 发出 300ms 内 pusher 不推送
#   B. PENDING 重发：有审批待处理时每 tick 必发，绕过去重
#
# 跑法: python tests/test_ping_collision.py   （退出码 0 = pass）

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))
import ble_daemon as d


# ── mock clock ─────────────────────────────────────────────
_clock = [1000.0]


class _MockTime:
    @staticmethod
    def time():
        return _clock[0]


def _set(t): _clock[0] = t
def _adv(dt): _clock[0] += dt


# ── capture _send ───────────────────────────────────────────
_sent = []


async def _capture(payload):
    _sent.append(dict(payload))


# ── helpers ─────────────────────────────────────────────────
def _g(sid="s"):
    return {"session_id": sid, "cwd": "/x", "transcript_path": "/x.j",
            "hook_event_name": "X", "permission_mode": "auto"}


def _env_pre(tool="Bash", sid="s", tool_use_id="t1",
             needs_approval=True, category="exec", risk_level="normal"):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_start", "tool": tool, "summary": "ls",
                      "needs_approval": needs_approval, "tool_use_id": tool_use_id,
                      "tool_category": category, "risk_level": risk_level},
            "generic": _g(sid)}


def _reset():
    d._sessions.clear()
    d._dirty = False
    d._stub = True
    d._device_online = True
    d._last_ping_ts = 0.0
    d._last_pong_ts = 0.0
    d._last_pending_send_ts = 0.0
    _sent.clear()
    _set(1000.0)


# ── test runner ─────────────────────────────────────────────
_pass = []
_fail = []


def _check(name, cond, detail=""):
    if cond:
        _pass.append(name)
        print(f"  [PASS] {name}")
    else:
        _fail.append(name)
        print(f"  [FAIL] {name}" + (f": {detail}" if detail else ""))


async def _run_tick(last=None):
    """运行一次 _pusher_tick，返回新的 last_pushed_wire。"""
    return await d._pusher_tick(last)


# ═══════════════════════════════════════════════════════════
# A. ping 后静默期测试
# ═══════════════════════════════════════════════════════════

async def test_cooldown_blocks_send():
    """ping 发出后 200ms 内，即使 dirty，pusher 应跳过推送。"""
    _reset()
    d._mark_dirty()
    # 模拟刚发完 ping
    d._last_ping_ts = _clock[0]           # ping 就在 now
    _adv(0.2)                             # 过了 200ms，仍在 300ms 静默期内

    last = await _run_tick(None)
    _check("cooldown_blocks_send",
           len(_sent) == 0,
           f"期望 0 条发送，实际 {len(_sent)} 条")


async def test_cooldown_allows_send_after_expiry():
    """ping 发出后 350ms，静默期过了，脏标记应正常推送。"""
    _reset()
    d._mark_dirty()
    d._last_ping_ts = _clock[0]
    _adv(0.35)                            # 超过 300ms 静默期

    last = await _run_tick(None)
    _check("cooldown_allows_after_expiry",
           len(_sent) == 1,
           f"期望 1 条发送，实际 {len(_sent)} 条")


async def test_cooldown_zero_ping_ts():
    """_last_ping_ts=0 时（daemon 刚启动，从未发过 ping），不触发静默期。"""
    _reset()
    d._mark_dirty()
    d._last_ping_ts = 0.0                 # 初始状态
    _adv(0.1)

    last = await _run_tick(None)
    _check("cooldown_zero_ping_no_block",
           len(_sent) == 1,
           f"初始状态不应被阻塞，实际发送 {len(_sent)} 条")


# ═══════════════════════════════════════════════════════════
# B. PENDING 重发测试
# ═══════════════════════════════════════════════════════════

def _make_pending_sess():
    """构造一个有待审批工具的 session，注入 _sessions["s"]。"""
    sess = d._Session()
    sess.decision_event = asyncio.Event()
    sess.tools["t1"] = {"tool": "Bash", "category": "exec", "summary": "ls",
                        "status": "waiting", "ts": _clock[0], "risk_level": "normal"}
    sess.approval_queue.append("t1")
    sess.approval_in_progress = True
    sess.last_activity_ts = _clock[0]
    d._sessions["s"] = sess
    return sess


async def test_pending_resend_bypasses_dedup():
    """approval_queue 非空时，1s 后仍会绕过去重再发一次。"""
    _reset()
    _make_pending_sess()

    _adv(0.5)                             # 静默期已过
    # 第一次 tick：发出，记录 last_pushed_wire
    last = await _run_tick(None)
    n_after_first = len(_sent)

    # 第二次 tick：过 1.1s，wire 相同但 pending_resend_due → 应再发
    _adv(1.1)
    last = await _run_tick(last)
    n_after_second = len(_sent)

    _check("pending_resend_tick1",
           n_after_first == 1,
           f"首次 tick 应发 1 条，实际 {n_after_first}")
    _check("pending_resend_tick2_same_wire",
           n_after_second == 2,
           f"第二次 tick（1.1s 后）wire 未变但 pending，应再发 1 条，实际累计 {n_after_second}")


async def test_pending_resend_stops_at_max():
    """达到 MAX_PENDING_RESENDS 次后，即使仍有 pending，不再重发。
    逻辑：首发（dirty/last=None）+ MAX 次重发 = MAX+1 条；之后不再发。"""
    _reset()
    sess = _make_pending_sess()
    # 初始 count=0，让重发计数从零开始积累到上限
    # 模拟 approval 路径首发：dirty + 记录 _last_pending_send_ts（count 保持 0）
    d._mark_dirty()
    _adv(0.5)
    d._last_pending_send_ts = _clock[0]   # 标记"刚刚首发"，使 pending_resend_due=False
    last = await _run_tick(None)           # dirty send，count 保持 0
    n0 = len(_sent)                        # 应 = 1

    # 连续触发 MAX 次重发（每次间隔 1.1s）
    for _ in range(d.MAX_PENDING_RESENDS):
        _adv(1.1)
        last = await _run_tick(last)

    n_at_max = len(_sent)   # 应 = 1 + MAX_PENDING_RESENDS

    # 再触发两次：count 已到上限，不再重发
    _adv(1.1)
    last = await _run_tick(last)
    _adv(1.1)
    last = await _run_tick(last)
    n_after = len(_sent)    # 应与 n_at_max 相同

    expected_max = 1 + d.MAX_PENDING_RESENDS
    _check("pending_stops_at_max_total",
           n_at_max == expected_max,
           f"首发+{d.MAX_PENDING_RESENDS}次重发应共 {expected_max} 条，实际 {n_at_max}")
    _check("pending_stops_after_max",
           n_after == expected_max,
           f"超限后不应再发，实际累计 {n_after}")


async def test_no_pending_no_resend():
    """approval_queue 为空时，相同 wire 不重发（正常去重）。"""
    _reset()
    d._mark_dirty()
    _adv(0.5)

    last = await _run_tick(None)          # 发出一次，记录 wire
    n1 = len(_sent)
    d._mark_dirty()
    _adv(0.2)
    last = await _run_tick(last)          # 内容未变 + 无 pending → 不重发
    n2 = len(_sent)

    _check("no_pending_dedup_works",
           n1 == 1 and n2 == 1,
           f"无 pending 时去重应生效，发送次数 n1={n1} n2={n2}")


# ═══════════════════════════════════════════════════════════
# C. 组合场景：ping 碰撞 + PENDING 自愈
# ═══════════════════════════════════════════════════════════

async def test_collision_scenario_pending_recovers():
    """
    模拟生产碰撞场景：
      t=0     发 ping（_last_ping_ts=0）
      t=+50ms PreToolUse(Bash) 到来，状态变 PENDING
              此时在静默期内，第一次 tick 被屏蔽
      t=+200ms tick → 仍在静默期，屏蔽
      t=+350ms tick → 静默期过，PENDING 消息发出
    验证：设备最终能收到审批请求（在 350ms 内而非永久丢失）。
    """
    _reset()
    _set(2000.0)

    # t=0: ping 发出
    d._last_ping_ts = 2000.0

    # t=+50ms: Bash 审批到来
    _set(2000.050)
    sess = d._Session()
    sess.decision_event = asyncio.Event()
    sess.tools["t1"] = {"tool": "Bash", "category": "exec", "summary": "ls",
                        "status": "waiting", "ts": 2000.050, "risk_level": "normal"}
    sess.approval_queue.append("t1")
    sess.approval_in_progress = True
    sess.last_activity_ts = 2000.050
    d._sessions["s"] = sess
    d._mark_dirty()

    # t=+200ms tick → 在静默期
    _set(2000.200)
    last = await _run_tick(None)
    n_200 = len(_sent)

    # t=+350ms tick → 静默期结束
    _set(2000.350)
    last = await _run_tick(last)
    n_350 = len(_sent)

    _check("collision_blocked_at_200ms",
           n_200 == 0,
           f"200ms 时应被屏蔽，发送了 {n_200} 条")
    _check("collision_recovered_at_350ms",
           n_350 == 1,
           f"350ms 时应发出 1 条，实际 {n_350} 条")

    # 再过 1.1s（超过 MIN_PENDING_RESEND_S），PENDING 重发
    _set(2001.450)
    last = await _run_tick(last)
    _check("collision_pending_resend_continues",
           len(_sent) == 2,
           f"PENDING 重发应再发 1 条，实际累计 {len(_sent)} 条")


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

async def _main():
    d.time = _MockTime
    d._send = _capture

    print("\n── A. ping 后静默期 ──────────────────────────────────")
    await test_cooldown_blocks_send()
    await test_cooldown_allows_send_after_expiry()
    await test_cooldown_zero_ping_ts()

    print("\n── B. PENDING 重发 ───────────────────────────────────")
    await test_pending_resend_bypasses_dedup()
    await test_pending_resend_stops_at_max()
    await test_no_pending_no_resend()

    print("\n── C. 组合场景 ───────────────────────────────────────")
    await test_collision_scenario_pending_recovers()

    print(f"\n{'='*50}")
    print(f"结果: {len(_pass)} passed / {len(_fail)} failed")
    if _fail:
        print("失败项:", _fail)
    return len(_fail)


if __name__ == "__main__":
    exit(asyncio.run(_main()))
