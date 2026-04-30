#!/usr/bin/env python3
# tests/test_hook_normalize.py
# 自包含测试,不依赖 pytest。
# 跑法: 在仓库根 `python tests/test_hook_normalize.py` 退出码 0 = pass。
#
# 覆盖:
#   1. 8 类真实 fixture 喂对应 normalizer, 校验 v2 envelope shape
#   2. _tool_category 6 桶映射
#   3. _hint_from_tool_input 各字段优先级 + None / 空字典边界
#   4. fallback path: 未识别 hook 不崩, kind="unknown"
#   5. PreToolUse approval gate: Bash/Write/Edit needs_approval=True

import json
import os
import sys

# 让 import 找到仓库根的 hook_bridge.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
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
        ({}, "{}"),                                        # 空 dict 退化为 str
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
    """敏感字段截断: error_msg 200, prompt 80, message 200。"""
    g = {
        "session_id": "s", "cwd": "/x", "transcript_path": "/x.j",
        "permission_mode": "auto",
    }
    env_err = hb.NORMALIZERS["PostToolUseFailure"]({
        **g, "hook_event_name": "PostToolUseFailure",
        "tool_name": "Bash", "tool_input": {},
        "error": "x" * 500, "is_interrupt": False, "duration_ms": 0,
        "tool_use_id": "t",
    })
    _assert(len(env_err["event"]["error_msg"]) == 200,
            f"error_msg not truncated: len={len(env_err['event']['error_msg'])}")

    env_p = hb.NORMALIZERS["UserPromptSubmit"]({
        **g, "hook_event_name": "UserPromptSubmit",
        "prompt": "y" * 500,
    })
    _assert(len(env_p["event"]["prompt"]) == 80,
            f"prompt not truncated: len={len(env_p['event']['prompt'])}")
    print("  ok  truncation (error 500→200, prompt 500→80)")


def main():
    tests = [
        test_fixtures_normalize,
        test_tool_category,
        test_hint_extraction,
        test_fallback_unknown,
        test_approval_gate,
        test_truncation,
    ]
    print(f"running {len(tests)} test groups...")
    for t in tests:
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n{'='*50}\n  ALL TESTS PASSED ({len(tests)} groups)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
