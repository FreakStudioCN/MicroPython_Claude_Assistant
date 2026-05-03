#!/usr/bin/env python3
# tests/test_hook_normalize.py
# 自包含测试,不依赖 pytest。
# 跑法: 在仓库根 `python tests/test_hook_normalize.py` 退出码 0 = pass。
#
# 覆盖:
#   1. 8 类真实 fixture 喂对应 normalizer, 校验 v2 envelope shape
#   2. _tool_category 6 桶映射
#   3. _hint_from_tool_input 各字段优先级 + None / 空字典 / 无已知 key 边界
#   4. fallback path: 未识别 hook 不崩, kind="unknown"
#   5. PreToolUse approval gate: Bash/Write/Edit needs_approval=True
#   6. 截断长度统一 80 字 (error_msg / message / error / last_assistant_message)
#   7. PostToolUse 提取 tool_response.interrupted
#   8. _hint_from_tool_input 无已知 key 时返回空串（不序列化 dict）

import json
import os
import sys

# 让 import 找到仓库根的 hook_bridge.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))
import hook_bridge as hb  # noqa: E402

FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "probe_samples")

EXPECTED_KIND = {
    "PreToolUse":         "tool_start",
    "PostToolUse":        "tool_done",
    "PostToolUseFailure": "tool_error",
    "PostToolBatch":      "tool_batch_done",
    "SubagentStart":      "subagent_start",
    "Notification":       "notification",
    "UserPromptSubmit":   "user_prompt",
    "StopFailure":        "task_error",
}


def _assert(cond: bool, msg: str):
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)


def test_fixtures_normalize():
    """8 类真实 fixture 全部产出合法 v2 envelope。"""
    for hook_name, expected in EXPECTED_KIND.items():
        path = os.path.join(FIXTURE_DIR, f"{hook_name}.json")
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        env = hb.NORMALIZERS[hook_name](payload)

        _assert(env.get("type") == "event", f"{hook_name}: type != event")
        _assert(env.get("v") == 2, f"{hook_name}: v != 2")
        _assert("event" in env and "generic" in env, f"{hook_name}: missing keys")
        _assert(env["event"].get("kind") == expected,
                f"{hook_name}: kind {env['event'].get('kind')!r} != {expected!r}")

        g = env["generic"]
        for k in ("session_id", "cwd", "hook_event_name",
                  "transcript_path", "permission_mode"):
            _assert(k in g, f"{hook_name}: generic missing {k}")
        _assert(g["hook_event_name"] == hook_name,
                f"{hook_name}: hook_event_name mismatch")
        print(f"  ok  {hook_name:25s} → kind={expected}")


def test_tool_category():
    pairs = [
        ("Bash", "exec"),
        ("Write", "edit"), ("Edit", "edit"), ("NotebookEdit", "edit"),
        ("Read", "read"), ("Glob", "read"), ("Grep", "read"),
        ("WebFetch", "web"), ("WebSearch", "web"),
        ("Task", "agent"), ("Subagent", "agent"),
        ("Unknown", "other"), ("", "other"),
    ]
    for tool, expected in pairs:
        got = hb._tool_category(tool)
        _assert(got == expected, f"_tool_category({tool!r}) → {got!r} != {expected!r}")
    print(f"  ok  tool_category 6 buckets ({len(pairs)} cases)")


def test_hint_extraction():
    cases = [
        ({"command": "ls -la"}, "ls -la"),                # Bash
        ({"file_path": "/etc/hosts"}, "/etc/hosts"),       # Read/Edit/Write
        ({"pattern": "foo"}, "foo"),                       # Grep
        ({"url": "https://x"}, "https://x"),               # WebFetch
        ({"description": "doing thing"}, "doing thing"),   # 兜底 description
        # command 优先于 description
        ({"command": "ls", "description": "list"}, "ls"),
        ({}, ""),                                          # 无已知 key → 空串（不暴露 dict）
        ({"unknown_key": "val"}, ""),                      # 无已知 key → 空串
        (None, ""),                                        # 非 dict 输入
        ("not a dict", ""),                                # 非 dict 输入
    ]
    for inp, expected in cases:
        got = hb._hint_from_tool_input(inp)
        _assert(got == expected, f"_hint({inp!r}) → {got!r} != {expected!r}")
    # 80 字截断
    long_cmd = "x" * 200
    _assert(len(hb._hint_from_tool_input({"command": long_cmd})) == 80,
            "long command not truncated to 80")
    print(f"  ok  hint extraction ({len(cases)} cases + truncation)")


def test_fallback_unknown():
    """未识别 hook 走 _normalize_fallback, kind=unknown, 不崩。"""
    cases = [
        {"hook_event_name": "Stop"},
        {"hook_event_name": "SessionStart"},
        {"hook_event_name": "PreCompact"},
        {},
    ]
    for c in cases:
        env = hb._normalize_fallback(c)
        _assert(env["event"]["kind"] == "unknown", f"fallback {c} not unknown")
    print(f"  ok  fallback ({len(cases)} unknown hooks)")


