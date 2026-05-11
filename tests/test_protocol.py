#!/usr/bin/env python3
# tests/test_protocol.py
# protocol.py 单元测试（PC 端运行）
#
# 跑法: 在仓库根 `python tests/test_protocol.py` 退出码 0 = pass。
#
# 覆盖:
#   1. StateEvent 类所有 6 个方法（使用 SessionStatus 构造测试对象）
#   2. parse() 函数各种输入（v5 ss 格式 / 命令 / 非法 / 缺失字段）
#   3. build_decision() 和 build_ack() 输出格式
#   4. SessionStatus 类字段提取和默认值
#   5. MultiSessionMsg 类

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "device"))
import protocol as p  # noqa: E402
import state as st    # noqa: E402


def _assert(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)


# ── StateEvent 类测试（使用 SessionStatus 构造）──────────────
def test_should_celebrate():
    msg_yes = p.SessionStatus({"s": "C"})
    msg_no  = p.SessionStatus({"s": "W"})
    _assert(st.StateEvent.should_celebrate(msg_yes) is True,  "C should celebrate")
    _assert(st.StateEvent.should_celebrate(msg_no)  is False, "W should not celebrate")
    print("  ok  StateEvent.should_celebrate()")


def test_should_show_error():
    msg_error       = p.SessionStatus({"s": "E"})
    msg_no_error    = p.SessionStatus({"s": "W"})
    # interrupted 在 SessionStatus 中固定为 False，error 非空时 show_error=True
    _assert(st.StateEvent.should_show_error(msg_error)    is True,  "E should show error")
    _assert(st.StateEvent.should_show_error(msg_no_error) is False, "W should not show error")
    print("  ok  StateEvent.should_show_error()")


def test_should_skip_error():
    # SessionStatus 的 interrupted 固定为 False，skip_error 需要 interrupted=True
    # 用简单 namespace 对象覆盖该场景
    class _Msg:
        def __init__(self, error, interrupted):
            self.error = error
            self.interrupted = interrupted
    msg_skip    = _Msg("boom", True)
    msg_no_skip = _Msg("boom", False)
    msg_no_err  = _Msg("",    True)
    _assert(st.StateEvent.should_skip_error(msg_skip)    is True,  "error+interrupted should skip")
    _assert(st.StateEvent.should_skip_error(msg_no_skip) is False, "error without interrupt should not skip")
    _assert(st.StateEvent.should_skip_error(msg_no_err)  is False, "interrupt without error should not skip")
    print("  ok  StateEvent.should_skip_error()")


def test_get_base_state():
    msg_pending = p.SessionStatus({"s": "P"})
    msg_working = p.SessionStatus({"s": "W"})
    msg_idle    = p.SessionStatus({"s": "I"})
    _assert(st.StateEvent.get_base_state(msg_pending) == st.PENDING, "P should be PENDING")
    _assert(st.StateEvent.get_base_state(msg_working) == st.WORKING, "W should be WORKING")
    _assert(st.StateEvent.get_base_state(msg_idle)    == st.IDLE,    "I should be IDLE")
    print("  ok  StateEvent.get_base_state()")


def test_needs_approval():
    msg_yes = p.SessionStatus({"s": "P", "t": "Bash", "h": "rm -rf /"})
    msg_no  = p.SessionStatus({"s": "W"})
    _assert(st.StateEvent.needs_approval(msg_yes) is True,  "P should need approval")
    _assert(st.StateEvent.needs_approval(msg_no)  is False, "W should not need approval")
    print("  ok  StateEvent.needs_approval()")


def test_is_idle():
    msg_idle    = p.SessionStatus({"s": "I"})
    msg_running = p.SessionStatus({"s": "W"})
    msg_waiting = p.SessionStatus({"s": "P"})
    _assert(st.StateEvent.is_idle(msg_idle)    is True,  "I should be idle")
    _assert(st.StateEvent.is_idle(msg_running) is False, "W should not be idle")
    _assert(st.StateEvent.is_idle(msg_waiting) is False, "P should not be idle")
    print("  ok  StateEvent.is_idle()")


