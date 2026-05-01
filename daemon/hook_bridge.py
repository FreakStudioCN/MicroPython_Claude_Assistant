#!/usr/bin/env python3
# hook_bridge.py —— Claude Code hook 上位机接收层 (v2 envelope)
#
# 链路: Claude Code hook → stdin → 字段规整 → v2 envelope → TCP 57320
#       → ble_daemon.py → BLE → ESP32
#
# 字段规整覆盖 8 类已观测真实触发的 hook (依据 ~/.claude_buddy/probe.jsonl 实测):
#   PreToolUse / PostToolUse / PostToolUseFailure / PostToolBatch /
#   SubagentStart / Notification / UserPromptSubmit / StopFailure
# 其余 21 类 settings.json 注册了但当前 Claude Code 版本未冒,fallback 走 unknown.
#
# 阻塞语义: 仅 PreToolUse on {Bash, Write, Edit} 同步等 daemon 回 once/deny.
# daemon 不可达时 fail-open (返回 {}),保证硬件离线不会拖死 Claude Code.

import json
import socket
import sys

HOST = "127.0.0.1"
PORT = 57320
CONNECT_TIMEOUT = 1.0     # localhost connect 应该 ms 级,1s 是 daemon 卡死的兜底
RECV_TIMEOUT = 35         # 覆盖 daemon 端 30s approval 窗口 + 缓冲
MAX_STDIN_BYTES = 1 << 20  # 1MB hook payload 上限,防超大 tool_response 内存炸
APPROVAL_TOOLS = {"Bash", "Write", "Edit"}

# ── 5 桶 tool_category (research/hook_to_device_mapping_v1.md) ───
_TOOL_CATEGORY = {
    "Bash":         "exec",
    "Write":        "edit",
    "Edit":         "edit",
    "NotebookEdit": "edit",
    "Read":         "read",
    "Glob":         "read",
    "Grep":         "read",
    "WebFetch":     "web",
    "WebSearch":    "web",
    "Task":         "agent",
    "Subagent":     "agent",
}


def _tool_category(name: str) -> str:
    return _TOOL_CATEGORY.get(name, "other")


def _generic(event: dict) -> dict:
    """v2 envelope 的 generic 字段,所有 hook 通用 5 字段。"""
    return {
        "session_id":      event.get("session_id", ""),
        "cwd":             event.get("cwd", ""),
        "hook_event_name": event.get("hook_event_name", ""),
        "transcript_path": event.get("transcript_path", ""),
        "permission_mode": event.get("permission_mode", ""),
    }


def _trunc(v, n: int) -> str:
    """把 str 截到 n 字, 非 str 返回空串。设备显示用,防长尾敏感数据。"""
    return v[:n] if isinstance(v, str) else ""


def _hint_from_tool_input(tool_input) -> str:
    """从 tool_input 抽一句给设备 LCD 显示的短提示, 80 字以内。
    优先级: command (Bash) > file_path (Read/Edit/Write) > description > 截断 dict。"""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "pattern", "url", "description"):
        v = tool_input.get(key)
        if isinstance(v, str) and v:
            return v[:80]
    return str(tool_input)[:80]


# ── 6 类 normalizer (返回 v2 envelope) ──────────────────
def _normalize_pre_tool(event: dict) -> dict:
    tool = event.get("tool_name", "")
    return {
        "type": "event",
        "v": 2,
        "event": {
            "kind":           "tool_start",
            "tool":           tool,
            "tool_category":  _tool_category(tool),
            "summary":        _hint_from_tool_input(event.get("tool_input")),
            "needs_approval": tool in APPROVAL_TOOLS,
            "tool_use_id":    event.get("tool_use_id", ""),
        },
        "generic": _generic(event),
    }


def _normalize_post_tool(event: dict) -> dict:
    """PostToolUse 仅 success path,失败走 PostToolUseFailure 独立 hook。
    实测 tool_response 无 exit_code 字段,只能据 hook 名区分成功/失败。"""
    tool = event.get("tool_name", "")
    return {
        "type": "event",
        "v": 2,
        "event": {
            "kind":          "tool_done",
            "tool":          tool,
            "tool_category": _tool_category(tool),
            "duration_ms":   event.get("duration_ms", 0),
            "tool_use_id":   event.get("tool_use_id", ""),
        },
        "generic": _generic(event),
    }


def _normalize_post_tool_fail(event: dict) -> dict:
    err = _trunc(event.get("error", ""), 200)
    tool = event.get("tool_name", "")
    return {
        "type": "event",
        "v": 2,
        "event": {
            "kind":          "tool_error",
            "tool":          tool,
            "tool_category": _tool_category(tool),
            "error_msg":     err,
            "is_interrupt":  event.get("is_interrupt", False),
            "duration_ms":   event.get("duration_ms", 0),
            "tool_use_id":   event.get("tool_use_id", ""),
        },
        "generic": _generic(event),
    }


