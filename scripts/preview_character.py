#!/usr/bin/env python3
"""
preview_character.py — 在 PC 端预览所有预设角色（无需烧录设备）

用法：
    python scripts/preview_character.py                     # 输出 preview.png
    python scripts/preview_character.py -o my_preview.png  # 指定输出路径
    python scripts/preview_character.py --char cat robot    # 只预览指定角色
    python scripts/preview_character.py --state W           # 只预览指定状态
    python scripts/preview_character.py --frames 4          # 每个状态显示帧数（1-8）

依赖：pip install Pillow
"""

import sys
import os
import argparse

# ── Mock lvgl ──────────────────────────────────────────────────────────────

_MOCK_OBJS = []   # 全局注册表，每次 build 前清空


class MockColor:
    def __init__(self, hex_val):
        self.hex_val = hex_val & 0xFFFFFF

    def to_rgb(self):
        v = self.hex_val
        return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


class MockObj:
    def __init__(self, parent):
        self.x = 0; self.y = 0; self.w = 0; self.h = 0
        self.color = MockColor(0xCCCCCC)
        self.radius = 4
        self.visible = True
        _MOCK_OBJS.append(self)

    def set_pos(self, x, y):    self.x = x; self.y = y
    def set_size(self, w, h):   self.w = w; self.h = h
    def set_style_radius(self, r, part): self.radius = r
    def set_style_bg_color(self, c, part): self.color = c
    def set_style_border_width(self, w, part): pass
    def clean(self): pass
    def add_event_cb(self, *a): pass


class MockPart:
    MAIN = 0


class MockLv:
    PART = MockPart()

    @staticmethod
    def color_hex(v):
        return MockColor(v)

    @staticmethod
    def obj(parent):
        return MockObj(parent)


# 注入 mock 模块（必须在 import character 之前）
sys.modules['lvgl'] = MockLv()

# mock state 模块（device/ 路径下的 state.py 需要先加入 sys.path）
DEVICE_DIR = os.path.join(os.path.dirname(__file__), '..', 'device')
sys.path.insert(0, os.path.abspath(DEVICE_DIR))

# mock logo_data（character.py 依赖）
class _MockLogoData:
    LOGO_SIZE = 110
    _LOGO_HEAD  = (5,  5, 100, 40)
    _LOGO_ARMS  = (0, 50, 110,  20)
    _LOGO_TORSO = (20, 45, 70, 30)
    _LOGO_LEGS  = [(10, 80, 22, 20), (35, 80, 22, 20), (60, 80, 22, 20), (85, 80, 22, 20)]
    _LOGO_EYES  = [(20, 12, 18, 12), (72, 12, 18, 12)]

_mod = type(sys)('logo_data')
_mod.LOGO_SIZE  = _MockLogoData.LOGO_SIZE
_mod._LOGO_HEAD  = _MockLogoData._LOGO_HEAD
_mod._LOGO_ARMS  = _MockLogoData._LOGO_ARMS
_mod._LOGO_TORSO = _MockLogoData._LOGO_TORSO
_mod._LOGO_LEGS  = _MockLogoData._LOGO_LEGS
_mod._LOGO_EYES  = _MockLogoData._LOGO_EYES
sys.modules['logo_data'] = _mod

# ── 导入所有角色 ──────────────────────────────────────────────────────────

from state import S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR  # noqa: E402
from character      import ClaudeCharacter    # noqa: E402
from char_cat       import CatCharacter       # noqa: E402
from char_robot     import RobotCharacter     # noqa: E402
from char_ghost     import GhostCharacter     # noqa: E402
from char_among_us  import AmongUsCharacter   # noqa: E402
from char_creeper   import CreeperCharacter   # noqa: E402
from char_kirby     import KirbyCharacter     # noqa: E402
from char_pikachu   import PikachuCharacter   # noqa: E402

ALL_CHARS = {
    'claude':   ClaudeCharacter,
    'cat':      CatCharacter,
    'robot':    RobotCharacter,
    'ghost':    GhostCharacter,
    'among_us': AmongUsCharacter,
    'creeper':  CreeperCharacter,
    'kirby':    KirbyCharacter,
    'pikachu':  PikachuCharacter,
}

ALL_STATES = [S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR]
STATE_LABELS = {S_IDLE: 'IDLE', S_WORKING: 'WORK', S_PENDING: 'PEND', S_DONE: 'DONE', S_ERROR: 'ERR'}
STATE_BG = {
    S_IDLE:    (30, 30, 40),
    S_WORKING: (20, 30, 50),
    S_PENDING: (50, 40, 10),
    S_DONE:    (20, 45, 20),
    S_ERROR:   (50, 20, 20),
}

# ── Pillow 渲染 ────────────────────────────────────────────────────────────

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("缺少 Pillow，请运行：pip install Pillow")
    sys.exit(1)

