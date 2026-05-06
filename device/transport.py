# transport.py —— 通信传输抽象层
#
# Transport 基类定义统一接口，具体实现类负责各自传输细节。
# 当前实现：BleTransport（BLE NUS）
# 预留接口：WifiTransport（TCP）、UartTransport（USB 串口）


class Transport:
    async def connect(self): raise NotImplementedError
    async def recv_line(self) -> str: raise NotImplementedError
    async def send(self, msg: str): raise NotImplementedError
    def connected(self) -> bool: raise NotImplementedError


# ── BLE 实现 ─────────────────────────────────────────────────

import bluetooth
import aioble
import time
from config import BLE_NAME, NUS_SERVICE, NUS_RX, NUS_TX

# GATT 服务与特征注册（模块导入时执行，全局唯一）
_svc = aioble.Service(bluetooth.UUID(NUS_SERVICE))
_rx = aioble.Characteristic(
    _svc, bluetooth.UUID(NUS_RX),
    write=True, write_no_response=True, capture=True,
)
_tx = aioble.Characteristic(
    _svc, bluetooth.UUID(NUS_TX),
    notify=True,
)
aioble.register_services(_svc)


class BleTransport(Transport):
    def __init__(self):
        self._conn = None
        self._buf  = b""

    async def connect(self):
        self._buf = b""
        print(f"[ble] advertising... t={time.time()}")
        self._conn = await aioble.advertise(
            250_000,
            name=BLE_NAME,
            services=[bluetooth.UUID(NUS_SERVICE)],
        )
        print(f"[ble] connected: {self._conn} t={time.time()}")
        return self._conn

    async def recv_line(self) -> str:
        chunks = 0
        while True:
            conn, data = await _rx.written(timeout_ms=None)
            self._buf += data
            chunks += 1
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                decoded = line.decode().strip()
                print(f"[ble] recv t={time.time()} chunks={chunks} len={len(decoded)} : {decoded[:60]}")
                return decoded

    async def send(self, msg: str):
        if self._conn is None:
            print("[ble] send skipped: not connected")
            return
        data = msg.encode()
        print(f"[ble] send t={time.time()} total={len(data)}B chunks={(len(data)+19)//20}")
        for i in range(0, len(data), 20):
            chunk = data[i:i+20]
            print(f"[ble] send chunk {i//20}: {chunk}")
            _tx.notify(self._conn, chunk)

    def connected(self) -> bool:
        return self._conn is not None and self._conn.is_connected()


# ── WiFi 实现（预留） ─────────────────────────────────────────

class WifiTransport(Transport):
    """TCP socket 传输（未实现）。"""
    async def connect(self): raise NotImplementedError
    async def recv_line(self) -> str: raise NotImplementedError
    async def send(self, msg: str): raise NotImplementedError
    def connected(self) -> bool: raise NotImplementedError


# ── 串口实现（预留） ─────────────────────────────────────────

class UartTransport(Transport):
    """USB-UART 串口传输（未实现）。"""
    async def connect(self): raise NotImplementedError
    async def recv_line(self) -> str: raise NotImplementedError
    async def send(self, msg: str): raise NotImplementedError
    def connected(self) -> bool: raise NotImplementedError
