#!/usr/bin/env python3
"""Capture real Claude Code hook traffic through the daemon.

This does not replay fixtures. It starts the real daemon in --stub mode and can
optionally launch a real `claude -p` subprocess so Claude Code itself emits the
hooks.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import shutil
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DAEMON = ROOT / "daemon" / "ble_daemon.py"
DEFAULT_PORT = 57320
HOST = "127.0.0.1"

WIRE_RE = re.compile(r"\[stub-send\] t=([\d.]+) (.+)")
REQ_RE = re.compile(r"\[req v2\] session='([^']*)' kind='([^']*)'")


def find_claude_command() -> list[str]:
    direct = shutil.which("claude.exe") or shutil.which("claude.cmd") or shutil.which("claude")
    if direct:
        return [direct]

    node_bin = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / (
        "nodejs/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    )
    if node_bin.exists():
        return [str(node_bin)]

    ps1 = shutil.which("claude.ps1")
    if ps1:
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1]

    raise FileNotFoundError("could not find claude.exe/claude.cmd/claude.ps1 on PATH")


def wait_for_port(port: int, proc: subprocess.Popen, lines: list[str], timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"daemon exited before listening, code={proc.returncode}\n"
                + "".join(lines[-80:])
            )
        try:
            with socket.create_connection((HOST, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"daemon did not listen on {HOST}:{port} within {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / ".context" / "real_claude_wire_capture.jsonl",
    )
    parser.add_argument("--run-claude", action="store_true")
    parser.add_argument("--post-exit-wait", type=float, default=5.0)
    parser.add_argument(
        "--prompt",
        default='Read VERSION.txt, run `echo real-hook-ok`, then answer "done".',
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    try:
        with socket.create_connection((HOST, args.port), timeout=0.25):
            print(f"ERROR: {HOST}:{args.port} is already in use", file=sys.stderr)
            return 2
    except OSError:
        pass

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLAUDE_BUDDY_PORT"] = str(args.port)

    proc = subprocess.Popen(
        [sys.executable, "-u", str(DAEMON), "--stub"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    lines: list[str] = []
    events: list[dict] = []
    lock = threading.Lock()
    stop_reader = False

    def reader() -> None:
        nonlocal stop_reader
        assert proc.stdout is not None
        with args.out.open("w", encoding="utf-8") as f:
            for line in proc.stdout:
                line = line.rstrip("\n")
                with lock:
                    lines.append(line + "\n")
                    if len(lines) > 300:
                        lines.pop(0)

                rec = {"ts": time.time(), "raw": line}
                m_req = REQ_RE.search(line)
                if m_req:
                    rec.update({"type": "event", "session": m_req.group(1), "kind": m_req.group(2)})
                m_wire = WIRE_RE.search(line)
                if m_wire:
                    try:
                        rec.update({
                            "type": "wire",
                            "wire_ts": float(m_wire.group(1)),
                            "wire": json.loads(m_wire.group(2)),
                        })
                    except json.JSONDecodeError:
                        rec.update({"type": "wire_parse_error"})

                if rec.get("type"):
                    events.append(rec)
                    print(json.dumps(rec, ensure_ascii=False))
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                elif "listening on" in line or "日志文件" in line:
                    print(line)

                if stop_reader:
                    return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    try:
        wait_for_port(args.port, proc, lines, timeout=8.0)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        proc.terminate()
        return 2

    print("\nREADY: real daemon stub is listening.")
    claude_proc: Optional[subprocess.Popen] = None
    if args.run_claude:
        print(f"Launching real Claude Code: claude -p {args.prompt!r}")
        claude_env = os.environ.copy()
        claude_env["CLAUDE_BUDDY_PORT"] = str(args.port)
        claude_cmd = find_claude_command()
        claude_proc = subprocess.Popen(
            claude_cmd + ["-p", args.prompt],
            cwd=str(ROOT),
            env=claude_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    else:
        print("Open another PowerShell and run:")
        print(f'  cd "{ROOT}"')
        print("  claude")
        print(f"Prompt: {args.prompt}")

    print(f"\nCapturing for {args.seconds}s. Press Ctrl+C here to stop early.\n")

    deadline = time.time() + args.seconds
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                print(f"daemon exited with code {proc.returncode}", file=sys.stderr)
                return 2
            if claude_proc is not None and claude_proc.poll() is not None:
                time.sleep(args.post_exit_wait)
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop_reader = True
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    if claude_proc is not None:
        if claude_proc.poll() is None:
            claude_proc.terminate()
            try:
                claude_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                claude_proc.kill()
                claude_proc.wait(timeout=5)
        out = claude_proc.stdout.read() if claude_proc.stdout is not None else ""
        print("\nCLAUDE SUBPROCESS")
        print(f"  exit: {claude_proc.returncode}")
        if out.strip():
            print("  output:")
            for line in out.strip().splitlines()[-40:]:
                print(f"    {line}")

    wire_states = []
    kinds = []
    for rec in events:
        if rec.get("type") == "event":
            kinds.append(rec.get("kind"))
        if rec.get("type") == "wire":
            states = [s.get("s") for s in rec.get("wire", {}).get("ss", [])]
            wire_states.append(states)

    print("\nSUMMARY")
    print(f"  events: {kinds}")
    print(f"  wire states: {wire_states}")
    print(f"  saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