# ── parse() 函数测试 ───────────────────────────────────────
def test_parse_multi_session_working():
    """v5 ss 格式：单个 WORKING session。"""
    line = json.dumps({"ss": [{"s": "W", "m": "Read: /etc/hosts"}]})
    result = p.parse(line)
    _assert(isinstance(result, p.MultiSessionMsg), "should return MultiSessionMsg")
    _assert(len(result.sessions) == 1, "should have 1 session")
    s = result.sessions[0]
    _assert(s.running == 1,              "running should be 1")
    _assert(s.waiting == 0,              "waiting should be 0")
    _assert(s.completed is False,        "completed should be False")
    _assert(s.msg == "Read: /etc/hosts", "msg should match")
    _assert(s.prompt is None,            "prompt should be None")
    print("  ok  parse() v5 ss WORKING session")


def test_parse_multi_session_pending():
    """v5 ss 格式：PENDING session（需要审批）。"""
    line = json.dumps({"ss": [{"s": "P", "t": "Bash", "h": "rm -rf /"}]})
    result = p.parse(line)
    _assert(isinstance(result, p.MultiSessionMsg), "should return MultiSessionMsg")
    s = result.sessions[0]
    _assert(s.waiting == 1,          "waiting should be 1")
    _assert(s.running == 0,          "running should be 0")
    _assert(s.prompt is not None,    "prompt should not be None")
    _assert(s.prompt["tool"] == "Bash",      "prompt.tool should be Bash")
    _assert(s.prompt["hint"] == "rm -rf /",  "prompt.hint should match")
    print("  ok  parse() v5 ss PENDING session")


def test_parse_control_cmd():
    line = '{"cmd":"name","value":"MyDevice"}\n'
    result = p.parse(line)
    _assert(isinstance(result, dict), "should return dict for cmd")
    _assert(result["cmd"] == "name",       "cmd should be name")
    _assert(result["value"] == "MyDevice", "value should match")
    print("  ok  parse() control command")


def test_parse_invalid_json():
    result = p.parse('not a json{{{]\n')
    _assert(result is None, "invalid JSON should return None")
    print("  ok  parse() invalid JSON returns None")


def test_parse_empty_string():
    result = p.parse("")
    _assert(result is None, "empty string should return None")
    print("  ok  parse() empty string returns None")


def test_parse_unknown_format():
    """既无 cmd 也无 ss 字段 → None。"""
    result = p.parse('{"running":2}')
    _assert(result is None, "unknown format should return None")
    print("  ok  parse() unknown format returns None")


# ── build_decision() 测试 ──────────────────────────────────
def test_build_decision_once():
    result = p.build_decision(0, "once")
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["d"] == "once", "d should be once")
    _assert(data["n"] == 0,      "n should be 0")
    print("  ok  build_decision() once")


def test_build_decision_deny():
    result = p.build_decision(1, "deny")
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["d"] == "deny", "d should be deny")
    _assert(data["n"] == 1,      "n should be 1")
    print("  ok  build_decision() deny")


# ── build_ack() 测试 ───────────────────────────────────────
def test_build_ack_ok():
    result = p.build_ack("name", ok=True)
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["ack"] == "name", "ack should be name")
    _assert(data["ok"] is True,    "ok should be True")
    print("  ok  build_ack() ok=True")


def test_build_ack_fail():
    result = p.build_ack("unpair", ok=False)
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["ack"] == "unpair", "ack should be unpair")
    _assert(data["ok"] is False,     "ok should be False")
    print("  ok  build_ack() ok=False")


