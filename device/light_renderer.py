"""
light_renderer.py —— WS2812 双灯渲染器（闹钟版）

灯光效果（帧计数器驱动，不阻塞）：
  I  空闲    蓝色呼吸灯，周期 3s
  W  工作中  青色流水灯，左→右→左
  P  等待审批 黄色同步慢闪
  C  完成    绿色快闪 3 下后转空闲
  E  出错    红色双灯交替快闪
  连接瞬间   白色亮 0.5s
  断线       全灭
"""

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import math
import time
import machine
import neopixel
import config as cfg
from voice_task import VoiceTask
from state import sess_state as _sess_state

# 状态优先级：多 session 时取最高优先级
_PRIORITY = {"E": 0, "P": 1, "W": 2, "C": 3, "I": 4}

# 历史记录条目：{"name", "state", "msg"}
_STATE_LABEL = {"I": "空闲", "W": "工作中", "P": "等待审批", "C": "完成", "E": "出错"}


def _dominant(sessions) -> str:
    if not sessions:
        return "I"
    return min((_sess_state(s) for s in sessions), key=lambda s: _PRIORITY[s])


class LightRenderer:

    def __init__(self):
        self._np: neopixel.NeoPixel | None = None
        self._voice = VoiceTask()

        self._sessions = []
        self._prev_states: dict[str, str] = {}  # session name → last triggered state
        self._history: list[dict] = []

        self._frame = 0
        self._state = "I"
        self._state_frame = 0
        self._connect_flash = 0
        self._disconnect_fade = 0
        self._disconnect_loop = False
        self._rainbow_frames = 60

        # C/E/P 状态队列，每项最少显示 MIN_FRAMES 帧后出队
        self._state_queue: list = []
        self._queue_frame = 0
        self._log_queue: list = []  # ISR→asyncio 日志缓冲

        # 偶发播报计时
        self._idle_speak_deadline = self._next_idle_deadline()

    # ── 公共接口 ──────────────────────────────────────────────

    async def init(self):
        print("[light] init hardware...")
        pin = machine.Pin(cfg.CLOCK_LED_PIN, machine.Pin.OUT)
        self._np = neopixel.NeoPixel(pin, cfg.CLOCK_LED_COUNT)
        self._set_all(0, 0, 0)
        self._timer = machine.Timer(0)
        self._timer.init(period=50, mode=machine.Timer.PERIODIC, callback=lambda _: self._tick_leds())
        print(f"[light] NeoPixel ready: pin={cfg.CLOCK_LED_PIN} count={cfg.CLOCK_LED_COUNT}")
        self._disconnect_loop = True
        self._disconnect_fade = 30
        asyncio.create_task(self._voice.trigger([], None, "startup"))
        asyncio.create_task(self._disconnect_speak_loop())

    async def render(self, msg):
        while self._log_queue:
            print(self._log_queue.pop(0))
        if msg is None:
            return
        self._sessions = msg.sessions

        # 状态跳变检测 → 触发语音 + 入队列
        for sess in self._sessions:
            cur = _sess_state(sess)
            prev = self._prev_states.get(sess.name)
            if cur != prev:
                label = _STATE_LABEL.get(cur, cur)
                print(f"[light] sess={sess.name} {prev} → {cur}({label}) msg={sess.msg or ''!r}")
                self._push_history(sess, cur)
                if cur in ("C", "E", "P"):
                    await self._voice.trigger(self._history, sess, cur)
                    if cur not in self._state_queue:
                        self._state_queue.append(cur)
                    print(f"[light] queue: {self._state_queue}")
                self._prev_states[sess.name] = cur

        # 偶发播报
        dom = _dominant(self._sessions)
        if dom == "W" and time.time() >= self._idle_speak_deadline:
            await self._voice.maybe_idle_speak(self._history)
            self._idle_speak_deadline = self._next_idle_deadline()

        # 队列空时才更新背景状态（W/I）
        if not self._state_queue:
            bg = dom if dom in ("W", "I") else "I"
            if bg != self._state:
                sess_info = " | ".join(
                    f"{s.name}:{_sess_state(s)}" for s in self._sessions
                ) if self._sessions else "none"
                print(f"[light] state: {self._state} → {bg}  sessions=[{sess_info}]")
                self._state = bg
                self._state_frame = self._frame

    async def on_connect(self):
        print("[light] on_connect: flash=10, clearing sessions/queue/prev_states")
        self._connect_flash = 30
        self._disconnect_loop = False
        self._sessions = []
        self._state_queue.clear()
        self._prev_states.clear()
        self._state = "I"
        self._state_frame = self._frame
        asyncio.create_task(self._voice.trigger([], None, "connect", force=True))

    async def on_disconnect(self):
        print(f"[light] on_disconnect: fade=10, state was={self._state}, queue={self._state_queue}")
        self._disconnect_fade = 30
        self._disconnect_loop = True
        self._sessions = []
        self._state_queue.clear()
        self._prev_states.clear()
        self._state = "I"
        self._state_frame = self._frame
        asyncio.create_task(self._disconnect_speak_loop())

    async def _disconnect_speak_loop(self):
        count = 0
        while self._disconnect_loop and count < 3:
            await self._voice.trigger([], None, "disconnect")
            count += 1
            await asyncio.sleep(10)

    # ── 灯光帧驱动 ────────────────────────────────────────────
    _MIN_FRAMES = 20  # 每个队列状态最少显示帧数（20帧×50ms=1s）

    def _tick_leds(self):
        self._frame += 1

        if self._rainbow_frames > 0:
            self._rainbow_frames -= 1
            h0 = (self._frame * 4) % 256
            h1 = (h0 + 128) % 256
            self._np[0] = self._hsv(h0)
            self._np[1] = self._hsv(h1)
            self._np.write()
            return

        if self._connect_flash > 0:
            self._connect_flash -= 1
            self._set_all(80, 80, 80)
            return

        if self._disconnect_fade > 0:
            v = self._disconnect_fade * 8
            self._disconnect_fade -= 1
            self._set_all(v // 3, v, 0)
            if self._disconnect_fade == 0:
                self._state = "I"
                self._state_frame = self._frame
            return

        # 队列消费：队列头满足最少帧数后出队
        if self._state_queue:
            head = self._state_queue[0]
            if self._state != head:
                self._log_queue.append(f"[light] queue→state: {self._state} → {head}  remaining={self._state_queue}")
                self._state = head
                self._state_frame = self._frame
                self._queue_frame = self._frame
            elif (self._frame - self._queue_frame) >= self._MIN_FRAMES:
                self._state_queue.pop(0)
                if self._state_queue:
                    self._state = self._state_queue[0]
                    self._state_frame = self._frame
                    self._queue_frame = self._frame
                    self._log_queue.append(f"[light] queue dequeue→next: {self._state}  remaining={self._state_queue}")
                else:
                    self._log_queue.append(f"[light] queue empty, state={self._state}")

        s = self._state
        f = self._frame - self._state_frame

        if s == "I":
            v = int((math.sin(f * math.pi / 30) + 1) / 2 * 40)
            self._set_all(0, 0, v)  # 蓝色 GRB=(0,0,B)

        elif s == "W":
            if (f // 6) % 2 == 0:
                self._np[0] = (100, 0, 128)   # 青色 GRB=(G,R,B)
                self._np[1] = (15, 0, 20)
            else:
                self._np[0] = (15, 0, 20)
                self._np[1] = (100, 0, 128)
            self._np.write()

        elif s == "P":
            on = (f % 24) < 16
            c = (100, 128, 0) if on else (0, 0, 0)  # 黄色 GRB=(G,R,B)
            self._set_all(*c)

        elif s == "C":
            if f < 18:
                on = (f % 6) < 3
                c = (128, 0, 40) if on else (0, 0, 0)  # 绿色 GRB=(G,R,B)
                self._set_all(*c)
            else:
                v = int((math.sin(f * math.pi / 30) + 1) / 2 * 30)
                self._set_all(v, 0, 0)  # 绿色呼吸

        elif s == "E":
            if (f // 2) % 2 == 0:
                self._np[0] = (0, 128, 0)   # 红色 GRB=(0,R,0)
                self._np[1] = (0, 0, 0)
            else:
                self._np[0] = (0, 0, 0)
                self._np[1] = (0, 128, 0)
            self._np.write()

    @staticmethod
    def _hsv(h, v=70):
        h = h % 256
        i = h // 43
        f = (h % 43) * 6
        q = v * (255 - f) // 255
        t = v * f // 255
        if i == 0: return t, v, 0   # GRB
        if i == 1: return v, q, 0
        if i == 2: return v, 0, t
        if i == 3: return q, 0, v
        if i == 4: return 0, t, v
        return 0, v, q

    def _set_all(self, r: int, g: int, b: int):
        for i in range(cfg.CLOCK_LED_COUNT):
            self._np[i] = (r, g, b)
        self._np.write()

    def _push_history(self, sess, state: str):
        self._history.append({
            "name":  sess.name,
            "state": _STATE_LABEL.get(state, state),
            "msg":   sess.msg or "",
        })
        if len(self._history) > cfg.VOICE_HISTORY_DEPTH:
            self._history.pop(0)

    @staticmethod
    def _next_idle_deadline() -> int:
        import urandom
        span = cfg.VOICE_IDLE_MAX_S - cfg.VOICE_IDLE_MIN_S
        return time.time() + cfg.VOICE_IDLE_MIN_S + (urandom.getrandbits(8) * span // 256)