def test_approval_gate():
    """Bash/Write/Edit needs_approval=True; 其它 False。"""
    g = {
        "session_id": "s", "cwd": "/x", "transcript_path": "/x.j",
        "permission_mode": "auto",
    }
    for tool in ("Bash", "Write", "Edit"):
        env = hb.NORMALIZERS["PreToolUse"]({
            **g, "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": {},
        })
        _assert(env["event"]["needs_approval"] is True,
                f"{tool} should need approval")
    for tool in ("Read", "Grep", "Glob", "WebFetch", "Task"):
        env = hb.NORMALIZERS["PreToolUse"]({
            **g, "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": {},
        })
        _assert(env["event"]["needs_approval"] is False,
                f"{tool} should NOT need approval")
    print("  ok  approval gate (3 approval tools, 5 non-approval)")


def test_truncation():
    """所有字段截断统一为 80 字: error_msg / prompt / message / error / last_assistant_message。"""
    g = {
        "session_id": "s", "cwd": "/x", "transcript_path": "/x.j",
        "permission_mode": "auto",
    }
    # PostToolUseFailure: error_msg 截 80
    env_err = hb.NORMALIZERS["PostToolUseFailure"]({
        **g, "hook_event_name": "PostToolUseFailure",
        "tool_name": "Bash", "tool_input": {},
        "error": "x" * 500, "is_interrupt": False, "duration_ms": 0,
        "tool_use_id": "t",
    })
    _assert(len(env_err["event"]["error_msg"]) == 80,
            f"error_msg not truncated to 80: len={len(env_err['event']['error_msg'])}")

    # UserPromptSubmit: prompt 截 80
    env_p = hb.NORMALIZERS["UserPromptSubmit"]({
        **g, "hook_event_name": "UserPromptSubmit",
        "prompt": "y" * 500,
    })
    _assert(len(env_p["event"]["prompt"]) == 80,
            f"prompt not truncated to 80: len={len(env_p['event']['prompt'])}")

    # Notification: message 截 80
    env_n = hb.NORMALIZERS["Notification"]({
        **g, "hook_event_name": "Notification",
        "message": "z" * 500, "notification_type": "permission_prompt",
    })
    _assert(len(env_n["event"]["message"]) == 80,
            f"message not truncated to 80: len={len(env_n['event']['message'])}")

    # StopFailure: error / last_assistant_message 截 80
    env_sf = hb.NORMALIZERS["StopFailure"]({
        **g, "hook_event_name": "StopFailure",
        "error": "e" * 500, "last_assistant_message": "m" * 500,
    })
    _assert(len(env_sf["event"]["error"]) == 80,
            f"StopFailure.error not truncated to 80")
    _assert(len(env_sf["event"]["last_assistant_message"]) == 80,
            f"last_assistant_message not truncated to 80")

    print("  ok  truncation: all fields unified to 80 chars")


def test_post_tool_interrupted():
    """PostToolUse 的 tool_response.interrupted 字段应被正确提取。"""
    g = {
        "session_id": "s", "cwd": "/x", "transcript_path": "/x.j",
        "permission_mode": "auto",
    }
    # interrupted=True：用户主动中断
    env_yes = hb.NORMALIZERS["PostToolUse"]({
        **g, "hook_event_name": "PostToolUse",
        "tool_name": "Bash", "tool_use_id": "t", "duration_ms": 100,
        "tool_input": {"command": "sleep 60"},
        "tool_response": {"stdout": "", "stderr": "", "interrupted": True,
                          "isImage": False, "noOutputExpected": False},
    })
    _assert(env_yes["event"]["interrupted"] is True,
            "interrupted=True should be extracted from tool_response")

    # interrupted=False：正常完成
    env_no = hb.NORMALIZERS["PostToolUse"]({
        **g, "hook_event_name": "PostToolUse",
        "tool_name": "Bash", "tool_use_id": "t", "duration_ms": 100,
        "tool_input": {"command": "ls"},
        "tool_response": {"stdout": "file.py", "interrupted": False,
                          "isImage": False, "noOutputExpected": False},
    })
    _assert(env_no["event"]["interrupted"] is False,
            "interrupted=False should be extracted")

    # 缺失 tool_response：默认 False（向后兼容）
    env_missing = hb.NORMALIZERS["PostToolUse"]({
        **g, "hook_event_name": "PostToolUse",
        "tool_name": "Read", "tool_use_id": "t", "duration_ms": 50,
        "tool_input": {"file_path": "/x"},
    })
    _assert(env_missing["event"]["interrupted"] is False,
            "missing tool_response should default interrupted=False")

    print("  ok  PostToolUse interrupted field (3 cases)")


def main():
    tests = [
        test_fixtures_normalize,
        test_tool_category,
        test_hint_extraction,
        test_fallback_unknown,
        test_approval_gate,
        test_truncation,
        test_post_tool_interrupted,
    ]
    print(f"running {len(tests)} test groups...")
    for t in tests:
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n{'='*50}\n  ALL TESTS PASSED ({len(tests)} groups)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