# ── SessionStatus 类测试 ───────────────────────────────────
def test_session_status_all_states():
    """SessionStatus 对 I/W/P/E/C 五种状态的字段推导。"""
    s_i = p.SessionStatus({"s": "I"})
    _assert(s_i.running == 0,       "I: running should be 0")
    _assert(s_i.waiting == 0,       "I: waiting should be 0")
    _assert(s_i.completed is False, "I: completed should be False")
    _assert(s_i.error == "",        "I: error should be empty")
    _assert(s_i.prompt is None,     "I: prompt should be None")

    s_w = p.SessionStatus({"s": "W", "m": "Bash: ls"})
    _assert(s_w.running == 1,          "W: running should be 1")
    _assert(s_w.waiting == 0,          "W: waiting should be 0")
    _assert(s_w.msg == "Bash: ls",     "W: msg should match")
    _assert(s_w.prompt is None,        "W: prompt should be None")

    s_p = p.SessionStatus({"s": "P", "t": "Write", "h": "main.py"})
    _assert(s_p.waiting == 1,              "P: waiting should be 1")
    _assert(s_p.running == 0,              "P: running should be 0")
    _assert(s_p.prompt is not None,        "P: prompt should not be None")
    _assert(s_p.prompt["tool"] == "Write", "P: prompt.tool should be Write")
    _assert(s_p.prompt["hint"] == "main.py","P: prompt.hint should match")

    s_e = p.SessionStatus({"s": "E"})
    _assert(s_e.error != "",        "E: error should be non-empty")
    _assert(s_e.running == 0,       "E: running should be 0")

    s_c = p.SessionStatus({"s": "C"})
    _assert(s_c.completed is True,  "C: completed should be True")
    _assert(s_c.running == 0,       "C: running should be 0")

    print("  ok  SessionStatus I/W/P/E/C 五种状态字段推导")


def test_session_status_default():
    """SessionStatus 缺省 s 字段时默认 I。"""
    s = p.SessionStatus({})
    _assert(s.running == 0,       "default: running should be 0")
    _assert(s.waiting == 0,       "default: waiting should be 0")
    _assert(s.completed is False, "default: completed should be False")
    _assert(s.error == "",        "default: error should be empty")
    _assert(s.prompt is None,     "default: prompt should be None")
    print("  ok  SessionStatus 缺省字段使用默认值")


# ── MultiSessionMsg 类测试 ─────────────────────────────────
def test_parse_multi_session_msg():
    """parse() 识别 ss 字段，返回 MultiSessionMsg（v5 格式）。"""
    line = json.dumps({
        "ss": [
            {"s": "W", "m": "Bash"},
            {"s": "P", "t": "Write", "h": "main.py"},
        ]
    })
    result = p.parse(line)
    _assert(isinstance(result, p.MultiSessionMsg), f"should return MultiSessionMsg, got {type(result)}")
    _assert(len(result.sessions) == 2, f"should have 2 sessions, got {len(result.sessions)}")
    s0 = result.sessions[0]
    _assert(s0.running == 1,    "s0.running should be 1")
    _assert(s0.waiting == 0,    "s0.waiting should be 0")
    _assert(s0.msg == "Bash",   "s0.msg should be Bash")
    s1 = result.sessions[1]
    _assert(s1.waiting == 1,                          "s1.waiting should be 1")
    _assert(st.StateEvent.needs_approval(s1) is True, "s1 should need approval")
    print("  ok  parse() MultiSessionMsg v5 (ss key)")


def test_parse_multi_session_empty():
    """ss 数组为空时返回 MultiSessionMsg(sessions=[])。"""
    line = json.dumps({"ss": []})
    result = p.parse(line)
    _assert(isinstance(result, p.MultiSessionMsg), "should return MultiSessionMsg")
    _assert(len(result.sessions) == 0, "sessions should be empty")
    print("  ok  parse() MultiSessionMsg empty ss")


def test_state_event_with_session_status():
    """StateEvent 方法对 v5 SessionStatus 全部适用。"""
    s_pending = p.SessionStatus({"s": "P", "t": "Bash", "h": "hint"})
    s_working = p.SessionStatus({"s": "W", "m": "Read"})
    s_idle    = p.SessionStatus({"s": "I"})
    s_done    = p.SessionStatus({"s": "C"})
    s_err     = p.SessionStatus({"s": "E"})

    _assert(st.StateEvent.get_base_state(s_pending) == st.PENDING, "P should be PENDING")
    _assert(st.StateEvent.get_base_state(s_working) == st.WORKING, "W should be WORKING")
    _assert(st.StateEvent.get_base_state(s_idle)    == st.IDLE,    "I should be IDLE")
    _assert(st.StateEvent.needs_approval(s_pending) is True,       "P should need approval")
    _assert(st.StateEvent.should_celebrate(s_done)  is True,       "C should celebrate")
    _assert(st.StateEvent.should_show_error(s_err)  is True,       "E should show error")
    print("  ok  StateEvent 方法对 v5 SessionStatus 全部适用")


