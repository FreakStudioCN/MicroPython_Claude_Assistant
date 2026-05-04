#!/usr/bin/env python3
# ble_daemon.py —— 长驻 BLE 桥接守护进程 (v3)
#
# 输入: hook_bridge.py 通过 TCP 57320 推 v2 envelope
#       {type:"event", v:2, event:{kind, ...}, generic:{session_id, ...}}
# 输出: 翻译为 protocol.py v3 多 session wire
#       {"v":2, "sessions":[{id, running, waiting, completed, msg, category, error, interrupted, prompt}, ...]}
#       通过 BLE NUS 写到 ESP32
#
# 状态机: 每个 session_id 独立 _Session 对象，彻底消除全局状态竞争
#   approval 路径: 每个 _Session 有独立 decision_event/decision_value
#                  多 session 同时等审批互不干扰
#
# 推断兜底 (Stop hook 不冒,只能从沉默期推):
#   session._tools==0 and _approval_queue==0 and now-last_activity > threshold
#     → completed=True 短暂 (CELEBRATE 触发后清掉)
#     threshold = 8s if has_subagent else 4s
#
# 5Hz 节流: 状态变化只标 dirty, 单独 _pusher_task 每 200ms 推一次
#           approval 路径绕过节流同步推 (不能让用户等)
#
# session 生命周期:
#   活跃 (has_tools or recently_active or special_state) → 纳入 wire sessions 数组
#   30s 无活动且无工具 → 清理
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
APPROVAL_TIMEOUT_S = int(__import__('os').environ.get("APPROVAL_TIMEOUT_S", 60))
PUSH_INTERVAL_S = 0.2          # 5Hz throttle
TASK_COMPLETE_QUIET_S = 4.0    # PostTool 后 N 秒无新 PreTool → 推断 task_complete
COMPLETED_HOLD_S = 2.0         # completed=True 持续秒数 (覆盖 CELEBRATE 3s)
DIZZY_HOLD_S = 3.0             # tool_error / task_error msg="error" 持续秒数
SESSION_ACTIVE_TIMEOUT_S = 10.0  # 超过此时间无活动的 IDLE session 不纳入 wire
SESSION_CLEANUP_S = 30.0         # 超过此时间清理 session 对象

# 设备 wire msg 字段值
MSG_ERROR = "error"
MSG_COMPLETED = "completed"
APPROVE_PREFIX = "approve: "

# ── per-session 状态 ──────────────────────────────────────


class _Session:
    def __init__(self):
        self.tools: dict = {}       # tool_use_id → {tool, category, summary, status, ts}
        self.approval_queue: list = []
        self.has_subagent: bool = False
        self.current_error: str = ""
        self.current_interrupted: bool = False
        self.last_activity_ts: float = 0.0
        self.completed_until: float = 0.0
        self.completed_inferred_for_ts: float = 0.0
        self.dizzy_until: float = 0.0
        self.decision_event: Optional[asyncio.Event] = None  # 懒初始化
        self.decision_value: Optional[str] = None
        self.approval_in_progress: bool = False


_sessions: dict = {}   # session_id → _Session
_dirty = False         # 全局 dirty 标志（pusher 用）

# ── stub 模式 ─────────────────────────────────────────────
_stub = False

# ── BLE 连接 ──────────────────────────────────────────────
_client = None
_connected = False
_rx_buf = ""
_lock = asyncio.Lock()


# ── BLE 层 ─────────────────────────────────────────────────
def _on_disconnect(client):
    global _connected
    _connected = False
    print("[daemon] disconnected, will reconnect...")


def _on_notify(sender, data: bytearray):
    """设备 → PC: 接 {cmd:"permission", id, decision:"once|deny"} 解 approval。
    广播给所有正在等待的 session 的 decision_event。"""
    global _rx_buf
    _rx_buf += data.decode(errors="ignore")
    while "\n" in _rx_buf:
        line, _rx_buf = _rx_buf.split("\n", 1)
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("cmd") == "permission":
                decision = msg.get("decision", "deny")
                prompt_id = msg.get("id", "")
                # 找到对应 tool_use_id 所属的 session
                for sess in _sessions.values():
                    if sess.approval_in_progress and sess.approval_queue:
                        if sess.approval_queue[0] == prompt_id:
                            sess.decision_value = decision
                            if sess.decision_event:
                                sess.decision_event.set()
                            break
                else:
                    # 广播给所有等待审批的 session（兜底）
                    for sess in _sessions.values():
                        if sess.approval_in_progress and sess.decision_event:
                            sess.decision_value = decision
                            sess.decision_event.set()
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
    """推送 wire JSON 到 BLE（或 stub 打印）。"""
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


