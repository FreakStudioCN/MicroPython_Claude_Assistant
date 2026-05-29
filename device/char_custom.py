import lvgl as lv
from character import Character
from state import S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR

_SWING = {
    S_IDLE:    (0, 0, 0, 0, 0, 0, 0, 0),
    S_WORKING: (0, 2, 4, 2, 0, -2, -4, -2),
    S_PENDING: (0, 3, 6, 3, 0, -3, -6, -3),
    S_DONE:    (0, 3, 0, -3, 0, 3, 0, -3),
    S_ERROR:   (0, 4, 8, 4, 0, -4, -8, -4),
}

class NinjaCharacter(Character):
    def build(self, panel, x, y, size):
        self._objs = []; self._bx = []; self._by = []
        def mk(px, py, pw, ph, color, r=2):
            o = lv.obj(panel)
            o.set_pos(x + px, y + py)
            o.set_size(pw, ph)
            o.set_style_radius(r, lv.PART.MAIN)
            o.set_style_bg_color(color, lv.PART.MAIN)
            o.set_style_border_width(0, lv.PART.MAIN)
            self._objs.append(o); self._bx.append(x + px); self._by.append(y + py)
            return o
        _DK = lv.color_hex(0x1A1A2E)
        _SK = lv.color_hex(0xFFCC80)
        _RD = lv.color_hex(0xE53935)
        _WH = lv.color_hex(0xFFFFFF)
        _BK = lv.color_hex(0x000000)
        _GY = lv.color_hex(0x888888)
        # 头巾
        mk(8,  4, 94, 20, _RD, 4)
        mk(4, 14, 20, 24, _RD, 4)
        mk(86, 14, 20, 24, _RD, 4)
        # 脸
        mk(20, 18, 70, 54, _SK, 8)
        # 蒙面
        mk(18, 44, 74, 28, _DK, 6)
        # 眼睛（仅露眼）
        mk(28, 26, 18, 14, _WH, 4)
        mk(64, 26, 18, 14, _WH, 4)
        mk(32, 28, 10, 10, _BK, 3)
        mk(68, 28, 10, 10, _BK, 3)
        # 眉毛（犀利）
        mk(24, 20, 28, 4, _BK, 2)
        mk(58, 20, 28, 4, _BK, 2)
        # 身体（黑色紧身衣）
        mk(26, 66, 58, 28, _DK, 6)
        # 腰带
        mk(24, 80, 62, 8, _RD, 4)
        # 手里剑标志
        mk(51, 72, 8, 8, _GY, 4)

    def tick(self, state, frame):
        if state in (S_WORKING, S_ERROR):
            return (_SWING[state][frame], 0)
        if state == S_DONE:
            return (0, _SWING[state][frame])
        return (0, 0)
