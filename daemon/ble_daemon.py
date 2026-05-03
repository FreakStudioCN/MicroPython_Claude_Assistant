#!/usr/bin/env python3
# ble_daemon.py —— 长驻 BLE 桥接守护进程 (v2)
#
# 输入: hook_bridge.py 通过 TCP 57320 推 v2 envelope
#       {type:"event", v:2, event:{kind, ...}, generic:{...}}
# 输出: 翻译为 protocol.py v2 设备 wire (9 字段: running/waiting/completed/msg/tokens/prompt/category/error/interrupted)
#       通过 BLE NUS 写到 ESP32
#
# 状态机 (v2 - 基于 _tools 字典):
#   tool_start → _tools[tool_use_id] = {tool, category, summary, status:"running"|"waiting", ts}
#                needs_approval=True 时加入 _approval_queue, 阻塞等设备回 once|deny
#   tool_done  → 从 _tools 删除, mark last_activity
#   tool_error → 从 _tools 删除, 设置 _current_error, _dizzy_until 短暂
#   task_error → 清空 _tools, 设置 _current_error, _dizzy_until
#   tool_batch_done → soft task_complete 候选信号
#   user_prompt → 清 completed, 清 _has_subagent
#   subagent_start → 设置 _has_subagent=True
#
# 推断兜底 (Stop hook 不冒,只能从沉默期推):
#   len(_tools)==0 and len(_approval_queue)==0 and now-last_activity > threshold
#     → completed=True 短暂 (CELEBRATE 触发后清掉)
#     threshold = 8s if _has_subagent else 4s
#
# 5Hz 节流: 状态变化只标 dirty, 单独 _pusher_task 每 200ms 推一次
#           approval 路径绕过节流同步推 (不能让用户等)
#
# stub 模式: --stub 不启 BLE,_send 改 stdout 打印,用于无设备 e2e 测试

import argparse
import asyncio
import json
import sys
import time
from typing import Optional

HOST = "127.0.0.1"
PORT = 57320

# BLE 常量
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME_PREFIX = "Claude"

# 业务常量
APPROVAL_TIMEOUT_S = 30
PUSH_INTERVAL_S = 0.2          # 5Hz throttle
TASK_COMPLETE_QUIET_S = 4.0    # PostTool 后 N 秒无新 PreTool → 推断 task_complete
COMPLETED_HOLD_S = 2.0         # completed=True 持续秒数 (覆盖 CELEBRATE 3s)
DIZZY_HOLD_S = 3.0             # tool_error / task_error msg="error" 持续秒数

# 设备 wire msg 字段值 (设备 firmware 可能 startswith 匹配 / 等值检查,改这些前先核对 state.py)
MSG_ERROR = "error"
MSG_COMPLETED = "completed"
APPROVE_PREFIX = "approve: "

# ── 全局状态 ───────────────────────────────────────────
_stub = False  # --stub 模式

# v2 状态机: 基于 _tools 字典
_tools: dict = {}  # tool_use_id → {tool, category, summary, status:"running"|"waiting", ts}
_approval_queue: list = []  # 等待审批的 tool_use_id 列表 (串行化多审批)
_has_subagent: bool = False  # 是否有子 Agent 活动 (影响 completed 推断阈值)
_current_error: str = ""  # 最近一次错误信息 (截断 80 字)
_current_interrupted: bool = False  # 最近一次是否被用户中断

_dirty = False
_last_activity_ts = 0.0
_completed_until = 0.0       # > now → completed=True
_completed_inferred_for_ts = 0.0  # one-shot guard: 已为这个 _last_activity_ts 推过 task_complete
_dizzy_until = 0.0           # > now → msg="error"

# BLE 连接
_client = None
_connected = False
_decision_event = asyncio.Event()
_decision_value: Optional[str] = None
_rx_buf = ""
_lock = asyncio.Lock()
_approval_in_progress = False


# ── BLE 层 ─────────────────────────────────────────────
def _on_disconnect(client):
    global _connected
    _connected = False
    print("[daemon] disconnected, will reconnect...")


def _on_notify(sender, data: bytearray):
    """设备 → PC: 接 {cmd:"permission", id, decision:"once|deny"} 解 approval。"""
    global _decision_value, _rx_buf
    _rx_buf += data.decode(errors="ignore")
    while "\n" in _rx_buf:
        line, _rx_buf = _rx_buf.split("\n", 1)
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("cmd") == "permission":
                _decision_value = msg.get("decision", "deny")
                _decision_event.set()
        except Exception:
            pass


