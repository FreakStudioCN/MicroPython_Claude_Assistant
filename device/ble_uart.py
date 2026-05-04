# ============================================================
# ble_uart.py —— BLE Nordic UART Service（NUS）驱动
#
# 封装 aioble 库，提供异步的 BLE 广播、接收、发送接口。
#
# NUS 协议简介：
#   NUS 是一种标准化的 BLE"虚拟串口"服务，由 Nordic 定义。
#   - RX 特征（UUID 6e400002）：中心设备（PC）写入 → 外设（ESP32）接收
#   - TX 特征（UUID 6e400003）：外设（ESP32）通知 → 中心设备（PC）接收
#
# BLE MTU（最大传输单元）限制：
#   BLE 4.x 默认 ATT MTU = 23 字节，去掉 3 字节头部实际有效载荷 = 20 字节。
#   因此发送时需要将数据切成 20 字节的块分批发送。
# ============================================================

import bluetooth
import aioble
import time
from config import BLE_NAME, NUS_SERVICE, NUS_RX, NUS_TX

# ── GATT 服务与特征注册（模块导入时立即执行）────────────────
# 创建 NUS 服务实例
_svc = aioble.Service(bluetooth.UUID(NUS_SERVICE))

# RX 特征：允许中心设备写入（write + write_no_response）
# capture=True 表示 aioble 会缓存收到的所有写入，等待 .written() 读取
_rx = aioble.Characteristic(
    _svc,
    bluetooth.UUID(NUS_RX),
    write=True,
    write_no_response=True,  # 允许无应答写入（更高吞吐，PC 端用此方式）
    capture=True             # 捕获模式：数据入队，不丢失
)

# TX 特征：外设通知中心设备（notify=True）
_tx = aioble.Characteristic(
    _svc,
    bluetooth.UUID(NUS_TX),
    notify=True
)

# 向 BLE 栈注册所有服务（必须在 advertise 之前调用）
aioble.register_services(_svc)

# ── 模块级状态 ───────────────────────────────────────────────
_conn = None    # 当前 BLE 连接对象（aioble.Connection），None 表示未连接
_buf  = b""     # 接收缓冲区，累积直到出现换行符才返回一行


async def advertise():
    """
    开始 BLE 广播并等待中心设备连接。
    此函数会阻塞直到有设备连接上为止。

    广播间隔：250_000 μs = 250 ms（影响被扫描到的速度，越小越快但越耗电）
    广播内容：设备名称 + NUS 服务 UUID（PC 端用 UUID 过滤找到本设备）

    返回：aioble.Connection 对象
    """
    global _conn, _buf
    _buf = b""
    print(f"[ble] advertising... t={time.time()}")
    _conn = await aioble.advertise(
        250_000,
        name=BLE_NAME,
        services=[bluetooth.UUID(NUS_SERVICE)],
    )
    print(f"[ble] connected: {_conn} t={time.time()}")
    return _conn


async def recv_line() -> str:
    """
    异步接收一行文本（以 '\\n' 为边界）。
    BLE 数据可能被分成多个 20 字节的包到达，此函数负责拼合。

    工作原理：
      1. 循环调用 _rx.written()，每次得到一段二进制数据
      2. 追加到 _buf 缓冲区
      3. 发现 '\\n' 后，切分并返回第一行（剩余部分留在 _buf）

    注意：timeout_ms=None 表示无限等待，由 main.py 的 asyncio.wait_for 控制超时。
    """
    global _buf
    while True:
        conn, data = await _rx.written(timeout_ms=None)
        print(f"[ble] chunk t={time.time()} len={len(data)} data={data}")
        _buf += data
        print(f"[ble] buf len={len(_buf)} has_newline={b'\n' in _buf}")

        if b"\n" in _buf:
            line, _buf = _buf.split(b"\n", 1)
            decoded = line.decode().strip()
            print(f"[ble] recv complete t={time.time()}: {decoded[:80]}")
            return decoded


async def send(msg: str):
    """
    向已连接的中心设备发送文本消息（BLE Notify）。

    由于 BLE ATT MTU 限制，每次最多发送 20 字节，
    需要将消息切成 20 字节的块逐块 notify。

    参数：
      msg — 待发送的 JSON 字符串（含末尾换行符）
    """
    if _conn is None:
        print("[ble] send skipped: not connected")
        return

    data = msg.encode()
    print(f"[ble] send t={time.time()} total={len(data)}B chunks={(len(data)+19)//20}")
    for i in range(0, len(data), 20):
        chunk = data[i:i+20]
        print(f"[ble] send chunk {i//20}: {chunk}")
        _tx.notify(_conn, chunk)


def connected() -> bool:
    """
    返回当前 BLE 是否处于连接状态。
    用于 render_task 判断右上角连接指示灯颜色，
    以及 ble_task 判断是否需要重新广播。
    """
    return _conn is not None and _conn.is_connected()
