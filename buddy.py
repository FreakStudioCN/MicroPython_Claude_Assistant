# ============================================================
# buddy.py —— ASCII 角色动画控制器
#
# 维护当前选中的角色（species）和帧索引，
# 每 500 ms 自动推进到下一帧，实现帧动画效果。
# 通过 tick() 函数驱动，由 render_task 每帧调用。
# ============================================================

import time
from buddies import SPECIES          # 所有角色的帧数据字典列表
from protocol import IDLE, STATE_NAMES  # 状态枚举和名称

# ── 模块级动画状态（全局变量）────────────────────────────────
_species_idx = 0      # 当前角色索引（0=猫, 1=机器人, 2=鸭子）
_frame_idx   = 0      # 当前帧索引（在该状态的帧列表中循环）
_last_frame  = 0      # 上一次切换帧的时间戳（ticks_ms，毫秒）
FRAME_MS     = 500    # 帧间隔：每 500 ms 切换到下一帧


def set_species(idx: int):
    """
    切换当前显示的角色。
    idx 会自动取模，确保不越界。
    切换角色时重置帧索引到第 0 帧，避免新角色帧数不足时越界。

    调用时机：暂未在主流程中调用，预留给未来的角色切换功能
    （如通过触摸屏或按钮 B 循环切换角色）。
    """
    global _species_idx, _frame_idx
    _species_idx = idx % len(SPECIES)
    _frame_idx = 0


def tick(screen, state: int, msg: str, connected: bool):
    """
    每帧调用一次，完成帧推进 + 屏幕绘制。

    参数：
      screen    — Screen 对象（display.py），提供 draw_buddy() 方法
      state     — 当前动画状态（来自 State.active），决定播放哪组帧
      msg       — 屏幕底部显示的文字（来自最新 StatusMsg.msg）
      connected — BLE 是否已连接，用于右上角状态指示灯颜色

    工作流程：
      1. 获取当前角色在当前状态下的帧列表
         （若该状态无对应帧，回退到 IDLE 帧，保证不崩溃）
      2. 检查距上次切帧是否已过 FRAME_MS 毫秒
      3. 若是，frame_idx + 1（循环）
      4. 调用 screen.draw_buddy() 渲染当前帧
    """
    global _frame_idx, _last_frame

    now = time.ticks_ms()

    frames = SPECIES[_species_idx]["frames"].get(
        state,
        SPECIES[_species_idx]["frames"][IDLE]
    )

    # 重置帧索引防止越界（状态切换后帧数可能变少）
    if _frame_idx >= len(frames):
        _frame_idx = 0

    if time.ticks_diff(now, _last_frame) >= FRAME_MS:
        _frame_idx = (_frame_idx + 1) % len(frames)
        _last_frame = now

    screen.draw_buddy(frames[_frame_idx], STATE_NAMES[state], msg, connected)
