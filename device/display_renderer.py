"""
display_renderer.py —— LVGL 屏幕渲染器

三个面板：
  主界面：顶部导航 + 小人动画 + session 圆点概要 + 当前状态消息块（60 字符跑马灯）
  Sessions：5 个选项卡 + Flex 彩色历史记录（自动换行）
  Config：亮度调节
"""

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import machine
import lcd_bus
import lvgl as lv
import st7789
import i2c
import cst816s
import task_handler
from micropython import const
import config as cfg
from character import ClaudeCharacter  # 换形象只改这一行
from state import sess_state as _sess_state

# ── 颜色常量 ──────────────────────────────────────────────────
_C_TAB_IDLE  = lv.color_hex(0xCCCCCC)
_C_TAB_WORK  = lv.color_hex(0x2196F3)
_C_TAB_ERR   = lv.color_hex(0xF44336)
_C_TAB_ERR2  = lv.color_hex(0xC62828)
_C_TAB_CELE  = lv.color_hex(0x4CAF50)

_C_BG_NORMAL  = lv.color_hex(0x2196F3)
_C_BG_ERROR   = lv.color_hex(0xF44336)
_C_BG_SUCCESS = lv.color_hex(0x4CAF50)
_C_BG_IDLE    = lv.color_hex(0x9E9E9E)
_C_BG_PENDING = lv.color_hex(0xFFC107)
_C_TEXT_WHITE = lv.color_hex(0xFFFFFF)

_C_FACE_IDLE  = lv.color_hex(0xBDBDBD)
_C_FACE_WORK  = lv.color_hex(0x2196F3)
_C_FACE_ERR   = lv.color_hex(0xF44336)
_C_FACE_DONE  = lv.color_hex(0x4CAF50)

_C_DOT_IDLE   = lv.color_hex(0xCCCCCC)
_C_DOT_WORK   = lv.color_hex(0x2196F3)
_C_DOT_ERR    = lv.color_hex(0xF44336)
_C_DOT_DONE   = lv.color_hex(0x4CAF50)
_C_DOT_PEND   = lv.color_hex(0xFFC107)

_C_BLE_ON     = lv.color_hex(0x4CAF50)
_C_BLE_OFF    = lv.color_hex(0xF44336)

# ── 布局常量 ──────────────────────────────────────────────────
SCREEN_W     = const(320)
SCREEN_H     = const(240)
NAV_H        = const(40)
FACE_H       = const(110)
DOTS_H       = const(30)
MSG_H        = const(60)
TAB_H        = const(52)
TAB_W        = const(64)
MAX_SESSIONS = const(5)
FACE_SIZE    = const(110)
EYE_SIZE     = const(10)
DOT_SIZE     = const(18)

# 状态颜色查找表
_DOT_COLORS   = {"W": _C_DOT_WORK,  "E": _C_DOT_ERR,  "C": _C_DOT_DONE,  "I": _C_DOT_IDLE,  "P": _C_DOT_PEND}
_BLOCK_COLORS = {"W": _C_BG_NORMAL, "E": _C_BG_ERROR, "C": _C_BG_SUCCESS, "I": _C_BG_IDLE, "P": _C_BG_PENDING}
_STATE_LABELS = {"W": "Working", "E": "Error", "C": "Done", "I": "Idle", "P": "Pending"}

def _dominant_state(sessions) -> str:
    states = [_sess_state(s) for s in sessions] if sessions else []
    for s in ("E", "W", "C"):
        if s in states:
            return s
    return "I"


