# ============================================================
# protocol.py —— PC ↔ ESP32 消息协议定义
#
# 所有消息均为换行符结尾的 JSON 字符串，通过 BLE NUS 传输。
#
# PC → 设备（状态推送）：
#   {"running": 1, "waiting": 0, "completed": false,
#    "msg": "显示文字", "tokens": 0,
#    "prompt": {"id": "xxx", "tool": "Bash", "hint": "命令内容"}}
#   其中 prompt 字段存在时表示需要用户审批。
#
# 设备 → PC（审批决策）：
#   {"cmd": "permission", "id": "cli-req", "decision": "once"}
#
# PC → 设备（控制命令）：
#   {"cmd": "name"/"owner"/"unpair"}
#
# 设备 → PC（命令应答）：
#   {"ack": "name", "ok": true}
# ============================================================

import ujson  # MicroPython 内置的轻量级 JSON 库（比标准 json 省内存）

# ── 角色/动画状态枚举 ─────────────────────────────────────────
# 这些整数值同时作为 buddies.py 字典的 key，与动画帧一一对应。
SLEEP     = 0   # 睡眠：长时间无活动
IDLE      = 1   # 待机：已连接但 Claude 无任务
BUSY      = 2   # 忙碌：Claude 正在运行工具
ATTENTION = 3   # 注意：有工具在等待审批
CELEBRATE = 4   # 庆祝：任务刚刚完成（短暂覆盖）
DIZZY     = 5   # 晕眩：保留状态，暂未使用
HEART     = 6   # 爱心：用户刚刚按下批准按钮（短暂覆盖）

# 状态名称映射表，下标与上面枚举对应，用于在屏幕上显示文字
STATE_NAMES = ["sleep", "idle", "busy", "attention", "celebrate", "dizzy", "heart"]


class StatusMsg:
    """
    封装从 PC 端收到的状态消息。
    PC 的 hook_bridge.py 会在以下时机发送此类消息：
      - PreToolUse：工具开始前（running/waiting 字段变化）
      - PostToolUse：工具结束后
      - Stop：Claude 完成整个任务
    """
    def __init__(self, d: dict):
        # 当前正在执行的工具数量（>0 表示 BUSY 状态）
        self.running   = d.get("running", 0)
        # 等待审批的工具数量（>0 表示 ATTENTION 状态）
        self.waiting   = d.get("waiting", 0)
        # 任务是否刚完成（True 时触发 CELEBRATE 动画）
        self.completed = d.get("completed", False)
        # 显示在屏幕底部的简短说明文字
        self.msg       = d.get("msg", "")
        # 本次消耗的 token 数（当前版本仅接收，暂不显示）
        self.tokens    = d.get("tokens", 0)
        # 审批请求字典，格式：{"id": str, "tool": str, "hint": str}
        # 为 None 时表示无需审批
        self.prompt    = d.get("prompt")


def parse(line: str):
    """
    解析 BLE 收到的一行 JSON 文本。

    返回值：
      - StatusMsg 对象：普通状态消息（无 "cmd" 字段）
      - dict：控制命令（含 "cmd" 字段，如 "name"/"owner"/"unpair"）
      - None：JSON 解析失败（忽略该行）
    """
    try:
        d = ujson.loads(line)
    except Exception:
        # 收到非法 JSON（如 BLE 分包粘包导致的乱码），直接丢弃
        return None

    if "cmd" in d:
        # 含 cmd 字段 → 控制命令，原样返回字典交给上层处理
        return d

    # 普通状态消息 → 封装为 StatusMsg
    return StatusMsg(d)


def build_decision(prompt_id: str, decision: str) -> str:
    """
    构建"审批决策"消息，发给 PC 端的 hook_bridge.py。

    参数：
      prompt_id  — 与请求中的 id 对应，用于 PC 端匹配
      decision   — "once" 表示批准本次，"deny" 表示拒绝

    返回以换行符结尾的 JSON 字符串（BLE 传输的消息边界）。
    """
    return ujson.dumps({"cmd": "permission", "id": prompt_id, "decision": decision}) + "\n"


def build_ack(cmd: str, ok=True) -> str:
    """
    构建命令应答消息，回复 PC 端的控制命令（name/owner/unpair）。

    参数：
      cmd — 被应答的命令名
      ok  — 执行是否成功
    """
    return ujson.dumps({"ack": cmd, "ok": ok}) + "\n"
