"""sim_device 入口：python -m scripts.sim_device"""
import asyncio
import logging
import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SIM_DIR = os.path.dirname(os.path.abspath(__file__))
_DEVICE_DIR = os.path.join(_ROOT, "device")
sys.path.insert(0, _DEVICE_DIR)
sys.path.insert(0, _SIM_DIR)  # sim_device/ 优先，覆盖 device/transport.py

_LOG_FILE = os.path.join(_SIM_DIR, "logs", "sim_device.log")
os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=_LOG_FILE, filemode="w",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)

from transport import TcpTransport  # noqa: E402
from renderer import SimRenderer    # noqa: E402
from protocol import parse          # noqa: E402
from queue import Queue             # noqa: E402

_log = logging.getLogger("main")


_transport = None
_msg_queue = None
_renderer = None


async def recv_task():
    """复用 device/main.py 的 ble_recv_task 结构"""
    while True:
        _log.info("waiting for daemon connection...")
        print("[sim_device] waiting for daemon connection...")
        await _transport.connect()
        await _renderer.on_connect()
        try:
            while _transport.connected():
                line = await _transport.recv_line()
                _log.debug("recv: %s", line)
                msg = parse(line)
                _msg_queue.put_nowait(msg)
        except OSError:
            pass
        await _renderer.on_disconnect()


async def render_task():
    """复用 device/main.py 的 render_task 结构"""
    while True:
        msg = await _msg_queue.get()
        if msg is not None:
            await _renderer.render(msg)


async def main():
    global _transport, _msg_queue, _renderer

    _transport = TcpTransport()
    _renderer = SimRenderer()
    _msg_queue = Queue()

    await _renderer.init()
    await asyncio.gather(recv_task(), render_task())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[sim_device] bye")
