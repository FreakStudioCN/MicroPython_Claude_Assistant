#!/usr/bin/env python3
# ble_daemon.py —— 长驻 BLE 桥接守护进程 (v4)
#
# 输入: hook_bridge.py 通过 TCP 57320 推 v2 envelope
#       {type:"event", v:2, event:{kind, ...}, generic:{session_id, ...}}
# 输出: 翻译为 protocol.py v4 精简 wire（1-4 BLE chunks，原 v3 需 9-16 chunks）
#       {"ss":[{"s":"I"}]}                                    → 1 chunk
#       {"ss":[{"s":"W","m":"Bash"}]}                         → 2 chunks
#       {"ss":[{"s":"P","t":"Bash","h":"cd /proj && gh pr"}]} → 3 chunks
#       状态码: I=IDLE W=WORKING P=PENDING E=ERROR C=CELEBRATE
#       通过 BLE NUS 写到 ESP32
# 设备→PC 审批回传: {"d":"once"/"deny","n":session_idx}
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
HEARTBEAT_INTERVAL_S = 10.0      # 心跳间隔
HEARTBEAT_TIMEOUT_S = 30.0       # 3 次心跳未响应 → 判定离线
POST_PING_COOLDOWN_S = 0.3       # ping 发出后屏蔽 BLE 推送的静默期（避免 pong 写入时丢包）
MIN_PENDING_RESEND_S = 1.0       # PENDING 重发最小间隔（防止 200ms 连发淹没 BLE 队列）
MAX_PENDING_RESENDS = 5          # 每次审批最多重发 5 次（之后认为设备已收到）

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
        self.pending_resend_count: int = 0  # 当前审批已重发次数


_sessions: dict = {}   # session_id → _Session
_dirty = False         # 全局 dirty 标志（pusher 用）

# ── stub 模式 ─────────────────────────────────────────────
_stub = False
_force_offline = False  # --offline 标志：强制 device_online=False，覆盖 stub 的在线假设

# ── BLE 连接 ──────────────────────────────────────────────
_client = None
_connected = False
_rx_buf = ""
_lock = None
_send_lock = None        # 串行化所有 BLE 物理写入，防止分包交叉
_device_online = False         # 心跳判定：设备是否在线
_last_pong_ts = 0.0            # 最后一次收到 pong 的时间戳
_last_ping_ts = 0.0            # 最后一次发出 ping 的时间戳（用于 POST_PING_COOLDOWN）
_last_pending_send_ts = 0.0    # 最后一次推送 PENDING 状态的时间戳（用于 MIN_PENDING_RESEND）
_last_pushed_wire = None       # 最后推送的 wire（pusher 和 approval 共享，防止重复推送）


# ── BLE 层 ─────────────────────────────────────────────────
def _on_disconnect(client):
    global _connected
    _connected = False
    print("[daemon] disconnected, will reconnect...")


def _on_notify(sender, data: bytearray):
    """设备 → PC: 接 {cmd:"permission", id, decision:"once|deny"} 解 approval。
    广播给所有正在等待的 session 的 decision_event。
    同时处理 pong 心跳响应。"""
    global _rx_buf, _last_pong_ts, _device_online
    _rx_buf += data.decode(errors="ignore")
    while "\n" in _rx_buf:
        line, _rx_buf = _rx_buf.split("\n", 1)
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)

            # 处理 pong 心跳
            if msg.get("ack") == "pong":
                _last_pong_ts = time.time()
                if not _device_online:
                    print("[heartbeat] device back online")
                    _device_online = True
                continue

            # 处理审批决策（新格式 {"d":"once","n":0}，无需 prompt_id）
            if "d" in msg:
                decision = msg["d"]
                for sess in _sessions.values():
                    if sess.approval_in_progress and sess.decision_event:
                        sess.decision_value = decision
                        sess.decision_event.set()
        except Exception:
            pass


async def _connect_loop():
    global _client, _connected, _device_online, _last_pong_ts
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
            _device_online = True  # 连接成功时标记在线
            _last_pong_ts = time.time()
            print(f"[daemon] connected to {addr}")
            await asyncio.sleep(1.0)
        except Exception as e:
            print(f"[daemon] connect failed: {e}")
            _client = None
            _connected = False
            _device_online = False
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
    async with _send_lock:
        for i in range(0, len(data), 20):
            await _client.write_gatt_char(NUS_RX, data[i:i+20], response=False)


