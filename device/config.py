# ============================================================
# config.py —— 硬件引脚与全局常量配置
# 目标板：ESP32-S3 触摸屏（ST7789 + CST816S，320×240 横屏）
# ============================================================

# ── 显示屏参数（ST7789 驱动，SPI 接口）──────────────────────
LCD_WIDTH  = 240        # 物理宽度（竖屏方向，旋转后变高度）
LCD_HEIGHT = 320        # 物理高度（竖屏方向，旋转后变宽度）
SCREEN_W   = 320        # 逻辑宽度（横屏后）
SCREEN_H   = 240        # 逻辑高度（横屏后）
SPI_BUS    = 2

SPI_FREQ   = 40_000_000

LCD_SCLK   = 39
LCD_MOSI   = 38
LCD_MISO   = 40
LCD_DC     = 42
LCD_CS     = 45
LCD_BL     = 1

# 帧缓冲大小：28800 字节（和测试代码一致）
FB_SIZE    = 28800

# ── 触摸屏参数（CST816S 驱动，I2C 接口）──────────────────────
I2C_BUS    = 0
I2C_FREQ   = 400_000
TP_SDA     = 48
TP_SCL     = 47
TP_ADDR    = 0x15
TP_REGBITS = 8

# ── 蓝牙低功耗（BLE）配置 ────────────────────────────────────
BLE_NAME    = "Claude-Buddy"                        # BLE 广播名称，PC 端用此名搜索设备

# Nordic UART Service（NUS）是一种模拟串口的 BLE GATT 服务，
# 广泛用于微控制器 BLE 透传场景。
NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"  # NUS 服务 UUID
NUS_RX      = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # RX 特征：PC → 设备（write）
NUS_TX      = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # TX 特征：设备 → PC（notify）

# ── 时序 ────────────────────────────────────────────────────
FPS               = 20  # 渲染帧率（帧/秒），每帧间隔 50 ms
HEARTBEAT_TIMEOUT = 30  # 若 30 秒内未收到 PC 消息，认为连接超时
