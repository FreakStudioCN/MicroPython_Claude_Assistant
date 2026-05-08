try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import gc
from queue import Queue
from display_renderer import DisplayRenderer
import protocol as p

_transport = None
_msg_queue = None
_renderer = None


async def ble_recv_task():
    while True:
        print("[ble] waiting for PC connection...")
        await _transport.connect()
        await _renderer.on_connect()
        print("[ble] connected")
        try:
            while _transport.connected():
                line = await _transport.recv_line()
                _msg_queue.put_nowait(p.parse(line))
        except OSError:
            pass
        await _renderer.on_disconnect()
        print("[ble] disconnected")


async def render_task():
    while True:
        msg = await _msg_queue.get()
        if msg is not None:
            await _renderer.render(msg)


async def _main():
    global _msg_queue, _renderer, _transport

    # 启动延时 3s，方便 mpremote 连接
    print("[init] waiting 3s for mpremote connection...")
    await asyncio.sleep(3)

    # 初始状态内存
    gc.collect()
    print(f"[mem] startup: free={gc.mem_free()} alloc={gc.mem_alloc()}")

    # 1. 先初始化 LVGL（占用帧缓冲）
    _renderer = DisplayRenderer()
    await _renderer.init()
    gc.collect()
    print(f"[mem] after UI: free={gc.mem_free()} alloc={gc.mem_alloc()}")
    print("[init] LVGL initialized")

    # 2. 再导入 BLE（此时帧缓冲已分配，剩余内存给 BLE）
    print("[init] loading BLE stack...")
    from transport import BleTransport
    gc.collect()
    print(f"[mem] after import: free={gc.mem_free()} alloc={gc.mem_alloc()}")

    _transport = BleTransport()
    gc.collect()
    print(f"[mem] after BLE init: free={gc.mem_free()} alloc={gc.mem_alloc()}")
    print("[init] BLE transport loaded")

    _msg_queue = Queue()
    await asyncio.gather(ble_recv_task(), render_task())

asyncio.run(_main())
