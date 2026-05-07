try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

from transport import BleTransport
from queue import Queue
import protocol as p
import state as st

_transport = BleTransport()
_msg_queue = None


def _print_status(s):
    state = st.StateEvent.get_base_state(s)
    print(f"  running={s.running} waiting={s.waiting} completed={s.completed}")
    print(f"  msg={s.msg!r} state={st.STATE_NAMES[state]} needs_approval={st.StateEvent.needs_approval(s)}")
    if s.error:
        print(f"  error={s.error!r}")


def _print_msg(msg):
    if msg is None:
        print("[parse] None — skipped")
        return
    if isinstance(msg, dict):
        print(f"[ack/cmd] {msg}")
        return
    print(f"[MultiSessionMsg] {len(msg.sessions)} session(s):")
    for s in msg.sessions:
        _print_status(s)


async def ble_recv_task():
    while True:
        print("[ble] waiting for PC connection...")
        await _transport.connect()
        print("[ble] connected")
        while _transport.connected():
            line = await _transport.recv_line()
            _msg_queue.put_nowait(p.parse(line))
        print("[ble] disconnected")


async def render_task():
    while True:
        msg = await _msg_queue.get()
        _print_msg(msg)


async def _main():
    global _msg_queue
    _msg_queue = Queue()
    await asyncio.gather(ble_recv_task(), render_task())

asyncio.run(_main())
