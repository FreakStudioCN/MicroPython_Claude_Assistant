#!/usr/bin/env python3
"""Check that Claude Code's installed Claude Buddy files match required hooks."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()

REQUIRED_HOOKS = {
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "Stop",
    "StopFailure",
    "SessionEnd",
}

REQUIRED_NORMALIZER_SNIPPETS = [
    '"Stop":',
    "_normalize_stop",
    '"SessionEnd":',
    '"StopFailure":',
    '"PostToolUseFailure":',
    '"Notification":',
]

REQUIRED_DAEMON_SNIPPETS = [
    'os.environ.get("CLAUDE_BUDDY_PORT"',
    'kind == "stop"',
    'last_activity_ts = now',
    'kind == "session_end"',
    "last_stop_ts",
    "idle_prompt",
    "elicitation_dialog",
]


def load_hooks(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return set((data.get("hooks") or {}).keys())


def check_hooks(path: Path) -> list[str]:
    if not path.exists():
        return [f"missing {path}"]
    hooks = load_hooks(path)
    missing = sorted(REQUIRED_HOOKS - hooks)
    return [f"{path}: missing hooks {missing}"] if missing else []


def check_bridge(path: Path) -> list[str]:
    if not path.exists():
        return [f"missing {path}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [s for s in REQUIRED_NORMALIZER_SNIPPETS if s not in text]
    return [f"{path}: missing bridge snippets {missing}"] if missing else []


def check_daemon(path: Path) -> list[str]:
    if not path.exists():
        return [f"missing {path}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [s for s in REQUIRED_DAEMON_SNIPPETS if s not in text]
    return [f"{path}: missing daemon snippets {missing}"] if missing else []


def main() -> int:
    paths = [
        ROOT / "hooks" / "hooks.json",
        HOME / ".claude-buddy" / "hooks" / "hooks.json",
        HOME / ".claude" / "plugins" / "cache" / "claude-buddy" / "claude-buddy-bridge" / "0.1.0" / "hooks" / "hooks.json",
    ]
    bridge_paths = [
        ROOT / "daemon" / "hook_bridge.py",
        HOME / ".claude-buddy" / "daemon" / "hook_bridge.py",
        HOME / ".claude" / "plugins" / "cache" / "claude-buddy" / "claude-buddy-bridge" / "0.1.0" / "daemon" / "hook_bridge.py",
    ]
    daemon_paths = [
        ROOT / "daemon" / "ble_daemon.py",
        HOME / ".claude-buddy" / "daemon" / "ble_daemon.py",
        HOME / ".claude" / "plugins" / "cache" / "claude-buddy" / "claude-buddy-bridge" / "0.1.0" / "daemon" / "ble_daemon.py",
    ]

    failures: list[str] = []
    for path in paths:
        failures.extend(check_hooks(path))
    for path in bridge_paths:
        failures.extend(check_bridge(path))
    for path in daemon_paths:
        failures.extend(check_daemon(path))

    if failures:
        print("FAIL real install consistency")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("PASS real install consistency")
    for path in paths + bridge_paths + daemon_paths:
        print(f"  ok {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