# ── 心跳任务 ───────────────────────────────────────────────
def _resolve_pending_approvals_on_offline():
    """设备掉线时，立刻按 risk_level 解决所有进行中的审批，避免卡到超时。"""
    for sid, sess in _sessions.items():
        if not sess.approval_in_progress or not sess.approval_queue:
            continue
        tid = sess.approval_queue[0]
        risk = sess.tools.get(tid, {}).get("risk_level", "normal")
        decision = "once" if risk in {"safe", "normal"} else "deny"
        print(f"[heartbeat] offline → resolve approval {tid[:8]} risk={risk} → {decision}")
        sess.decision_value = decision
        if sess.decision_event:
            sess.decision_event.set()


async def _heartbeat_task():
    """后台心跳任务：每 10s 发 ping，30s 无 pong 判定离线。"""
    global _device_online, _last_pong_ts, _last_ping_ts
    while True:
        if _connected and not _stub:
            _last_ping_ts = time.time()
            await _send({"cmd": "ping", "ts": _last_ping_ts})
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            now = time.time()
            if now - _last_pong_ts > HEARTBEAT_TIMEOUT_S:
                if _device_online:
                    print("[heartbeat] device offline (no pong for 30s)")
                    _device_online = False
                    _resolve_pending_approvals_on_offline()
        else:
            await asyncio.sleep(1.0)


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
    now = time.time()
    if sess.approval_queue:
        tid = sess.approval_queue[0]
        t = sess.tools.get(tid, {})
        return {"s": "P", "t": t.get("tool", "")[:10], "h": t.get("summary", "")[:18]}
    if sess.dizzy_until > now:
        return {"s": "E"}
    if sess.completed_until > now:
        return {"s": "C"}
    for t in sess.tools.values():
        if t["status"] == "running":
            summary = t.get("summary", "")[:10]
            m = f"{t['tool']}: {summary}" if summary else t["tool"]
            return {"s": "W", "m": m[:15]}
    return {"s": "I"}


def _to_device_wire() -> dict:
    now = time.time()
    active = []
    for sid, sess in list(_sessions.items()):
        has_tools = bool(sess.tools) or bool(sess.approval_queue)
        recently  = sess.last_activity_ts > 0 and (now - sess.last_activity_ts) < SESSION_ACTIVE_TIMEOUT_S
        special   = sess.completed_until > now or sess.dizzy_until > now
        if has_tools or recently or special:
            active.append(_session_to_wire(sid, sess))
    return {"ss": active}


def _mark_dirty():
    global _dirty
    _dirty = True


def _clear_dirty():
    global _dirty
    _dirty = False


