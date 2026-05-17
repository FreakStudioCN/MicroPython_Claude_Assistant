try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import gc
import config as cfg
from queue import Queue
import protocol as p

_transport = None
_msg_queue = None
_renderer  = None


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

    print("[init] waiting 3s for mpremote connection...")
    await asyncio.sleep(3)
    gc.collect()
    print(f"[mem] startup: free={gc.mem_free()} alloc={gc.mem_alloc()}")

    if cfg.VARIANT == "clock":
        from light_renderer import LightRenderer
        _renderer = LightRenderer()
    else:
        from display_renderer import DisplayRenderer
        _renderer = DisplayRenderer()

    await _renderer.init()
    gc.collect()
    print(f"[mem] after renderer: free={gc.mem_free()} alloc={gc.mem_alloc()}")

    from transport import BleTransport
    _transport = BleTransport()
    gc.collect()
    print(f"[mem] after BLE: free={gc.mem_free()} alloc={gc.mem_alloc()}")

    _msg_queue = Queue()
    if cfg.VARIANT == "clock":
        await asyncio.gather(ble_recv_task(), render_task())
    else:
        await asyncio.gather(ble_recv_task(), render_task())


asyncio.run(_main())
