"""
display_renderer.py —— LVGL 屏幕渲染器

布局：
  顶部 5 个选项卡按钮（S1~S5），颜色反映 session 状态
  下方 5 个 textarea，显示对应 session 的操作历史

选项卡颜色：
  无 session  → 浅灰
  IDLE (I)    → 浅灰
  WORKING (W) → 蓝色
  ERROR (E)   → 红色（闪烁）
  CELEBRATE (C)→ 绿色
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

# ── 颜色常量 ──────────────────────────────────────────────────
_C_TAB_IDLE  = lv.color_hex(0xCCCCCC)   # 浅灰（无 session / IDLE）
_C_TAB_WORK  = lv.color_hex(0x2196F3)   # 蓝色（WORKING）
_C_TAB_ERR   = lv.color_hex(0xF44336)   # 红色（ERROR）
_C_TAB_ERR2  = lv.color_hex(0xC62828)   # 深红（ERROR 闪烁交替色）
_C_TAB_CELE  = lv.color_hex(0x4CAF50)   # 绿色（CELEBRATE）

# 历史记录消息块背景颜色
_C_BG_NORMAL  = lv.color_hex(0x2196F3)  # 蓝色（普通操作 W）
_C_BG_ERROR   = lv.color_hex(0xF44336)  # 红色（错误 E）
_C_BG_SUCCESS = lv.color_hex(0x4CAF50)  # 绿色（完成 C）
_C_BG_IDLE    = lv.color_hex(0x9E9E9E)  # 灰色（空闲 I）
_C_BG_PENDING = lv.color_hex(0xFFA726)  # 橙黄色（等待审批）

# 消息块文字颜色（统一白色）
_C_TEXT_WHITE = lv.color_hex(0xFFFFFF)

def _sess_state(sess) -> str:
    """从 SessionStatus 字段推导状态码 I/W/E/C。"""
    if sess.error:
        return "E"
    if sess.completed:
        return "C"
    if sess.running:
        return "W"
    return "I"



TAB_H      = const(52)
TAB_W      = const(64)   # 320 / 5
SCREEN_W   = const(320)
SCREEN_H   = const(240)
MAX_SESSIONS = 5


class DisplayRenderer:
    """LVGL 屏幕渲染器，5 个选项卡 + Flex 布局历史记录"""

    def __init__(self):
        self._tab_btns   = []       # 5 个选项卡 lv.button
        self._tab_labels = []       # 5 个选项卡文字 lv.label
        self._containers = []       # 5 个滚动容器（Flex 布局）
        self._histories  = [[] for _ in range(5)]  # 每个 session 的历史记录（字典列表）
        self._selected   = 0        # 当前选中的选项卡索引
        self._sessions   = []       # 最新的 session 列表（最多 5 个）
        self._blink_tasks = [None] * 5  # 每个 tab 的闪烁 task
        self._disp = None           # 保持 display 对象引用，防止被 GC

    async def init(self):
        """初始化硬件和 LVGL UI"""
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

        # 触摸初始化完成后再设置旋转（CST816S 要求）
        self._disp.set_rotation(lv.DISPLAY_ROTATION._90)
        print("[renderer] rotation set")

        task_handler.TaskHandler(duration=5)
        print("[renderer] task handler started")

    def _build_ui(self):
        scrn = lv.screen_active()
        # 使用默认白色背景

        # ── 创建 5 个选项卡按钮 ──
        for i in range(MAX_SESSIONS):
            btn = lv.button(scrn)
            btn.set_pos(i * TAB_W, 0)
            btn.set_size(TAB_W, TAB_H)
            btn.set_style_bg_color(_C_TAB_IDLE, lv.PART.MAIN)
            btn.set_style_radius(8, lv.PART.MAIN)

            # 绑定点击事件
            btn.add_event_cb(lambda e, idx=i: self._on_tab_click(idx), lv.EVENT.CLICKED, None)

            lbl = lv.label(btn)
            lbl.set_text(f"S{i+1}")
            lbl.center()

            self._tab_btns.append(btn)
            self._tab_labels.append(lbl)

        # ── 创建 5 个滚动容器（Flex 垂直布局）──
        for i in range(MAX_SESSIONS):
            container = lv.obj(scrn)
            container.set_pos(0, TAB_H)
            container.set_size(SCREEN_W, SCREEN_H - TAB_H)

            # 设置 Flex 布局：垂直方向，从上到下
            container.set_flex_flow(lv.FLEX_FLOW.COLUMN)
            container.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)

            # 设置滚动方向：只允许垂直滚动
            container.set_scroll_dir(lv.DIR.VER)

            # 设置内边距
            container.set_style_pad_all(8, lv.PART.MAIN)
            container.set_style_pad_row(4, lv.PART.MAIN)  # 行间距

            container.add_flag(lv.obj.FLAG.HIDDEN)  # 初始隐藏
            self._containers.append(container)

        # 显示第一个容器
        self._containers[0].remove_flag(lv.obj.FLAG.HIDDEN)

        print("[renderer] UI created: 5 tabs + 5 flex containers")

    # ── 事件回调 ──────────────────────────────────────────────

    def _on_tab_click(self, idx):
        if idx != self._selected:
            # 隐藏旧的容器
            self._containers[self._selected].add_flag(lv.obj.FLAG.HIDDEN)
            # 显示新的容器
            self._containers[idx].remove_flag(lv.obj.FLAG.HIDDEN)
            self._selected = idx
            print(f"[tab] switch to Session {idx + 1}")

    # ── 渲染入口 ──────────────────────────────────────────────

    async def render(self, msg):
        if msg is None:
            return
        if isinstance(msg, dict):
            return

        # 提取最多 5 个 session
        self._sessions = msg.sessions[:MAX_SESSIONS]

        # 更新所有选项卡颜色和历史记录
        for i in range(MAX_SESSIONS):
            sess = self._sessions[i] if i < len(self._sessions) else None
            self._update_tab(i, sess)
            if sess:
                self._update_history(i, sess)

    async def on_connect(self):
        self._append_to_current("BLE connected\n")

    async def on_disconnect(self):
        self._append_to_current("BLE disconnected\n")

    # ── 内部更新方法 ──────────────────────────────────────────

    def _update_tab(self, index: int, sess):
        btn = self._tab_btns[index]
        lbl = self._tab_labels[index]

        if sess is None:
            # 无 session：浅灰背景
            btn.set_style_bg_color(_C_TAB_IDLE, lv.PART.MAIN)
            lbl.set_text(f"S{index+1}")
            self._stop_blink(index)
            return

        state = _sess_state(sess)
        tool_text = self._short_tool(index, sess)

        if state == "E":
            btn.set_style_bg_color(_C_TAB_ERR, lv.PART.MAIN)
            lbl.set_text(tool_text)
            self._start_blink(index)
        elif state == "W":
            btn.set_style_bg_color(_C_TAB_WORK, lv.PART.MAIN)
            lbl.set_text(tool_text)
            self._stop_blink(index)
        elif state == "C":
            btn.set_style_bg_color(_C_TAB_CELE, lv.PART.MAIN)
            lbl.set_text(tool_text)
            self._stop_blink(index)
        else:  # I
            btn.set_style_bg_color(_C_TAB_IDLE, lv.PART.MAIN)
            lbl.set_text(f"S{index+1}")
            self._stop_blink(index)

    def _update_history(self, index: int, sess):
        """更新历史记录"""
        state = _sess_state(sess)
        history = self._histories[index]

        # 构造显示文字：有 msg 用 msg，没有则用状态名
        _state_labels = {"W": "Working", "E": "Error", "C": "Done", "I": "Idle"}
        text = sess.msg if sess.msg else _state_labels.get(state, state)
        record = {"msg": text, "state": state}

        # 相同消息 + 相同状态：跳过
        if history and history[-1]["msg"] == text and history[-1]["state"] == state:
            return

        # 相同消息但状态变化（如 W→E）：更新最后一条颜色
        if history and history[-1]["msg"] == text:
            history[-1]["state"] = state
            self._render_container(index)
            return

        # 新消息：追加
        history.append(record)
        if len(history) > 20:
            history.pop(0)
            container = self._containers[index]
            if container.get_child_count() > 0:
                container.get_child(0).delete()

        self._append_message_block(index, record)

    def _append_message_block(self, index: int, record: dict):
        """追加一个消息块到容器"""
        container = self._containers[index]

        # 创建消息块容器
        msg_block = lv.obj(container)
        msg_block.set_width(SCREEN_W - 20)
        msg_block.set_height(lv.SIZE_CONTENT)  # 高度自适应内容
        msg_block.set_style_pad_all(8, lv.PART.MAIN)
        msg_block.set_style_radius(6, lv.PART.MAIN)

        # 根据状态设置背景色
        state = record["state"]
        if state == "E":
            msg_block.set_style_bg_color(_C_BG_ERROR, lv.PART.MAIN)
        elif state == "C":
            msg_block.set_style_bg_color(_C_BG_SUCCESS, lv.PART.MAIN)
        elif state == "W":
            msg_block.set_style_bg_color(_C_BG_NORMAL, lv.PART.MAIN)
        else:  # I
            msg_block.set_style_bg_color(_C_BG_IDLE, lv.PART.MAIN)

        # 创建文字 label（白色）
        label = lv.label(msg_block)
        label.set_text(record["msg"])
        label.set_style_text_color(_C_TEXT_WHITE, lv.PART.MAIN)
        label.set_width(SCREEN_W - 40)  # 留出消息块的内边距

        # 滚动到底部
        container.scroll_to_y(9999, 0)

    def _render_container(self, index: int):
        """重新渲染整个容器（用于状态更新）"""
        container = self._containers[index]

        # 清空容器
        container.clean()

        # 重新创建所有消息块
        for record in self._histories[index]:
            self._append_message_block(index, record)

    # ── 闪烁控制 ──────────────────────────────────────────────

    def _start_blink(self, index: int):
        if self._blink_tasks[index] is not None:
            return
        task = asyncio.create_task(self._blink_loop(index))
        self._blink_tasks[index] = task

    def _stop_blink(self, index: int):
        task = self._blink_tasks[index]
        if task is not None:
            task.cancel()
            self._blink_tasks[index] = None
        # 恢复红色（停止时确保颜色正确）
        if index < len(self._sessions) and self._sessions[index] and _sess_state(self._sessions[index]) == "E":
            self._tab_btns[index].set_style_bg_color(_C_TAB_ERR, lv.PART.MAIN)

    async def _blink_loop(self, index: int):
        btn = self._tab_btns[index]
        toggle = False
        try:
            while True:
                color = _C_TAB_ERR2 if toggle else _C_TAB_ERR
                btn.set_style_bg_color(color, lv.PART.MAIN)
                toggle = not toggle
                await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            pass

    # ── 工具名截断 ────────────────────────────────────────────

    def _append_to_current(self, text: str):
        """追加文本到当前选中的容器"""
        idx = self._selected
        record = {"msg": text.strip(), "state": "I"}
        self._histories[idx].append(record)
        if len(self._histories[idx]) > 20:
            self._histories[idx].pop(0)
            container = self._containers[idx]
            if container.get_child_count() > 0:
                container.get_child(0).delete()
        self._append_message_block(idx, record)

    @staticmethod
    def _short_tool(index: int, sess) -> str:
        if not sess or not sess.msg:
            return f"S{index+1}"
        msg = sess.msg
        colon = msg.find(":")
        name = msg[:colon] if colon > 0 else msg
        return name[:6]
