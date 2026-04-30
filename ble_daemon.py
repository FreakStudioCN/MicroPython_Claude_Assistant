#!/usr/bin/env python3
# ble_daemon.py —— 长驻 BLE 桥接守护进程 (v2)
#
# 输入: hook_bridge.py 通过 TCP 57320 推 v2 envelope
#       {type:"event", v:2, event:{kind, ...}, generic:{...}}
# 输出: 翻译为 protocol.py v1 设备 wire (running/waiting/completed/msg/tokens/prompt)
#       通过 BLE NUS 写到 ESP32, 6 字段严格,设备 firmware 不动
#
# 状态机:
#   tool_start → running++  / 或 waiting++ (approval) 阻塞等设备回 once|deny
#   tool_done  → running--, mark last_activity
#   tool_error / task_error → running--, msg="error", _dizzy_until 短暂
#   tool_batch_done → soft task_complete 候选信号
#   user_prompt → 标记 session_active, 清 idle, 清 completed
#   subagent_start / notification → 仅信息, 不改状态
#
# 推断兜底 (Stage 1 发现 Stop hook 不冒,只能从沉默期推):
#   running==0 and waiting==0 and now-last_activity > TASK_COMPLETE_QUIET_S
#     → completed=True 短暂 (CELEBRATE 触发后清掉)
#   now-last_activity > IDLE_QUIET_S → 设备进 IDLE 状态由 base 自动覆盖
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
APPROVAL_TOOLS = {"Bash", "Write", "Edit"}
APPROVAL_TIMEOUT_S = 30
PUSH_INTERVAL_S = 0.2          # 5Hz throttle
TASK_COMPLETE_QUIET_S = 4.0    # PostTool 后 N 秒无新 PreTool → 推断 task_complete
COMPLETED_HOLD_S = 2.0         # completed=True 持续秒数 (覆盖 CELEBRATE 3s)
DIZZY_HOLD_S = 3.0             # tool_error / task_error msg="error" 持续秒数

# ── 全局状态 ───────────────────────────────────────────
_stub = False  # --stub 模式
_running = 0
_waiting = 0
_dirty = False
_last_activity_ts = 0.0
_completed_until = 0.0       # > now → completed=True
_completed_inferred_for_ts = 0.0  # one-shot guard: 已为这个 _last_activity_ts 推过 task_complete
_dizzy_until = 0.0           # > now → msg="error"
_session_active = False
_current_prompt: Optional[dict] = None
_current_running_msg = ""    # 显示在设备上的"running ..."文字

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
    """v1 设备 wire 严格按 protocol.py: running/waiting/completed/msg/tokens/prompt。
    payload 仅这 6 字段; 多余字段会被设备 StatusMsg 忽略但浪费 BLE 带宽。"""
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


# ── 状态翻译: 内部 v2 状态 → v1 设备 wire ──────────────
def _build_msg() -> str:
    """msg 字段优先级: approval > error > completed > running > 默认空 (设备显 IDLE)。"""
    if _waiting > 0 and _current_prompt:
        return f"approve: {_current_prompt.get('tool','')}"
    now = time.time()
    if _dizzy_until > now:
        return "error"
    if _completed_until > now:
        return "completed"
    if _running > 0 and _current_running_msg:
        return _current_running_msg
    if _running > 0:
        return "running"
    return ""


def _to_device_wire() -> dict:
    """严格按 protocol.py v1 schema, 6 字段。"""
    return {
        "running":   _running,
        "waiting":   _waiting,
        "completed": _completed_until > time.time(),
        "msg":       _build_msg(),
        "tokens":    0,  # TODO: 下个 PR 从 tool_response 抽
        "prompt":    _current_prompt,
    }


def _mark_dirty():
    global _dirty
    _dirty = True


