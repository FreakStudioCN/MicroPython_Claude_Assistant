#!/usr/bin/env python3
# ble_daemon.py —— 长驻 BLE 桥接守护进程 (v5 纯展示版)
#
# 输入: hook_bridge.py 通过 TCP 57320 推 v2 envelope
#       {type:"event", v:2, event:{kind, ...}, generic:{session_id, ...}}
# 输出: 翻译为 protocol.py v5 精简 wire（1-3 BLE chunks）
#       {"ss":[{"s":"I"}]}                    → 1 chunk
#       {"ss":[{"s":"W","m":"Bash"}]}         → 2 chunks
#       {"ss":[{"s":"W","m":"Read: main.py"}]} → 3 chunks
#       状态码: I=IDLE W=WORKING E=ERROR C=CELEBRATE
#       通过 BLE NUS 写到 ESP32
#
# v5 变化: 删除设备审批，改为纯展示 + 终端审批
#   - 删除 PENDING 状态（审批在终端完成）
#   - 删除设备→PC 审批回传
#   - 删除心跳机制（单向推送）
#   - 简化状态机（无审批队列）
#
# 状态机: 每个 session_id 独立 _Session 对象
#
# 推断兜底 (Stop hook 不冒,只能从沉默期推):
#   session._tools==0 and now-last_activity > threshold
#     → completed=True 短暂 (CELEBRATE 触发后清掉)
#     threshold = 8s if has_subagent else 4s
#
# 5Hz 节流: 状态变化只标 dirty, 单独 _pusher_task 每 200ms 推一次
#
# session 生命周期:
#   活跃 (has_tools or recently_active or special_state) → 纳入 wire sessions 数组
#   30s 无活动且无工具 → 清理
#
# stub 模式: --stub 不启 BLE,_send 改 stdout 打印,用于无设备 e2e 测试

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Optional

from transport import BleTransport

HOST = "127.0.0.1"
PORT = 57320

# 业务常量
PUSH_INTERVAL_S = 0.2          # 5Hz throttle
TASK_COMPLETE_QUIET_S = 4.0    # PostTool 后 N 秒无新 PreTool → 推断 task_complete
COMPLETED_HOLD_S = 2.0         # completed=True 持续秒数 (覆盖 CELEBRATE 3s)
DIZZY_HOLD_S = 3.0             # tool_error / task_error msg="error" 持续秒数
SESSION_ACTIVE_TIMEOUT_S = 10.0  # 超过此时间无活动的 IDLE session 不纳入 wire
SESSION_CLEANUP_S = 10.0         # 超过此时间清理 session 对象

# 设备 wire msg 字段值
MSG_ERROR = "error"
MSG_COMPLETED = "completed"
APPROVE_PREFIX = "approve: "

# ── per-session 状态 ──────────────────────────────────────


class _Session:
    def __init__(self):
        self.tools: dict = {}       # tool_use_id → {tool, category, summary, status, ts}
        self.has_subagent: bool = False
        self.waiting: int = 0
        self.cwd: str = ""
        self.display_name: str = ""
        self.current_error: str = ""
        self.current_interrupted: bool = False
        self.last_activity_ts: float = 0.0
        self.completed_until: float = 0.0
        self.completed_inferred_for_ts: float = 0.0
        self.dizzy_until: float = 0.0


_sessions: dict = {}   # session_id → _Session
_dirty = False         # 全局 dirty 标志（pusher 用）

# ── stub 模式 ─────────────────────────────────────────────
_stub = False
_force_offline = False  # --offline 标志：强制 device_online=False，覆盖 stub 的在线假设

# ── Transport ─────────────────────────────────────────────
_transport: Optional[BleTransport] = None

# ── 业务层全局 ────────────────────────────────────────────
_lock = None
_last_pushed_wire = None       # 最后推送的 wire（pusher 用，防止重复推送）


# ── BLE 回调（业务层处理） ────────────────────────────────────
def _on_transport_connect():
    """BLE 重连成功：有活跃 session 时触发状态推送。"""
    print("[daemon] connected" if _transport.connected() else "")
    if _sessions:
        _mark_dirty()


def _on_transport_disconnect():
    print("[daemon] disconnected, will reconnect...")


async def _send(payload: dict):
    """推送 wire JSON（stub 打印 / 走 transport）。"""
    if _stub:
        print(f"[stub-send] t={time.time():.3f} {json.dumps(payload, ensure_ascii=False)}")
        return
    if not _transport.connected():
        print(f"[send] skipped (not connected): {payload}")
        return
    await _transport.send(payload)


# ── per-session 状态翻译 ───────────────────────────────────

def _get_running_count(sess: _Session) -> int:
    return sum(1 for t in sess.tools.values() if t["status"] == "running")


def _get_current_category(sess: _Session) -> str:
    for t in sess.tools.values():
        if t["status"] == "running":
            return t.get("category", "")
    return ""


def _build_msg(sess: _Session) -> str:
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
    result = {"n": sess.display_name or "?"}

    if sess.dizzy_until > now:
        result["s"] = "E"
        return result
    if sess.completed_until > now:
        result["s"] = "C"
        return result
    if sess.waiting > 0:
        result["s"] = "P"
        return result
    for t in sess.tools.values():
        if t["status"] == "running":
            summary = t.get("summary", "")[:10]
            m = f"{t['tool']}: {summary}" if summary else t["tool"]
            result["s"] = "W"
            result["m"] = m[:15]
            return result
    result["s"] = "I"
    return result


