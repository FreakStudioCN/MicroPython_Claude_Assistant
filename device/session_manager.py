try:
    from time import ticks_ms as _ticks_ms
except ImportError:
    from time import time as _t
    _ticks_ms = lambda: int(_t() * 1000)

import logging
_log = logging.getLogger("session_mgr")


class SessionManager:
    def __init__(self, max_slots: int, history_max_len: int):
        self._max = max_slots
        self._hmax = history_max_len
        self._assignments = {}           # slot_id → slot_index
        self._last_used = [0] * max_slots
        self.histories = [[] for _ in range(max_slots)]

    def update(self, sessions):
        """按 slot_id 分配/释放槽位。
        返回 (assigned, cleared, ordered):
          assigned: list of (slot_index, sess)
          cleared:  list of slot_index（本次 wire 消失的槽）
          ordered:  list[sess|None]，下标即槽位编号
        """
        slot_updated = [False] * self._max
        ordered = [None] * self._max
        assigned = []

        for sess in sessions:
            slot_id = sess.slot
            if not slot_id:
                continue
            if slot_id in self._assignments:
                idx = self._assignments[slot_id]
                _log.info("slot[%d] hit slot_id=%s name=%s", idx, slot_id, sess.name)
            else:
                idx = self._find_empty()
                if idx is None:
                    continue
                self._assignments[slot_id] = idx
                _log.info("slot[%d] assigned slot_id=%s", idx, slot_id)
            self._last_used[idx] = _ticks_ms()
            slot_updated[idx] = True
            ordered[idx] = sess
            assigned.append((idx, sess))

        cleared = []
        for i in range(self._max):
            if not slot_updated[i]:
                for sid, idx in list(self._assignments.items()):
                    if idx == i:
                        del self._assignments[sid]
                        _log.info("slot[%d] released sid=%s", i, sid)
                        break
                cleared.append(i)

        return assigned, cleared, ordered

    def push_history(self, slot_index: int, text: str, state: str) -> str:
        """追加历史记录（去重+截断）。
        返回 'skip' | 'update' | 'append' | 'overflow'
        """
        h = self.histories[slot_index]
        if h and h[-1]["msg"] == text and h[-1]["state"] == state:
            return "skip"
        if h and h[-1]["msg"] == text:
            h[-1]["state"] = state
            return "update"
        h.append({"msg": text, "state": state})
        if len(h) > self._hmax:
            h.pop(0)
            return "overflow"
        return "append"

    def reset(self):
        self._assignments.clear()
        for h in self.histories:
            h.clear()

    def _find_empty(self):
        occupied = set(self._assignments.values())
        for i in range(self._max):
            if i not in occupied:
                return i
        _log.warning("all slots full, session skipped")
        return None
