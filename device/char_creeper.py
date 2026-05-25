import lvgl as lv
from character import Character
from state import S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR

_SWING = {
    S_IDLE:    ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_WORKING: ( 0,  3,  6,  3,  0, -3, -6, -3),
    S_PENDING: ( 0,  5,  0, -5,  0,  5,  0, -5),
    S_DONE:    ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_ERROR:   ( 0,  5, 10,  5,  0, -5,-10, -5),
}
_JUMP_Y = (0, -4, -8, -4, 0, 0, 0, 0)

_BODY_COLORS = {
    S_IDLE:    lv.color_hex(0x4CAF50),
    S_WORKING: lv.color_hex(0x388E3C),
    S_PENDING: lv.color_hex(0xFFCA28),
    S_DONE:    lv.color_hex(0x81C784),
    S_ERROR:   lv.color_hex(0xEF5350),
}


class CreeperCharacter(Character):

    def build(self, panel, x, y, size):
        self._objs = []; self._bx = []; self._by = []

        def mk(px, py, pw, ph, color, r=0):
            o = lv.obj(panel)
            o.set_pos(x + px, y + py)
            o.set_size(pw, ph)
            o.set_style_radius(r, lv.PART.MAIN)
            o.set_style_bg_color(color, lv.PART.MAIN)
            o.set_style_border_width(0, lv.PART.MAIN)
            self._objs.append(o); self._bx.append(x + px); self._by.append(y + py)
            return o

        _GR = lv.color_hex(0x4CAF50)
        _DK = lv.color_hex(0x1B5E20)
        _BK = lv.color_hex(0x111111)

        # 头部（正方形像素风）
        self._head = mk(18,  2, 74, 62, _GR, 2)
        # 眼睛（两个黑色方块）
        self._eye_l = mk(28, 16, 18, 18, _BK, 0)
        self._eye_r = mk(64, 16, 18, 18, _BK, 0)
        # 嘴巴（T 形暗色块）
        mk(44, 34, 22,  8, _DK, 0)   # 横
        mk(48, 34, 14, 18, _DK, 0)   # 竖
        # 身体
        self._body = mk(28, 66, 54, 34, _GR, 2)
        # 左腿
        self._leg_l = mk(28, 94, 22, 16, _DK, 2)
        # 右腿
        self._leg_r = mk(60, 94, 22, 16, _DK, 2)

    def _set_color(self, color):
        dk = lv.color_hex(0x1B5E20)
        self._head.set_style_bg_color(color, lv.PART.MAIN)
        self._body.set_style_bg_color(color, lv.PART.MAIN)

    def tick(self, state, frame):
        c = _BODY_COLORS[state]
        if state == S_ERROR:
            lo = lv.color_hex(0xFF8F00)
            self._set_color(c if frame % 2 == 0 else lo)
            # 错误时眼睛闪白（爆炸前）
            wh = lv.color_hex(0xFFFFFF)
            bk = lv.color_hex(0x111111)
            ec = wh if frame % 2 == 0 else bk
            self._eye_l.set_style_bg_color(ec, lv.PART.MAIN)
            self._eye_r.set_style_bg_color(ec, lv.PART.MAIN)
        elif state == S_PENDING:
            lo = lv.color_hex(0xF57F17)
            self._set_color(c if frame % 2 == 0 else lo)
        else:
            self._set_color(c)
            # WORKING 状态腿部交替
            if state == S_WORKING:
                if frame % 2 == 0:
                    self._leg_l.set_pos(self._bx[-2] - 3, self._by[-2])
                    self._leg_r.set_pos(self._bx[-1] + 3, self._by[-1])
                else:
                    self._leg_l.set_pos(self._bx[-2] + 3, self._by[-2])
                    self._leg_r.set_pos(self._bx[-1] - 3, self._by[-1])
        if state == S_DONE:
            return (_SWING[state][frame], _JUMP_Y[frame])
        return (_SWING[state][frame], 0)
