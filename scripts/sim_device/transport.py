"""TCP 传输层，实现 device/transport.py 的 Transport 接口"""
import asyncio


class TcpTransport:
    """连接 daemon 的 TCP 57321，模拟 BLE NUS"""

    def __init__(self, host="127.0.0.1", port=57321):
        self._host = host
        self._port = port
        self._reader = None
        self._writer = None

    async def connect(self):
        """连接 daemon（阻塞直到成功）"""
        while True:
            try:
                self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
                return
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(2)

    async def recv_line(self) -> str:
        """读一行 JSON（阻塞）"""
        if self._reader is None:
            raise OSError("not connected")
        line = await self._reader.readline()
        if not line:
            raise OSError("connection closed")
        return line.decode().strip()

    async def send(self, msg: str):
        """发送一行（ack 用，模拟器不需要）"""
        if self._writer is None:
            return
        self._writer.write((msg + "\n").encode())
        await self._writer.drain()

    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()
