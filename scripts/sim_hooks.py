#!/usr/bin/env python3
# scripts/sim_hooks.py —— 手动集成测试：模拟完整 turn 的 hook 触发链路
#
# 用途: 无需修改 settings.json，模拟 Claude Code 触发 8 种 hook 事件
#       → hook_bridge.py（真实运行）→ ble_daemon.py（真实运行）→ BLE → ESP32
#
# 运行前提: ESP32 已烧录固件并开机，或先手动启动 ble_daemon.py --stub 做无设备测试
#
# 跑法:
#   python scripts/sim_hooks.py             # 自动启动 daemon（需要 ESP32）
#   python scripts/sim_hooks.py --stub      # 自动启动 daemon --stub（无设备）
#   python scripts/sim_hooks.py --no-daemon # daemon 已手动启动，跳过自动启动

import argparse
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "probe_samples")
HOOK_BRIDGE = os.path.join(ROOT, "daemon", "hook_bridge.py")
BLE_DAEMON   = os.path.join(ROOT, "daemon", "ble_daemon.py")
HOST, PORT   = "127.0.0.1", 57320

# 模拟一个完整 turn 的发送顺序
# PreToolUse(Read)  → 无需审批，验证 BUSY 状态
# PreToolUse(Bash)  → 需审批，等用户 ESP32 触摸（60s 内）
# 两条 PreToolUse 用同一个 fixture 文件，Bash 版本通过 patch 注入
SEND_SEQUENCE = [
    ("UserPromptSubmit",   "UserPromptSubmit.json",   None),
    ("SubagentStart",      "SubagentStart.json",       None),
    ("PreToolUse(Read)",   "PreToolUse.json",          {"tool_name": "Read",  "tool_use_id": "toolu_SIM_READ1",
                                                         "tool_input": {"file_path": "/etc/hosts"}}),
    ("PostToolUse(Read)",  "PostToolUse.json",         {"tool_name": "Read",  "tool_use_id": "toolu_SIM_READ1",
                                                         "tool_response": {"interrupted": False}}),
    ("PreToolUse(Bash)",   "PreToolUse.json",          None),   # 原始 fixture = Bash，需审批
    ("PostToolUse(Bash)",  "PostToolUse.json",         None),   # 原始 fixture = Bash
    ("PostToolBatch",      "PostToolBatch.json",       None),
    ("PostToolUseFailure", "PostToolUseFailure.json",  None),
    ("Notification",       "Notification.json",        None),
    ("StopFailure",        "StopFailure.json",         None),
]