# ── 主函数 ─────────────────────────────────────────────────
def main():
    tests = [
        # StateEvent 类（6 个方法）
        test_should_celebrate,
        test_should_show_error,
        test_should_skip_error,
        test_get_base_state,
        test_needs_approval,
        test_is_idle,
        # parse() 函数（6 个测试）
        test_parse_multi_session_working,
        test_parse_multi_session_pending,
        test_parse_control_cmd,
        test_parse_invalid_json,
        test_parse_empty_string,
        test_parse_unknown_format,
        # build_decision()（2 个测试）
        test_build_decision_once,
        test_build_decision_deny,
        # build_ack()（2 个测试）
        test_build_ack_ok,
        test_build_ack_fail,
        # SessionStatus 类（2 个测试）
        test_session_status_all_states,
        test_session_status_default,
        # MultiSessionMsg（3 个测试）
        test_parse_multi_session_msg,
        test_parse_multi_session_empty,
        test_state_event_with_session_status,
    ]
    print(f"running {len(tests)} protocol.py tests...")
    try:
        for t in tests:
            print(f"\n[{t.__name__}]")
            t()
        print(f"\n{'='*50}\n  ALL PROTOCOL TESTS PASSED ({len(tests)} tests)")
        return 0
    except Exception as e:
        print(f"\n{'='*50}\n  TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

# protocol.py 单元测试（PC 端运行）
#
# 跑法: 在仓库根 `python tests/test_protocol.py` 退出码 0 = pass。
#
# 覆盖:
#   1. StateEvent 类所有 6 个方法
#   2. parse() 函数各种输入（正常/命令/非法/缺失字段）
#   3. build_decision() 和 build_ack() 输出格式
#   4. StatusMsg 类字段提取和默认值

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "device"))
import protocol as p  # noqa: E402
import state as st    # noqa: E402


def _assert(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)


# ── StateEvent 类测试 ──────────────────────────────────────
def test_should_celebrate():
    msg_yes = p.StatusMsg({"completed": True})
    msg_no = p.StatusMsg({"completed": False})
    _assert(st.StateEvent.should_celebrate(msg_yes) is True, "completed=True should celebrate")
    _assert(st.StateEvent.should_celebrate(msg_no) is False, "completed=False should not celebrate")
    print("  ok  StateEvent.should_celebrate()")


def test_should_show_error():
    msg_error = p.StatusMsg({"error": "boom", "interrupted": False})
    msg_interrupted = p.StatusMsg({"error": "boom", "interrupted": True})
    msg_no_error = p.StatusMsg({"error": "", "interrupted": False})
    _assert(st.StateEvent.should_show_error(msg_error) is True, "error + not interrupted should show")
    _assert(st.StateEvent.should_show_error(msg_interrupted) is False, "interrupted should not show error")
    _assert(st.StateEvent.should_show_error(msg_no_error) is False, "no error should not show")
    print("  ok  StateEvent.should_show_error()")


def test_should_skip_error():
    msg_skip = p.StatusMsg({"error": "boom", "interrupted": True})
    msg_no_skip1 = p.StatusMsg({"error": "boom", "interrupted": False})
    msg_no_skip2 = p.StatusMsg({"error": "", "interrupted": True})
    _assert(st.StateEvent.should_skip_error(msg_skip) is True, "interrupted + error should skip")
    _assert(st.StateEvent.should_skip_error(msg_no_skip1) is False, "error without interrupt should not skip")
    _assert(st.StateEvent.should_skip_error(msg_no_skip2) is False, "interrupt without error should not skip")
    print("  ok  StateEvent.should_skip_error()")