# ── per-session 状态翻译 ───────────────────────────────────

def _get_running_count(sess: _Session) -> int:
    return sum(1 for t in sess.tools.values() if t["status"] == "running")


def _get_current_category(sess: _Session) -> str:
    if sess.approval_queue:
        tid = sess.approval_queue[0]
        if tid in sess.tools:
            return sess.tools[tid].get("category", "")
    for t in sess.tools.values():
        if t["status"] == "running":
            return t.get("category", "")
    return ""


def _build_prompt(sess: _Session) -> Optional[dict]:
    if not sess.approval_queue:
        return None
    tid = sess.approval_queue[0]
    if tid not in sess.tools:
        return None
    t = sess.tools[tid]
    return {
        "id": tid,
        "tool": t["tool"],
        "hint": t["summary"][:80] if t["summary"] else ""
    }


def _build_msg(sess: _Session) -> str:
    if sess.approval_queue:
        tid = sess.approval_queue[0]
        if tid in sess.tools:
            return APPROVE_PREFIX + sess.tools[tid]["tool"]
    now = time.time()
    if sess.dizzy_until > now:
        return MSG_ERROR
    if sess.completed_until > now:
        return MSG_COMPLETED
    for t in sess.tools.values():
        if t["status"] == "running":
            summary = t["summary"][:40] if t["summary"] else ""
            return f"{t['tool']}: {summary}" if summary else t["tool"]
    return ""


def _session_to_wire(sid: str, sess: _Session) -> dict:
    return {
        "id":          sid[:8],
        "running":     _get_running_count(sess),
        "waiting":     len(sess.approval_queue),
        "completed":   sess.completed_until > time.time(),
        "msg":         _build_msg(sess),
        "category":    _get_current_category(sess),
        "error":       sess.current_error,
        "interrupted": sess.current_interrupted,
        "prompt":      _build_prompt(sess),
    }


def _to_device_wire() -> dict:
    """v3 wire: sessions 数组，只含活跃 session。"""
    now = time.time()
    active = []
    for sid, sess in list(_sessions.items()):
        has_tools = bool(sess.tools) or bool(sess.approval_queue)
        recently  = sess.last_activity_ts > 0 and (now - sess.last_activity_ts) < SESSION_ACTIVE_TIMEOUT_S
        special   = sess.completed_until > now or sess.dizzy_until > now
        if has_tools or recently or special:
            active.append(_session_to_wire(sid, sess))
    return {"v": 2, "sessions": active}


def _mark_dirty():
    global _dirty
    _dirty = True


def _enter_error_state(sess: _Session, now: float, hard_reset: bool, error_msg: str, is_interrupt: bool) -> None:
    if hard_reset:
        sess.tools.clear()
        sess.approval_queue.clear()

    sess.current_error = error_msg[:80] if error_msg else ""
    sess.current_interrupted = is_interrupt
    sess.dizzy_until = now + DIZZY_HOLD_S if not is_interrupt else 0.0
    sess.completed_until = 0.0
    sess.last_activity_ts = now
    sess.completed_inferred_for_ts = sess.last_activity_ts
    _mark_dirty()


# ── 5Hz 推送 task ──────────────────────────────────────────
async def _pusher_tick(last_pushed_wire):
    global _dirty

    now = time.time()

    # 清理长期无活动 session
    for sid in [k for k, s in list(_sessions.items())
                if not s.tools and not s.approval_queue
                and s.last_activity_ts > 0
                and now - s.last_activity_ts > SESSION_CLEANUP_S]:
        del _sessions[sid]

    # 每个 session 的 task_complete 推断
    for sess_id, sess in list(_sessions.items()):
        threshold = 8.0 if sess.has_subagent else TASK_COMPLETE_QUIET_S
        if (
            len(sess.tools) == 0
            and len(sess.approval_queue) == 0
            and sess.last_activity_ts > 0
            and now - sess.last_activity_ts > threshold
            and sess.completed_inferred_for_ts != sess.last_activity_ts
            and sess.dizzy_until < now
        ):
            sess.completed_until = now + COMPLETED_HOLD_S
            sess.completed_inferred_for_ts = sess.last_activity_ts
            _mark_dirty()
            print(f"[infer] task_complete session={sess_id[:8]!r} (quiet={now - sess.last_activity_ts:.1f}s)")

        # completed 到期标 dirty
        if sess.completed_until > 0 and sess.completed_until <= now:
            if last_pushed_wire:
                for s in last_pushed_wire.get("sessions", []):
                    if s.get("completed"):
                        _mark_dirty()
                        break

    if _dirty:
        wire = _to_device_wire()
        if wire != last_pushed_wire:
            await _send(wire)
            last_pushed_wire = wire
        _dirty = False
    return last_pushed_wire


