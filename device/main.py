try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import sys
import ble_uart
import protocol as p
import state as st

STATE_NAME = {
    st.SLEEP: "SLEEP", st.IDLE: "IDLE", st.WORKING: "WORKING",
    st.PENDING: "PENDING", st.CELEBRATE: "CELEBRATE",
    st.ERROR: "ERROR", st.APPROVED: "APPROVED",
}

class _Queue:
    def __init__(self):
        self._items = []
        self._event = asyncio.Event()

    def put_nowait(self, item):
        self._items.append(item)
        self._event.set()

    async def get(self):
        while not self._items:
            self._event.clear()
            await self._event.wait()
        return self._items.pop(0)


_msg_queue = None
_approval_pending = False


def _print_status(s):
    sid = getattr(s, "id", "—")
    state = st.StateEvent.get_base_state(s)
    print(f"  id={sid} running={s.running} waiting={s.waiting} completed={s.completed}")
    print(f"  msg={s.msg!r} category={s.category!r} error={s.error!r}")
    print(f"  state={STATE_NAME.get(state, state)} needs_approval={st.StateEvent.needs_approval(s)}")
    if st.StateEvent.needs_approval(s):
        print(f"  !! tool={s.prompt.get('tool','?')} id={s.prompt.get('id','?')}")
        print(f"     hint={s.prompt.get('hint','')[:60]}")


def _print_msg(msg):
    if msg is None:
        print("[parse] None — skipped")
        return
    if isinstance(msg, dict):
        print(f"[ack/cmd] {msg}")
        return
    if _approval_pending:
        return  # 审批等待中，静默丢弃打印，避免刷走输入提示
    if isinstance(msg, p.MultiSessionMsg):
        print(f"[MultiSessionMsg] {len(msg.sessions)} session(s):")
        for s in msg.sessions:
            _print_status(s)
        return
    print("[StatusMsg]:")
    _print_status(msg)


async def _async_input(prompt: str) -> str:
    import select
    _poll = select.poll()
    _poll.register(sys.stdin, select.POLLIN)
    while _poll.poll(0):
        sys.stdin.read(1)
    print(prompt, end="")
    buf = ""
    while True:
        if _poll.poll(0):
            c = sys.stdin.read(1)
            if c in ("\r", "\n"):
                if buf:
                    return buf.strip()
            elif c:
                buf += c
        await asyncio.sleep(0)


async def _handle_approval(session_idx: int):
    global _approval_pending
    try:
        choice = await _async_input("  Input y=approve / n=deny, then Enter: ")
    except Exception:
        choice = "n"
    decision = "once" if choice == "y" else ("session" if choice == "s" else "deny")
    reply = p.build_decision(session_idx, decision)
    await ble_uart.send(reply)
    print(f"  → sent decision='{decision}'")


async def ble_recv_task():
    global _approval_pending
    while True:
        print("[ble] waiting for PC connection...")
        await ble_uart.advertise()
        print("[ble] connected")
        _approval_pending = False
        while ble_uart.connected():
            line = await ble_uart.recv_line()
            _msg_queue.put_nowait(p.parse(line))
        print("[ble] disconnected")


async def render_task():
    global _approval_pending
    while True:
        msg = await _msg_queue.get()

        if isinstance(msg, dict) and msg.get("cmd") == "ping":
            await ble_uart.send(p.build_ack("pong", ok=True))
            continue

        _print_msg(msg)

        if _approval_pending:
            has_pending = False
            if isinstance(msg, p.MultiSessionMsg):
                has_pending = any(st.StateEvent.needs_approval(s) for s in msg.sessions)
            elif isinstance(msg, p.StatusMsg):
                has_pending = st.StateEvent.needs_approval(msg)
            if has_pending:
                continue  # 仍在等待 daemon 处理决策，丢弃重发消息
            _approval_pending = False  # 解锁，继续处理这条消息

        if isinstance(msg, p.MultiSessionMsg):
            for i, s in enumerate(msg.sessions):
                if st.StateEvent.needs_approval(s):
                    _approval_pending = True
                    asyncio.create_task(_handle_approval(i))
                    break
        elif isinstance(msg, p.StatusMsg) and st.StateEvent.needs_approval(msg):
            _approval_pending = True
            asyncio.create_task(_handle_approval(0))


async def _main():
    global _msg_queue
    _msg_queue = _Queue()
    await asyncio.gather(ble_recv_task(), render_task())

asyncio.run(_main())