def test_get_base_state():
    msg_pending = p.StatusMsg({"waiting": 1, "running": 0})
    msg_working = p.StatusMsg({"waiting": 0, "running": 1})
    msg_idle = p.StatusMsg({"waiting": 0, "running": 0})
    msg_both = p.StatusMsg({"waiting": 2, "running": 3})  # waiting 优先
    _assert(st.StateEvent.get_base_state(msg_pending) == st.PENDING, "waiting>0 should be PENDING")
    _assert(st.StateEvent.get_base_state(msg_working) == st.WORKING, "running>0 should be WORKING")
    _assert(st.StateEvent.get_base_state(msg_idle) == st.IDLE, "both 0 should be IDLE")
    _assert(st.StateEvent.get_base_state(msg_both) == st.PENDING, "waiting takes priority over running")
    print("  ok  StateEvent.get_base_state()")


def test_needs_approval():
    msg_yes = p.StatusMsg({"prompt": {"id": "t1", "tool": "Bash", "hint": "rm -rf /"}})
    msg_no = p.StatusMsg({"prompt": None})
    _assert(st.StateEvent.needs_approval(msg_yes) is True, "prompt dict should need approval")
    _assert(st.StateEvent.needs_approval(msg_no) is False, "prompt None should not need approval")
    print("  ok  StateEvent.needs_approval()")


def test_is_idle():
    msg_idle = p.StatusMsg({"running": 0, "waiting": 0})
    msg_running = p.StatusMsg({"running": 1, "waiting": 0})
    msg_waiting = p.StatusMsg({"running": 0, "waiting": 1})
    msg_both = p.StatusMsg({"running": 1, "waiting": 1})
    _assert(st.StateEvent.is_idle(msg_idle) is True, "running=0 waiting=0 should be idle")
    _assert(st.StateEvent.is_idle(msg_running) is False, "running>0 should not be idle")
    _assert(st.StateEvent.is_idle(msg_waiting) is False, "waiting>0 should not be idle")
    _assert(st.StateEvent.is_idle(msg_both) is False, "both>0 should not be idle")
    print("  ok  StateEvent.is_idle()")


# ── parse() 函数测试 ───────────────────────────────────────
def test_parse_status_msg():
    line = '{"running":1,"waiting":0,"completed":false,"msg":"Read: /etc/hosts","tokens":0,"prompt":null,"category":"read","error":"","interrupted":false}\n'
    result = p.parse(line)
    _assert(isinstance(result, p.StatusMsg), "should return StatusMsg")
    _assert(result.running == 1, "running should be 1")
    _assert(result.waiting == 0, "waiting should be 0")
    _assert(result.completed is False, "completed should be False")
    _assert(result.msg == "Read: /etc/hosts", "msg should match")
    _assert(result.category == "read", "category should be read")
    _assert(result.error == "", "error should be empty")
    _assert(result.interrupted is False, "interrupted should be False")
    _assert(result.prompt is None, "prompt should be None")
    print("  ok  parse() status message (9 fields)")


def test_parse_control_cmd():
    line = '{"cmd":"name","value":"MyDevice"}\n'
    result = p.parse(line)
    _assert(isinstance(result, dict), "should return dict for cmd")
    _assert(result["cmd"] == "name", "cmd should be name")
    _assert(result["value"] == "MyDevice", "value should match")
    print("  ok  parse() control command")


def test_parse_invalid_json():
    line = 'not a json{{{]\n'
    result = p.parse(line)
    _assert(result is None, "invalid JSON should return None")
    print("  ok  parse() invalid JSON returns None")


def test_parse_missing_fields():
    line = '{"running":2}\n'  # 只有 running，其他字段缺失
    result = p.parse(line)
    _assert(isinstance(result, p.StatusMsg), "should return StatusMsg")
    _assert(result.running == 2, "running should be 2")
    _assert(result.waiting == 0, "waiting should default to 0")
    _assert(result.completed is False, "completed should default to False")
    _assert(result.msg == "", "msg should default to empty")
    _assert(result.tokens == 0, "tokens should default to 0")
    _assert(result.prompt is None, "prompt should default to None")
    _assert(result.category == "", "category should default to empty")
    _assert(result.error == "", "error should default to empty")
    _assert(result.interrupted is False, "interrupted should default to False")
    print("  ok  parse() missing fields use defaults")