async def _connect_loop():
    global _client, _connected
    if _stub:
        return
    from bleak import BleakClient, BleakScanner
    while True:
        if _connected:
            await asyncio.sleep(1)
            continue
        try:
            devices = await BleakScanner.discover(timeout=5.0)
            addr = next(
                (d.address for d in devices if d.name and d.name.startswith(DEVICE_NAME_PREFIX)),
                None,
            )
            if not addr:
                print("[daemon] device not found, retrying...")
                await asyncio.sleep(3)
                continue
            _client = BleakClient(addr, disconnected_callback=_on_disconnect)
            await _client.connect()
            await _client.start_notify(NUS_TX, _on_notify)
            _connected = True
            print(f"[daemon] connected to {addr}")
            await asyncio.sleep(1.0)
        except Exception as e:
            print(f"[daemon] connect failed: {e}")
            _client = None
            _connected = False
            await asyncio.sleep(3)


async def _send(payload: dict):
    """v2 设备 wire 9 字段: running/waiting/completed/msg/tokens/prompt/category/error/interrupted。
    多余字段会被设备 StatusMsg 忽略但浪费 BLE 带宽。"""
    if _stub:
        print(f"[stub-send] t={time.time():.3f} {json.dumps(payload, ensure_ascii=False)}")
        return
    if not _connected or _client is None:
        print(f"[send] skipped (not connected): {payload}")
        return
    data = (json.dumps(payload) + "\n").encode()
    print(f"[send] t={time.time():.3f} {payload} ({len(data)}B)")
    for i in range(0, len(data), 20):
        await _client.write_gatt_char(NUS_RX, data[i:i+20], response=False)


# ── 状态翻译: 内部 v2 状态 → v2 设备 wire ──────────────
def _get_running_count() -> int:
    """统计 status="running" 的工具数。"""
    return sum(1 for t in _tools.values() if t["status"] == "running")


def _get_waiting_count() -> int:
    """等待审批的工具数 = _approval_queue 长度。"""
    return len(_approval_queue)


def _get_current_category() -> str:
    """返回当前正在运行的工具类别 (优先 waiting > running)。
    多个工具时返回第一个; 无工具时返回空串。"""
    if _approval_queue:
        tid = _approval_queue[0]
        if tid in _tools:
            return _tools[tid].get("category", "")
    for t in _tools.values():
        if t["status"] == "running":
            return t.get("category", "")
    return ""


def _build_prompt() -> Optional[dict]:
    """从 _approval_queue[0] 构建 prompt 字段。"""
    if not _approval_queue:
        return None
    tid = _approval_queue[0]
    if tid not in _tools:
        return None
    t = _tools[tid]
    return {
        "id": tid,
        "tool": t["tool"],
        "hint": t["summary"][:80] if t["summary"] else ""
    }


def _build_msg() -> str:
    """msg 字段优先级: approval > error > completed > running > 默认空 (设备显 IDLE)。"""
    if _approval_queue:
        tid = _approval_queue[0]
        if tid in _tools:
            return APPROVE_PREFIX + _tools[tid]["tool"]
    now = time.time()
    if _dizzy_until > now:
        return MSG_ERROR
    if _completed_until > now:
        return MSG_COMPLETED
    # running: 显示第一个 running 工具
    for t in _tools.values():
        if t["status"] == "running":
            summary = t["summary"][:40] if t["summary"] else ""
            return f"{t['tool']}: {summary}" if summary else t["tool"]
    return ""


def _to_device_wire() -> dict:
    """v2 wire schema, 9 字段。"""
    return {
        "running":     _get_running_count(),
        "waiting":     _get_waiting_count(),
        "completed":   _completed_until > time.time(),
        "msg":         _build_msg(),
        "tokens":      0,  # TODO: 下个 PR 从 tool_response 抽
        "prompt":      _build_prompt(),
        "category":    _get_current_category(),
        "error":       _current_error,
        "interrupted": _current_interrupted,
    }


def _mark_dirty():
    global _dirty
    _dirty = True


