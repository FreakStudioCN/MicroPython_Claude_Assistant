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
# PC → 设备（心跳）：
#   {"cmd": "ping", "ts": 1234567890.123}
#
# 设备 → PC（心跳响应）：
#   {"ack": "pong", "ts": 1234567890.456}
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


class SessionStatus:
    """v4 wire 中单个 session 的状态（从 s 字段推导所有属性）。"""
    def __init__(self, d: dict):
        s = d.get("s", "I")
        self.name        = d.get("n", "?")
        self.running     = 1 if s == "W" else 0
        self.waiting     = 1 if s == "P" else 0
        self.completed   = s == "C"
        self.error       = "!" if s == "E" else ""
        self.interrupted = False
        self.msg         = d.get("m", "")
        self.prompt      = {"tool": d.get("t", ""), "hint": d.get("h", ""), "id": ""} if s == "P" else None


class MultiSessionMsg:
    """v4 wire 消息：包含所有活跃 session 的状态数组。"""
    def __init__(self, sessions: list):
        self.sessions = [SessionStatus(s) for s in sessions]


def parse(line: str):
    """解析 BLE 收到的一行 JSON 文本。"""
    try:
        d = ujson.loads(line)
    except Exception:
        return None

    if "cmd" in d:
        return d

    if "ss" in d:
        return MultiSessionMsg(d["ss"])

    return None


def build_decision(session_idx: int, decision: str) -> str:
    return ujson.dumps({"d": decision, "n": session_idx}) + "\n"


def build_ack(cmd: str, ok=True) -> str:
    return ujson.dumps({"ack": cmd, "ok": ok}) + "\n"
