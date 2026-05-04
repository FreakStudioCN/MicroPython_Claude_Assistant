try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import ble_uart
import protocol as p
import state as st

STATE_NAME = {
    st.SLEEP: "SLEEP", st.IDLE: "IDLE", st.WORKING: "WORKING",
    st.PENDING: "PENDING", st.CELEBRATE: "CELEBRATE",
    st.ERROR: "ERROR", st.APPROVED: "APPROVED",
}


def _print_status(s):
    """打印单个 session 或旧 StatusMsg 的字段（两者字段相同）。"""
    sid = getattr(s, "id", "—")
    print(f"  id          = {sid}")
    print(f"  running     = {s.running}")
    print(f"  waiting     = {s.waiting}")
    print(f"  completed   = {s.completed}")
    print(f"  msg         = {s.msg!r}")
    print(f"  category    = {s.category!r}")
    print(f"  error       = {s.error!r}")
    print(f"  interrupted = {s.interrupted}")
    print(f"  prompt      = {s.prompt}")
    state = st.StateEvent.get_base_state(s)
    print(f"  [StateEvent]")
    print(f"    base_state      = {STATE_NAME.get(state, state)}")
    print(f"    needs_approval  = {st.StateEvent.needs_approval(s)}")
    print(f"    should_celebrate= {st.StateEvent.should_celebrate(s)}")
    print(f"    should_show_err = {st.StateEvent.should_show_error(s)}")
    print(f"    should_skip_err = {st.StateEvent.should_skip_error(s)}")
    if st.StateEvent.needs_approval(s):
        print(f"  !! Approval request:")
        print(f"     tool = {s.prompt.get('tool','?')}")
        print(f"     hint = {s.prompt.get('hint','')[:60]}")
        print(f"     id   = {s.prompt.get('id','?')}")


def _print_msg(msg):
    if msg is None:
        print("[parse] None — skipped")
        return
    if isinstance(msg, dict):
        print(f"[ack/cmd] {msg}")
        return
    if isinstance(msg, p.MultiSessionMsg):
        print(f"[MultiSessionMsg] {len(msg.sessions)} session(s):")
        print("─" * 48)
        for s in msg.sessions:
            _print_status(s)
            print("·" * 24)
        print("─" * 48)
        return
    # 旧 StatusMsg（向后兼容）
    print("[StatusMsg] (legacy single-session):")
    print("─" * 48)
    _print_status(msg)
    print("─" * 48)


async def _handle_approval(prompt, tool_id):
    print("  Input y=approve / n=deny, then Enter: ", end="")
    try:
        choice = input().strip().lower()
    except Exception:
        choice = "n"
    decision = "once" if choice == "y" else ("session" if choice == "s" else "deny")
    reply = p.build_decision(tool_id, decision)
    await ble_uart.send(reply)
    print(f"  → sent decision='{decision}'")


async def ble_task():
    while True:
        print("[ble] waiting for PC connection...")
        await ble_uart.advertise()
        print(f"[ble] connected")
        while ble_uart.connected():
            try:
                line = await asyncio.wait_for(ble_uart.recv_line(), timeout=None)
            except asyncio.TimeoutError:
                break
            msg = p.parse(line)
            _print_msg(msg)

            if isinstance(msg, p.MultiSessionMsg):
                for s in msg.sessions:
                    if st.StateEvent.needs_approval(s):
                        await _handle_approval(s.prompt, s.prompt["id"])
                        break
            elif isinstance(msg, p.StatusMsg) and st.StateEvent.needs_approval(msg):
                # 旧格式兼容
                await _handle_approval(msg.prompt, msg.prompt["id"])

        print("[ble] disconnected")


asyncio.run(ble_task())
