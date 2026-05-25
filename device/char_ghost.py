import lvgl as lv
from character import Character
from state import S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR

_SWING = {
    S_IDLE:    ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_WORKING: ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_PENDING: ( 0,  5,  0, -5,  0,  5,  0, -5),
    S_DONE:    ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_ERROR:   ( 0,  4,  8,  4,  0, -4, -8, -4),
}
_JUMP_Y = (0, -4, -8, -4, 0, 0, 0, 0)

_BODY_COLORS = {
    S_IDLE:    lv.color_hex(0xF5F5F5),
    S_WORKING: lv.color_hex(0xB3E5FC),
    S_PENDING: lv.color_hex(0xFFF9C4),
    S_DONE:    lv.color_hex(0xC8E6C9),
    S_ERROR:   lv.color_hex(0xFFCDD2),
}


class GhostCharacter(Character):

    def build(self, panel, x, y, size):
        self._objs = []; self._bx = []; self._by = []

        def mk(px, py, pw, ph, color, r=4):
            o = lv.obj(panel)
            o.set_pos(x + px, y + py)
            o.set_size(pw, ph)
            o.set_style_radius(r, lv.PART.MAIN)
            o.set_style_bg_color(color, lv.PART.MAIN)
            o.set_style_border_width(0, lv.PART.MAIN)
            self._objs.append(o); self._bx.append(x + px); self._by.append(y + py)
            return o

        _WH = lv.color_hex(0xF5F5F5)
        _BK = lv.color_hex(0x111111)
        _WH2 = lv.color_hex(0xFFFFFF)

        # 主体（圆顶）
        self._body = mk( 8,  8, 94, 72, _WH, 47)
        # 身体下半（接圆顶）
        self._skirt = mk( 8, 56, 94, 34, _WH, 0)
        # 裙摆扇贝（4个圆形向下突出）
        self._sc = [
            mk( 8, 80, 24, 24, _WH, 12),
            mk(32, 80, 24, 24, _WH, 12),
            mk(56, 80, 24, 24, _WH, 12),
            mk(80, 80, 24, 24, _WH, 12),
        ]
        # 眼睛（黑色椭圆）
        mk(24, 32, 22, 28, _BK, 11)
        mk(64, 32, 22, 28, _BK, 11)
        # 眼睛高光
        mk(27, 34, 10, 10, _WH2, 5)
        mk(67, 34, 10, 10, _WH2, 5)

    def _set_color(self, color):
        self._body.set_style_bg_color(color, lv.PART.MAIN)
        self._skirt.set_style_bg_color(color, lv.PART.MAIN)
        for s in self._sc:
            s.set_style_bg_color(color, lv.PART.MAIN)

    def tick(self, state, frame):
        c = _BODY_COLORS[state]
        if state in (S_PENDING, S_ERROR):
            lo = lv.color_hex(0xFFD54F) if state == S_PENDING else lv.color_hex(0xEF5350)
            self._set_color(c if frame % 2 == 0 else lo)
        else:
            self._set_color(c)
        if state == S_DONE:
            return (_SWING[state][frame], _JUMP_Y[frame])
        return (_SWING[state][frame], 0)
