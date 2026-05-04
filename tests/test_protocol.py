#!/usr/bin/env python3
# tests/test_protocol.py
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


def _assert(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)


# ── StateEvent 类测试 ──────────────────────────────────────
def test_should_celebrate():
    msg_yes = p.StatusMsg({"completed": True})
    msg_no = p.StatusMsg({"completed": False})
    _assert(p.StateEvent.should_celebrate(msg_yes) is True, "completed=True should celebrate")
    _assert(p.StateEvent.should_celebrate(msg_no) is False, "completed=False should not celebrate")
    print("  ok  StateEvent.should_celebrate()")


def test_should_show_error():
    msg_error = p.StatusMsg({"error": "boom", "interrupted": False})
    msg_interrupted = p.StatusMsg({"error": "boom", "interrupted": True})
    msg_no_error = p.StatusMsg({"error": "", "interrupted": False})
    _assert(p.StateEvent.should_show_error(msg_error) is True, "error + not interrupted should show")
    _assert(p.StateEvent.should_show_error(msg_interrupted) is False, "interrupted should not show error")
    _assert(p.StateEvent.should_show_error(msg_no_error) is False, "no error should not show")
    print("  ok  StateEvent.should_show_error()")


def test_should_skip_error():
    msg_skip = p.StatusMsg({"error": "boom", "interrupted": True})
    msg_no_skip1 = p.StatusMsg({"error": "boom", "interrupted": False})
    msg_no_skip2 = p.StatusMsg({"error": "", "interrupted": True})
    _assert(p.StateEvent.should_skip_error(msg_skip) is True, "interrupted + error should skip")
    _assert(p.StateEvent.should_skip_error(msg_no_skip1) is False, "error without interrupt should not skip")
    _assert(p.StateEvent.should_skip_error(msg_no_skip2) is False, "interrupt without error should not skip")
    print("  ok  StateEvent.should_skip_error()")


def test_get_base_state():
    msg_pending = p.StatusMsg({"waiting": 1, "running": 0})
    msg_working = p.StatusMsg({"waiting": 0, "running": 1})
    msg_idle = p.StatusMsg({"waiting": 0, "running": 0})
    msg_both = p.StatusMsg({"waiting": 2, "running": 3})  # waiting 优先
    _assert(p.StateEvent.get_base_state(msg_pending) == p.PENDING, "waiting>0 should be PENDING")
    _assert(p.StateEvent.get_base_state(msg_working) == p.WORKING, "running>0 should be WORKING")
    _assert(p.StateEvent.get_base_state(msg_idle) == p.IDLE, "both 0 should be IDLE")
    _assert(p.StateEvent.get_base_state(msg_both) == p.PENDING, "waiting takes priority over running")
    print("  ok  StateEvent.get_base_state()")


def test_needs_approval():
    msg_yes = p.StatusMsg({"prompt": {"id": "t1", "tool": "Bash", "hint": "rm -rf /"}})
    msg_no = p.StatusMsg({"prompt": None})
    _assert(p.StateEvent.needs_approval(msg_yes) is True, "prompt dict should need approval")
    _assert(p.StateEvent.needs_approval(msg_no) is False, "prompt None should not need approval")
    print("  ok  StateEvent.needs_approval()")


def test_is_idle():
    msg_idle = p.StatusMsg({"running": 0, "waiting": 0})
    msg_running = p.StatusMsg({"running": 1, "waiting": 0})
    msg_waiting = p.StatusMsg({"running": 0, "waiting": 1})
    msg_both = p.StatusMsg({"running": 1, "waiting": 1})
    _assert(p.StateEvent.is_idle(msg_idle) is True, "running=0 waiting=0 should be idle")
    _assert(p.StateEvent.is_idle(msg_running) is False, "running>0 should not be idle")
    _assert(p.StateEvent.is_idle(msg_waiting) is False, "waiting>0 should not be idle")
    _assert(p.StateEvent.is_idle(msg_both) is False, "both>0 should not be idle")
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
    result = p.build_decision("toolu_abc123", "once")
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["cmd"] == "permission", "cmd should be permission")
    _assert(data["id"] == "toolu_abc123", "id should match")
    _assert(data["decision"] == "once", "decision should be once")
    print("  ok  build_decision() once")


