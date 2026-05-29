# ============================================================
# state.py —— 设备状态枚举与状态转换逻辑
#
# 职责：定义角色/动画状态常量，封装状态转换条件判断。
# protocol.py 只负责 wire 契约；状态机逻辑全部在这里。
# ============================================================

# ── 角色/动画状态枚举 ─────────────────────────────────────────
# 整数值同时作为 buddies.py 字典的 key，与动画帧一一对应。
SLEEP     = 0   # 休眠：长时间无活动
IDLE      = 1   # 空闲：已连接但 Claude 无任务
WORKING   = 2   # 执行中：Claude 正在运行工具
PENDING   = 3   # 待审批：有工具在等待审批
CELEBRATE = 4   # 完成庆祝：任务刚刚完成（短暂覆盖）
ERROR     = 5   # 出错：工具执行失败或 API 超时
APPROVED  = 6   # 已批准：用户刚刚按下批准按钮（短暂覆盖）

# 状态名称列表，下标与枚举对应，用于屏幕显示
STATE_NAMES = ["sleep", "idle", "working", "pending", "celebrate", "error", "approved"]

# ── wire 协议状态码 ───────────────────────────────────────────
S_IDLE    = "I"
S_WORKING = "W"
S_PENDING = "P"
S_DONE    = "C"
S_ERROR   = "E"


# ── 状态转换事件判断器 ─────────────────────────────────────────
class StateEvent:
    """
    状态转换触发器，封装所有状态转换的条件判断逻辑。
    接受 StatusMsg 或 SessionStatus（字段名相同，直接复用）。
    """

    @staticmethod
    def should_celebrate(msg) -> bool:
        """判断是否应触发完成庆祝动画（CELEBRATE 覆盖 2-3s）"""
        return msg.completed

    @staticmethod
    def should_show_error(msg) -> bool:
        """判断是否应显示错误状态（ERROR 覆盖 3s）"""
        return bool(msg.error) and not msg.interrupted

    @staticmethod
    def should_skip_error(msg) -> bool:
        """判断是否应跳过错误显示（用户主动中断，直接回 IDLE）"""
        return msg.interrupted and bool(msg.error)

    @staticmethod
    def get_base_state(msg) -> int:
        """
        根据 running/waiting 计算基础状态。
        优先级：PENDING > WORKING > IDLE
        """
        if msg.waiting > 0:
            return PENDING
        elif msg.running > 0:
            return WORKING
        else:
            return IDLE

    @staticmethod
    def needs_approval(msg) -> bool:
        """判断是否有待审批的工具（prompt 非空）"""
        return msg.prompt is not None

    @staticmethod
    def is_idle(msg) -> bool:
        """判断是否完全空闲（无运行、无等待）"""
        return msg.running == 0 and msg.waiting == 0


def sess_state(sess) -> str:
    """将 SessionStatus 映射为 wire 状态码（E/P/W/C/I）"""
    if sess.error:      return S_ERROR
    if sess.waiting:    return S_PENDING
    if sess.running:    return S_WORKING
    if sess.completed:  return S_DONE
    return S_IDLE


def dominant_state(sessions) -> str:
    """从 session 列表中取优先级最高的状态（E > P > W > C > I）"""
    states = [sess_state(s) for s in sessions] if sessions else []
    for s in (S_ERROR, S_PENDING, S_WORKING, S_DONE):
        if s in states:
            return s
    return S_IDLE


def sticky_dominant(current, last) -> str:
    """粘滞守卫：C/P 不被 I 覆盖，等待 W/E 明确到来"""
    if current == S_IDLE and last in (S_DONE, S_PENDING):
        return last
    return current