def _normalize_post_batch(event: dict) -> dict:
    """一批并行 tool 完成统一发一条;daemon 用作 task_complete 推断的强信号。"""
    calls = event.get("tool_calls") or []
    tools = []
    for c in calls:
        if isinstance(c, dict) and c.get("tool_name"):
            tools.append(c["tool_name"])
    return {
        "type": "event",
        "v": 2,
        "event": {
            "kind":       "tool_batch_done",
            "batch_size": len(calls),
            "tools":      tools[:8],  # 防设备显示过长
        },
        "generic": _generic(event),
    }


def _normalize_subagent_start(event: dict) -> dict:
    return {
        "type": "event",
        "v": 2,
        "event": {
            "kind":       "subagent_start",
            "agent_id":   event.get("agent_id", ""),
            "agent_type": event.get("agent_type", ""),
        },
        "generic": _generic(event),
    }


def _normalize_notification(event: dict) -> dict:
    """实测 notification_type 见过 'permission_prompt';其它子类型未观测,字段透传。"""
    msg = _trunc(event.get("message", ""), 200)
    return {
        "type": "event",
        "v": 2,
        "event": {
            "kind":              "notification",
            "notification_type": event.get("notification_type", ""),
            "message":           msg,
        },
        "generic": _generic(event),
    }


def _normalize_user_prompt(event: dict) -> dict:
    """用户提交 prompt → 强 turn_start 信号,daemon 用作清 idle / 启动 busy 状态。
    prompt 原文截 80 字给设备显示,避免敏感内容长尾。"""
    prompt = _trunc(event.get("prompt", ""), 80)
    return {
        "type": "event",
        "v": 2,
        "event": {
            "kind":   "user_prompt",
            "prompt": prompt,
        },
        "generic": _generic(event),
    }


def _normalize_stop_failure(event: dict) -> dict:
    """assistant turn 失败 (API timeout / stream error 等)。
    daemon 用作 task_error 信号,设备可显示 dizzy 状态。"""
    err = _trunc(event.get("error", ""), 200)
    last_msg = _trunc(event.get("last_assistant_message", ""), 200)
    return {
        "type": "event",
        "v": 2,
        "event": {
            "kind":                  "task_error",
            "error":                 err,
            "last_assistant_message": last_msg,
        },
        "generic": _generic(event),
    }


def _normalize_fallback(event: dict) -> dict:
    """未识别 hook (Stop / SessionStart / ... 23 类),daemon 会忽略 kind=unknown。"""
    return {
        "type": "event",
        "v": 2,
        "event": {"kind": "unknown"},
        "generic": _generic(event),
    }


NORMALIZERS = {
    "PreToolUse":         _normalize_pre_tool,
    "PostToolUse":        _normalize_post_tool,
    "PostToolUseFailure": _normalize_post_tool_fail,
    "PostToolBatch":      _normalize_post_batch,
    "SubagentStart":      _normalize_subagent_start,
    "Notification":       _normalize_notification,
    "UserPromptSubmit":   _normalize_user_prompt,
    "StopFailure":        _normalize_stop_failure,
}


def _call_daemon(envelope: dict) -> dict:
    """同步 socket 调用。daemon 不可达 / 超时 / JSON 错都 fail-open 返回 {}。
    connect 用短超时 (1s) 防 daemon 卡死时拖死 Claude Code,
    recv 用长超时 (35s) 覆盖 approval 窗口。"""
    try:
        with socket.create_connection((HOST, PORT), timeout=CONNECT_TIMEOUT) as s:
            s.settimeout(RECV_TIMEOUT)
            s.sendall(json.dumps(envelope).encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                return {}
            return json.loads(buf.decode("utf-8"))
    except Exception:
        return {}


def main():
    # 上限读 1MB:超大 tool_response (例如 Bash 长输出) 不该把 hook_bridge 撑爆,
    # 设备只能显几十字,后面又会再截 80 字,1MB 已经远超有用范围
    raw = sys.stdin.read(MAX_STDIN_BYTES).strip()
    if not raw:
        print(json.dumps({}))
        return
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({}))
        return

    hook = event.get("hook_event_name", "")
    normalize = NORMALIZERS.get(hook, _normalize_fallback)
    envelope = normalize(event)

    resp = _call_daemon(envelope)

    # 仅 PreToolUse on approval tool 时让 daemon 决定阻塞;其它 hook resp 一律忽略
    if hook == "PreToolUse" and resp.get("decision") == "deny":
        print(json.dumps({
            "decision": "block",
            "reason":   "Denied by hardware buddy",
        }))
    else:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
