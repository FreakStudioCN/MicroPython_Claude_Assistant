"""终端渲染器，复用 device/session_manager.py 的 slot 映射和历史逻辑"""
import logging
import sys
import os

_log = logging.getLogger("renderer")

# 让 device/ 模块可以 import
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "device"))

from session_manager import SessionManager  # noqa: E402
from state import sess_state as _sess_state, S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR, dominant_state, sticky_dominant  # noqa: E402

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

MAX_SESSIONS = 5


class SimRenderer:
    """终端渲染器，复用 SessionManager 做 slot 映射和历史记录"""

    def __init__(self, max_slots=MAX_SESSIONS, history_max_len=20):
        self._sm = SessionManager(max_slots, history_max_len)
        self._prev_states = {}
        self._connected = False
        self._max = max_slots
        self._logo_state = S_IDLE
        self._last_active_sess = None
        # 同步 display_renderer: slot 名追踪 + 粘滞计数去重
        self._slot_names = [""] * max_slots
        self._last_sticky_dot_count = 0

    async def init(self):
        print("[sim_device] renderer initialized")

    async def render(self, msg):
        if msg is None or isinstance(msg, dict):
            return

        assigned, cleared, ordered = self._sm.update(msg.sessions)

        pending = {}  # 同步: 延迟写入 _prev_states

        for slot_index, sess in assigned:
            # 同步: session 名变化 → 清空历史
            if sess.name != self._slot_names[slot_index]:
                self._sm.histories[slot_index].clear()
                self._slot_names[slot_index] = sess.name
                _log.info("slot[%d] session changed: %s", slot_index, sess.name)

            # 状态跳变检测
            cur = _sess_state(sess)
            prev = self._prev_states.get(sess.name)
            if cur != prev:
                _log.info("state_change slot[%d] %s: %s -> %s msg=%s",
                          slot_index, sess.name, prev or "?", cur, sess.msg)
                print(f"[state_change] {sess.name}: {prev or '?'} → {cur}")
                pending[sess.name] = cur  # 同步: 延迟写入

        # v6 修复：部分清理保护 — 全部清空时跳过，且保留 slot_names 不重置，避免重连时误清历史
        # (slot_names 仅由 session 名变化检测更新，connect/disconnect/clear 均不清除)

        # 粘滞后的 dominant state
        raw_dominant = dominant_state([s for s in ordered if s is not None])
        state = sticky_dominant(raw_dominant, self._logo_state)
        if raw_dominant != state:
            _log.info("sticky dominant: raw=%s -> %s (keep %s against I)",
                      raw_dominant, state, self._logo_state)
        if state != self._logo_state:
            if self._logo_state in (S_DONE, S_PENDING) and state not in (S_DONE, S_PENDING, S_IDLE):
                _log.info("sticky broken: %s->%s (sticky was holding %s)",
                          self._logo_state, state, self._logo_state)
            _log.info("dominant: %s -> %s (sticky=%s)",
                      self._logo_state, state, state != raw_dominant)
            self._logo_state = state

        # 清屏渲染
        self._render_screen(ordered, state)

        # 同步: 延后写入 _prev_states，确保粘滞判断拿到旧值
        for name, cur in pending.items():
            self._prev_states[name] = cur

    async def on_connect(self):
        self._connected = True
        self._prev_states.clear()
        self._last_sticky_dot_count = 0
        _log.info("connected to daemon")
        print("[sim_device] connected to daemon")

    async def on_disconnect(self):
        self._connected = False
        self._prev_states.clear()
        self._sm.reset()
        self._logo_state = S_IDLE
        self._last_active_sess = None
        self._last_sticky_dot_count = 0
        _log.info("disconnected from daemon")
        print("[sim_device] disconnected from daemon")

    def _render_screen(self, ordered, state):
        """清屏打印当前状态"""
        print("\033[2J\033[H", end="")  # 清屏
        conn_status = "connected" if self._connected else "disconnected"
        state_color = _C.get(state, "")
        state_label = _STATE_NAME.get(state, state)
        print("═" * 60)
        print(f"  sim_device  [{conn_status}]  dominant: {state_color}{state_label}{_RESET}")
        print("═" * 60)

        has_active = False
        sticky_dot_count = 0  # 同步: 空槽粘滞计数

        for i, sess in enumerate(ordered):
            if sess is not None:
                has_active = True
                # 同步: per-dot sticky
                raw_s = _sess_state(sess)
                prev = self._prev_states.get(sess.name)
                s = sticky_dominant(raw_s, prev)
                if raw_s != s:
                    _log.info("sticky dot[%d] %s: raw=%s prev=%s -> sticky=%s",
                              i, sess.name, raw_s, prev, s)
                color = _C.get(s, "")
                label = _STATE_NAME.get(s, s)
                slot_id = sess.slot[:8] if sess.slot else "?"
                msg_text = f"  {sess.msg}" if sess.msg else ""

                print(f"  slot[{i}]  {color}{sess.name:<14} {label:<10}{_RESET}"
                      f"  [{slot_id}]{msg_text}")
                if s not in (S_IDLE,):
                    self._last_active_sess = sess

                # 打印历史（最近 3 条）
                history = self._sm.histories[i]
                if history:
                    recent = history[-3:]
                    for h in recent:
                        h_state = h["state"]
                        h_color = _C.get(h_state, "")
                        h_label = _STATE_NAME.get(h_state, h_state)
                        print(f"           {h_color}[{h_label}]{_RESET} {h['msg']}")
            elif state in (S_DONE, S_PENDING):
                # 同步: 空槽粘滞保持 — 不灰化
                sticky_dot_count += 1
            # else: 空槽 + 非 C/P dominant → 无显示

        # 同步: 空槽粘滞日志（去重）
        if sticky_dot_count != self._last_sticky_dot_count:
            if sticky_dot_count > 0:
                _log.info("sticky hold: %d empty dot(s) kept at dominant=%s",
                          sticky_dot_count, state)
            self._last_sticky_dot_count = sticky_dot_count

        if not has_active:
            if state in (S_DONE, S_PENDING) and self._last_active_sess:
                sess = self._last_active_sess
                color = _C.get(state, "")
                label = _STATE_NAME.get(state, state)
                _log.info("sticky msg: show %s as %s (all sessions gone, sticky hold)",
                          sess.name, label)
                print(f"  [sticky]  {color}{sess.name:<14} {label:<10}{_RESET}")
            else:
                print("  (no active sessions)")

        print("═" * 60)