# ── 5Hz 推送 task ──────────────────────────────────────
async def _pusher_tick(last_pushed_wire):
    """单次 pusher 迭代逻辑, 返回更新后的 last_pushed_wire。
    抽出来方便单测 (mock time.time + 直接 await 这个函数)。"""
    global _dirty, _completed_until, _completed_inferred_for_ts

    # task_complete 推断: 静默期 + 之前有过活动 + 还没为这个 _last_activity_ts 推过
    now = time.time()
    if (
        _running == 0
        and _waiting == 0
        and _last_activity_ts > 0
        and now - _last_activity_ts > TASK_COMPLETE_QUIET_S
        and _completed_inferred_for_ts != _last_activity_ts  # one-shot 关键
        and _dizzy_until < now           # 错误状态不要被 completed 盖
    ):
        _completed_until = now + COMPLETED_HOLD_S
        _completed_inferred_for_ts = _last_activity_ts
        _mark_dirty()
        print(f"[infer] task_complete (quiet={now - _last_activity_ts:.1f}s)")

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
    global _running, _waiting, _last_activity_ts, _dizzy_until
    global _session_active, _current_prompt, _current_running_msg
    global _approval_in_progress, _decision_value, _completed_until
    global _completed_inferred_for_ts

    event = env.get("event") or {}
    kind = event.get("kind", "")
    print(f"[req v2] kind={kind!r} event={event}")

    now = time.time()

    if kind == "tool_start":
        tool = event.get("tool", "")
        summary = event.get("summary", "")
        if not event.get("needs_approval"):
            _running += 1
            _last_activity_ts = now
            _current_running_msg = f"{tool}: {summary[:40]}" if summary else tool
            _mark_dirty()
            return {"decision": "once"}

        # approval path: 同步阻塞等设备
        _waiting += 1
        _approval_in_progress = True
        _current_prompt = {"id": "cli-req", "tool": tool, "hint": summary[:80]}
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
        _waiting = max(0, _waiting - 1)
        _approval_in_progress = False
        _current_prompt = None
        # 刷新到 wait 之后的真实时间, 防 quiet 立刻满足 (codex P2 bug 2)
        # 不能用 path 顶部的 now —— wait_for 可能已经过了 30s
        _last_activity_ts = time.time()
        if decision == "once":
            _running += 1   # 视为 tool_start 真正开始
            _current_running_msg = f"{tool}: {summary[:40]}" if summary else tool
        else:
            # deny / timeout: 不让 pusher 把它当 task_complete 候选
            _completed_inferred_for_ts = _last_activity_ts
        _mark_dirty()
        await _send(_to_device_wire())  # 立即推, 别等 throttle
        return {"decision": decision}

    if kind == "tool_done":
        _running = max(0, _running - 1)
        _last_activity_ts = now
        if _running == 0:
            _current_running_msg = ""
        _mark_dirty()
        return {"ok": True}

    if kind == "tool_error":
        _running = max(0, _running - 1)
        _last_activity_ts = now
        _dizzy_until = now + DIZZY_HOLD_S
        _completed_until = 0.0
        # 锁 inference guard, 防 dizzy 过期后 quiet 4s 又推 task_complete (codex P2 bug 1)
        _completed_inferred_for_ts = _last_activity_ts
        if _running == 0:
            _current_running_msg = ""
        _mark_dirty()
        return {"ok": True}

    if kind == "tool_batch_done":
        # 不直接改 running 计数 (PostToolUse 已分别减过), 只刷活动时间
        _last_activity_ts = now
        _mark_dirty()
        return {"ok": True}

    if kind == "user_prompt":
        _session_active = True
        _last_activity_ts = now
        # 清 completed 让设备退出 CELEBRATE, 准备进 BUSY
        _completed_until = 0.0
        _mark_dirty()
        return {"ok": True}

    if kind == "task_error":
        # StopFailure: API 错 / stream timeout
        _running = 0
        _waiting = 0
        _dizzy_until = now + DIZZY_HOLD_S
        _completed_until = 0.0
        _current_running_msg = ""
        _last_activity_ts = now
        # 锁 inference guard, 防 dizzy 过期后 quiet 4s 又推 task_complete (codex P2 bug 1)
        _completed_inferred_for_ts = _last_activity_ts
        _mark_dirty()
        return {"ok": True}

    if kind in ("subagent_start", "notification", "unknown"):
        # 当前阶段仅记录,不改基础状态
        return {"ok": True}

    return {"ok": True}


# ── TCP server ─────────────────────────────────────────
async def _handle_client(reader, writer):
    try:
        data = await asyncio.wait_for(reader.read(8192), timeout=35)
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
