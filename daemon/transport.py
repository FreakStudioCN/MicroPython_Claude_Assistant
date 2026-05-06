# transport.py —— PC 端通信传输抽象层
#
# Transport 基类定义统一接口，具体实现类负责各自传输细节。
# 当前实现：BleTransport（BLE NUS，基于 bleak）
# 预留接口：WifiTransport（TCP）、UartTransport（USB 串口）

import asyncio
import json
import time
from typing import Callable, Optional


class Transport:
    async def start(
        self,
        on_recv: Callable[[dict], None],
        on_connect: Callable[[], None],
        on_disconnect: Callable[[], None],
    ): raise NotImplementedError

    async def send(self, payload: dict): raise NotImplementedError
    def connected(self) -> bool: raise NotImplementedError
    def device_online(self) -> bool: raise NotImplementedError


# ── BLE 实现 ─────────────────────────────────────────────────

NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME_PREFIX = "Claude"
HEARTBEAT_INTERVAL_S = 10.0
HEARTBEAT_TIMEOUT_S  = 30.0


class BleTransport(Transport):
    def __init__(self):
        self._client       = None
        self._connected    = False
        self._device_online = False
        self._rx_buf       = ""
        self._send_lock    = None   # 初始化在 start()（需要 event loop）
        self._last_pong_ts = 0.0
        self._last_ping_ts = 0.0

        self._on_recv       = None
        self._on_connect    = None
        self._on_disconnect = None

    # ── 公开接口 ────────────────────────────────────────────

    async def start(self, on_recv, on_connect, on_disconnect):
        self._on_recv       = on_recv
        self._on_connect    = on_connect
        self._on_disconnect = on_disconnect
        self._send_lock     = asyncio.Lock()
        await asyncio.gather(
            self._connect_loop(),
            self._heartbeat_loop(),
        )

    async def send(self, payload: dict):
        data = (json.dumps(payload) + "\n").encode()
        print(f"[send] t={time.time():.3f} {payload} ({len(data)}B)")
        async with self._send_lock:
            for i in range(0, len(data), 20):
                await self._client.write_gatt_char(NUS_RX, data[i:i+20], response=False)

    def connected(self) -> bool:
        return self._connected

    def device_online(self) -> bool:
        return self._device_online

    # ── 内部 BLE 回调 ────────────────────────────────────────

    def _on_ble_disconnect(self, client):
        self._connected = False
        if self._on_disconnect:
            self._on_disconnect()

    def _on_ble_notify(self, sender, data: bytearray):
        self._rx_buf += data.decode(errors="ignore")
        while "\n" in self._rx_buf:
            line, self._rx_buf = self._rx_buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if msg.get("ack") == "pong":
                    self._last_pong_ts = time.time()
                    if not self._device_online:
                        print("[heartbeat] device back online")
                        self._device_online = True
                    continue
                if self._on_recv:
                    self._on_recv(msg)
            except Exception:
                pass

    # ── 连接循环 ─────────────────────────────────────────────

    async def _connect_loop(self):
        from bleak import BleakClient, BleakScanner
        while True:
            if self._connected:
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
                self._client = BleakClient(addr, disconnected_callback=self._on_ble_disconnect)
                await self._client.connect()
                await self._client.start_notify(NUS_TX, self._on_ble_notify)
                self._connected     = True
                self._device_online = True
                self._last_pong_ts  = time.time()
                print(f"[daemon] connected to {addr}")
                if self._on_connect:
                    self._on_connect()
                await asyncio.sleep(1.0)
            except Exception as e:
                print(f"[daemon] connect failed: {e}")
                self._client        = None
                self._connected     = False
                self._device_online = False
                await asyncio.sleep(3)

    # ── 心跳循环 ─────────────────────────────────────────────

    async def _heartbeat_loop(self):
        while True:
            if self._connected:
                self._last_ping_ts = time.time()
                await self.send({"cmd": "ping", "ts": self._last_ping_ts})
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                if time.time() - self._last_pong_ts > HEARTBEAT_TIMEOUT_S:
                    if self._device_online:
                        print("[heartbeat] device offline (no pong for 30s)")
                        self._device_online = False
                        if self._on_recv:
                            self._on_recv({"_event": "offline"})
            else:
                await asyncio.sleep(1.0)


# ── WiFi 实现（预留） ─────────────────────────────────────────

class WifiTransport(Transport):
    """TCP socket 传输（未实现）。"""
    async def start(self, on_recv, on_connect, on_disconnect): raise NotImplementedError
    async def send(self, payload: dict): raise NotImplementedError
    def connected(self) -> bool: raise NotImplementedError
    def device_online(self) -> bool: raise NotImplementedError


# ── 串口实现（预留） ─────────────────────────────────────────

class UartTransport(Transport):
    """USB-UART 串口传输（未实现）。"""
    async def start(self, on_recv, on_connect, on_disconnect): raise NotImplementedError
    async def send(self, payload: dict): raise NotImplementedError
    def connected(self) -> bool: raise NotImplementedError
    def device_online(self) -> bool: raise NotImplementedError
