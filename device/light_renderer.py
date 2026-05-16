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

# 状态优先级：多 session 时取最高优先级
_PRIORITY = {"E": 0, "P": 1, "W": 2, "C": 3, "I": 4}

# 历史记录条目：{"name", "state", "msg"}
_STATE_LABEL = {"I": "空闲", "W": "工作中", "P": "等待审批", "C": "完成", "E": "出错"}


def _dominant(sessions) -> str:
    if not sessions:
        return "I"
    return min((_sess_state(s) for s in sessions), key=lambda s: _PRIORITY[s])


def _sess_state(sess) -> str:
    if sess.error:    return "E"
    if sess.waiting:  return "P"
    if sess.running:  return "W"
    if sess.completed: return "C"
    return "I"


class LightRenderer:

    def __init__(self):
        self._np: neopixel.NeoPixel | None = None
        self._voice = VoiceTask()

        self._sessions = []
        self._prev_states: dict[str, str] = {}  # session name → last triggered state
        self._history: list[dict] = []

        self._frame = 0
        self._state = "I"
        self._connect_flash = 0   # >0 时显示白色连接闪光（帧数倒计）

        # 偶发播报计时
        self._idle_speak_deadline = self._next_idle_deadline()

    # ── 公共接口 ──────────────────────────────────────────────

    async def init(self):
        print("[light] init hardware...")
        pin = machine.Pin(cfg.CLOCK_LED_PIN, machine.Pin.OUT)
        self._np = neopixel.NeoPixel(pin, cfg.CLOCK_LED_COUNT)
        self._set_all(0, 0, 0)
        print(f"[light] NeoPixel ready: pin={cfg.CLOCK_LED_PIN} count={cfg.CLOCK_LED_COUNT}")

    async def render(self, msg):
        if msg is None:
            return
        self._sessions = msg.sessions

        # 状态跳变检测 → 触发语音
        for sess in self._sessions:
            cur = _sess_state(sess)
            prev = self._prev_states.get(sess.name)
            if cur != prev and cur in ("C", "E", "P"):
                self._push_history(sess, cur)
                await self._voice.trigger(self._history, sess, cur)
                self._prev_states[sess.name] = cur
            elif cur != prev:
                self._push_history(sess, cur)
                self._prev_states[sess.name] = cur

        # 偶发播报
        dom = _dominant(self._sessions)
        if dom == "W" and time.time() >= self._idle_speak_deadline:
            await self._voice.maybe_idle_speak(self._history)
            self._idle_speak_deadline = self._next_idle_deadline()

        # 灯光帧推进
        self._state = dom
        self._tick_leds()

    async def on_connect(self):
        self._connect_flash = 10  # ~0.5s @ 20fps
        self._tick_leds()

    async def on_disconnect(self):
        self._connect_flash = 0
        self._set_all(0, 0, 0)

    # ── 灯光帧驱动 ────────────────────────────────────────────

    def _tick_leds(self):
        self._frame += 1

        if self._connect_flash > 0:
            self._connect_flash -= 1
            self._set_all(60, 60, 60)
            return

        s = self._state
        f = self._frame

        if s == "I":
            # 蓝色呼吸灯，周期 60 帧（3s @ 20fps）
            v = int((math.sin(f * math.pi / 30) + 1) / 2 * 60)
            self._set_all(0, 0, v)

        elif s == "W":
            # 青色流水：灯0→灯1→灯0，每 6 帧切换
            if (f // 6) % 2 == 0:
                self._np[0] = (0, 200, 255)
                self._np[1] = (0, 30, 40)
            else:
                self._np[0] = (0, 30, 40)
                self._np[1] = (0, 200, 255)
            self._np.write()

        elif s == "P":
            # 黄色同步慢闪：亮 16 帧灭 8 帧（0.8s/0.4s @ 20fps）
            on = (f % 24) < 16
            c = (255, 200, 0) if on else (0, 0, 0)
            self._set_all(*c)

        elif s == "C":
            # 绿色快闪 3 下（共 18 帧），之后转空闲呼吸
            if f % 60 < 18:
                on = (f % 6) < 3
                c = (0, 255, 80) if on else (0, 0, 0)
                self._set_all(*c)
            else:
                v = int((math.sin(f * math.pi / 30) + 1) / 2 * 40)
                self._set_all(0, v, 0)

        elif s == "E":
            # 红色双灯交替快闪，每 2 帧切换
            if (f // 2) % 2 == 0:
                self._np[0] = (255, 0, 0)
                self._np[1] = (0, 0, 0)
            else:
                self._np[0] = (0, 0, 0)
                self._np[1] = (255, 0, 0)
            self._np.write()

    # ── 工具方法 ──────────────────────────────────────────────

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
