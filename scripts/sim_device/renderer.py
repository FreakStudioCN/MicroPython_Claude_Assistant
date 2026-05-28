"""终端渲染器，复用 device/session_manager.py 的 slot 映射和历史逻辑"""
import logging
import sys
import os

_log = logging.getLogger("renderer")

# 让 device/ 模块可以 import
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "device"))

from session_manager import SessionManager  # noqa: E402
from state import sess_state as _sess_state, S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR  # noqa: E402

# ANSI 颜色
_C = {
    S_IDLE: "\033[37m",      # 白/灰
    S_WORKING: "\033[33m",   # 黄
    S_PENDING: "\033[35m",   # 紫
    S_DONE: "\033[32m",      # 绿
    S_ERROR: "\033[31m",     # 红
}
_RESET = "\033[0m"
_STATE_NAME = {
    S_IDLE: "IDLE",
    S_WORKING: "WORKING",
    S_PENDING: "PENDING",
    S_DONE: "CELEBRATE",
    S_ERROR: "ERROR",
}


class SimRenderer:
    """终端渲染器，复用 SessionManager 做 slot 映射和历史记录"""

    def __init__(self, max_slots=5, history_max_len=20):
        self._sm = SessionManager(max_slots, history_max_len)
        self._prev_states = {}
        self._connected = False
        self._max = max_slots

    async def init(self):
        print("[sim_device] renderer initialized")

    async def render(self, msg):
        if msg is None or isinstance(msg, dict):
            return

        assigned, cleared, ordered = self._sm.update(msg.sessions)

        # 状态跳变检测
        for slot_index, sess in assigned:
            cur = _sess_state(sess)
            prev = self._prev_states.get(sess.name)
            if cur != prev:
                _log.info("state_change slot[%d] %s: %s -> %s msg=%s", slot_index, sess.name, prev or "?", cur, sess.msg)
                print(f"[state_change] {sess.name}: {prev or '?'} → {cur}")
                self._prev_states[sess.name] = cur

        # 清屏渲染
        self._render_screen(ordered)

    async def on_connect(self):
        self._connected = True
        self._prev_states.clear()
        _log.info("connected to daemon")
        print("[sim_device] connected to daemon")

    async def on_disconnect(self):
        self._connected = False
        self._prev_states.clear()
        self._sm.reset()
        _log.info("disconnected from daemon")
        print("[sim_device] disconnected from daemon")

    def _render_screen(self, ordered):
        """清屏打印当前状态"""
        print("\033[2J\033[H", end="")  # 清屏
        conn_status = "connected" if self._connected else "disconnected"
        print("═" * 60)
        print(f"  sim_device  [{conn_status}]")
        print("═" * 60)

        has_active = False
        for i, sess in enumerate(ordered):
            if sess is None:
                continue
            has_active = True
            state = _sess_state(sess)
            color = _C.get(state, "")
            label = _STATE_NAME.get(state, state)
            slot_id = sess.slot[:8] if sess.slot else "?"
            msg_text = f"  {sess.msg}" if sess.msg else ""

            print(f"  slot[{i}]  {color}{sess.name:<14} {label:<10}{_RESET}  [{slot_id}]{msg_text}")

            # 打印历史（最近 3 条）
            history = self._sm.histories[i]
            if history:
                recent = history[-3:]
                for h in recent:
                    h_state = h["state"]
                    h_color = _C.get(h_state, "")
                    h_label = _STATE_NAME.get(h_state, h_state)
                    print(f"           {h_color}[{h_label}]{_RESET} {h['msg']}")

        if not has_active:
            print("  (no active sessions)")

        print("═" * 60)
