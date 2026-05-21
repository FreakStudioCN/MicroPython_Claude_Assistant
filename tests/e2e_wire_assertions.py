#!/usr/bin/env python3
# tests/e2e_wire_assertions.py —— 真实子进程链路 e2e 测试
#
# 链路：fixture JSON
#   → spawn `python daemon/hook_bridge.py` 子进程，stdin 灌 envelope
#   → hook_bridge 真实 normalize + TCP 发 envelope 到 daemon
#   → spawn `python daemon/ble_daemon.py --stub` 子进程，监听 TCP
#   → daemon 收 envelope → state machine → _pusher_task → _send → stdout `[stub-send] t=... <wire>`
#   → e2e 主进程后台线程读 stdout 抓 wire
#   → 断言
#
# 关键设计：用 CLAUDE_BUDDY_PORT=57321 env var 让 e2e daemon/hook_bridge 用临时端口，
# 避开生产 claude-buddy-daemon 占的 57320。两套 daemon 并存互不影响。
#
# 覆盖：hook_bridge.NORMALIZERS + TCP 序列化 + daemon state machine + async pusher 5Hz + _send
#
# 跑法：python tests/e2e_wire_assertions.py
# 退出码：0 全 PASS / 1 任意 FAIL / 2 infra error

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DAEMON = ROOT / "daemon" / "ble_daemon.py"
HOOK_BRIDGE = ROOT / "daemon" / "hook_bridge.py"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "probe_samples"

# e2e 用隔离端口，避开生产 daemon (57320)
E2E_PORT = 57321
HOST = "127.0.0.1"

WIRE_RE = re.compile(r"\[stub-send\] t=([\d.]+) (.+)")

# daemon 常量（同步自 ble_daemon.py）
COMPLETED_HOLD_S = 2.0
DIZZY_HOLD_S = 3.0
PUSH_INTERVAL_S = 0.2  # 5Hz

# 5Hz tick + TCP 往返 + python 启动开销，每条 hook 后等的余量
SETTLE_S = 0.6


# ── daemon 子进程 + wire 抓取 ───────────────────────────────
class DaemonRunner:
    def __init__(self, port: int, keep_stdout: bool = False):
        self.port = port
        self.proc: Optional[subprocess.Popen] = None
        self.wires: list[tuple[float, dict]] = []
        self.raw_lines: list[str] = []
        self._keep_stdout = keep_stdout
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_flag = False

    def start(self, listen_timeout=8.0) -> None:
        # 端口必须空闲（用我们隔离的 57321，生产 57320 不动）
        try:
            s = socket.create_connection((HOST, self.port), timeout=0.3)
            s.close()
            raise RuntimeError(
                f"port {HOST}:{self.port} already in use. "
                f"e2e 用隔离端口 {self.port}，理论上不该被占。检查是不是上次 e2e 没退干净："
                f"netstat -ano | findstr :{self.port}"
            )
        except OSError:
            pass

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["CLAUDE_BUDDY_PORT"] = str(self.port)

        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(DAEMON), "--stub"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            cwd=str(ROOT),
        )
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

        deadline = time.time() + listen_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"daemon exited with code {self.proc.returncode} before listening\n"
                    f"--- daemon output ---\n{''.join(self.raw_lines[-50:])}"
                )
            try:
                s = socket.create_connection((HOST, self.port), timeout=0.3)
                s.close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(
            f"daemon did not start listening on {HOST}:{self.port} within {listen_timeout}s\n"
            f"--- daemon output ---\n{''.join(self.raw_lines[-50:])}"
        )

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            if self._keep_stdout:
                self.raw_lines.append(line)
            else:
                # 不留全文也保留最后 100 行兜底诊断
                self.raw_lines.append(line)
                if len(self.raw_lines) > 100:
                    self.raw_lines.pop(0)
            stripped = line.rstrip("\n")
            m = WIRE_RE.search(stripped)
            if m:
                try:
                    ts = float(m.group(1))
                    payload = json.loads(m.group(2))
                    self.wires.append((ts, payload))
                except (ValueError, json.JSONDecodeError):
                    pass
            if self._stop_flag:
                return

    def latest_session(self) -> Optional[dict]:
        if not self.wires:
            return None
        _, payload = self.wires[-1]
        sessions = payload.get("ss", [])
        if not sessions:
            return None
        return sessions[0]

    def wire_count(self) -> int:
        return len(self.wires)

    def stop(self) -> None:
        self._stop_flag = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ── hook 子进程灌入（真实 hook_bridge.py） ─────────────────
