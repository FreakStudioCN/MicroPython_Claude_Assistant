import asyncio
import time
import lvgl as lv
import ble_uart
import protocol
import state as st
import buddy
from display import Screen
from config import FPS

_state            = st.State()
_screen           = Screen()
_approval_pending = False
_last_msg         = ""


async def ble_task():
    global _approval_pending, _last_msg
    while True:
        await ble_uart.advertise()
        print("[ble] connected")
        while ble_uart.connected():
            try:
                line = await asyncio.wait_for(ble_uart.recv_line(), timeout=None)
            except asyncio.TimeoutError:
                break
            msg = protocol.parse(line)
            if msg is None:
                continue
            if isinstance(msg, dict):
                cmd = msg.get("cmd")
                if cmd in ("name", "owner", "unpair"):
                    await ble_uart.send(protocol.build_ack(cmd))
            else:
                _last_msg = msg.msg
                _state.update(msg)
                _approval_pending = bool(msg.prompt)


async def touch_task():
    global _approval_pending
    _tapped = [False]

    def _on_tap(e):
        _tapped[0] = True

    lv.screen_active().add_event_cb(_on_tap, lv.EVENT.RELEASED, None)

    while True:
        if _tapped[0] and _approval_pending and _state.pending_prompt:
            _tapped[0] = False
            await ble_uart.send(
                protocol.build_decision(_state.pending_prompt["id"], "once")
            )
            _approval_pending = False
            _state.set_heart()
            print("[touch] approved")
        await asyncio.sleep_ms(50)


async def render_task():
    interval = 1000 // FPS
    t0 = time.time()
    while True:
        _state.tick()
        if _approval_pending and _state.pending_prompt:
            p = _state.pending_prompt
            secs = max(0, 30 - (time.time() - t0))
            _screen.draw_approval(p.get("tool", "?"), p.get("hint", ""), int(secs))
        else:
            t0 = time.time()
            buddy.tick(_screen, _state.active, _last_msg, ble_uart.connected())
        await asyncio.sleep_ms(interval)


async def _safe(coro, name):
    try:
        await coro
    except Exception as e:
        print(f"[{name}] error: {e}")
        import sys; sys.print_exception(e)


async def main():
    await asyncio.gather(
        _safe(ble_task(), "ble"),
        _safe(touch_task(), "touch"),
        _safe(render_task(), "render"),
    )

asyncio.run(main())