CELL   = 120   # 每格大小（角色区）
PAD    = 6     # 格内边距
HEADER = 24    # 顶部标题行高度
LWIDTH = 64    # 左侧角色名列宽


def _draw_rounded_rect(draw, x, y, w, h, r, color):
    r = min(r, w // 2, h // 2)
    if r <= 0:
        draw.rectangle([x, y, x + w, y + h], fill=color)
        return
    draw.rectangle([x + r, y, x + w - r, y + h], fill=color)
    draw.rectangle([x, y + r, x + w, y + h - r], fill=color)
    draw.ellipse([x, y, x + 2*r, y + 2*r], fill=color)
    draw.ellipse([x + w - 2*r, y, x + w, y + 2*r], fill=color)
    draw.ellipse([x, y + h - 2*r, x + 2*r, y + h], fill=color)
    draw.ellipse([x + w - 2*r, y + h - 2*r, x + w, y + h], fill=color)


def render_frame(char_cls, state, frame):
    """渲染单帧，返回 (CELL×CELL) RGBA Image"""
    global _MOCK_OBJS
    _MOCK_OBJS.clear()

    ch = char_cls()
    panel = object()
    ch.build(panel, 0, 0, CELL)

    # 记录 build 后对象快照（基准位置）
    snapshot = []
    for o in list(_MOCK_OBJS):
        snapshot.append((o, o.x, o.y, o.w, o.h, o.color, o.radius))

    # 调用 tick 得到摆动偏移
    ox, oy = ch.tick(state, frame)

    # tick 可能改变 obj 颜色/位置，重新采集
    bg = STATE_BG[state]
    img = Image.new('RGB', (CELL, CELL), bg)
    draw = ImageDraw.Draw(img)

    for o in _MOCK_OBJS:
        if o.w <= 0 or o.h <= 0:
            continue
        r, g, b = o.color.to_rgb()
        _draw_rounded_rect(draw, o.x + ox, o.y + oy, o.w, o.h, o.radius, (r, g, b))

    return img


def build_grid(char_names, states, frames_per_state, output_path):
    """生成大图网格：行=角色, 列=状态×帧"""
    cols = len(states) * frames_per_state
    rows = len(char_names)

    img_w = LWIDTH + cols * CELL + (len(states) - 1) * 2   # 状态分组间距
    img_h = HEADER + rows * CELL

    canvas = Image.new('RGB', (img_w, img_h), (18, 18, 24))
    draw   = ImageDraw.Draw(canvas)

    try:
        font_sm = ImageFont.truetype("arial.ttf", 11)
        font_md = ImageFont.truetype("arial.ttf", 13)
    except Exception:
        font_sm = ImageFont.load_default()
        font_md = font_sm

    # 列标题
    col = 0
    for si, st in enumerate(states):
        for fi in range(frames_per_state):
            cx = LWIDTH + col * CELL + si * 2 + CELL // 2
            label = f"{STATE_LABELS[st]} f{fi}"
            draw.text((cx, 4), label, fill=(180, 180, 200), font=font_sm, anchor='mt')
            col += 1

    # 行
    for ri, name in enumerate(char_names):
        char_cls = ALL_CHARS[name]
        # 行标签
        cy = HEADER + ri * CELL + CELL // 2
        draw.text((LWIDTH // 2, cy), name, fill=(220, 220, 240), font=font_md, anchor='mm')

        col = 0
        for si, st in enumerate(states):
            for fi in range(frames_per_state):
                frame_img = render_frame(char_cls, st, fi)
                px = LWIDTH + col * CELL + si * 2
                py = HEADER + ri * CELL
                canvas.paste(frame_img, (px, py))
                col += 1

    canvas.save(output_path)
    print(f"已保存预览图：{output_path}  ({img_w}×{img_h})")


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='预览预设角色（无需设备）')
    parser.add_argument('-o', '--output', default='preview.png', help='输出图片路径')
    parser.add_argument('--char', nargs='+', choices=list(ALL_CHARS.keys()),
                        default=list(ALL_CHARS.keys()), help='要预览的角色（默认全部）')
    parser.add_argument('--state', nargs='+',
                        choices=['I', 'W', 'P', 'C', 'E'],
                        default=None, help='要预览的状态（默认全部）')
    parser.add_argument('--frames', type=int, default=4, choices=range(1, 9),
                        help='每个状态显示几帧（默认 4）')
    args = parser.parse_args()

    state_map = {'I': S_IDLE, 'W': S_WORKING, 'P': S_PENDING, 'C': S_DONE, 'E': S_ERROR}
    states = [state_map[s] for s in args.state] if args.state else ALL_STATES

    build_grid(args.char, states, args.frames, args.output)


if __name__ == '__main__':
    main()