def send_hook(fixture_name: str, port: int, patch: Optional[dict] = None) -> tuple[str, str, int]:
    """
    spawn `python daemon/hook_bridge.py`, stdin 喂 fixture JSON。
    hook_bridge 真实 normalize + TCP 发到 daemon (port)。
    返回 (stdout, stderr, returncode)。
    """
    path = FIXTURE_DIR / fixture_name
    data = json.loads(path.read_text(encoding="utf-8"))
    if patch:
        data.update(patch)
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLAUDE_BUDDY_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, str(HOOK_BRIDGE)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(ROOT),
    )
    try:
        out, err = proc.communicate(input=payload, timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return (out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
            proc.returncode)


# ── 断言层 ──────────────────────────────────────────────────
class Asserter:
    def __init__(self, name: str):
        self.name = name
        self.passes = 0
        self.failures: list[str] = []

    def eq(self, actual, expected, label: str) -> bool:
        if actual == expected:
            print(f"  PASS  {label}: {actual!r}")
            self.passes += 1
            return True
        msg = f"FAIL  {label}: expected {expected!r}, got {actual!r}"
        print(f"  {msg}")
        self.failures.append(msg)
        return False

    def true(self, cond: bool, label: str) -> bool:
        return self.eq(bool(cond), True, label)


# ── 场景：BASELINE ─────────────────────────────────────────
def scenario_baseline(d: DaemonRunner) -> Asserter:
    sid = "S-E2E-BASELINE"
    print(f"\n[SCENARIO BASELINE]  expect wire: W → W(m=Read) → W → C → I")
    a = Asserter("BASELINE")

    # Step 1: UserPromptSubmit → s=W
    send_hook("UserPromptSubmit.json", d.port, {"session_id": sid})
    time.sleep(SETTLE_S)
    s = d.latest_session()
    a.true(s is not None, "wire emitted after UserPromptSubmit")
    if s:
        a.eq(s.get("s"), "W", "step1 UserPromptSubmit → s=W")

    # Step 2: PreToolUse(Read) → s=W m='Read: ...'
    send_hook("PreToolUse.json", d.port, {
        "session_id": sid,
        "tool_name": "Read",
        "tool_use_id": "toolu_BASELINE_1",
        "tool_input": {"file_path": "/etc/hosts"},
    })
    time.sleep(SETTLE_S)
    s = d.latest_session()
    if s:
        a.eq(s.get("s"), "W", "step2 PreToolUse(Read) → s=W")
        a.true("Read" in (s.get("m") or ""), "step2 wire m contains 'Read'")

    # Step 3: PostToolUse(Read) → tools 清, turn_active 仍 True → s=W
    send_hook("PostToolUse.json", d.port, {
        "session_id": sid,
        "tool_name": "Read",
        "tool_use_id": "toolu_BASELINE_1",
        "tool_response": {"interrupted": False},
    })
    time.sleep(SETTLE_S)
    s = d.latest_session()
    if s:
        a.eq(s.get("s"), "W", "step3 PostToolUse(Read) → s=W (turn still active)")

    # Step 4: Stop → s=C
    send_hook("Stop.json", d.port, {"session_id": sid})
    time.sleep(SETTLE_S)
    s = d.latest_session()
    if s:
        a.eq(s.get("s"), "C", "step4 Stop → s=C")

    # Step 5: 等 COMPLETED_HOLD_S 过期 → s=I
    time.sleep(COMPLETED_HOLD_S + SETTLE_S)
    s = d.latest_session()
    if s:
        a.eq(s.get("s"), "I", "step5 after COMPLETED_HOLD_S → s=I")

    return a


# ── 场景：A1 StopFailure ───────────────────────────────────
def scenario_a1_stopfailure(d: DaemonRunner) -> Asserter:
    sid = "S-E2E-A1"
    print(f"\n[SCENARIO A1_STOPFAILURE]  expect wire: W → E → I  (NOT 卡 W 或 E)")
    print(f"  修前预期 FAIL: _enter_error_state 不清 turn_active + current_error 永远不过期")
    print(f"  PR-A 修复后应 PASS: task_error handler 清 turn_active + dizzy 过期同步清 current_error")
    a = Asserter("A1_STOPFAILURE")

    # Step 1: UserPromptSubmit → s=W
    send_hook("UserPromptSubmit.json", d.port, {"session_id": sid})
    time.sleep(SETTLE_S)
    s = d.latest_session()
    a.true(s is not None, "wire emitted after UserPromptSubmit")
    if s:
        a.eq(s.get("s"), "W", "step1 UserPromptSubmit → s=W")

    # Step 2: StopFailure → hook_bridge 转 task_error → daemon 进 dizzy
    send_hook("StopFailure.json", d.port, {"session_id": sid})
    time.sleep(SETTLE_S)
    s = d.latest_session()
    if s:
        a.eq(s.get("s"), "E", "step2 StopFailure → s=E (dizzy active)")

    # Step 3 (关键): 等 DIZZY_HOLD_S 过期，看 turn_active / current_error 是否被清
    # 修前：current_error 永远不空 → s=E 永久卡（也是 A-4 表现）
    # 修后：s=I
    time.sleep(DIZZY_HOLD_S + SETTLE_S)
    s = d.latest_session()
    if s:
        observed = s.get("s")
        print(f"  observation after dizzy expires: s={observed!r}")
        a.true(observed != "W", f"step3 NOT W (got {observed!r})")
        a.true(observed != "E", f"step3 NOT E -- error 应过期 (got {observed!r})")
        a.eq(observed, "I", "step3 strict: after dizzy → s=I")

    return a


# ── 入口 ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["baseline", "a1"], default=None)
    parser.add_argument("--keep-daemon-out", action="store_true",
                        help="保留 daemon stdout 全文供 debug")
    parser.add_argument("--port", type=int, default=E2E_PORT,
                        help=f"e2e 隔离端口（默认 {E2E_PORT}，避开生产 57320）")
    args = parser.parse_args()

    if not FIXTURE_DIR.exists():
        print(f"infra ERROR: fixture dir missing: {FIXTURE_DIR}", file=sys.stderr)
        sys.exit(2)
    for name in ("UserPromptSubmit.json", "PreToolUse.json", "PostToolUse.json",
                 "Stop.json", "StopFailure.json"):
        if not (FIXTURE_DIR / name).exists():
            print(f"infra ERROR: missing fixture {name}", file=sys.stderr)
            sys.exit(2)

    print(f"== e2e wire assertions (real subprocess link) ==")
    print(f"daemon: {DAEMON.relative_to(ROOT)}  --stub  CLAUDE_BUDDY_PORT={args.port}")
    print(f"hook_bridge: {HOOK_BRIDGE.relative_to(ROOT)}  CLAUDE_BUDDY_PORT={args.port}")
    print(f"fixtures: {FIXTURE_DIR.relative_to(ROOT)}")
    print(f"settle={SETTLE_S}s  push={PUSH_INTERVAL_S}s  "
          f"completed_hold={COMPLETED_HOLD_S}s  dizzy_hold={DIZZY_HOLD_S}s")

    asserters: list[Asserter] = []
    all_daemon_lines: list[str] = []

    # 每个 scenario 独立 daemon，避免 session 残留污染。
    scenarios = []
    if args.only in (None, "baseline"):
        scenarios.append(("BASELINE", scenario_baseline))
    if args.only in (None, "a1"):
        scenarios.append(("A1_STOPFAILURE", scenario_a1_stopfailure))

    for name, fn in scenarios:
        d = DaemonRunner(port=args.port, keep_stdout=args.keep_daemon_out)
        print(f"\nstarting daemon --stub for {name} ...")
        try:
            d.start()
        except RuntimeError as e:
            print(f"infra ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"daemon listening on {HOST}:{args.port}  (wires so far: {d.wire_count()})")
        try:
            asserters.append(fn(d))
        finally:
            if args.keep_daemon_out:
                all_daemon_lines.extend([f"=== {name} ===\n"])
                all_daemon_lines.extend(d.raw_lines)
            d.stop()

    print(f"\n{'=' * 60}\nSUMMARY")
    total_pass = total_fail = 0
    for a in asserters:
        status = "PASS" if not a.failures else "FAIL"
        print(f"  {status}  {a.name}  ({a.passes} ok, {len(a.failures)} fail)")
        total_pass += a.passes
        total_fail += len(a.failures)
    print(f"{'=' * 60}")
    print(f"total: pass={total_pass}  fail={total_fail}")

    if args.keep_daemon_out and all_daemon_lines:
        out_path = ROOT / ".context" / "e2e_daemon_stdout.txt"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text("".join(all_daemon_lines), encoding="utf-8")
        print(f"daemon stdout dumped to {out_path}")

    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
