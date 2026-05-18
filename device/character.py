"""
character.py —— 可替换的角色形象接口

换形象只需：
  1. 新建文件实现 Character 子类
  2. 修改 display_renderer.py 顶部的 import 一行
"""

import lvgl as lv
from logo_data import LOGO_SIZE, _LOGO_HEAD, _LOGO_ARMS, _LOGO_TORSO, _LOGO_LEGS, _LOGO_EYES
from state import S_IDLE, S_WORKING, S_DONE, S_ERROR


class Character:
    """
    角色基类。子类实现 build() 和 tick()。

    build(panel, x, y, size):
        在 panel 上创建所有 lv.obj。
        x, y: 建议的左上角坐标（可忽略，自行布局）
        size: 建议的区域大小（正方形边长）

    tick(state, frame) -> (ox, oy):
        每 150ms 调用一次。
        state: "I" / "W" / "E" / "C"
        frame: 0~7 循环
        返回整体平移量 (ox, oy)；不需要平移返回 (0, 0)。
        也可在此直接操作自己的 lv.obj（颜色/位置/大小），返回 (0,0) 跳过外部平移。
    """

    def build(self, panel, x, y, size):
        raise NotImplementedError

    def tick(self, state, frame):
        raise NotImplementedError


# ── Claude Code 像素风角色 ────────────────────────────────────

_W_HI  = lv.color_hex(0x64B5F6)
_W_LO  = lv.color_hex(0x1565C0)
_E_HI  = lv.color_hex(0xF44336)
_E_LO  = lv.color_hex(0xC62828)
_C_HI  = lv.color_hex(0xA5D6A7)
_C_LO  = lv.color_hex(0x2E7D32)
_PULSE = (
    lv.color_hex(0xFF8C00), lv.color_hex(0xFFAA00),
    lv.color_hex(0xFF8C00), lv.color_hex(0xFF7000),
    lv.color_hex(0xFF5500), lv.color_hex(0xFF7000),
    lv.color_hex(0xFF8C00), lv.color_hex(0xFFAA00),
)
_SWING = {
    S_IDLE:    ( 0,  3,  6,  3,  0, -3, -6, -3),
    S_WORKING: ( 0,  2,  4,  2,  0, -2, -4, -2),
    S_ERROR:   ( 0,  5, 10,  5,  0, -5,-10, -5),
    S_DONE:    ( 0,  4,  8,  4,  0, -4, -8, -4),
}
_JUMP_Y = (-3, -5, -3, 0, 0, 0, 0, 0)


class ClaudeCharacter(Character):

    def build(self, panel, x, y, size):
        lx = x + (size - LOGO_SIZE) // 2
        ly = y + (size - LOGO_SIZE) // 2

        self._objs   = []
        self._base_x = []
        self._base_y = []

        def mk(px, py, pw, ph, color, radius=2):
            o = lv.obj(panel)
            o.set_pos(lx + px, ly + py)
            o.set_size(pw, ph)
            o.set_style_radius(radius, lv.PART.MAIN)
            o.set_style_bg_color(color, lv.PART.MAIN)
            o.set_style_border_width(0, lv.PART.MAIN)
            self._objs.append(o)
            self._base_x.append(lx + px)
            self._base_y.append(ly + py)
            return o

        _BG = lv.color_hex(0xFFFFFF)
        hx, hy, hw, hh = _LOGO_HEAD
        self._head = mk(hx, hy, hw, hh, _PULSE[0])
        self._eyes = [mk(ex, ey, ew, eh, _BG, 0) for ex, ey, ew, eh in _LOGO_EYES]
        ax, ay, aw, ah = _LOGO_ARMS
        self._arms = mk(ax, ay, aw, ah, _PULSE[0])
        self._torso = None
        if _LOGO_TORSO is not None:
            tx, ty, tw, th = _LOGO_TORSO
            self._torso = mk(tx, ty, tw, th, _PULSE[0])
        self._legs = [mk(lx2, ly2, lw, lh, _PULSE[0]) for lx2, ly2, lw, lh in _LOGO_LEGS]

    def _set_all(self, color):
        self._head.set_style_bg_color(color, lv.PART.MAIN)
        self._arms.set_style_bg_color(color, lv.PART.MAIN)
        if self._torso:
            self._torso.set_style_bg_color(color, lv.PART.MAIN)
        for leg in self._legs:
            leg.set_style_bg_color(color, lv.PART.MAIN)

    def tick(self, state, frame):
        f = frame
        if state == S_WORKING:
            for i, leg in enumerate(self._legs):
                leg.set_style_bg_color(_W_HI if i == f % 4 else _W_LO, lv.PART.MAIN)
            self._head.set_style_bg_color(_W_LO, lv.PART.MAIN)
            self._arms.set_style_bg_color(_W_LO, lv.PART.MAIN)
            if self._torso:
                self._torso.set_style_bg_color(_W_LO, lv.PART.MAIN)
        elif state == S_ERROR:
            self._set_all(_E_HI if f % 2 == 0 else _E_LO)
        elif state == S_DONE:
            for i, leg in enumerate(self._legs):
                leg.set_style_bg_color(_C_HI if i == f % 4 else _C_LO, lv.PART.MAIN)
            self._head.set_style_bg_color(_C_LO, lv.PART.MAIN)
            self._arms.set_style_bg_color(_C_LO, lv.PART.MAIN)
            if self._torso:
                self._torso.set_style_bg_color(_C_LO, lv.PART.MAIN)
            return (_SWING[S_DONE][f], _JUMP_Y[f])
        else:
            self._set_all(_PULSE[f])
        return (_SWING[state][f], 0)

    def apply_swing(self, ox, oy):
        for i, obj in enumerate(self._objs):
            obj.set_pos(self._base_x[i] + ox, self._base_y[i] + oy)
