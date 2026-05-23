try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import gc
import os
import config as cfg
from queue import Queue
import protocol as p
import logging

if cfg.LOG_ENABLE:
    try:
        os.mkdir("/log")
    except OSError:
        pass
    logging.basicConfig(filename=cfg.LOG_FILE, filemode="w", level=cfg.LOG_LEVEL)
else:
    logging.basicConfig(level=cfg.LOG_LEVEL)

_log = logging.getLogger("main")

_transport = None
_msg_queue = None
_renderer  = None


async def ble_recv_task():
    while True:
        _log.info("waiting for PC connection...")
        await _transport.connect()
        await _renderer.on_connect()
        _log.info("connected")
        try:
            while _transport.connected():
                line = await _transport.recv_line()
                _msg_queue.put_nowait(p.parse(line))
        except OSError:
            pass
        await _renderer.on_disconnect()
        _log.info("disconnected")


async def render_task():
    while True:
        msg = await _msg_queue.get()
        if msg is not None:
            await _renderer.render(msg)


async def _main():
    global _msg_queue, _renderer, _transport

    _log.info("waiting 3s for mpremote connection...")
    await asyncio.sleep(3)
    gc.collect()
    _log.info("startup: free=%d alloc=%d", gc.mem_free(), gc.mem_alloc())

    if cfg.VARIANT == "clock":
        from light_renderer import LightRenderer
        _renderer = LightRenderer()
    else:
        from display_renderer import DisplayRenderer
        _renderer = DisplayRenderer()

    await _renderer.init()
    gc.collect()
    _log.info("after renderer: free=%d alloc=%d", gc.mem_free(), gc.mem_alloc())

    from transport import BleTransport
    _transport = BleTransport()
    gc.collect()
    _log.info("after BLE: free=%d alloc=%d", gc.mem_free(), gc.mem_alloc())

    _msg_queue = Queue()
    await asyncio.gather(ble_recv_task(), render_task())


asyncio.run(_main())