def _enter_error_state(now: float, hard_reset: bool, error_msg: str, is_interrupt: bool) -> None:
    """tool_error / task_error 共同状态推进。
    hard_reset=False (tool_error): 单个工具失败，从 _tools 删除该工具。
    hard_reset=True  (task_error): 整个 turn 失败，清空 _tools 和 _approval_queue。
    inference guard 锁住，避免 dizzy 过期后 quiet 4s 又被推 task_complete。"""
    global _last_activity_ts, _dizzy_until, _completed_until, _completed_inferred_for_ts
    global _current_error, _current_interrupted

    if hard_reset:
        _tools.clear()
        _approval_queue.clear()

    _current_error = error_msg[:80] if error_msg else ""
    _current_interrupted = is_interrupt
    # Bug fix: 中断时不触发 DIZZY，直接回 IDLE
    _dizzy_until = now + DIZZY_HOLD_S if not is_interrupt else 0.0
    _completed_until = 0.0
    _last_activity_ts = now
    _completed_inferred_for_ts = _last_activity_ts
    _mark_dirty()


# ── 5Hz 推送 task ──────────────────────────────────────
async def _pusher_tick(last_pushed_wire):
    """单次 pusher 迭代逻辑, 返回更新后的 last_pushed_wire。
    抽出来方便单测 (mock time.time + 直接 await 这个函数)。"""
    global _dirty, _completed_until, _completed_inferred_for_ts

    # task_complete 推断: 静默期 + 之前有过活动 + 还没为这个 _last_activity_ts 推过
    # 阈值动态化: 有子 Agent 时 8s, 否则 4s
    now = time.time()
    threshold = 8.0 if _has_subagent else TASK_COMPLETE_QUIET_S
    if (
        len(_tools) == 0
        and len(_approval_queue) == 0
        and _last_activity_ts > 0
        and now - _last_activity_ts > threshold
        and _completed_inferred_for_ts != _last_activity_ts  # one-shot 关键
        and _dizzy_until < now           # 错误状态不要被 completed 盖
    ):
        _completed_until = now + COMPLETED_HOLD_S
        _completed_inferred_for_ts = _last_activity_ts
        _mark_dirty()
        print(f"[infer] task_complete (quiet={now - _last_activity_ts:.1f}s, threshold={threshold:.1f}s, subagent={_has_subagent})")

    # completed 到期了也是状态变化,标 dirty 让 wire 回归 IDLE
    if _completed_until > 0 and _completed_until <= now and last_pushed_wire and last_pushed_wire.get("completed"):
        _mark_dirty()

    if _dirty:
        wire = _to_device_wire()
        if wire != last_pushed_wire:  # 同 wire 不重复推, 省 BLE
            await _send(wire)
            last_pushed_wire = wire
        _dirty = False
    return last_pushed_wire


async def _pusher_task():
    """5Hz 节流: dirty 才推, 同时跑 task_complete 推断。"""
    last_pushed_wire = None
    while True:
        await asyncio.sleep(PUSH_INTERVAL_S)
        last_pushed_wire = await _pusher_tick(last_pushed_wire)