def test_parse_empty_string():
    result = p.parse("")
    _assert(result is None, "empty string should return None")
    print("  ok  parse() empty string returns None")


# ── build_decision() 测试 ──────────────────────────────────
def test_build_decision_once():
    result = p.build_decision(0, "once")
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["d"] == "once", "d should be once")
    _assert(data["n"] == 0, "n should be 0")
    print("  ok  build_decision() once")


def test_build_decision_deny():
    result = p.build_decision(1, "deny")
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["d"] == "deny", "d should be deny")
    _assert(data["n"] == 1, "n should be 1")
    print("  ok  build_decision() deny")


# ── build_ack() 测试 ───────────────────────────────────────
def test_build_ack_ok():
    result = p.build_ack("name", ok=True)
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["ack"] == "name", "ack should be name")
    _assert(data["ok"] is True, "ok should be True")
    print("  ok  build_ack() ok=True")


def test_build_ack_fail():
    result = p.build_ack("unpair", ok=False)
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["ack"] == "unpair", "ack should be unpair")
    _assert(data["ok"] is False, "ok should be False")
    print("  ok  build_ack() ok=False")


# ── StatusMsg 类测试 ───────────────────────────────────────
def test_status_msg_full_fields():
    d = {
        "running": 2,
        "waiting": 1,
        "completed": True,
        "msg": "test message",
        "tokens": 1500,
        "prompt": {"id": "t1", "tool": "Bash", "hint": "ls"},
        "category": "exec",
        "error": "timeout",
        "interrupted": True,
    }
    msg = p.StatusMsg(d)
    _assert(msg.running == 2, "running should be 2")
    _assert(msg.waiting == 1, "waiting should be 1")
    _assert(msg.completed is True, "completed should be True")
    _assert(msg.msg == "test message", "msg should match")
    _assert(msg.tokens == 1500, "tokens should be 1500")
    _assert(msg.prompt["id"] == "t1", "prompt.id should be t1")
    _assert(msg.category == "exec", "category should be exec")
    _assert(msg.error == "timeout", "error should be timeout")
    _assert(msg.interrupted is True, "interrupted should be True")
    print("  ok  StatusMsg full fields")


def test_status_msg_empty_dict():
    msg = p.StatusMsg({})
    _assert(msg.running == 0, "running should default to 0")
    _assert(msg.waiting == 0, "waiting should default to 0")
    _assert(msg.completed is False, "completed should default to False")
    _assert(msg.msg == "", "msg should default to empty")
    _assert(msg.tokens == 0, "tokens should default to 0")
    _assert(msg.prompt is None, "prompt should default to None")
    _assert(msg.category == "", "category should default to empty")
    _assert(msg.error == "", "error should default to empty")
    _assert(msg.interrupted is False, "interrupted should default to False")
    print("  ok  StatusMsg empty dict uses all defaults")


# ── MultiSessionMsg / SessionStatus 测试 ──────────────────
def test_parse_multi_session_msg():
    """parse() 识别 ss 字段，返回 MultiSessionMsg（v4 格式）。"""
    line = json.dumps({
        "ss": [
            {"s": "W", "m": "Bash"},
            {"s": "P", "t": "Write", "h": "main.py"},
        ]
    })
    result = p.parse(line)
    _assert(isinstance(result, p.MultiSessionMsg), f"should return MultiSessionMsg, got {type(result)}")
    _assert(len(result.sessions) == 2, f"should have 2 sessions, got {len(result.sessions)}")
    s0 = result.sessions[0]
    _assert(s0.running == 1, "s0.running should be 1")
    _assert(s0.waiting == 0, "s0.waiting should be 0")
    _assert(s0.msg == "Bash", "s0.msg should be Bash")
    s1 = result.sessions[1]
    _assert(s1.waiting == 1, "s1.waiting should be 1")
    _assert(st.StateEvent.needs_approval(s1) is True, "s1 should need approval")
    print("  ok  parse() MultiSessionMsg v4 (ss key)")


