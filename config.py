# ============================================================
# config.py —— 硬件引脚与全局常量配置
# 目标板：Waveshare ESP32-S3-Touch-LCD-2（320×240 彩色触摸屏）
# ============================================================

# ── 显示屏参数（ST7789 驱动，SPI 接口）──────────────────────
LCD_WIDTH  = 320        # 屏幕宽度（像素）
LCD_HEIGHT = 240        # 屏幕高度（像素）
SPI_BUS    = 2          # 使用 ESP32-S3 的 SPI2 总线（HSPI）

SPI_FREQ   = 40_000_000 # SPI 时钟频率：40 MHz（ST7789 最高支持 80 MHz）

# SPI 信号线 GPIO 编号
LCD_SCLK   = 39        # SPI 时钟线（SCLK / CLK）
LCD_MOSI   = 38        # SPI 数据输出（MOSI / DIN）
LCD_MISO   = 40        # SPI 数据输入（MISO，此屏通常不用但需占用）
LCD_DC     = 42        # 数据/命令选择线（D/C，高=数据，低=命令）
LCD_CS     = 45        # 片选信号（低电平有效）
LCD_BL     = 1         # 背光控制（PWM 输出，控制亮度）

# ── 触摸屏参数（CST816S 驱动，I2C 接口）──────────────────────
I2C_BUS  = 0           # 使用 ESP32-S3 的 I2C0 总线
I2C_FREQ = 400_000     # I2C 时钟频率：400 kHz（Fast Mode）
TP_SDA   = 48          # I2C 数据线（SDA）
TP_SCL   = 47          # I2C 时钟线（SCL）
TP_ADDR  = 0x15        # CST816S 的 I2C 从设备地址（固定为 0x15）

# ── 按钮 ────────────────────────────────────────────────────
BTN_A = 0              # GPIO 0 即板载 BOOT 按钮，用作"审批/确认"键
                       # 按下 = 低电平（内部上拉，PULL_UP）

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
