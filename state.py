# ============================================================
# state.py —— 设备全局状态机
#
# 负责将 PC 推送的状态消息转换为动画播放状态，
# 并管理"短暂覆盖"（临时状态，几秒后自动恢复基础状态）。
#
# 状态优先级（从高到低）：
#   覆盖状态（CELEBRATE / HEART / DIZZY） > 基础状态（IDLE/BUSY/ATTENTION）
# ============================================================

import time
from protocol import SLEEP, IDLE, BUSY, ATTENTION, CELEBRATE, DIZZY, HEART, StatusMsg


class State:
    """
    设备状态机。

    属性说明：
      base            — 由最新 StatusMsg 决定的"持久"基础状态
      active          — 当前实际播放的状态（含短暂覆盖）
      _override_until — 覆盖状态的到期时间戳（秒），过期后恢复 base
      pending_prompt  — 待审批的 prompt 字典，None 表示无需审批
      last_update     — 最后一次收到 PC 消息的时间戳，用于超时检测
    """

    def __init__(self):
        self.base            = IDLE          # 初始基础状态：待机
        self.active          = IDLE          # 初始显示状态：待机
        self._override_until = 0             # 0 表示当前无覆盖
        self.pending_prompt  = None          # 无待审批请求
        self.last_update     = time.time()   # 记录启动时间

    def update(self, msg: StatusMsg):
        """
        处理从 PC 收到的新状态消息，更新基础状态和审批请求。

        调用时机：ble_task 每次收到完整 JSON 行后调用。
        """
        # 刷新"最后活跃"时间戳，防止误判超时
        self.last_update = time.time()

        # 保存审批请求（None = 无需审批，dict = 需要显示审批界面）
        self.pending_prompt = msg.prompt

        # 任务完成 → 触发庆祝动画 3 秒
        if msg.completed:
            self._set_override(CELEBRATE, 3)

        # 根据运行/等待数量确定基础状态
        if msg.waiting > 0:
            # 有工具正在等待用户审批 → 高优先级注意状态
            self.base = ATTENTION
        elif msg.running > 0:
            # 有工具正在执行 → 忙碌状态
            self.base = BUSY
        else:
            # 无任何活动 → 待机
            self.base = IDLE

        # 立即刷新 active（让覆盖到期检查生效）
        self._tick()

    def set_dizzy(self):
        """触发"晕眩"动画 2 秒（保留接口，暂未在主流程中调用）。"""
        self._set_override(DIZZY, 2)

    def set_heart(self):
        """
        触发"爱心"动画 2 秒。
        在用户按下按钮批准工具请求后由 button_task 调用，
        给用户一个视觉反馈：按钮已响应。
        """
        self._set_override(HEART, 2)

    def tick(self):
        """
        每帧调用一次，检查覆盖状态是否到期并切换回基础状态。
        由 render_task 的渲染循环调用。
        """
        self._tick()

    def timed_out(self) -> bool:
        """
        检查是否超过 30 秒未收到 PC 消息。
        超时通常意味着 BLE 已断开或 Claude 进程已退出。
        （当前版本中此方法已定义但主循环暂未使用）
        """
        return (time.time() - self.last_update) > 30

    # ── 内部方法 ──────────────────────────────────────────────

    def _set_override(self, s: int, secs: float):
        """
        设置一个持续 secs 秒的临时覆盖状态。
        覆盖期间 active = s，到期后 _tick() 会自动还原为 base。
        """
        self.active = s
        self._override_until = time.time() + secs

    def _tick(self):
        """
        检查覆盖是否到期：
          - 未到期 → 保持 active 不变（覆盖状态继续播放）
          - 已到期 → active 恢复为 base（回到基础状态）
        """
        if time.time() < self._override_until:
            return          # 覆盖仍有效，直接返回
        self.active = self.base  # 覆盖到期，恢复基础状态