class DisplayRenderer:
    """LVGL 屏幕渲染器：主界面 + Sessions + Config 三个面板"""

    def __init__(self):
        # 面板
        self._main_panel     = None
        self._sessions_panel = None
        self._config_panel   = None
        self._active_panel   = None

        # 主界面控件
        self._ble_dot      = None
        self._character    = ClaudeCharacter()
        self._logo_timer   = None
        self._logo_frame   = 0
        self._logo_state   = "I"
        self._session_dots = []
        self._msg_block    = None
        self._msg_label    = None

        # Sessions 面板控件
        self._tab_btns    = []
        self._tab_labels  = []
        self._containers  = []
        self._histories   = [[] for _ in range(MAX_SESSIONS)]
        self._selected    = 0
        self._blink_tasks = [None] * MAX_SESSIONS

        # Config 面板控件
        self._brightness_slider = None
        self._brightness_label  = None

        # 共享
        self._sessions  = []
        self._disp      = None

    async def init(self):
        print("[renderer] init hardware...")
        self._init_hardware()
        print("[renderer] hardware OK, building UI...")
        self._build_ui()
        print("[renderer] UI built")

    def _init_hardware(self):
        spi_bus = machine.SPI.Bus(
            host=cfg.SPI_BUS, sck=cfg.LCD_SCLK, mosi=cfg.LCD_MOSI, miso=cfg.LCD_MISO
        )
        display_bus = lcd_bus.SPIBus(
            spi_bus=spi_bus, freq=cfg.SPI_FREQ, dc=cfg.LCD_DC, cs=cfg.LCD_CS
        )
        fb1 = display_bus.allocate_framebuffer(cfg.FB_SIZE, lcd_bus.MEMORY_INTERNAL | lcd_bus.MEMORY_DMA)
        fb2 = display_bus.allocate_framebuffer(cfg.FB_SIZE, lcd_bus.MEMORY_INTERNAL | lcd_bus.MEMORY_DMA)
        self._disp = st7789.ST7789(
            data_bus=display_bus,
            display_width=cfg.LCD_WIDTH,
            display_height=cfg.LCD_HEIGHT,
            frame_buffer1=fb1,
            frame_buffer2=fb2,
            backlight_pin=cfg.LCD_BL,
            backlight_on_state=st7789.STATE_PWM,
            color_space=lv.COLOR_FORMAT.RGB565,
            color_byte_order=st7789.BYTE_ORDER_BGR,
            rgb565_byte_swap=True,
        )
        self._disp.init()
        self._disp.set_power(True)
        self._disp.set_backlight(100)
        print("[renderer] display initialized")

        i2c_bus = i2c.I2C.Bus(
            host=cfg.I2C_BUS, scl=cfg.TP_SCL, sda=cfg.TP_SDA,
            freq=cfg.I2C_FREQ, use_locks=False
        )
        touch_dev = i2c.I2C.Device(
            bus=i2c_bus, dev_id=cfg.TP_ADDR, reg_bits=cfg.TP_REGBITS
        )
        cst816s.CST816S(touch_dev, startup_rotation=lv.DISPLAY_ROTATION._180)
        print("[renderer] touch initialized")
        self._disp.set_rotation(lv.DISPLAY_ROTATION._90)
        print("[renderer] rotation set")
        task_handler.TaskHandler(duration=5)
        print("[renderer] task handler started")

    def _build_ui(self):
        scrn = lv.screen_active()
        self._build_main_panel(scrn)
        self._build_sessions_panel(scrn)
        self._build_config_panel(scrn)
        self._show_panel(self._main_panel)

    # ── 主界面 ────────────────────────────────────────────────

    def _build_main_panel(self, scrn):
        panel = lv.obj(scrn)
        panel.set_size(SCREEN_W, SCREEN_H)
        panel.set_pos(0, 0)
        panel.set_style_border_width(0, lv.PART.MAIN)
        panel.set_style_pad_all(0, lv.PART.MAIN)

        # 顶部导航栏
        nav = lv.obj(panel)
        nav.set_pos(0, 0)
        nav.set_size(SCREEN_W, NAV_H)
        nav.set_style_border_width(0, lv.PART.MAIN)
        nav.set_style_pad_all(5, lv.PART.MAIN)

        btn_s = lv.button(nav)
        btn_s.set_pos(0, 0)
        btn_s.set_size(100, 30)
        lbl_s = lv.label(btn_s)
        lbl_s.set_text("Sessions")
        lbl_s.center()
        btn_s.add_event_cb(lambda e: self._show_panel(self._sessions_panel), lv.EVENT.CLICKED, None)

        btn_c = lv.button(nav)
        btn_c.set_pos(108, 0)
        btn_c.set_size(80, 30)
        lbl_c = lv.label(btn_c)
        lbl_c.set_text("Config")
        lbl_c.center()
        btn_c.add_event_cb(lambda e: self._show_panel(self._config_panel), lv.EVENT.CLICKED, None)

        # BLE 圆点指示
        ble_dot = lv.obj(nav)
        ble_dot.set_pos(SCREEN_W - 50, 8)
        ble_dot.set_size(14, 14)
        ble_dot.set_style_radius(lv.RADIUS_CIRCLE, lv.PART.MAIN)
        ble_dot.set_style_bg_color(_C_BLE_OFF, lv.PART.MAIN)
        ble_dot.set_style_border_width(0, lv.PART.MAIN)
        ble_lbl = lv.label(nav)
        ble_lbl.set_pos(SCREEN_W - 34, 8)
        ble_lbl.set_text("BLE")
        self._ble_dot = ble_dot

        # 像素风 Claude Logo
        face_y = NAV_H + (FACE_H - FACE_SIZE) // 2
        face_x = (SCREEN_W - FACE_SIZE) // 2
        self._build_logo(panel, face_x, face_y)

        # Session 圆点
        dots_y = NAV_H + FACE_H + (DOTS_H - DOT_SIZE) // 2
        slot_w = SCREEN_W // MAX_SESSIONS
        for i in range(MAX_SESSIONS):
            dot = lv.obj(panel)
            dot.set_pos(i * slot_w + (slot_w - DOT_SIZE) // 2, dots_y)
            dot.set_size(DOT_SIZE, DOT_SIZE)
            dot.set_style_radius(lv.RADIUS_CIRCLE, lv.PART.MAIN)
            dot.set_style_bg_color(_C_DOT_IDLE, lv.PART.MAIN)
            dot.set_style_border_width(0, lv.PART.MAIN)
            self._session_dots.append(dot)

        # 消息块
        msg_y = NAV_H + FACE_H + DOTS_H + 4
        msg_block = lv.obj(panel)
        msg_block.set_pos(8, msg_y)
        msg_block.set_size(SCREEN_W - 16, MSG_H - 8)
        msg_block.set_style_bg_color(_C_BG_IDLE, lv.PART.MAIN)
        msg_block.set_style_radius(8, lv.PART.MAIN)
        msg_block.set_style_border_width(0, lv.PART.MAIN)
        msg_block.set_style_pad_all(8, lv.PART.MAIN)
        msg_label = lv.label(msg_block)
        msg_label.set_text("Waiting...")
        msg_label.set_style_text_color(_C_TEXT_WHITE, lv.PART.MAIN)
        msg_label.set_long_mode(lv.label.LONG_MODE.SCROLL_CIRCULAR)
        msg_label.set_width(SCREEN_W - 40)
        self._msg_block = msg_block
        self._msg_label = msg_label
        self._main_panel = panel

    # ── Sessions 面板 ─────────────────────────────────────────

    def _build_sessions_panel(self, scrn):
        panel = lv.obj(scrn)
        panel.set_size(SCREEN_W, SCREEN_H)
        panel.set_pos(0, 0)
        panel.set_style_border_width(0, lv.PART.MAIN)
        panel.set_style_pad_all(0, lv.PART.MAIN)
        panel.add_flag(lv.obj.FLAG.HIDDEN)

        btn_back = lv.button(panel)
        btn_back.set_pos(0, 0)
        btn_back.set_size(80, NAV_H)
        lbl = lv.label(btn_back)
        lbl.set_text("< Back")
        lbl.center()
        btn_back.add_event_cb(lambda e: self._show_panel(self._main_panel), lv.EVENT.CLICKED, None)

        for i in range(MAX_SESSIONS):
            btn = lv.button(panel)
            btn.set_pos(i * TAB_W, NAV_H)
            btn.set_size(TAB_W, TAB_H)
            btn.set_style_bg_color(_C_TAB_IDLE, lv.PART.MAIN)
            btn.set_style_radius(8, lv.PART.MAIN)
            btn.add_event_cb(lambda e, idx=i: self._on_tab_click(idx), lv.EVENT.CLICKED, None)
            lbl = lv.label(btn)
            lbl.set_text(f"S{i+1}")
            lbl.set_long_mode(lv.label.LONG_MODE.SCROLL_CIRCULAR)
            lbl.set_width(TAB_W - 8)
            lbl.align(lv.ALIGN.CENTER, 0, 0)
            self._tab_btns.append(btn)
            self._tab_labels.append(lbl)

        content_y = NAV_H + TAB_H
        content_h = SCREEN_H - content_y
        for i in range(MAX_SESSIONS):
            c = lv.obj(panel)
            c.set_pos(0, content_y)
            c.set_size(SCREEN_W, content_h)
            c.set_flex_flow(lv.FLEX_FLOW.COLUMN)
            c.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
            c.set_scroll_dir(lv.DIR.VER)
            c.set_style_pad_all(8, lv.PART.MAIN)
            c.set_style_pad_row(4, lv.PART.MAIN)
            c.add_flag(lv.obj.FLAG.HIDDEN)
            self._containers.append(c)

        self._containers[0].remove_flag(lv.obj.FLAG.HIDDEN)
        self._sessions_panel = panel

    # ── Config 面板 ───────────────────────────────────────────

    def _build_config_panel(self, scrn):
        panel = lv.obj(scrn)
        panel.set_size(SCREEN_W, SCREEN_H)
        panel.set_pos(0, 0)
        panel.set_style_border_width(0, lv.PART.MAIN)
        panel.set_style_pad_all(16, lv.PART.MAIN)
        panel.add_flag(lv.obj.FLAG.HIDDEN)

        btn_back = lv.button(panel)
        btn_back.set_pos(0, 0)
        btn_back.set_size(80, 34)
        lbl = lv.label(btn_back)
        lbl.set_text("< Back")
        lbl.center()
        btn_back.add_event_cb(lambda e: self._show_panel(self._main_panel), lv.EVENT.CLICKED, None)

        title = lv.label(panel)
        title.set_pos(0, 50)
        title.set_text("Brightness")
        title.set_style_text_font(lv.font_montserrat_16, lv.PART.MAIN)

        slider = lv.slider(panel)
        slider.set_pos(0, 80)
        slider.set_width(SCREEN_W - 32)
        slider.set_range(10, 100)
        slider.set_value(100, 0)
        slider.add_event_cb(self._on_brightness_change, lv.EVENT.VALUE_CHANGED, None)
        self._brightness_slider = slider

        val_label = lv.label(panel)
        val_label.set_pos(0, 120)
        val_label.set_text("100%")
        self._brightness_label = val_label

        self._config_panel = panel

    # ── 面板切换 ──────────────────────────────────────────────

    def _show_panel(self, panel):
        if self._active_panel is not None:
            self._active_panel.add_flag(lv.obj.FLAG.HIDDEN)
        panel.remove_flag(lv.obj.FLAG.HIDDEN)
        self._active_panel = panel

    # ── 事件回调 ──────────────────────────────────────────────

    def _on_tab_click(self, idx):
        if idx != self._selected:
            self._containers[self._selected].add_flag(lv.obj.FLAG.HIDDEN)
            self._containers[idx].remove_flag(lv.obj.FLAG.HIDDEN)
            self._selected = idx
            print(f"[tab] switch to S{idx + 1}")

    def _on_brightness_change(self, e):
        val = self._brightness_slider.get_value()
        self._disp.set_backlight(val)
        self._brightness_label.set_text(f"{val}%")

    # ── 渲染入口 ──────────────────────────────────────────────

    async def render(self, msg):
        if msg is None or isinstance(msg, dict):
            return
        self._sessions = msg.sessions[:MAX_SESSIONS]
        for i in range(MAX_SESSIONS):
            sess = self._sessions[i] if i < len(self._sessions) else None
            self._update_tab(i, sess)
            if sess:
                self._update_history(i, sess)
        self._update_main()

    async def on_connect(self):
        self._ble_dot.set_style_bg_color(_C_BLE_ON, lv.PART.MAIN)

    async def on_disconnect(self):
        self._ble_dot.set_style_bg_color(_C_BLE_OFF, lv.PART.MAIN)
        self._sessions = []
        self._update_main()

    # ── 主界面更新 ────────────────────────────────────────────

    def _update_main(self):
        state = _dominant_state(self._sessions)

        # Logo 动画状态
        if state != self._logo_state:
            self._logo_state = state
            self._logo_frame = 0

        # session 圆点
        for i, dot in enumerate(self._session_dots):
            s = _sess_state(self._sessions[i]) if i < len(self._sessions) else "I"
            dot.set_style_bg_color(_DOT_COLORS[s], lv.PART.MAIN)

        # 消息块：取优先级最高的 session
        active_sess = None
        active_idx  = 0
        for priority in ("E", "W", "C", "I"):
            for i, s in enumerate(self._sessions):
                if _sess_state(s) == priority:
                    active_sess = s
                    active_idx  = i + 1
                    break
            if active_sess:
                break

        if active_sess:
            text = active_sess.msg if active_sess.msg else _STATE_LABELS[_sess_state(active_sess)]
            self._msg_label.set_text(f"{active_sess.name}: {text}")
            self._msg_block.set_style_bg_color(_BLOCK_COLORS[_sess_state(active_sess)], lv.PART.MAIN)
        else:
            self._msg_label.set_text("Idle")
            self._msg_block.set_style_bg_color(_C_BG_IDLE, lv.PART.MAIN)

    # ── 角色动画 ──────────────────────────────────────────────

    def _build_logo(self, panel, face_x, face_y):
        self._character.build(panel, face_x, face_y, FACE_SIZE)
        self._logo_timer = lv.timer_create(
            lambda t: self._on_logo_timer(), 150, None
        )

    def _on_logo_timer(self):
        self._logo_frame = (self._logo_frame + 1) % 8
        ox, oy = self._character.tick(self._logo_state, self._logo_frame)
        if ox or oy:
            self._character.apply_swing(ox, oy)

    # ── Sessions 面板更新 ─────────────────────────────────────

    def _update_tab(self, index: int, sess):
        btn = self._tab_btns[index]
        lbl = self._tab_labels[index]
        if sess is None:
            btn.set_style_bg_color(_C_TAB_IDLE, lv.PART.MAIN)
            lbl.set_text(f"S{index+1}")
            self._stop_blink(index)
            return
        state     = _sess_state(sess)
        tool_text = self._short_tool(index, sess)
        color_map = {"E": _C_TAB_ERR, "W": _C_TAB_WORK, "C": _C_TAB_CELE}
        btn.set_style_bg_color(color_map.get(state, _C_TAB_IDLE), lv.PART.MAIN)
        # 使用 sess.name 显示项目名，状态活跃时显示工具名
        lbl.set_text(tool_text if state in ("E", "W", "C") else sess.name)
        if state == "E":
            self._start_blink(index)
        else:
            self._stop_blink(index)

    def _update_history(self, index: int, sess):
        state   = _sess_state(sess)
        history = self._histories[index]
        text    = sess.msg if sess.msg else _STATE_LABELS[state]
        record  = {"msg": text, "state": state}

        if history and history[-1]["msg"] == text and history[-1]["state"] == state:
            return

        if history and history[-1]["msg"] == text:
            history[-1]["state"] = state
            self._render_container(index)
            return

        history.append(record)
        if len(history) > 20:
            history.pop(0)
            c = self._containers[index]
            if c.get_child_count() > 0:
                c.get_child(0).delete()

        self._append_message_block(index, record)

    def _append_message_block(self, index: int, record: dict):
        container = self._containers[index]
        block = lv.obj(container)
        block.set_width(SCREEN_W - 20)
        block.set_height(lv.SIZE_CONTENT)
        block.set_style_pad_all(8, lv.PART.MAIN)
        block.set_style_radius(6, lv.PART.MAIN)
        block.set_style_border_width(0, lv.PART.MAIN)
        block.set_style_bg_color(_BLOCK_COLORS.get(record["state"], _C_BG_IDLE), lv.PART.MAIN)
        lbl = lv.label(block)
        lbl.set_text(record["msg"])
        lbl.set_style_text_color(_C_TEXT_WHITE, lv.PART.MAIN)
        lbl.set_width(SCREEN_W - 40)
        container.scroll_to_y(9999, 0)

    def _render_container(self, index: int):
        self._containers[index].clean()
        for record in self._histories[index]:
            self._append_message_block(index, record)

    # ── 选项卡闪烁 ────────────────────────────────────────────

    def _start_blink(self, index: int):
        if self._blink_tasks[index] is not None:
            return
        self._blink_tasks[index] = asyncio.create_task(self._blink_loop(index))

    def _stop_blink(self, index: int):
        if self._blink_tasks[index] is not None:
            self._blink_tasks[index].cancel()
            self._blink_tasks[index] = None
        if index < len(self._sessions) and self._sessions[index] and _sess_state(self._sessions[index]) == "E":
            self._tab_btns[index].set_style_bg_color(_C_TAB_ERR, lv.PART.MAIN)

    async def _blink_loop(self, index: int):
        btn    = self._tab_btns[index]
        toggle = False
        try:
            while True:
                btn.set_style_bg_color(_C_TAB_ERR2 if toggle else _C_TAB_ERR, lv.PART.MAIN)
                toggle = not toggle
                await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _short_tool(index: int, sess) -> str:
        if not sess or not sess.msg:
            return f"S{index+1}"
        msg   = sess.msg
        colon = msg.find(":")
        name  = msg[:colon] if colon > 0 else msg
        return name[:6]
