#!/usr/bin/env python3
# hook_bridge.py —— Claude Code hook 轻量客户端，连接 ble_daemon.py
# 启动 daemon：python ble_daemon.py

import sys
import json
import socket

HOST = "127.0.0.1"
PORT = 57320


def _call_daemon(req: dict) -> dict:
    try:
        with socket.create_connection((HOST, PORT), timeout=35) as s:
            s.sendall(json.dumps(req).encode())
            s.shutdown(socket.SHUT_WR)
            data = b""
            while chunk := s.recv(4096):
                data += chunk
            return json.loads(data.decode())
    except Exception:
        return {}


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({}))
        return

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({}))
        return

    hook = event.get("hook_event_name", "")

    if hook == "PreToolUse":
        tool = event.get("tool_name", "")
        print(f"[hook] tool={tool!r} hook={hook!r}", file=sys.stderr)
        tool_input = event.get("tool_input", {})
        hint = (
            tool_input.get("command") or
            tool_input.get("path") or
            str(tool_input)[:80]
        )
        resp = _call_daemon({"type": "pre", "tool": tool, "hint": hint})
        if resp.get("decision") == "deny":
            print(json.dumps({"decision": "block", "reason": "Denied by hardware buddy"}))
        else:
            print(json.dumps({}))

    elif hook == "PostToolUse":
        tool = event.get("tool_name", "")
        success = event.get("tool_response", {}).get("exit_code", 0) == 0
        print(f"[hook] PostToolUse tool={tool!r} success={success}", file=sys.stderr)
        _call_daemon({"type": "post", "tool": tool, "success": success})
        print(json.dumps({}))

    elif hook == "Stop":
        _call_daemon({"type": "stop"})
        print(json.dumps({}))

    else:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
