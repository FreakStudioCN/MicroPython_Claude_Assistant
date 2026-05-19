#!/usr/bin/env python3
"""Analyze real Claude Code -> daemon wire capture JSONL files."""

import argparse
import json
import sys
from pathlib import Path

BAD_FINAL_STATES = {"W", "P", "E"}


def load_records(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def states_from_wire(rec: dict) -> list[str]:
    return [s.get("s", "?") for s in rec.get("wire", {}).get("ss", [])]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--expect-stop", action="store_true")
    parser.add_argument("--expect-ci", action="store_true", help="require C then I after completion")
    parser.add_argument("--expect-event", action="append", default=[])
    parser.add_argument("--allow-task-error", action="store_true")
    parser.add_argument("--no-stuck-final", action="store_true")
    args = parser.parse_args()

    if not args.capture.exists():
        print(f"FAIL missing capture: {args.capture}")
        return 1

    records = load_records(args.capture)
    events = [r.get("kind") for r in records if r.get("type") == "event"]
    wires = [states_from_wire(r) for r in records if r.get("type") == "wire"]
    flat_states = [s for states in wires for s in states]
    final_state = flat_states[-1] if flat_states else None

    failures: list[str] = []

    if args.expect_stop and "stop" not in events and "session_end" not in events:
        if not (args.allow_task_error and "task_error" in events):
            failures.append("expected stop/session_end event, got none")

    for expected in args.expect_event:
        if expected not in events:
            failures.append(f"expected event {expected!r}, got none")

    if args.expect_ci:
        try:
            c_idx = flat_states.index("C")
        except ValueError:
            c_idx = -1
            failures.append("expected wire state C, got none")
        if c_idx >= 0 and "I" not in flat_states[c_idx + 1:]:
            failures.append("expected wire state I after C, got none")

    if args.allow_task_error and "task_error" in events:
        try:
            e_idx = flat_states.index("E")
        except ValueError:
            e_idx = -1
            failures.append("task_error occurred but E state was not emitted")
        if e_idx >= 0 and "I" not in flat_states[e_idx + 1:]:
            failures.append("expected I after E, got none")

    if args.no_stuck_final and final_state in BAD_FINAL_STATES:
        failures.append(f"final wire state is stuck-looking: {final_state}")

    print(f"capture: {args.capture}")
    print(f"events: {events}")
    print(f"wire states: {wires}")
    print(f"final state: {final_state}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
