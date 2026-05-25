import lvgl as lv
from character import Character
from state import S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR

_SWING = {
    S_IDLE:    ( 0,  1,  2,  1,  0, -1, -2, -1),
    S_WORKING: ( 0,  3,  6,  3,  0, -3, -6, -3),
    S_PENDING: ( 0,  5,  0, -5,  0,  5,  0, -5),
    S_DONE:    ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_ERROR:   ( 0,  4,  8,  4,  0, -4, -8, -4),
}
_JUMP_Y = (0, -4, -8, -4, 0, 0, 0, 0)

_CREW_COLORS = {
    S_IDLE:    lv.color_hex(0xF44336),
    S_WORKING: lv.color_hex(0x42A5F5),
    S_PENDING: lv.color_hex(0xFFCA28),
    S_DONE:    lv.color_hex(0x66BB6A),
    S_ERROR:   lv.color_hex(0xEF5350),
}


class AmongUsCharacter(Character):

    def build(self, panel, x, y, size):
        self._objs = []; self._bx = []; self._by = []

        def mk(px, py, pw, ph, color, r=6):
            o = lv.obj(panel)
            o.set_pos(x + px, y + py)
            o.set_size(pw, ph)
            o.set_style_radius(r, lv.PART.MAIN)
            o.set_style_bg_color(color, lv.PART.MAIN)
            o.set_style_border_width(0, lv.PART.MAIN)
            self._objs.append(o); self._bx.append(x + px); self._by.append(y + py)
            return o

        _C  = lv.color_hex(0xF44336)
        _DK = lv.color_hex(0xB71C1C)
        _VS = lv.color_hex(0x1A237E)
        _SH = lv.color_hex(0x3949AB)
        _WH = lv.color_hex(0xE3F2FD)

        # 背包
        self._pack = mk(82, 28, 20, 32, _DK, 8)
        # 头部
        self._head = mk(22, 8, 66, 50, _C, 22)
        # 身体
        self._body = mk(14, 44, 76, 56, _C, 16)
        # 头盔玻璃（标志性深蓝色）
        mk(28, 14, 46, 30, _VS, 12)
        mk(32, 16, 22, 12, _SH, 6)  # 反光
        # 左脚
        self._leg_l = mk(18, 92, 28, 18, _DK, 6)
        # 右脚
        self._leg_r = mk(64, 92, 28, 18, _DK, 6)

    def _set_color(self, hi, lo=None):
        if lo is None:
            lo = hi
        self._head.set_style_bg_color(hi, lv.PART.MAIN)
        self._body.set_style_bg_color(hi, lv.PART.MAIN)
        self._pack.set_style_bg_color(lo, lv.PART.MAIN)
        self._leg_l.set_style_bg_color(lo, lv.PART.MAIN)
        self._leg_r.set_style_bg_color(lo, lv.PART.MAIN)

    def tick(self, state, frame):
        c = _CREW_COLORS[state]
        if state == S_PENDING:
            lo = lv.color_hex(0xE65100)
            self._set_color(c if frame % 2 == 0 else lo)
        elif state == S_ERROR:
            lo = lv.color_hex(0xB71C1C)
            self._set_color(c if frame % 2 == 0 else lo)
        else:
            # 走路动画：左右脚交替
            dk_c = lv.color_hex(int(c._hex * 0.7) if hasattr(c, '_hex') else 0xB71C1C)
            self._set_color(c)
            if state == S_WORKING:
                if frame % 2 == 0:
                    self._leg_l.set_pos(self._bx[-2] - 4, self._by[-2])
                    self._leg_r.set_pos(self._bx[-1] + 4, self._by[-1])
                else:
                    self._leg_l.set_pos(self._bx[-2] + 4, self._by[-2])
                    self._leg_r.set_pos(self._bx[-1] - 4, self._by[-1])
        if state == S_DONE:
            return (_SWING[state][frame], _JUMP_Y[frame])
        return (_SWING[state][frame], 0)
