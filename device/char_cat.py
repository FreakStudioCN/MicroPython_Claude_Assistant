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
_JUMP_Y = (0, -3, -6, -3, 0, 0, 0, 0)

_COLORS = {
    S_IDLE:    (lv.color_hex(0xF4A460), lv.color_hex(0xCD853F)),
    S_WORKING: (lv.color_hex(0x64B5F6), lv.color_hex(0x1E88E5)),
    S_PENDING: (lv.color_hex(0xFFD54F), lv.color_hex(0xF9A825)),
    S_DONE:    (lv.color_hex(0x81C784), lv.color_hex(0x388E3C)),
    S_ERROR:   (lv.color_hex(0xEF5350), lv.color_hex(0xC62828)),
}


class CatCharacter(Character):

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

        _C  = lv.color_hex(0xF4A460)
        _PK = lv.color_hex(0xFFB6C1)
        _WH = lv.color_hex(0xFFFFFF)
        _BK = lv.color_hex(0x111111)
        _NS = lv.color_hex(0xFF69B4)
        _GR = lv.color_hex(0xAAAAAA)
        _DK = lv.color_hex(0xCD853F)

        self._ear_l  = mk(10,  0, 22, 34, _C, 3)
        self._ear_r  = mk(78,  0, 22, 34, _C, 3)
        mk(16,  5, 10, 20, _PK, 3)
        mk(84,  5, 10, 20, _PK, 3)
        self._head   = mk( 5, 22,100, 72, _C, 20)
        mk(16, 40, 26, 20, _WH, 10)
        mk(68, 40, 26, 20, _WH, 10)
        self._pl     = mk(23, 42, 14, 16, _BK, 7)
        self._pr     = mk(75, 42, 14, 16, _BK, 7)
        mk(45, 63, 20, 13, _NS, 7)
        mk(30, 76, 18,  3, _DK, 2)
        mk(62, 76, 18,  3, _DK, 2)
        mk( 0, 58, 28,  2, _GR, 0)
        mk( 0, 66, 24,  2, _GR, 0)
        mk(82, 58, 28,  2, _GR, 0)
        mk(86, 66, 24,  2, _GR, 0)

    def _set_fur(self, color):
        self._head.set_style_bg_color(color, lv.PART.MAIN)
        self._ear_l.set_style_bg_color(color, lv.PART.MAIN)
        self._ear_r.set_style_bg_color(color, lv.PART.MAIN)

    def tick(self, state, frame):
        hi, lo = _COLORS[state]
        self._set_fur(hi if frame % 2 == 0 else lo)
        if state == S_DONE:
            return (_SWING[state][frame], _JUMP_Y[frame])
        return (_SWING[state][frame], 0)