def _to_device_wire() -> dict:
    now = time.time()
    active = []
    for sid, sess in list(_sessions.items()):
        has_tools = bool(sess.tools)
        recently  = sess.last_activity_ts > 0 and (now - sess.last_activity_ts) < SESSION_ACTIVE_TIMEOUT_S
        special   = sess.completed_until > now or sess.dizzy_until > now
        if has_tools or recently or special:
            active.append(_session_to_wire(sid, sess))
    return {"ss": active}


def _generate_display_name(session_id: str, cwd: str) -> str:
    """生成 session 显示名称：basename 或 basename+sid后4位（冲突时）。"""
    basename = os.path.basename(cwd) if cwd else "unknown"
    basename = basename[:12]  # 截断到12字符

    # 检查是否已有同 basename 的 session
    conflict = any(
        s.display_name.startswith(basename) and s.cwd != cwd
        for s in _sessions.values()
        if s.display_name
    )

    if conflict:
        suffix = session_id[-4:] if len(session_id) >= 4 else session_id
        return f"{basename[:8]}-{suffix}"
    return basename


def _mark_dirty():
    global _dirty
    _dirty = True


def _clear_dirty():
    global _dirty
    _dirty = False


def _enter_error_state(sess: _Session, now: float, hard_reset: bool, error_msg: str, is_interrupt: bool) -> None:
    if hard_reset:
        sess.tools.clear()

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
                if not s.tools
                and s.last_activity_ts > 0
                and now - s.last_activity_ts > SESSION_CLEANUP_S]:
        del _sessions[sid]

    # 每个 session 的 task_complete 推断
    for sess_id, sess in list(_sessions.items()):
        threshold = 8.0 if sess.has_subagent else TASK_COMPLETE_QUIET_S
        if (
            len(sess.tools) == 0
            and sess.last_activity_ts > 0
            and now - sess.last_activity_ts > threshold
            and sess.completed_inferred_for_ts != sess.last_activity_ts
            and sess.dizzy_until < now
        ):
            sess.completed_until = now + COMPLETED_HOLD_S
            sess.completed_inferred_for_ts = sess.last_activity_ts
            _mark_dirty()
            print(f"[infer] task_complete session={sess_id!r} (quiet={now - sess.last_activity_ts:.1f}s)")

        # completed 到期标 dirty
        if sess.completed_until > 0 and sess.completed_until <= now:
            if last_pushed_wire:
                for s in last_pushed_wire.get("ss", []):
                    if s.get("s") == "C":
                        _mark_dirty()
                        break

    if _dirty:
        wire = _to_device_wire()
        if wire != last_pushed_wire:
            await _send(wire)
            last_pushed_wire = wire
            _last_pushed_wire = wire
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

    # 提取 cwd 并生成 display_name（首次）
    if not sess.display_name:
        cwd = env.get("generic", {}).get("cwd", "")
        if cwd:
            sess.cwd = cwd
            sess.display_name = _generate_display_name(session_id, cwd)
            print(f"[session] {session_id!r} → display_name={sess.display_name!r}")

    event = env.get("event") or {}
    kind = event.get("kind", "")
    print(f"[req v2] session={session_id!r} kind={kind!r}")

    now = time.time()

    if kind == "tool_start":
        tool = event.get("tool", "")
        tool_use_id = event.get("tool_use_id", "")
        category = event.get("tool_category", "")
        summary = event.get("summary", "")

        if not tool_use_id:
            print(f"[warn] tool_start missing tool_use_id, ignoring")
            return {"decision": "once"}

        needs_approval = event.get("needs_approval", False)
        sess.tools[tool_use_id] = {
            "tool": tool,
            "category": category,
            "summary": summary,
            "status": "running",
            "ts": now,
        }
        if needs_approval:
            sess.waiting += 1
            print(f"[approval] session={session_id!r} waiting={sess.waiting}")
        sess.last_activity_ts = now
        _mark_dirty()
        return {"decision": "once"}

    if kind == "tool_done":
        tool_use_id = event.get("tool_use_id", "")
        interrupted = event.get("interrupted", False)

        if tool_use_id in sess.tools:
            del sess.tools[tool_use_id]

        if sess.waiting > 0:
            sess.waiting -= 1
            print(f"[approval] session={session_id!r} done, waiting={sess.waiting}")

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

        if sess.waiting > 0:
            sess.waiting -= 1
            print(f"[approval] session={session_id!r} error, waiting={sess.waiting}")

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
        async with _lock:
            resp = await _handle_envelope(env)
    except Exception as e:
        resp = {"ok": True, "error": str(e)}
    writer.write(json.dumps(resp).encode())
    await writer.drain()
    writer.close()


async def main():
    global _lock, _transport
    _lock = asyncio.Lock()
    _transport = BleTransport()
    server = await asyncio.start_server(_handle_client, HOST, PORT)
    print(f"[daemon] listening on {HOST}:{PORT}  stub={_stub}")
    async with server:
        if _stub:
            await asyncio.gather(server.serve_forever(), _pusher_task())
        else:
            await asyncio.gather(
                server.serve_forever(),
                _transport.start(
                    on_recv=lambda msg: None,
                    on_connect=_on_transport_connect,
                    on_disconnect=_on_transport_disconnect,
                ),
                _pusher_task(),
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
