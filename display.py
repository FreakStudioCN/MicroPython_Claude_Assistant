# ============================================================
# display.py —— 显示屏与触摸屏初始化 + 屏幕绘制封装
#
# 使用的库：
#   - lcd_bus / st7789：SPI 显示屏底层驱动（MicroPython 专用）
#   - lvgl（lv）：嵌入式 GUI 框架，提供 label、样式等控件
#   - i2c / cst816s：I2C 触摸屏驱动
#   - task_handler：LVGL 定时刷新任务（每 5 ms 调用一次 lv.task_handler）
#
# 注意：所有硬件初始化代码在模块级（import 时）立即执行，
# 保证 Screen 类实例化时显示屏已就绪。
# ============================================================

import machine
import lcd_bus
import lvgl as lv
import st7789
import i2c
import cst816s
import task_handler
from micropython import const

# DMA 帧缓冲区大小：320×240 像素，RGB565 格式每像素 2 字节
# 此处只分配半屏（320×240÷2 = 38400字节 → 28800字节≈3/4屏，节省内存用双缓冲）
BUFFER_SIZE = const(28800)

# ── 显示屏硬件初始化 ─────────────────────────────────────────
# 创建 SPI 总线（host=2 即 SPI2/HSPI）
spi_bus = machine.SPI.Bus(host=2, mosi=38, miso=40, sck=39)

# 创建 SPI 显示总线，绑定 DC（数据/命令）和 CS（片选）引脚
display_bus = lcd_bus.SPIBus(spi_bus=spi_bus, freq=40000000, dc=42, cs=45)

# 分配两块 DMA 帧缓冲区（双缓冲：一块用于渲染，一块用于传输，交替使用）
# MEMORY_INTERNAL | MEMORY_DMA：使用内部 SRAM 并启用 DMA 传输（减少 CPU 占用）
fb1 = display_bus.allocate_framebuffer(BUFFER_SIZE, lcd_bus.MEMORY_INTERNAL | lcd_bus.MEMORY_DMA)
fb2 = display_bus.allocate_framebuffer(BUFFER_SIZE, lcd_bus.MEMORY_INTERNAL | lcd_bus.MEMORY_DMA)

# 初始化 ST7789 显示驱动
# display_width/height 是面板物理分辨率（240×320，竖屏）
# color_byte_order=BGR、rgb565_byte_swap=True 是 ST7789 的硬件特性，必须配置否则颜色错误
_disp = st7789.ST7789(
    data_bus=display_bus,
    frame_buffer1=fb1,
    frame_buffer2=fb2,
    display_width=240,
    display_height=320,
    color_space=lv.COLOR_FORMAT.RGB565,
    color_byte_order=st7789.BYTE_ORDER_BGR,
    rgb565_byte_swap=True,
    backlight_pin=1,
    backlight_on_state=st7789.STATE_PWM,  # 使用 PWM 控制背光亮度
)
_disp.init()                        # 发送初始化命令序列
_disp.set_power(True)               # 开启显示
_disp.set_backlight(100)            # 背光亮度 100%

# ── 触摸屏硬件初始化 ─────────────────────────────────────────
# 创建 I2C 总线
i2c_bus = i2c.I2C.Bus(host=0, scl=47, sda=48, freq=400000, use_locks=False)

# 创建 CST816S 触摸屏 I2C 设备
touch_dev = i2c.I2C.Device(bus=i2c_bus, dev_id=0x15, reg_bits=8)

# 初始化 CST816S 驱动，startup_rotation=_180 修正触摸坐标方向
cst816s.CST816S(touch_dev, startup_rotation=lv.DISPLAY_ROTATION._180)

# 将显示旋转 90°（将竖屏变为横屏 320×240 使用）
_disp.set_rotation(lv.DISPLAY_ROTATION._90)

# 启动 LVGL 任务处理器，每 5 ms 执行一次 lv.task_handler()
# 负责处理触摸事件、动画、重绘脏区域等 LVGL 内部定时任务
task_handler.TaskHandler(duration=5)