def _wait_listen(timeout=8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((HOST, PORT), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def _wait_ble_connected(log_path: str, timeout=60.0) -> bool:
    """轮询 daemon 日志，等待出现 [daemon] connected 表示 BLE 已连上 ESP32。"""
    deadline = time.time() + timeout
    dots = 0
    while time.time() < deadline:
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            if "[daemon] connected" in content:
                return True
        except OSError:
            pass
        time.sleep(1.0)
        dots += 1
        print(f"\r[sim] 等待 ESP32 BLE 连接... {dots}s", end="", flush=True)
    print()
    return False


def _load_fixture(filename: str, patch) -> dict:
    path = os.path.join(FIXTURE_DIR, filename)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if patch:
        data.update(patch)
    return data


def _send_hook(label: str, fixture_json: bytes, approval_step: bool) -> tuple[str, float]:
    """
    用 communicate(input=...) 把 fixture_json 喂给 hook_bridge.py 的 stdin。
    返回 (stdout输出, 耗时秒数)。

    communicate() 内部用线程同时：
      1. 写完 stdin 后立刻关闭（发送 EOF，hook_bridge.read() 才能结束）
      2. 并发读取 stdout，防止 stdout 缓冲区塞满导致子进程卡死
    两件事同步完成后才返回，彻底规避死锁。
    """
    if approval_step:
        print(f"  !! 请在 ESP32 屏幕上触摸 YES 按钮（最多 60s）...")

    t0 = time.time()
    proc = subprocess.Popen(
        [sys.executable, HOOK_BRIDGE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # timeout 覆盖 approval 窗口 60s + 缓冲
    stdout, stderr = proc.communicate(input=fixture_json, timeout=75)
    elapsed = time.time() - t0

    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    if err:
        print(f"  [stderr] {err}")
    return out, elapsed


def main():
    parser = argparse.ArgumentParser(description="模拟 Claude Code hook 触发，测试完整链路")
    parser.add_argument("--stub",      action="store_true", help="以 --stub 模式启动 daemon（无设备测试）")
    parser.add_argument("--no-daemon", action="store_true", help="daemon 已手动启动，跳过自动启动")
    args = parser.parse_args()

    # ── 1. 启动 daemon ────────────────────────────────────
    daemon_proc = None
    if not args.no_daemon:
        if _wait_listen(timeout=1.0):
            print("[sim] daemon 已在运行，跳过自动启动")
        else:
            cmd = [sys.executable, "-u", BLE_DAEMON]
            if args.stub:
                cmd.append("--stub")
            import tempfile
            log_path = os.path.join(tempfile.gettempdir(), "sim_hooks_daemon.log")
            log_f = open(log_path, "w")
            daemon_proc = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT)
            print(f"[sim] 启动 daemon (stub={args.stub})，日志: {log_path}")
            if not _wait_listen(timeout=8.0):
                print("[sim] FAIL: daemon 未能在 8s 内监听 57320，退出")
                daemon_proc.terminate()
                sys.exit(1)
            print(f"[sim] daemon 就绪")

    else:
        if not _wait_listen(timeout=1.0):
            print("[sim] WARN: --no-daemon 但 57320 不可达，hook_bridge 将 fail-open")

    # ── 2. 等待 BLE 连接（非 stub 模式）────────────────────
    if not args.stub:
        import tempfile
        log_path = os.path.join(tempfile.gettempdir(), "sim_hooks_daemon.log")
        print(f"[sim] 等待 ble_daemon 连上 ESP32（请先运行 ESP32 的 main_mvp.py）...")
        if not _wait_ble_connected(log_path, timeout=90.0):
            print("[sim] FAIL: 90s 内未检测到 BLE 连接，退出")
            if daemon_proc:
                daemon_proc.terminate()
            sys.exit(1)
        print(f"\n[sim] BLE 已连接，开始发送事件")

    print(f"\n[sim] 开始发送 {len(SEND_SEQUENCE)} 个 hook 事件\n{'─'*60}")

    # ── 2. 按顺序发送 ─────────────────────────────────────
    try:
        for label, filename, patch in SEND_SEQUENCE:
            print(f"\n[{label}]")
            raw = _load_fixture(filename, patch)
            fixture_json = json.dumps(raw, ensure_ascii=False).encode("utf-8")

            # Bash PreToolUse 需要用户在设备上操作
            is_approval = (label == "PreToolUse(Bash)") and not args.stub

            try:
                out, elapsed = _send_hook(label, fixture_json, is_approval)
            except subprocess.TimeoutExpired:
                print(f"  TIMEOUT (>75s)，hook_bridge 未在时限内返回")
                continue

            try:
                result = json.loads(out) if out else {}
            except json.JSONDecodeError:
                result = {"raw": out}

            status = "DENY" if result.get("decision") == "deny" else ("ONCE" if result.get("decision") == "once" else "ok")
            print(f"  fixture  : {filename}{' + patch' if patch else ''}")
            print(f"  response : {result}  [{status}]  ({elapsed:.2f}s)")
            time.sleep(0.3)

        print(f"\n{'='*60}")
        print(f"[sim] 全部 {len(SEND_SEQUENCE)} 个 hook 事件发送完毕")

    finally:
        if daemon_proc is not None:
            print("[sim] 终止 daemon")
            daemon_proc.terminate()
            try:
                daemon_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()


if __name__ == "__main__":
    main()