# ── v2 envelope dispatch ───────────────────────────────
async def _handle_envelope(env: dict) -> dict:
    """根据 event.kind 改 daemon 状态。返回给 hook_bridge 的 dict。
    仅 tool_start needs_approval=True 走 approval 同步阻塞 path。"""
    global _last_activity_ts, _dizzy_until, _approval_in_progress, _decision_value
    global _completed_until, _completed_inferred_for_ts, _has_subagent
    global _current_error, _current_interrupted

    event = env.get("event") or {}
    kind = event.get("kind", "")
    print(f"[req v2] kind={kind!r} event={event}")

    now = time.time()

    if kind == "tool_start":
        tool = event.get("tool", "")
        tool_use_id = event.get("tool_use_id", "")
        category = event.get("tool_category", "")
        summary = event.get("summary", "")

        if not tool_use_id:
            print(f"[warn] tool_start missing tool_use_id, ignoring")
            return {"decision": "once"}

        # 添加到 _tools
        _tools[tool_use_id] = {
            "tool": tool,
            "category": category,
            "summary": summary,
            "status": "waiting" if event.get("needs_approval") else "running",
            "ts": now,
        }
        _last_activity_ts = now

        if not event.get("needs_approval"):
            _mark_dirty()
            return {"decision": "once"}

        # approval path: 同步阻塞等设备
        _approval_queue.append(tool_use_id)
        _approval_in_progress = True
        _decision_event.clear()
        _decision_value = None
        # approval 绕过 5Hz 立刻推, 否则用户要等 200ms 看 LED
        await _send(_to_device_wire())

        if _stub:
            # stub 模式无真设备,自动 once,免锁 Claude Code 30s
            print("[approval] stub mode → auto-once")
            decision = "once"
        else:
            try:
                await asyncio.wait_for(_decision_event.wait(), timeout=APPROVAL_TIMEOUT_S)
                decision = _decision_value or "deny"
            except asyncio.TimeoutError:
                print("[approval] timeout → deny")
                decision = "deny"

        # 清 approval 状态
        if tool_use_id in _approval_queue:
            _approval_queue.remove(tool_use_id)
        _approval_in_progress = False

        # 刷新到 wait 之后的真实时间, 防 quiet 立刻满足 (codex P2 bug 2)
        _last_activity_ts = time.time()

        if decision == "once":
            if tool_use_id in _tools:
                _tools[tool_use_id]["status"] = "running"
        else:
            # deny / timeout: 从 _tools 删除, 不让 pusher 把它当 task_complete 候选
            if tool_use_id in _tools:
                del _tools[tool_use_id]
            _completed_inferred_for_ts = _last_activity_ts

        _mark_dirty()
        await _send(_to_device_wire())  # 立即推, 别等 throttle
        return {"decision": decision}

    if kind == "tool_done":
        tool_use_id = event.get("tool_use_id", "")
        interrupted = event.get("interrupted", False)

        if tool_use_id in _tools:
            del _tools[tool_use_id]

        _last_activity_ts = now

        # 清除 error/interrupted 状态 (如果没有其他工具在 error 状态)
        if len(_tools) == 0:
            _current_error = ""
            _current_interrupted = interrupted  # Bug fix: 使用读到的值而非写死 False

        _mark_dirty()
        return {"ok": True}

    if kind == "tool_error":
        tool_use_id = event.get("tool_use_id", "")
        error_msg = event.get("error_msg", "")
        is_interrupt = event.get("is_interrupt", False)

        if tool_use_id in _tools:
            del _tools[tool_use_id]

        _enter_error_state(now, hard_reset=False, error_msg=error_msg, is_interrupt=is_interrupt)
        return {"ok": True}

    if kind == "tool_batch_done":
        # 不直接改 _tools (PostToolUse 已分别删过), 只刷活动时间
        _last_activity_ts = now
        _mark_dirty()
        return {"ok": True}

    if kind == "user_prompt":
        _last_activity_ts = now
        # 清 completed 让设备退出 CELEBRATE, 准备进 BUSY
        _completed_until = 0.0
        # 新 turn 开始, 清除 subagent 标志 (假设新 turn 不继承上个 turn 的 subagent)
        _has_subagent = False
        # Bug fix: 清除旧 turn 的错误状态
        _current_error = ""
        _current_interrupted = False
        _mark_dirty()
        return {"ok": True}

    if kind == "task_error":
        # StopFailure: API 错 / stream timeout, 整个 turn 算失败
        error_msg = event.get("error", "")
        _enter_error_state(now, hard_reset=True, error_msg=error_msg, is_interrupt=False)
        return {"ok": True}

    if kind == "subagent_start":
        _has_subagent = True
        # subagent 启动不算活动 (不刷 _last_activity_ts), 只影响 completed 阈值
        return {"ok": True}

    if kind in ("notification", "unknown"):
        # 当前阶段仅记录,不改基础状态
        return {"ok": True}

    return {"ok": True}


# ── TCP server ─────────────────────────────────────────
MAX_ENVELOPE_BYTES = 64 * 1024  # 远大于实测最大 envelope (~1.5KB),防超大 tool_input 截断


async def _handle_client(reader, writer):
    try:
        # hook_bridge 发完 SHUT_WR, read(N) 会读到 EOF 或 N 字节为止
        data = await asyncio.wait_for(reader.read(MAX_ENVELOPE_BYTES), timeout=35)
        env = json.loads(data.decode())
        # approval path 不持锁 (需长时间等设备); 其它路径串行化
        kind = (env.get("event") or {}).get("kind")
        is_approval = kind == "tool_start" and (env.get("event") or {}).get("needs_approval")
        if is_approval:
            resp = await _handle_envelope(env)
        else:
            async with _lock:
                resp = await _handle_envelope(env)
    except Exception as e:
        resp = {"ok": True, "error": str(e)}
    writer.write(json.dumps(resp).encode())
    await writer.drain()
    writer.close()


async def main():
    server = await asyncio.start_server(_handle_client, HOST, PORT)
    print(f"[daemon] listening on {HOST}:{PORT}  stub={_stub}")
    async with server:
        await asyncio.gather(
            server.serve_forever(),
            _connect_loop(),
            _pusher_task(),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stub", action="store_true",
                        help="跳过 BLE 连接, _send 改 stdout 打印用于 e2e 测试")
    args = parser.parse_args()
    _stub = args.stub
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[daemon] bye")
        sys.exit(0)
