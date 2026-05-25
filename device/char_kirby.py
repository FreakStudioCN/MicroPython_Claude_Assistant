import lvgl as lv
from character import Character
from state import S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR

_SWING = {
    S_IDLE:    ( 0,  3,  6,  3,  0, -3, -6, -3),
    S_WORKING: ( 0,  3,  6,  3,  0, -3, -6, -3),
    S_PENDING: ( 0,  5,  0, -5,  0,  5,  0, -5),
    S_DONE:    ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_ERROR:   ( 0,  5, 10,  5,  0, -5,-10, -5),
}
_JUMP_Y = (0, -5, -10, -5, 0, 0, 0, 0)

_BODY_COLORS = {
    S_IDLE:    lv.color_hex(0xFF6B8A),
    S_WORKING: lv.color_hex(0xFF8FAB),
    S_PENDING: lv.color_hex(0xFFD54F),
    S_DONE:    lv.color_hex(0xA5D6A7),
    S_ERROR:   lv.color_hex(0xEF5350),
}
_CHEEK_COLORS = {
    S_IDLE:    lv.color_hex(0xFF8FAB),
    S_WORKING: lv.color_hex(0xFFAEC9),
    S_PENDING: lv.color_hex(0xFFCA28),
    S_DONE:    lv.color_hex(0x66BB6A),
    S_ERROR:   lv.color_hex(0xFF8A80),
}


class KirbyCharacter(Character):

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

        _PK  = lv.color_hex(0xFF6B8A)
        _BK  = lv.color_hex(0x111111)
        _BL  = lv.color_hex(0x1565C0)
        _WH  = lv.color_hex(0xFFFFFF)
        _RD  = lv.color_hex(0xFF3D57)
        _CK  = lv.color_hex(0xFF8FAB)

        # 主体（大圆球）
        self._body = mk( 5,  5, 100, 90, _PK, 50)
        # 眼睛（黑色竖椭圆，带蓝色反光）
        self._eye_l = mk(24, 28, 20, 26, _BK, 10)
        self._eye_r = mk(66, 28, 20, 26, _BK, 10)
        mk(27, 30, 10, 10, _BL, 5)   # 左眼蓝色高光
        mk(69, 30, 10, 10, _BL, 5)   # 右眼蓝色高光
        mk(28, 30,  6,  6, _WH, 3)   # 左眼白点
        mk(70, 30,  6,  6, _WH, 3)   # 右眼白点
        # 嘴巴
        mk(40, 57, 30,  8, _RD, 4)
        # 腮红（两侧粉色圆圈）
        self._chk_l = mk( 8, 52, 24, 16, _CK, 12)
        self._chk_r = mk(78, 52, 24, 16, _CK, 12)
        # 脚（两个小半圆）
        self._foot_l = mk(16, 86, 28, 20, _RD, 10)
        self._foot_r = mk(66, 86, 28, 20, _RD, 10)
        # 手臂（两侧短圆柱）
        self._arm_l = mk( 0, 44, 14, 30, _PK, 7)
        self._arm_r = mk(96, 44, 14, 30, _PK, 7)

    def _set_body(self, color, cheek):
        self._body.set_style_bg_color(color, lv.PART.MAIN)
        self._arm_l.set_style_bg_color(color, lv.PART.MAIN)
        self._arm_r.set_style_bg_color(color, lv.PART.MAIN)
        self._chk_l.set_style_bg_color(cheek, lv.PART.MAIN)
        self._chk_r.set_style_bg_color(cheek, lv.PART.MAIN)

    def tick(self, state, frame):
        c = _BODY_COLORS[state]
        ck = _CHEEK_COLORS[state]
        if state == S_PENDING:
            lo = lv.color_hex(0xF57F17)
            self._set_body(c if frame % 2 == 0 else lo, ck)
        elif state == S_ERROR:
            lo = lv.color_hex(0xC62828)
            self._set_body(c if frame % 2 == 0 else lo, ck)
        else:
            self._set_body(c, ck)
            # WORKING：脚部交替踏步
            if state == S_WORKING:
                if frame % 2 == 0:
                    self._foot_l.set_pos(self._bx[-2] - 4, self._by[-2])
                    self._foot_r.set_pos(self._bx[-1] + 4, self._by[-1])
                else:
                    self._foot_l.set_pos(self._bx[-2] + 4, self._by[-2])
                    self._foot_r.set_pos(self._bx[-1] - 4, self._by[-1])
        if state == S_DONE:
            return (_SWING[state][frame], _JUMP_Y[frame])
        return (_SWING[state][frame], 0)
