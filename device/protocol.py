# ============================================================
# protocol.py —— PC ↔ ESP32 消息协议定义（wire 契约）
#
# 所有消息均为换行符结尾的 JSON 字符串，通过 BLE NUS 传输。
#
# PC → 设备（v3 多 session wire）：
#   {"v": 2, "sessions": [
#     {"id": "sess1234", "running": 1, "waiting": 0, "completed": false,
#      "msg": "Bash: ls", "category": "exec", "error": "", "interrupted": false,
#      "prompt": {"id": "toolu_xxx", "tool": "Bash", "hint": "命令内容"}}
#   ]}
#   sessions 数组只含活跃 session（有工具运行，或近 10s 内有活动）。
#   prompt 字段非 null 时表示该 session 有工具等待审批。
#
# 设备 → PC（审批决策）：
#   {"cmd": "permission", "id": "toolu_xxx", "decision": "once"}
#   id 字段为真实 tool_use_id，由 PC 端 prompt.id 提供
#
# PC → 设备（控制命令）：
#   {"cmd": "name"/"owner"/"unpair"}
#
# 设备 → PC（命令应答）：
#   {"ack": "name", "ok": true}
#
# 向后兼容：parse() 仍能解析旧 v2 单 session wire（无 sessions 字段）返回 StatusMsg
#
# 状态枚举与状态转换逻辑见 state.py
# ============================================================

try:
    import ujson  # MicroPython 内置的轻量级 JSON 库（比标准 json 省内存）
except ImportError:
    import json as ujson  # PC 端测试时回退到标准 json 库


class StatusMsg:
    """封装从 PC 端收到的 v2 wire 状态消息（9 字段）。"""
    def __init__(self, d: dict):
        # 当前正在执行的工具数量（>0 表示 WORKING 状态）
        self.running   = d.get("running", 0)
        # 等待审批的工具数量（>0 表示 PENDING 状态）
        self.waiting   = d.get("waiting", 0)
        # 任务是否刚完成（True 时触发 CELEBRATE 动画）
        self.completed = d.get("completed", False)
        # 显示在屏幕底部的简短说明文字
        self.msg       = d.get("msg", "")
        # 本次消耗的 token 数（当前版本仅接收，暂不显示）
        self.tokens    = d.get("tokens", 0)
        # 审批请求字典，格式：{"id": str, "tool": str, "hint": str}
        # 为 None 时表示无需审批
        self.prompt      = d.get("prompt")
        # 当前工具类别：exec/edit/read/web/agent/other/""
        self.category    = d.get("category", "")
        # 最近一次错误原文（截断 80 字），ERROR 状态下显示
        self.error       = d.get("error", "")
        # True = 用户主动 Ctrl+C 中断，设备跳过 ERROR 直接回 IDLE
        self.interrupted = d.get("interrupted", False)


class SessionStatus:
    """v3 wire 中单个 session 的状态字段（与 StatusMsg 字段名相同，多了 id）。"""
    def __init__(self, d: dict):
        self.id          = d.get("id", "")
        self.running     = d.get("running", 0)
        self.waiting     = d.get("waiting", 0)
        self.completed   = d.get("completed", False)
        self.msg         = d.get("msg", "")
        self.prompt      = d.get("prompt")
        self.category    = d.get("category", "")
        self.error       = d.get("error", "")
        self.interrupted = d.get("interrupted", False)


class MultiSessionMsg:
    """v3 wire 消息：包含所有活跃 session 的状态数组。"""
    def __init__(self, sessions: list):
        self.sessions = [SessionStatus(s) for s in sessions]


def parse(line: str):
    """
    解析 BLE 收到的一行 JSON 文本。

    返回值：
      - MultiSessionMsg 对象：v3 多 session 状态消息（含 "sessions" 字段）
      - StatusMsg 对象：旧 v2 单 session 状态消息（向后兼容）
      - dict：控制命令（含 "cmd" 字段，如 "name"/"owner"/"unpair"）
      - None：JSON 解析失败（忽略该行）
    """
    try:
        d = ujson.loads(line)
    except Exception:
        return None

    if "cmd" in d:
        return d

    if "sessions" in d:
        return MultiSessionMsg(d["sessions"])

    # 旧 v2 wire 向后兼容
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
