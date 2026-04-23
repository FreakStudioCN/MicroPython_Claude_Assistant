#!/usr/bin/env python3
# ble_daemon.py —— 长驻 BLE 桥接守护进程
# 启动方式：python ble_daemon.py

import asyncio
import json
import time
from bleak import BleakClient, BleakScanner
from typing import Optional

HOST = "127.0.0.1"
PORT = 57320
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME_PREFIX = "Claude"
APPROVAL_TOOLS = {"Bash", "Write", "Edit"}
APPROVAL_TIMEOUT = 30

_client: Optional[BleakClient] = None
_connected = False
_decision_event = asyncio.Event()
_decision_value: Optional[str] = None
_rx_buf = ""
_lock = asyncio.Lock()
_approval_in_progress = False


def _on_disconnect(client: BleakClient):
    global _connected
    _connected = False
    print("[daemon] disconnected, will reconnect...")


def _on_notify(sender, data: bytearray):
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
    """后台持续维护 BLE 连接，断线后自动重连。"""
    global _client, _connected
    while True:
        if _connected:
            await asyncio.sleep(1)
            continue
        try:
            devices = await BleakScanner.discover(timeout=5.0)
            addr = next((d.address for d in devices if d.name and d.name.startswith(DEVICE_NAME_PREFIX)), None)
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
    if not _connected or _client is None:
        print(f"[send] skipped (not connected): {payload}")
        return
    data = (json.dumps(payload) + "\n").encode()
    chunks = (len(data) + 19) // 20
    print(f"[send] t={time.time():.3f} {payload} ({len(data)}B, {chunks} chunks)")
    for i in range(0, len(data), 20):
        chunk = data[i:i+20]
        print(f"[send] chunk {i//20}/{chunks} t={time.time():.3f}: {chunk}")
        await _client.write_gatt_char(NUS_RX, chunk, response=False)
    print(f"[send] done t={time.time():.3f}")


async def _handle_request(req: dict) -> dict:
    global _decision_value
    t = req.get("type")
    print(f"[req] {req}")

    if t == "pre":
        tool = req.get("tool", "")
        hint = req.get("hint", "")
        if tool not in APPROVAL_TOOLS:
            msg = f"{tool}: {hint[:40]}" if hint else f"running: {tool}"
            print(f"[pre] non-approval tool={tool!r}, sending running")
            await _send({"running": 1, "msg": msg})
            return {"decision": "once"}
        print(f"[pre] approval needed tool={tool!r} hint={hint!r}")
        global _approval_in_progress
        _approval_in_progress = True
        _decision_event.clear()
        _decision_value = None
        await _send({
            "waiting": 1,
            "msg": f"approve: {tool}",
            "prompt": {"id": "cli-req", "tool": tool, "hint": hint[:80]}
        })
        print(f"[pre] waiting for decision (timeout={APPROVAL_TIMEOUT}s)...")
        try:
            await asyncio.wait_for(_decision_event.wait(), timeout=APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            print("[pre] timeout, defaulting to deny")
            await _send({"waiting": 0, "running": 0, "msg": "timeout"})
        _approval_in_progress = False
        print(f"[pre] decision={_decision_value!r}")
        return {"decision": _decision_value or "deny"}

    elif t == "post":
        # 等待审批完成后再发送，避免覆盖审批界面
        while _approval_in_progress:
            await asyncio.sleep(0.1)
        success = req.get("success", True)
        msg = "done" if success else "error"
        print(f"[post] success={success}")
        await _send({"running": 0, "msg": msg, "dizzy": not success})
        return {"ok": True}

    elif t == "stop":
        print("[stop] task completed")
        await _send({"running": 0, "completed": True, "msg": "completed"})
        return {"ok": True}

    return {"ok": True}


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        data = await asyncio.wait_for(reader.read(4096), timeout=35)
        req = json.loads(data.decode())
        # 只在发送 BLE 时加锁，审批等待不持锁
        t = req.get("type")
        if t == "pre" and req.get("tool", "") in APPROVAL_TOOLS:
            resp = await _handle_request(req)
        else:
            async with _lock:
                resp = await _handle_request(req)
    except Exception as e:
        resp = {"ok": True, "error": str(e)}
    writer.write(json.dumps(resp).encode())
    await writer.drain()
    writer.close()


async def main():
    server = await asyncio.start_server(_handle_client, HOST, PORT)
    print(f"[daemon] listening on {HOST}:{PORT}")
    async with server:
        await asyncio.gather(server.serve_forever(), _connect_loop())


if __name__ == "__main__":
    asyncio.run(main())