def _update_pending_send_ts():
    global _last_pending_send_ts
    _last_pending_send_ts = time.time()


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
    global _dirty, _last_pushed_wire

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
                for s in last_pushed_wire.get("ss", []):
                    if s.get("s") == "C":
                        _mark_dirty()
                        break

    # ping 刚发出时屏蔽推送，避免 ESP32 写 pong 期间丢包
    in_cooldown = (now - _last_ping_ts) < POST_PING_COOLDOWN_S
    if in_cooldown:
        return last_pushed_wire

    has_pending = any(sess.approval_queue for sess in _sessions.values())
    # 1s 限速 + 最多重发 5 次：避免淹没 BLE 队列，5 次后认为设备已收到
    pending_resend_due = (
        has_pending
        and (now - _last_pending_send_ts) >= MIN_PENDING_RESEND_S
        and any(sess.pending_resend_count < MAX_PENDING_RESENDS
                for sess in _sessions.values() if sess.approval_queue)
    )

    if _dirty or pending_resend_due:
        wire = _to_device_wire()
        if wire != last_pushed_wire or pending_resend_due:
            await _send(wire)
            last_pushed_wire = wire
            _last_pushed_wire = wire
            if pending_resend_due:
                _update_pending_send_ts()
                for sess in _sessions.values():
                    if sess.approval_queue:
                        sess.pending_resend_count += 1
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
        risk_level = event.get("risk_level", "normal")

        if not tool_use_id:
            print(f"[warn] tool_start missing tool_use_id, ignoring")
            return {"decision": "once"}

        sess.tools[tool_use_id] = {
            "tool": tool,
            "category": category,
            "summary": summary,
            "status": "waiting" if event.get("needs_approval") else "running",
            "ts": now,
            "risk_level": risk_level,
        }
        sess.last_activity_ts = now

        if not event.get("needs_approval"):
            _mark_dirty()
            return {"decision": "once"}

        # approval path: 设备在线走设备审批，离线根据风险分级处理
        sess.approval_queue.append(tool_use_id)
        sess.approval_in_progress = True
        sess.decision_event.clear()
        sess.decision_value = None
        sess.pending_resend_count = 0  # 新审批入队，重置重发计数

        # stub 模式视为设备在线（用于测试），--offline 强制覆盖
        # 用 _connected 而非 _device_online：BLE 已连接即可走设备审批，不依赖心跳时序
        device_online = (_connected or _stub) and not _force_offline

        # 设备离线 + 低/中风险 → 自动批准
        if not device_online and risk_level in {"safe", "normal"}:
            print(f"[approval] device offline, auto-approve {tool} (risk={risk_level})")
            decision = "once"
            if tool_use_id in sess.approval_queue:
                sess.approval_queue.remove(tool_use_id)
            sess.approval_in_progress = False
            sess.last_activity_ts = time.time()
            if tool_use_id in sess.tools:
                sess.tools[tool_use_id]["status"] = "running"
            _mark_dirty()
            wire = _to_device_wire()
            await _send(wire)
            _last_pushed_wire = wire
            _clear_dirty()
            return {"decision": decision}

        # 设备离线 + 高风险 → CLI 提示
        if not device_online and risk_level == "critical":
            print(f"\n{'='*60}")
            print(f"[CRITICAL APPROVAL REQUIRED] Device offline")
            print(f"  Tool: {tool}")
            print(f"  Hint: {summary[:80]}")
            print(f"{'='*60}")
            try:
                choice = input("Approve? (y=once / s=session / n=deny): ").strip().lower()
                decision = {"y": "once", "s": "session", "n": "deny"}.get(choice, "deny")
            except Exception:
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
            wire = _to_device_wire()
            await _send(wire)
            _last_pushed_wire = wire
            _clear_dirty()
            return {"decision": decision}

        # 设备在线 → 走原有审批流程
        _initial_wire = _to_device_wire()
        await _send(_initial_wire)
        _last_pushed_wire = _initial_wire
        _update_pending_send_ts()  # 启动重发限速计时

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
        wire = _to_device_wire()
        await _send(wire)
        _last_pushed_wire = wire
        _clear_dirty()
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
    global _lock, _send_lock
    _lock = asyncio.Lock()
    _send_lock = asyncio.Lock()
    server = await asyncio.start_server(_handle_client, HOST, PORT)
    print(f"[daemon] listening on {HOST}:{PORT}  stub={_stub}")
    async with server:
        await asyncio.gather(
            server.serve_forever(),
            _connect_loop(),
            _pusher_task(),
            _heartbeat_task(),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stub", action="store_true",
                        help="跳过 BLE 连接, _send 改 stdout 打印用于 e2e 测试")
    parser.add_argument("--offline", action="store_true",
                        help="强制模拟设备离线（覆盖 stub 在线假设），用于离线审批测试")
    parser.add_argument("--log", type=str, default=None,
                        help="日志文件路径（默认：临时目录下的 ble_daemon.log）")
    args = parser.parse_args()
    _stub = args.stub
    _force_offline = args.offline

    # 设置日志：始终同时输出到终端和文件
    import sys
    import tempfile
    log_path = args.log or __import__('os').path.join(tempfile.gettempdir(), "ble_daemon.log")

    class TeeOutput:
        def __init__(self, file_path, original_stream):
            self.file = open(file_path, 'w', encoding='utf-8', buffering=1)
            self.original = original_stream

        def write(self, data):
            self.original.write(data)
            self.file.write(data)
            self.file.flush()

        def flush(self):
            self.original.flush()
            self.file.flush()

    sys.stdout = TeeOutput(log_path, sys.stdout)
    sys.stderr = TeeOutput(log_path.replace('.log', '_err.log'), sys.stderr)
    print(f"[daemon] 日志文件: {log_path}")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[daemon] bye")
        sys.exit(0)
