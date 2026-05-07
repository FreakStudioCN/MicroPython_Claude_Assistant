"""
渲染器抽象基类

定义设备端渲染接口，支持多种硬件实现：
- DisplayRenderer: LVGL 屏幕渲染（状态面板）
- LightRenderer: LED 灯光渲染（闹钟灯光）

所有渲染器共用相同的通信栈（transport/protocol/state/queue），
只有渲染层不同，实现硬件解耦。
"""


class BaseRenderer:
    """渲染器基类，定义所有渲染器必须实现的接口"""

    async def init(self):
        """
        初始化硬件

        在主循环启动前调用一次，用于：
        - LVGL 初始化
        - LED GPIO 配置
        - 显示欢迎画面
        """
        pass

    async def render(self, msg):
        """
        渲染新消息

        Args:
            msg: 可能是以下类型之一：
                - MultiSessionMsg: 包含多个 session 状态
                - dict: ack/cmd 消息（通常忽略）
                - None: 空消息（跳过）

        状态码：
            I (IDLE): 空闲
            W (WORKING): 工作中，msg 字段包含工具名称
            E (ERROR): 错误
            C (CELEBRATE): 完成庆祝

        多 session 处理：
            - 状态面板：显示最多 3 个 session（优先级排序）
            - 闹钟灯光：只显示一个状态（E > W > C > I）
        """
        pass

    async def on_connect(self):
        """
        BLE 连接成功时调用

        可选实现：
        - 显示连接图标
        - 播放连接音效
        - 点亮指示灯
        """
        pass

    async def on_disconnect(self):
        """
        BLE 断开时调用

        可选实现：
        - 显示断开图标
        - 熄灭屏幕
        - 关闭指示灯
        """
        pass
