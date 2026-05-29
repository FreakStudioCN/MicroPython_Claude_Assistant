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

import time
import machine
import lcd_bus
import lvgl as lv
import st7789
import i2c
import cst816s
import task_handler
from micropython import const
import config as cfg
_char_mod = __import__("char_" + cfg.CHARACTER) if cfg.CHARACTER != "claude" else None
_CharClass = getattr(_char_mod, "".join(w[0].upper() + w[1:] for w in cfg.CHARACTER.split("_")) + "Character") if _char_mod else __import__("character").ClaudeCharacter
from state import sess_state as _sess_state, S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR, dominant_state, sticky_dominant
from voice_task import VoiceTask
from session_manager import SessionManager
import logging
_log = logging.getLogger("display")

# ── 颜色常量 ──────────────────────────────────────────────────
_C_TAB_IDLE  = lv.color_hex(0xCCCCCC)
_C_TAB_WORK  = lv.color_hex(0x2196F3)
_C_TAB_PEND  = lv.color_hex(0xFFC107)
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
MAX_SESSIONS = cfg.MAX_SESSIONS
FACE_SIZE    = const(110)
EYE_SIZE     = const(10)
DOT_SIZE     = const(18)

# 状态颜色查找表
_DOT_COLORS   = {S_WORKING: _C_DOT_WORK, S_ERROR: _C_DOT_ERR, S_DONE: _C_DOT_DONE, S_IDLE: _C_DOT_IDLE, S_PENDING: _C_DOT_PEND}
_BLOCK_COLORS = {S_WORKING: _C_BG_NORMAL, S_ERROR: _C_BG_ERROR, S_DONE: _C_BG_SUCCESS, S_IDLE: _C_BG_IDLE, S_PENDING: _C_BG_PENDING}
_STATE_LABELS = {S_WORKING: "Working", S_ERROR: "Error", S_DONE: "Done", S_IDLE: "Idle", S_PENDING: "Pending"}
_STATE_LABEL_ZH = {S_IDLE: "空闲", S_WORKING: "工作中", S_PENDING: "等待审批", S_DONE: "完成", S_ERROR: "出错"}

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
        self._character    = _CharClass()
        self._logo_timer   = None
        self._logo_frame   = 0
        self._logo_state   = S_IDLE
        self._session_dots = []
        self._msg_block    = None
        self._msg_label    = None

        # Sessions 面板控件
        self._tab_btns    = []
        self._tab_labels  = []
        self._containers  = []
        self._selected    = 0
        self._blink_tasks = [None] * MAX_SESSIONS

        # SessionManager（slot 映射 + 历史记录）
        self._sm = SessionManager(MAX_SESSIONS, cfg.HISTORY_MAX_LEN)

        # Config 面板控件
        self._brightness_slider = None
        self._brightness_label  = None
        self._storage_dd        = None

        # 共享
        self._sessions  = []
        self._ordered   = [None] * MAX_SESSIONS
        self._disp      = None
        self._last_active_sess = None
        self._last_sticky_dot_count = 0

        # 语音
        self._voice       = VoiceTask()
        self._prev_states = {}
        self._history     = []

        # 槽位名记录（用于检测 session 变化）
        self._slot_names = [""] * MAX_SESSIONS

        # 长按 guard：吃掉长按后紧随的 CLICKED
        self._tab_long_pressed = [False] * MAX_SESSIONS

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
            btn.add_event_cb(lambda e, idx=i: self._on_tab_click(idx),      lv.EVENT.CLICKED,      None)
            btn.add_event_cb(lambda e, idx=i: self._on_tab_long_press(idx), lv.EVENT.LONG_PRESSED, None)
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

        # Clear Logs 按钮（绿色，放在日志存储选择上方）
        btn_clear = lv.button(panel)
        btn_clear.set_pos(0, 150)
        btn_clear.set_size(140, 40)
        btn_clear.set_style_bg_color(lv.color_hex(0x4CAF50), lv.PART.MAIN)
        self._lbl_clear = lv.label(btn_clear)
        self._lbl_clear.set_text("Clear Logs")
        self._lbl_clear.center()
        btn_clear.add_event_cb(self._on_clear_logs, lv.EVENT.CLICKED, None)

        # Log Storage 下拉列表
        storage_lbl = lv.label(panel)
        storage_lbl.set_pos(0, 205)
        storage_lbl.set_text("Log Storage:")

        sd_opts = "Flash\nSD Card" if self._sd_available() else "Flash"
        self._storage_dd = lv.dropdown(panel)
        self._storage_dd.set_pos(0, 225)
        self._storage_dd.set_size(160, 40)
        self._storage_dd.set_options(sd_opts)
        self._storage_dd.set_selected(0 if cfg.LOG_STORAGE == "flash" else 1)
        self._storage_dd.add_event_cb(self._on_storage_change, lv.EVENT.VALUE_CHANGED, None)

        self._config_panel = panel

    # ── 面板切换 ──────────────────────────────────────────────

    def _show_panel(self, panel):
        if self._active_panel is not None:
            self._active_panel.add_flag(lv.obj.FLAG.HIDDEN)
        panel.remove_flag(lv.obj.FLAG.HIDDEN)
        self._active_panel = panel

    # ── 事件回调 ──────────────────────────────────────────────

    def _on_tab_click(self, idx):
        if self._tab_long_pressed[idx]:
            self._tab_long_pressed[idx] = False
            return
        if idx != self._selected:
            self._containers[self._selected].add_flag(lv.obj.FLAG.HIDDEN)
            self._containers[idx].remove_flag(lv.obj.FLAG.HIDDEN)
            self._selected = idx
            print(f"[tab] switch to S{idx + 1}")

    def _on_tab_long_press(self, idx):
        self._tab_long_pressed[idx] = True
        self._sm.histories[idx].clear()
        self._containers[idx].clean()
        lbl = self._tab_labels[idx]
        orig = lbl.get_text()
        lbl.set_text("\u2713")
        lv.timer_create(
            lambda t, l=lbl, o=orig: l.set_text(o), 1500, None
        ).set_repeat_count(1)
        _log.info("slot[%d] history cleared by long press", idx)

    def _on_brightness_change(self, e):
        val = self._brightness_slider.get_value()
        self._disp.set_backlight(val)
        self._brightness_label.set_text(f"{val}%")

    def _sd_available(self) -> bool:
        try:
            import os
            os.stat("/sd")
            return True
        except OSError:
            return False

    def _on_storage_change(self, e):
        idx = self._storage_dd.get_selected()
        storage = "flash" if idx == 0 else "sd"
        if storage == "sd" and not self._sd_available():
            self._storage_dd.set_selected(0)
            _log.info("SD card not available, fallback to flash")
            return
        self._save_config("LOG_STORAGE", storage)
        print(f"[config] log storage -> {storage} (restart required)")
        _log.info("log storage changed to %s (restart required)", storage)

    def _save_config(self, key: str, value):
        import os
        try:
            try:
                import ujson
            except ImportError:
                import json as ujson
            try:
                with open("/config.json", "r") as f:
                    cfg_data = ujson.load(f)
            except OSError:
                cfg_data = {}

            cfg_data[key] = value

            with open("/config.json", "w") as f:
                ujson.dump(cfg_data, f)
            _log.info("config saved: %s=%s", key, value)
        except Exception as e:
            _log.error("save config failed: %s", e)

    def _on_clear_logs(self, e):
        import os
        log_dir = "/sd/log" if cfg.LOG_STORAGE == "sd" and self._sd_available() else "/log"

        cleared = []
        for i in range(cfg.LOG_MAX_FILES):
            path = "{}/run_{}.log".format(log_dir, i)
            try:
                os.remove(path)
                cleared.append(path)
                _log.info("removed: %s", path)
            except OSError:
                pass

        print(f"[config] clear logs: {cleared if cleared else 'nothing to clear'}")
        self._lbl_clear.set_text("Cleared!")
        lv.timer_create(lambda t: self._lbl_clear.set_text("Clear Logs"), 2000, None)

    # ── 渲染入口 ──────────────────────────────────────────────

    async def render(self, msg):
        if msg is None or isinstance(msg, dict):
            return

        # v6 协议：SessionManager 处理 slot 映射
        assigned, cleared, ordered = self._sm.update(msg.sessions)

        pending = {}
        for slot_index, sess in assigned:
            # 更新槽位
            self._update_tab(slot_index, sess)
            self._update_history(slot_index, sess)

            # 状态跳变检测 → 触发语音
            cur = _sess_state(sess)
            prev = self._prev_states.get(sess.name)
            if cur != prev:
                self._push_voice_history(sess, cur)
                if cur in (S_DONE, S_ERROR, S_PENDING):
                    await self._voice.trigger(self._history, sess, cur)
                pending[sess.name] = cur

        # 清空未更新的槽（全部清空时跳过，避免粘滞中圆点被刷灰）
        if len(cleared) < MAX_SESSIONS:
            for slot_index in cleared:
                self._update_tab(slot_index, None)

        # self._ordered 保留完整槽位映射（含 None），供圆点按位置索引
        # self._sessions 压缩掉 None，供 dominant_state 和消息块遍历
        self._ordered = ordered
        self._sessions = [s for s in ordered if s is not None]

        self._update_main()

        # 延后写入 _prev_states，确保 _update_main() 中圆点粘滞判断拿到旧值
        for name, cur in pending.items():
            self._prev_states[name] = cur

    async def on_connect(self):
        self._ble_dot.set_style_bg_color(_C_BLE_ON, lv.PART.MAIN)
        _log.info("connected")
        self._prev_states.clear()
        asyncio.create_task(self._voice.trigger([], None, "connect", force=True))

    async def on_disconnect(self):
        self._ble_dot.set_style_bg_color(_C_BLE_OFF, lv.PART.MAIN)
        _log.info("disconnected")
        self._sessions = []
        self._ordered = [None] * MAX_SESSIONS
        self._prev_states.clear()
        self._last_sticky_dot_count = 0
        self._update_main()

    # ── 主界面更新 ────────────────────────────────────────────

    def _update_main(self):
        raw_dominant = dominant_state(self._sessions)
        state = sticky_dominant(raw_dominant, self._logo_state)
        if raw_dominant != state:
            _log.info("sticky dominant: raw=%s -> %s (keep %s against I)", raw_dominant, state, self._logo_state)

        # Logo 动画状态
        if state != self._logo_state:
            # 检测粘滞是否被新 W/E 打破
            if self._logo_state in (S_DONE, S_PENDING) and state not in (S_DONE, S_PENDING, S_IDLE):
                _log.info("sticky broken: %s->%s (sticky was holding %s)", self._logo_state, state, self._logo_state)
            _log.info("dominant: %s->%s", self._logo_state, state)
            self._logo_state = state
            self._logo_frame = 0

        # session 圆点：按槽位位置取 self._ordered，保持 dot[i] ↔ slot[i] 对应
        sticky_dot_count = 0
        for i, dot in enumerate(self._session_dots):
            sess = self._ordered[i] if i < len(self._ordered) else None
            if sess is not None:
                raw_s = _sess_state(sess)
                prev  = self._prev_states.get(sess.name)
                s = sticky_dominant(raw_s, prev)
                if raw_s != s:
                    _log.info("sticky dot[%d] %s: raw=%s prev=%s -> sticky=%s", i, sess.name, raw_s, prev, s)
                dot.set_style_bg_color(_DOT_COLORS[s], lv.PART.MAIN)
            elif state in (S_DONE, S_PENDING):
                sticky_dot_count += 1
                continue
            else:
                dot.set_style_bg_color(_DOT_COLORS[S_IDLE], lv.PART.MAIN)

        # 空槽粘滞：记录粘滞保持的 dot 数量（去重，只在变化时日志）
        if sticky_dot_count != getattr(self, '_last_sticky_dot_count', 0):
            if sticky_dot_count > 0:
                _log.info("sticky hold: %d empty dot(s) kept at dominant=%s", sticky_dot_count, state)
            self._last_sticky_dot_count = sticky_dot_count

        # 消息块：取优先级最高的 session
        active_sess = None
        active_idx  = 0
        for priority in (S_ERROR, S_WORKING, S_DONE, S_IDLE):
            for i, s in enumerate(self._sessions):
                if _sess_state(s) == priority:
                    active_sess = s
                    active_idx  = i + 1
                    break
            if active_sess:
                break

        if active_sess:
            self._last_active_sess = active_sess
            text = active_sess.msg if active_sess.msg else _STATE_LABELS[_sess_state(active_sess)]
            self._msg_label.set_text(f"{active_sess.name}: {text}")
            self._msg_block.set_style_bg_color(_BLOCK_COLORS[_sess_state(active_sess)], lv.PART.MAIN)
        elif state in (S_DONE, S_PENDING) and self._last_active_sess:
            sess = self._last_active_sess
            _log.info("sticky msg: show %s as %s (all sessions gone, sticky hold)", sess.name, _STATE_LABELS[state])
            self._msg_label.set_text(f"{sess.name}: {_STATE_LABELS[state]}")
            self._msg_block.set_style_bg_color(_BLOCK_COLORS[state], lv.PART.MAIN)
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
            # v6 修复：不清除 slot_names，保留原名字，避免重连时误清历史
            return

        # 检测 session 名变化，自动清空历史
        if sess.name != self._slot_names[index]:
            self._sm.histories[index].clear()
            self._containers[index].clean()
            self._slot_names[index] = sess.name
            _log.info("slot[%d] session changed: %s", index, sess.name)

        state     = _sess_state(sess)
        prev      = self._prev_states.get(sess.name)
        sticky_s  = sticky_dominant(state, prev)
        if state != sticky_s:
            _log.info("sticky tab[%d] %s: raw=%s prev=%s -> sticky=%s", index, sess.name, state, prev, sticky_s)
        color_map = {S_ERROR: _C_TAB_ERR, S_WORKING: _C_TAB_WORK, S_PENDING: _C_TAB_PEND, S_DONE: _C_TAB_CELE}
        btn.set_style_bg_color(color_map.get(sticky_s, _C_TAB_IDLE), lv.PART.MAIN)
        lbl.set_text(sess.name)
        if sticky_s == S_ERROR:
            self._start_blink(index)
        else:
            self._stop_blink(index)

    def _update_history(self, index: int, sess):
        state = _sess_state(sess)
        text = sess.msg if sess.msg else _STATE_LABELS[state]
        action = self._sm.push_history(index, text, state)

        _log.info("sess=%s state=%s msg=%s action=%s", sess.name, state, text, action)

        if action == "skip":
            return
        elif action == "update":
            self._render_container(index)
        elif action in ("append", "overflow"):
            if action == "overflow":
                c = self._containers[index]
                if c.get_child_count() > 0:
                    c.get_child(0).delete()
            record = self._sm.histories[index][-1]
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
        for record in self._sm.histories[index]:
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
        if index < len(self._sessions) and self._sessions[index] and _sess_state(self._sessions[index]) == S_ERROR:
            self._tab_btns[index].set_style_bg_color(_C_TAB_ERR, lv.PART.MAIN)

    async def _blink_loop(self, index: int):
        btn    = self._tab_btns[index]
        toggle = False
        try:
            while True:
                btn.set_style_bg_color(_C_TAB_ERR2 if toggle else _C_TAB_ERR, lv.PART.MAIN)
                toggle = not toggle
                await asyncio.sleep(cfg.BLINK_INTERVAL_S)
        except asyncio.CancelledError:
            pass

    def _push_voice_history(self, sess, state: str):
        self._history.append({
            "name":  sess.name,
            "state": _STATE_LABEL_ZH.get(state, state),
            "msg":   sess.msg or "",
        })
        if len(self._history) > cfg.VOICE_HISTORY_DEPTH:
            self._history.pop(0)
