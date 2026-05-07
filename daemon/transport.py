# transport.py —— PC 端通信传输抽象层
#
# Transport 基类定义统一接口，具体实现类负责各自传输细节。
# 当前实现：BleTransport（BLE NUS，基于 bleak）
# 预留接口：WifiTransport（TCP）、UartTransport（USB 串口）

import asyncio
import json
import os
import sys
import time
from pathlib import Path
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


def _get_config_path() -> Path:
    """获取配对配置文件路径（跨平台）。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "~"))
    else:
        base = Path.home() / ".config"
    return base / "claude-buddy" / "device.json"


def _load_pairing_config() -> dict:
    """加载配对配置，返回 {"device_name": str, "paired_mac": str, ...}。"""
    path = _get_config_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class BleTransport(Transport):
    def __init__(self):
        self._client       = None
        self._connected    = False
        self._rx_buf       = ""
        self._send_lock    = None   # 初始化在 start()（需要 event loop）

        self._on_recv       = None
        self._on_connect    = None
        self._on_disconnect = None

        # 读取配对配置
        config = _load_pairing_config()
        if config.get("device_name"):
            self._target_device_name = config["device_name"]
            print(f"[daemon] 已加载配对设备: {self._target_device_name}")
        else:
            self._target_device_name = None
            print("[daemon] 未配对设备，将连接任意 Claude-Buddy-* 设备")

    # ── 公开接口 ────────────────────────────────────────────

    async def start(self, on_recv, on_connect, on_disconnect):
        self._on_recv       = on_recv
        self._on_connect    = on_connect
        self._on_disconnect = on_disconnect
        self._send_lock     = asyncio.Lock()
        await self._connect_loop()

    async def send(self, payload: dict):
        data = (json.dumps(payload) + "\n").encode()
        print(f"[send] t={time.time():.3f} {payload} ({len(data)}B)")
        async with self._send_lock:
            for i in range(0, len(data), 20):
                await self._client.write_gatt_char(NUS_RX, data[i:i+20], response=False)

    def connected(self) -> bool:
        return self._connected

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

                # 根据配对状态选择设备
                addr = None
                if self._target_device_name:
                    # 精确匹配配对的设备名称
                    addr = next(
                        (d.address for d in devices if d.name == self._target_device_name),
                        None,
                    )
                    if not addr:
                        print(f"[daemon] 配对设备 {self._target_device_name} 未找到，重试中...")
                else:
                    # 前缀匹配任意 Claude-Buddy-* 设备
                    addr = next(
                        (d.address for d in devices if d.name and d.name.startswith("Claude-Buddy-")),
                        None,
                    )
                    if not addr:
                        print("[daemon] 未找到任何 Claude-Buddy 设备，重试中...")

                if not addr:
                    await asyncio.sleep(3)
                    continue

                self._client = BleakClient(addr, disconnected_callback=self._on_ble_disconnect)
                await self._client.connect()
                await self._client.start_notify(NUS_TX, self._on_ble_notify)
                self._connected     = True
                print(f"[daemon] connected to {addr}")
                if self._on_connect:
                    self._on_connect()
                await asyncio.sleep(1.0)
            except Exception as e:
                print(f"[daemon] connect failed: {e}")
                self._client        = None
                self._connected     = False
                await asyncio.sleep(3)

    # ── 连接循环结束 ─────────────────────────────────────────


# ── WiFi 实现（预留） ─────────────────────────────────────────

class WifiTransport(Transport):
    """TCP socket 传输（未实现）。"""
    async def start(self, on_recv, on_connect, on_disconnect): raise NotImplementedError
    async def send(self, payload: dict): raise NotImplementedError
    def connected(self) -> bool: raise NotImplementedError


# ── 串口实现（预留） ─────────────────────────────────────────

class UartTransport(Transport):
    """USB-UART 串口传输（未实现）。"""
    async def start(self, on_recv, on_connect, on_disconnect): raise NotImplementedError
    async def send(self, payload: dict): raise NotImplementedError
    def connected(self) -> bool: raise NotImplementedError
