import lvgl as lv
from character import Character
from state import S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR

_SWING = {
    S_IDLE:    ( 0,  1,  2,  1,  0, -1, -2, -1),
    S_WORKING: ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_PENDING: ( 0,  4,  0, -4,  0,  4,  0, -4),
    S_DONE:    ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_ERROR:   ( 0,  4,  8,  4,  0, -4, -8, -4),
}
_JUMP_Y = (0, -3, -6, -3, 0, 0, 0, 0)

_EYE_COLORS = {
    S_IDLE:    lv.color_hex(0x00E5FF),
    S_WORKING: lv.color_hex(0x00FF41),
    S_PENDING: lv.color_hex(0xFFD54F),
    S_DONE:    lv.color_hex(0x69FF47),
    S_ERROR:   lv.color_hex(0xFF1744),
}
_BODY_COLORS = {
    S_IDLE:    (lv.color_hex(0x90A4AE), lv.color_hex(0x546E7A)),
    S_WORKING: (lv.color_hex(0x78909C), lv.color_hex(0x37474F)),
    S_PENDING: (lv.color_hex(0xB0BEC5), lv.color_hex(0x78909C)),
    S_DONE:    (lv.color_hex(0xA5D6A7), lv.color_hex(0x546E7A)),
    S_ERROR:   (lv.color_hex(0xEF9A9A), lv.color_hex(0x546E7A)),
}


class RobotCharacter(Character):

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

        _GY = lv.color_hex(0x90A4AE)
        _DK = lv.color_hex(0x546E7A)
        _CY = lv.color_hex(0x00E5FF)
        _WH = lv.color_hex(0xFFFFFF)
        _BK = lv.color_hex(0x111111)

        # 天线
        mk(50, 0, 10, 12, _DK, 5)
        mk(54, 10, 2, 14, _DK, 1)
        # 头
        self._head = mk(15, 22, 80, 46, _GY, 8)
        # 眼睛 LED 条
        self._eye_l = mk(22, 34, 24, 14, _CY, 4)
        self._eye_r = mk(64, 34, 24, 14, _CY, 4)
        mk(26, 37, 10,  8, _WH, 4)
        mk(68, 37, 10,  8, _WH, 4)
        # 嘴巴 - 像素点行
        _MG = lv.color_hex(0x37474F)
        for i in range(5):
            mk(28 + i * 12, 54, 8, 6, _MG if i % 2 == 0 else _GY, 2)
        # 身体
        self._body = mk(20, 72, 70, 32, _DK, 6)
        # 手臂
        self._arm_l = mk( 4, 74, 14, 26, _GY, 6)
        self._arm_r = mk(92, 74, 14, 26, _GY, 6)
        # 腿
        mk(22, 96, 22, 14, _DK, 4)
        mk(66, 96, 22, 14, _DK, 4)

    def _set_body(self, hi, lo):
        self._head.set_style_bg_color(hi, lv.PART.MAIN)
        self._arm_l.set_style_bg_color(hi, lv.PART.MAIN)
        self._arm_r.set_style_bg_color(hi, lv.PART.MAIN)
        self._body.set_style_bg_color(lo, lv.PART.MAIN)

    def tick(self, state, frame):
        hi, lo = _BODY_COLORS[state]
        self._set_body(hi, lo)
        ec = _EYE_COLORS[state]
        # 工作时眼睛交替闪
        if state == S_WORKING:
            self._eye_l.set_style_bg_color(ec if frame % 2 == 0 else lo, lv.PART.MAIN)
            self._eye_r.set_style_bg_color(ec if frame % 2 == 1 else lo, lv.PART.MAIN)
        else:
            self._eye_l.set_style_bg_color(ec, lv.PART.MAIN)
            self._eye_r.set_style_bg_color(ec, lv.PART.MAIN)
        if state == S_DONE:
            return (_SWING[state][frame], _JUMP_Y[frame])
        return (_SWING[state][frame], 0)