def test_build_decision_deny():
    result = p.build_decision("toolu_xyz789", "deny")
    _assert(result.endswith("\n"), "should end with newline")
    data = json.loads(result.strip())
    _assert(data["cmd"] == "permission", "cmd should be permission")
    _assert(data["id"] == "toolu_xyz789", "id should match")
    _assert(data["decision"] == "deny", "decision should be deny")
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
    """parse() 识别 sessions 字段，返回 MultiSessionMsg。"""
    line = json.dumps({
        "v": 2,
        "sessions": [
            {"id": "sess-aaa", "running": 1, "waiting": 0, "completed": False,
             "msg": "Bash: ls", "category": "exec", "error": "", "interrupted": False,
             "prompt": None},
            {"id": "sess-bbb", "running": 0, "waiting": 1, "completed": False,
             "msg": "approve: Write", "category": "edit", "error": "", "interrupted": False,
             "prompt": {"id": "t2", "tool": "Write", "hint": "main.py"}},
        ]
    })
    result = p.parse(line)
    _assert(isinstance(result, p.MultiSessionMsg), f"should return MultiSessionMsg, got {type(result)}")
    _assert(len(result.sessions) == 2, f"should have 2 sessions, got {len(result.sessions)}")
    s0 = result.sessions[0]
    _assert(s0.id == "sess-aaa", f"s0.id should be 'sess-aaa', got {s0.id!r}")
    _assert(s0.running == 1, "s0.running should be 1")
    _assert(s0.waiting == 0, "s0.waiting should be 0")
    _assert(s0.category == "exec", "s0.category should be exec")
    s1 = result.sessions[1]
    _assert(s1.id == "sess-bbb", f"s1.id should be 'sess-bbb', got {s1.id!r}")
    _assert(s1.waiting == 1, "s1.waiting should be 1")
    _assert(p.StateEvent.needs_approval(s1) is True, "s1 should need approval")
    print("  ok  parse() MultiSessionMsg (2 sessions)")


def test_parse_multi_session_empty():
    """sessions 数组为空时返回 MultiSessionMsg(sessions=[])。"""
    line = json.dumps({"v": 2, "sessions": []})
    result = p.parse(line)
    _assert(isinstance(result, p.MultiSessionMsg), "should return MultiSessionMsg")
    _assert(len(result.sessions) == 0, "sessions should be empty")
    print("  ok  parse() MultiSessionMsg empty sessions")


def test_session_status_fields():
    """SessionStatus 字段提取和默认值。"""
    d = {
        "id": "abc12345",
        "running": 2, "waiting": 1, "completed": True,
        "msg": "Bash: ls", "category": "exec",
        "error": "timeout", "interrupted": True,
        "prompt": {"id": "t1", "tool": "Bash", "hint": "ls"},
    }
    s = p.SessionStatus(d)
    _assert(s.id == "abc12345", "id should match")
    _assert(s.running == 2, "running should be 2")
    _assert(s.waiting == 1, "waiting should be 1")
    _assert(s.completed is True, "completed should be True")
    _assert(s.msg == "Bash: ls", "msg should match")
    _assert(s.category == "exec", "category should be exec")
    _assert(s.error == "timeout", "error should match")
    _assert(s.interrupted is True, "interrupted should be True")
    _assert(s.prompt["tool"] == "Bash", "prompt.tool should be Bash")

    # 默认值
    s_empty = p.SessionStatus({})
    _assert(s_empty.id == "", "id default should be empty")
    _assert(s_empty.running == 0, "running default should be 0")
    _assert(s_empty.prompt is None, "prompt default should be None")
    print("  ok  SessionStatus fields + defaults")


def test_state_event_with_session_status():
    """StateEvent 方法对 SessionStatus 同样适用（接口相同）。"""
    s_pending = p.SessionStatus({"waiting": 1, "running": 0})
    s_working = p.SessionStatus({"waiting": 0, "running": 2})
    s_idle    = p.SessionStatus({"waiting": 0, "running": 0})
    s_approve = p.SessionStatus({"prompt": {"id": "t1", "tool": "Bash", "hint": ""}})
    s_done    = p.SessionStatus({"completed": True})
    s_err     = p.SessionStatus({"error": "boom", "interrupted": False})

    _assert(p.StateEvent.get_base_state(s_pending) == p.PENDING, "pending session should be PENDING")
    _assert(p.StateEvent.get_base_state(s_working) == p.WORKING, "working session should be WORKING")
    _assert(p.StateEvent.get_base_state(s_idle) == p.IDLE, "idle session should be IDLE")
    _assert(p.StateEvent.needs_approval(s_approve) is True, "should need approval")
    _assert(p.StateEvent.should_celebrate(s_done) is True, "should celebrate")
    _assert(p.StateEvent.should_show_error(s_err) is True, "should show error")
    print("  ok  StateEvent methods work with SessionStatus")


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
