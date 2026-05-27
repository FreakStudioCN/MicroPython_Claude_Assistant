# ============================================================
# protocol.py —— PC ↔ ESP32 消息协议定义（wire 契约）
#
# 所有消息均为换行符结尾的 JSON 字符串，通过 BLE NUS 传输。
#
# PC → 设备（v6 多 session wire）：
#   {"ss": [{"n": "proj", "s": "W", "m": "Bash: ls", "slot": "cd501167"}]}
#   ss 数组只含活跃 session（有工具运行，或近 10s 内有活动）。
#   s 字段：I=空闲 / W=执行中 / P=待审批提醒 / C=完成 / E=出错
#   m 字段：工具名或状态描述（可选）
#   slot 字段：session 唯一标识（SID 后 8 位），用于槽位稳定映射
#
# PC → 设备（控制命令）：
#   {"cmd": "name"/"owner"/"unpair"}
#
# 设备 → PC（命令应答）：
#   {"ack": "name", "ok": true}
#
# 状态枚举与状态转换逻辑见 state.py
# ============================================================

try:
    import ujson  # MicroPython 内置的轻量级 JSON 库（比标准 json 省内存）
except ImportError:
    import json as ujson  # PC 端测试时回退到标准 json 库

from state import S_IDLE, S_WORKING, S_PENDING, S_DONE, S_ERROR


class SessionStatus:
    """v6 wire 中单个 session 的状态（从 s 字段推导所有属性）。"""
    def __init__(self, d: dict):
        s = d.get("s", S_IDLE)
        self.name        = d.get("n", "?")
        self.slot        = d.get("slot", "")  # v6 新增：session 唯一标识（SID 后 8 位）
        self.running     = 1 if s == S_WORKING else 0
        self.waiting     = 1 if s == S_PENDING else 0
        self.completed   = s == S_DONE
        self.error       = "!" if s == S_ERROR else ""
        self.interrupted = False
        self.msg         = d.get("m", "")
        self.prompt      = {"tool": d.get("t", ""), "hint": d.get("h", ""), "id": ""} if s == S_PENDING else None


class MultiSessionMsg:
    """v6 wire 消息：包含所有活跃 session 的状态数组。"""
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