def test_parse_multi_session_empty():
    """sessions 数组为空时返回 MultiSessionMsg(sessions=[])。"""
    line = json.dumps({"v": 2, "sessions": []})
    result = p.parse(line)
    _assert(isinstance(result, p.MultiSessionMsg), "should return MultiSessionMsg")
    _assert(len(result.sessions) == 0, "sessions should be empty")
    print("  ok  parse() MultiSessionMsg empty sessions")


def test_session_status_fields():
    """SessionStatus v4 字段提取。"""
    s_w = p.SessionStatus({"s": "W", "m": "Bash: ls"})
    _assert(s_w.running == 1, "W: running should be 1")
    _assert(s_w.waiting == 0, "W: waiting should be 0")
    _assert(s_w.msg == "Bash: ls", "W: msg should match")
    _assert(s_w.prompt is None, "W: prompt should be None")

    s_p = p.SessionStatus({"s": "P", "t": "Bash", "h": "rm -rf /"})
    _assert(s_p.waiting == 1, "P: waiting should be 1")
    _assert(s_p.prompt is not None, "P: prompt should not be None")
    _assert(s_p.prompt["tool"] == "Bash", "P: prompt.tool should be Bash")
    _assert(s_p.prompt["hint"] == "rm -rf /", "P: prompt.hint should match")

    s_e = p.SessionStatus({"s": "E"})
    _assert(s_e.error == "error", "E: error should be 'error'")

    s_c = p.SessionStatus({"s": "C"})
    _assert(s_c.completed is True, "C: completed should be True")

    s_i = p.SessionStatus({})
    _assert(s_i.running == 0, "I: running should be 0")
    _assert(s_i.prompt is None, "I: prompt should be None")
    print("  ok  SessionStatus v4 fields")


def test_state_event_with_session_status():
    """StateEvent 方法对 v4 SessionStatus 同样适用。"""
    s_pending = p.SessionStatus({"s": "P", "t": "Bash", "h": "hint"})
    s_working = p.SessionStatus({"s": "W", "m": "Read"})
    s_idle    = p.SessionStatus({"s": "I"})
    s_done    = p.SessionStatus({"s": "C"})
    s_err     = p.SessionStatus({"s": "E"})

    _assert(st.StateEvent.get_base_state(s_pending) == st.PENDING, "P should be PENDING")
    _assert(st.StateEvent.get_base_state(s_working) == st.WORKING, "W should be WORKING")
    _assert(st.StateEvent.get_base_state(s_idle) == st.IDLE, "I should be IDLE")
    _assert(st.StateEvent.needs_approval(s_pending) is True, "P should need approval")
    _assert(st.StateEvent.should_celebrate(s_done) is True, "C should celebrate")
    _assert(st.StateEvent.should_show_error(s_err) is True, "E should show error")
    print("  ok  StateEvent methods work with v4 SessionStatus")


# ── 主函数 ─────────────────────────────────────────────────
def main():
    tests = [
        # StateEvent 类（6 个方法）
        test_should_celebrate,
        test_should_show_error,
        test_should_skip_error,
        test_get_base_state,
        test_needs_approval,
        test_is_idle,
        # parse() 函数（5 个测试）
        test_parse_status_msg,
        test_parse_control_cmd,
        test_parse_invalid_json,
        test_parse_missing_fields,
        test_parse_empty_string,
        # build_decision()（2 个测试）
        test_build_decision_once,
        test_build_decision_deny,
        # build_ack()（2 个测试）
        test_build_ack_ok,
        test_build_ack_fail,
        # StatusMsg 类（2 个测试）
        test_status_msg_full_fields,
        test_status_msg_empty_dict,
        # MultiSessionMsg / SessionStatus（4 个测试）
        test_parse_multi_session_msg,
        test_parse_multi_session_empty,
        test_session_status_fields,
        test_state_event_with_session_status,
    ]
    print(f"running {len(tests)} protocol.py tests...")
    try:
        for t in tests:
            print(f"\n[{t.__name__}]")
            t()
        print(f"\n{'='*50}\n  ALL PROTOCOL TESTS PASSED ({len(tests)} tests)")
        return 0
    except Exception as e:
        print(f"\n{'='*50}\n  TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
