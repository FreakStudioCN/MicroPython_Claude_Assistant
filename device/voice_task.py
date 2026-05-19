try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import machine
import config as cfg

from state import S_DONE, S_ERROR, S_PENDING

_EVENT_STATE = {S_DONE: "done", S_ERROR: "error", S_PENDING: "pending", "connect": "connect", "disconnect": "disconnect", "startup": "startup"}


class VoiceTask:
    def __init__(self):
        self._current: asyncio.Task | None = None
        self._queue: list = []
        self._i2s = machine.I2S(
            0,
            sck=machine.Pin(cfg.CLOCK_SPK_BCLK),
            ws=machine.Pin(cfg.CLOCK_SPK_LRC),
            sd=machine.Pin(cfg.CLOCK_SPK_DIN),
            mode=machine.I2S.TX,
            bits=cfg.I2S_BITS,
            format=machine.I2S.MONO,
            rate=cfg.I2S_RATE,
            ibuf=cfg.I2S_IBUF,
        )

    def is_busy(self) -> bool:
        return self._current is not None and not self._current.done()

    def _cancel_current(self):
        if self._current and not self._current.done():
            self._current.cancel()

    async def trigger(self, history: list, session, event_type: str, force=False):
        state = _EVENT_STATE.get(event_type)
        if not state:
            return
        if force:
            self._cancel_current()
            self._queue.clear()
            self._current = asyncio.create_task(self._play_and_drain(state))
        elif self.is_busy():
            if state not in self._queue:
                self._queue.append(state)
                print(f"[voice] queued: {state} queue={self._queue}")
        else:
            self._current = asyncio.create_task(self._play_and_drain(state))

    async def maybe_idle_speak(self, history: list, state: str = "working"):
        if self.is_busy() or self._queue:
            return
        self._current = asyncio.create_task(self._play_and_drain(state))

    async def _play_and_drain(self, state: str):
        await self._play_state(state)
        if self._queue:
            self._current = asyncio.create_task(self._play_and_drain(self._queue.pop(0)))

    async def _play_state(self, state: str):
        path = self._pick(state)
        if path:
            print(f"[voice] play: state={state} file={path}")
            await self._play(path)
        else:
            print(f"[voice] no file for state={state}")

    def _pick(self, state: str) -> str:
        import os, urandom
        try:
            files = [f for f in os.listdir(cfg.VOICE_ASSETS_DIR)
                     if f.endswith(".pcm") and ("-" + state + "-") in f]
        except OSError:
            return None
        if not files:
            return None
        return cfg.VOICE_ASSETS_DIR + "/" + files[urandom.getrandbits(8) % len(files)]

    async def _play(self, path: str):
        amp = machine.Pin(cfg.CLOCK_AMP_SD_PIN, machine.Pin.OUT)
        amp.value(1)
        swriter = asyncio.StreamWriter(self._i2s)
        try:
            with open(path, "rb") as f:
                buf = bytearray(cfg.I2S_READ_BUF)
                while True:
                    n = f.readinto(buf)
                    if not n:
                        break
                    swriter.write(memoryview(buf)[:n])
                    await swriter.drain()
            print(f"[voice] done: {path}")
        except Exception as e:
            print(f"[voice] play error: {e}")
        finally:
            amp.value(0)