# ── Screen 类 ────────────────────────────────────────────────
class Screen:
    """
    封装所有 LVGL 控件，提供两种屏幕模式的绘制接口：
      1. draw_buddy()    —— 正常模式：显示角色动画 + 状态 + BLE 指示
      2. draw_approval() —— 审批模式：黄色警告界面 + 倒计时
    """

    def __init__(self):
        scrn = lv.screen_active()  # 获取当前活跃屏幕对象

        # 设置背景为纯黑
        scrn.set_style_bg_color(lv.color_hex(0x000000), lv.PART.MAIN)

        # ── 角色动画 label（居中显示）──────────────────────────
        self._buddy = lv.label(scrn)
        self._buddy.set_style_text_color(lv.color_hex(0xFFFFFF), lv.PART.MAIN)  # 白色文字
        self._buddy.set_style_text_font(lv.font_montserrat_16, lv.PART.MAIN)    # 16pt 等宽感字体
        self._buddy.set_width(320)                                                # 占满全宽
        self._buddy.set_style_text_align(lv.TEXT_ALIGN.CENTER, lv.PART.MAIN)    # 文字居中
        self._buddy.align(lv.ALIGN.CENTER, 0, -10)                               # 整体向上偏移 10px

        # ── BLE 连接状态 label（右上角）────────────────────────
        self._ble = lv.label(scrn)
        self._ble.set_style_text_font(lv.font_montserrat_14, lv.PART.MAIN)
        self._ble.align(lv.ALIGN.TOP_RIGHT, -5, 5)   # 距右边 5px，距顶部 5px

        # ── 底部消息 label（左下角）────────────────────────────
        self._msg = lv.label(scrn)
        self._msg.set_style_text_color(lv.color_hex(0xAAAAAA), lv.PART.MAIN)
        self._msg.set_style_text_font(lv.font_montserrat_14, lv.PART.MAIN)
        self._msg.set_width(310)
        self._msg.align(lv.ALIGN.BOTTOM_LEFT, 5, -5)

        # ── 审批 YES 按钮（右下角，审批模式时显示）──────────────
        self._btn = lv.button(scrn)
        self._btn.set_size(80, 40)
        self._btn.align(lv.ALIGN.BOTTOM_RIGHT, -5, -5)
        self._btn.set_style_bg_color(lv.color_hex(0x00AA00), lv.PART.MAIN)
        btn_label = lv.label(self._btn)
        btn_label.set_text("YES")
        btn_label.center()
        self._btn.add_flag(lv.obj.FLAG.HIDDEN)

    def draw_buddy(self, lines: list, state_name: str, msg: str, connected: bool):
        """
        渲染正常模式（角色动画界面）。

        参数：
          lines       — ASCII 帧（字符串列表），每个元素为一行
          state_name  — 当前状态名（"idle"/"busy" 等），msg 为空时显示此内容
          msg         — 底部说明文字（来自 PC 的 msg 字段）
          connected   — BLE 连接状态，True→绿色"BLE"，False→红色"---"
        """
        # 恢复黑色背景（从审批模式切回时重置）
        lv.screen_active().set_style_bg_color(lv.color_hex(0x000000), lv.PART.MAIN)
        # 恢复白色角色文字（从审批模式的黄色切回）
        self._buddy.set_style_text_color(lv.color_hex(0xFFFFFF), lv.PART.MAIN)

        # 将帧的行列表用换行符拼合，设置为 label 文字
        self._buddy.set_text("\n".join(lines))

        # BLE 状态指示：已连接→绿色"BLE"，未连接→红色"---"
        self._ble.set_text("BLE" if connected else "---")
        self._ble.set_style_text_color(
            lv.color_hex(0x00FF00) if connected else lv.color_hex(0xFF4444),
            lv.PART.MAIN
        )

        self._msg.set_text((msg or state_name.upper())[:40])
        self._btn.add_flag(lv.obj.FLAG.HIDDEN)

    def set_approval_cb(self, cb):
        self._btn.add_event_cb(cb, lv.EVENT.PRESSED, None)

    def draw_approval(self, tool: str, hint: str, secs_left: int):
        """
        渲染审批模式（黄色警告界面）。
        当 hook_bridge.py 发来含 prompt 字段的消息时切换到此模式。

        参数：
          tool      — 需要审批的工具名（如 "Bash"、"Write"）
          hint      — 工具调用的简短说明（如命令行内容或文件路径）
          secs_left — 剩余等待秒数（倒计时，从 30 递减到 0）

        视觉设计：
          - 背景改为深黄色（0x1a1a00），与正常模式形成对比，吸引用户注意
          - 文字改为亮黄色（0xFFFF00），醒目
          - hint 超过 32 字符时自动换行到第二行显示
          - 底部显示操作提示：A=yes B=no 和倒计时秒数
        """
        # 深黄色背景，强调这是需要操作的重要界面
        lv.screen_active().set_style_bg_color(lv.color_hex(0x1a1a00), lv.PART.MAIN)
        # 亮黄色文字
        self._buddy.set_style_text_color(lv.color_hex(0xFFFF00), lv.PART.MAIN)

        # 显示审批内容：工具名 + hint（每 32 字符换行，最多显示 64 字符）
        self._buddy.set_text(
            "APPROVE?\n\n{}\n\n{}\n{}".format(tool, hint[:32], hint[32:64])
        )

        self._msg.set_text("tap YES  {}s".format(secs_left))
        self._btn.remove_flag(lv.obj.FLAG.HIDDEN)