async def _pusher_task():
    last_pushed_wire = None
    while True:
        await asyncio.sleep(PUSH_INTERVAL_S)
        last_pushed_wire = await _pusher_tick(last_pushed_wire)


# ── v2 envelope dispatch ───────────────────────────────────
async def _handle_envelope(env: dict) -> dict:
    """根据 event.kind 改对应 session 的状态。返回给 hook_bridge 的 dict。"""
    session_id = env.get("generic", {}).get("session_id", "") or "default"
    sess = _sessions.setdefault(session_id, _Session())
    if sess.decision_event is None:
        sess.decision_event = asyncio.Event()

    event = env.get("event") or {}
    kind = event.get("kind", "")
    print(f"[req v2] session={session_id[:8]!r} kind={kind!r}")

    now = time.time()

    if kind == "tool_start":
        tool = event.get("tool", "")
        tool_use_id = event.get("tool_use_id", "")
        category = event.get("tool_category", "")
        summary = event.get("summary", "")

        if not tool_use_id:
            print(f"[warn] tool_start missing tool_use_id, ignoring")
            return {"decision": "once"}

        sess.tools[tool_use_id] = {
            "tool": tool,
            "category": category,
            "summary": summary,
            "status": "waiting" if event.get("needs_approval") else "running",
            "ts": now,
        }
        sess.last_activity_ts = now

        if not event.get("needs_approval"):
            _mark_dirty()
            return {"decision": "once"}

        # approval path: 同步阻塞等设备
        sess.approval_queue.append(tool_use_id)
        sess.approval_in_progress = True
        sess.decision_event.clear()
        sess.decision_value = None
        await _send(_to_device_wire())

        if _stub:
            print("[approval] stub mode → auto-once")
            decision = "once"
        else:
            try:
                await asyncio.wait_for(sess.decision_event.wait(), timeout=APPROVAL_TIMEOUT_S)
                decision = sess.decision_value or "deny"
            except asyncio.TimeoutError:
                print("[approval] timeout → deny")
                decision = "deny"

        if tool_use_id in sess.approval_queue:
            sess.approval_queue.remove(tool_use_id)
        sess.approval_in_progress = False
        sess.last_activity_ts = time.time()

        if decision == "once":
            if tool_use_id in sess.tools:
                sess.tools[tool_use_id]["status"] = "running"
        else:
            if tool_use_id in sess.tools:
                del sess.tools[tool_use_id]
            sess.completed_inferred_for_ts = sess.last_activity_ts

        _mark_dirty()
        await _send(_to_device_wire())
        return {"decision": decision}

    if kind == "tool_done":
        tool_use_id = event.get("tool_use_id", "")
        interrupted = event.get("interrupted", False)

        if tool_use_id in sess.tools:
            del sess.tools[tool_use_id]

        sess.last_activity_ts = now

        if len(sess.tools) == 0:
            sess.current_error = ""
            sess.current_interrupted = interrupted

        _mark_dirty()
        return {"ok": True}

    if kind == "tool_error":
        tool_use_id = event.get("tool_use_id", "")
        error_msg = event.get("error_msg", "")
        is_interrupt = event.get("is_interrupt", False)

        if tool_use_id in sess.tools:
            del sess.tools[tool_use_id]

        _enter_error_state(sess, now, hard_reset=False, error_msg=error_msg, is_interrupt=is_interrupt)
        return {"ok": True}

    if kind == "tool_batch_done":
        sess.last_activity_ts = now
        _mark_dirty()
        return {"ok": True}

    if kind == "user_prompt":
        sess.last_activity_ts = now
        sess.completed_until = 0.0
        sess.has_subagent = False
        sess.current_error = ""
        sess.current_interrupted = False
        _mark_dirty()
        return {"ok": True}

    if kind == "task_error":
        error_msg = event.get("error", "")
        _enter_error_state(sess, now, hard_reset=True, error_msg=error_msg, is_interrupt=False)
        return {"ok": True}

    if kind == "subagent_start":
        sess.has_subagent = True
        return {"ok": True}

    if kind in ("notification", "unknown"):
        return {"ok": True}

    return {"ok": True}


# ── TCP server ─────────────────────────────────────────────
MAX_ENVELOPE_BYTES = 64 * 1024


async def _handle_client(reader, writer):
    try:
        data = await asyncio.wait_for(reader.read(MAX_ENVELOPE_BYTES), timeout=35)
        env = json.loads(data.decode())
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
