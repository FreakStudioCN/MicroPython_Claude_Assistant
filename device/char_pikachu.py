import lvgl as lv
from character import Character
from state import S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR

_SWING = {
    S_IDLE:    ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_WORKING: ( 0,  3,  6,  3,  0, -3, -6, -3),
    S_PENDING: ( 0,  4,  0, -4,  0,  4,  0, -4),
    S_DONE:    ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_ERROR:   ( 0,  5, 10,  5,  0, -5,-10, -5),
}
_JUMP_Y = (0, -4, -8, -4, 0, 0, 0, 0)

_BODY_COLORS = {
    S_IDLE:    lv.color_hex(0xFFCC02),
    S_WORKING: lv.color_hex(0xFFD740),
    S_PENDING: lv.color_hex(0xFF8F00),
    S_DONE:    lv.color_hex(0xA5D6A7),
    S_ERROR:   lv.color_hex(0xEF5350),
}


class PikachuCharacter(Character):

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

        _YL  = lv.color_hex(0xFFCC02)
        _DY  = lv.color_hex(0xF9A825)
        _BK  = lv.color_hex(0x111111)
        _RD  = lv.color_hex(0xFF3D00)
        _WH  = lv.color_hex(0xFFFFFF)
        _BR  = lv.color_hex(0x5D4037)

        # 耳朵（两个尖三角形用两个方块模拟）
        self._ear_l = mk(12,  0, 16, 32, _YL, 2)
        self._ear_r = mk(82,  0, 16, 32, _YL, 2)
        # 耳尖黑色
        mk(12,  0, 16, 12, _BK, 2)
        mk(82,  0, 16, 12, _BK, 2)
        # 头部
        self._head = mk( 8, 24, 94, 66, _YL, 28)
        # 眼睛
        mk(24, 36, 20, 22, _BK, 11)
        mk(66, 36, 20, 22, _BK, 11)
        mk(26, 37, 10, 10, _WH, 5)
        mk(68, 37, 10, 10, _WH, 5)
        # 腮红（红色圆）
        self._chk_l = mk( 8, 60, 26, 18, _RD, 12)
        self._chk_r = mk(76, 60, 26, 18, _RD, 12)
        # 鼻子
        mk(47, 52,  8,  6, _BK, 3)
        # 嘴巴
        mk(34, 60, 10,  4, _BK, 2)
        mk(66, 60, 10,  4, _BK, 2)
        mk(40, 62, 30,  6, _BK, 3)
        # 身体
        self._body = mk(20, 90, 70, 14, _DY, 6)
        # 尾巴（Z形用两块组成）
        mk(92, 30, 14, 10, _DY, 2)
        mk(98, 40, 10, 12, _BK, 2)
        mk(92, 50, 16,  8, _DY, 2)
        # 脚
        self._foot_l = mk(22, 98, 26, 12, _DY, 6)
        self._foot_r = mk(62, 98, 26, 12, _DY, 6)

    def _set_body(self, color):
        self._head.set_style_bg_color(color, lv.PART.MAIN)
        self._body.set_style_bg_color(color, lv.PART.MAIN)
        self._ear_l.set_style_bg_color(color, lv.PART.MAIN)
        self._ear_r.set_style_bg_color(color, lv.PART.MAIN)

    def tick(self, state, frame):
        c = _BODY_COLORS[state]
        if state == S_PENDING:
            lo = lv.color_hex(0xE65100)
            # 腮红变白模拟准备放电
            wh = lv.color_hex(0xFFFFFF)
            rd = lv.color_hex(0xFF3D00)
            self._chk_l.set_style_bg_color(wh if frame % 2 == 0 else rd, lv.PART.MAIN)
            self._chk_r.set_style_bg_color(wh if frame % 2 == 0 else rd, lv.PART.MAIN)
            self._set_body(c if frame % 2 == 0 else lo)
        elif state == S_ERROR:
            lo = lv.color_hex(0xC62828)
            # 腮红变黄模拟放完电
            ck = lv.color_hex(0xFFD740)
            self._chk_l.set_style_bg_color(ck, lv.PART.MAIN)
            self._chk_r.set_style_bg_color(ck, lv.PART.MAIN)
            self._set_body(c if frame % 2 == 0 else lo)
        else:
            self._chk_l.set_style_bg_color(lv.color_hex(0xFF3D00), lv.PART.MAIN)
            self._chk_r.set_style_bg_color(lv.color_hex(0xFF3D00), lv.PART.MAIN)
            self._set_body(c)
            if state == S_WORKING:
                if frame % 2 == 0:
                    self._foot_l.set_pos(self._bx[-2] - 3, self._by[-2])
                    self._foot_r.set_pos(self._bx[-1] + 3, self._by[-1])
                else:
                    self._foot_l.set_pos(self._bx[-2] + 3, self._by[-2])
                    self._foot_r.set_pos(self._bx[-1] - 3, self._by[-1])
        if state == S_DONE:
            return (_SWING[state][frame], _JUMP_Y[frame])
        return (_SWING[state][frame], 0)
