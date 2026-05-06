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

# 离线风险分级测试序列（需配合 --offline-risk 使用）
# 期望结果：
#   safe  → auto-approve（无需人工干预）
#   normal → auto-approve（无需人工干预）
#   critical → CLI 提示用户输入（需要人工确认）
OFFLINE_RISK_SEQUENCE = [
    # safe：Read 工具 → 自动批准
    ("PreToolUse(safe/Read)",          "PreToolUse_safe.json",          None),
    ("PostToolUse(safe/Read)",         "PostToolUse.json",              {"tool_name": "Read",  "tool_use_id": "toolu_FIXTURE_SAFE01",
                                                                          "tool_response": {"interrupted": False}}),
    # normal：Bash ls → 自动批准
    ("PreToolUse(normal/Bash-ls)",     "PreToolUse_normal_bash.json",   None),
    ("PostToolUse(normal/Bash-ls)",    "PostToolUse.json",              {"tool_name": "Bash",  "tool_use_id": "toolu_FIXTURE_NORM01",
                                                                          "tool_response": {"interrupted": False}}),
    # normal：Write 普通文件 → 自动批准
    ("PreToolUse(normal/Write)",       "PreToolUse_normal_write.json",  None),
    ("PostToolUse(normal/Write)",      "PostToolUse.json",              {"tool_name": "Write", "tool_use_id": "toolu_FIXTURE_NORM02",
                                                                          "tool_response": {"interrupted": False}}),
    # critical：Bash rm -rf → CLI 提示（需人工输入 y/n）
    ("PreToolUse(critical/Bash-rmrf)", "PreToolUse_critical_bash.json", None),
    ("PostToolUse(critical/Bash)",     "PostToolUse.json",              {"tool_name": "Bash",  "tool_use_id": "toolu_FIXTURE_CRIT01",
                                                                          "tool_response": {"interrupted": False}}),
    # critical：Write .env → CLI 提示（需人工输入 y/n）
    ("PreToolUse(critical/Write-env)", "PreToolUse_critical_write.json",None),
    ("PostToolUse(critical/Write)",    "PostToolUse.json",              {"tool_name": "Write", "tool_use_id": "toolu_FIXTURE_CRIT02",
                                                                          "tool_response": {"interrupted": False}}),
    # critical：Edit .git/config → CLI 提示（需人工输入 y/n）
    ("PreToolUse(critical/Edit-git)",  "PreToolUse_critical_edit.json", None),
    ("PostToolUse(critical/Edit)",     "PostToolUse.json",              {"tool_name": "Edit",  "tool_use_id": "toolu_FIXTURE_CRIT03",
                                                                          "tool_response": {"interrupted": False}}),
]


# 重连测试序列：触发 PENDING → 掉电 → 重连 → 验证 PENDING 重推
RECONNECT_SEQUENCE = [
    ("UserPromptSubmit",          "UserPromptSubmit.json",        None),
    ("PreToolUse(normal/Bash-ls)", "PreToolUse_normal_bash.json",  None),  # 触发 PENDING
    # [此处 sim 暂停，等用户断开/重连设备]
    ("PostToolUse(normal/Bash-ls)", "PostToolUse_normal_bash.json", None),
]


def _wait_ble_disconnected(log_path: str, after_ts: float, timeout=30.0) -> bool:
    """等待 daemon 日志出现 disconnected，且时间戳晚于 after_ts。"""
    deadline = time.time() + timeout
    dots = 0
    while time.time() < deadline:
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            idx = content.rfind("[daemon] disconnected")
            if idx >= 0:
                return True
        except OSError:
            pass
        time.sleep(1.0)
        dots += 1
        print(f"\r[sim] 等待设备断开... {dots}s", end="", flush=True)
    print()
    return False


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
    """
    轮询 daemon 日志，检测当前 BLE 是否在线。
    用 rfind 比较最后一条 connected 与 disconnected 的位置，
    确保检测的是当前状态而非历史记录。
    """
    deadline = time.time() + timeout
    dots = 0
    while time.time() < deadline:
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            last_conn = content.rfind("[daemon] connected")
            last_disc = content.rfind("[daemon] disconnected")
            if last_conn >= 0 and last_conn > last_disc:
                if dots > 0:
                    print()  # 换行
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
    parser.add_argument("--stub",         action="store_true", help="以 --stub 模式启动 daemon（无设备测试）")
    parser.add_argument("--no-daemon",    action="store_true", help="daemon 已手动启动，跳过自动启动")
    parser.add_argument("--skip-ble-check", action="store_true", help="跳过 BLE 连接检测（daemon 已手动连接设备）")
    parser.add_argument("--offline-risk", action="store_true",
                        help="离线风险分级测试：以 --stub --offline 启动 daemon，跑 OFFLINE_RISK_SEQUENCE")
    parser.add_argument("--reconnect-test", action="store_true",
                        help="掉电重连测试：触发 PENDING 后暂停，等用户断开/重连设备，验证 PENDING 重推")
    args = parser.parse_args()

    # --offline-risk 隐含 --stub（无设备），并强制 daemon 以 --offline 启动
    offline_risk_mode = args.offline_risk

    # ── 1. 启动 daemon ────────────────────────────────────
    import tempfile
    LOG_PATH = os.path.join(tempfile.gettempdir(), "ble_daemon.log")

    daemon_proc = None
    if not args.no_daemon:
        if _wait_listen(timeout=1.0):
            print("[sim] daemon 已在运行，跳过自动启动")
        else:
            cmd = [sys.executable, "-u", BLE_DAEMON, "--log", LOG_PATH]
            if args.stub or offline_risk_mode:
                cmd.append("--stub")
            if offline_risk_mode:
                cmd.append("--offline")
            daemon_proc = subprocess.Popen(cmd)
            print(f"[sim] 启动 daemon (stub={args.stub})，日志: {LOG_PATH}")
            if not _wait_listen(timeout=8.0):
                print("[sim] FAIL: daemon 未能在 8s 内监听 57320，退出")
                daemon_proc.terminate()
                sys.exit(1)
            print(f"[sim] daemon 就绪")

    else:
        if not _wait_listen(timeout=1.0):
            print("[sim] WARN: --no-daemon 但 57320 不可达，hook_bridge 将 fail-open")

    # ── 2. 等待 BLE 连接（非 stub 模式）────────────────────
    if not args.stub and not offline_risk_mode and not args.skip_ble_check:
        print(f"[sim] 等待 ble_daemon 连上 ESP32（日志: {LOG_PATH}）...")
        if not _wait_ble_connected(LOG_PATH, timeout=90.0):
            print("[sim] FAIL: 90s 内未检测到 BLE 连接，退出")
            if daemon_proc:
                daemon_proc.terminate()
            sys.exit(1)
        print(f"\n[sim] BLE 已连接，开始发送事件")
    elif args.skip_ble_check:
        print(f"[sim] 跳过 BLE 连接检测（假设 daemon 已手动连接设备）")

    # ── 3. 选择测试序列 ───────────────────────────────────
    if offline_risk_mode:
        sequence = OFFLINE_RISK_SEQUENCE
        print(f"\n[sim] 离线风险分级测试模式（设备强制离线）")
        print(f"  safe/normal → 期望 auto-approve")
        print(f"  critical    → 期望 CLI 提示（需人工输入 y/n）")
    elif args.reconnect_test:
        sequence = RECONNECT_SEQUENCE
        print(f"\n[sim] 掉电重连测试模式")
        print(f"  步骤: PreToolUse(normal) → 暂停 → 断开设备 → 重连 → 验证 PENDING 重推")
    else:
        sequence = SEND_SEQUENCE

    print(f"\n[sim] 开始发送 {len(sequence)} 个 hook 事件\n{'─'*60}")

    # ── 4. 按顺序发送 ─────────────────────────────────────
    try:
        for label, filename, patch in sequence:
            print(f"\n[{label}]")
            raw = _load_fixture(filename, patch)
            fixture_json = json.dumps(raw, ensure_ascii=False).encode("utf-8")

            # 在线模式下 Bash PreToolUse 需要用户在设备上操作
            is_approval = (label == "PreToolUse(Bash)") and not args.stub and not offline_risk_mode

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

            # 离线风险模式：标注期望结果
            if offline_risk_mode and label.startswith("PreToolUse"):
                if "(safe" in label or "(normal" in label:
                    expected = "ONCE"
                    ok = status == expected
                    print(f"  expected : {expected}  {'✓ PASS' if ok else '✗ FAIL'}")
                elif "(critical" in label:
                    print(f"  expected : ONCE or DENY（取决于人工输入）")

            # 重连测试：PreToolUse 发出后暂停，等用户断开/重连设备
            if args.reconnect_test and label.startswith("PreToolUse"):
                reconnect_ts = time.time()
                print(f"\n  [reconnect] 请现在断开 ESP32 电源（30s 内）...")
                if _wait_ble_disconnected(LOG_PATH, after_ts=reconnect_ts, timeout=30.0):
                    print(f"\n  [reconnect] 检测到断开，请重新连接 ESP32（60s 内）...")
                    if _wait_ble_connected(LOG_PATH, timeout=60.0):
                        # 等 pusher 推送一次（最多 1s）
                        time.sleep(1.2)
                        try:
                            with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
                                log = f.read()
                            # 验证重连后有 PENDING 推送
                            reconnect_idx = log.rfind("[daemon] connected")
                            pending_after = log.find("'s': 'P'", reconnect_idx)
                            ok = pending_after >= 0
                            print(f"  [reconnect] 重连后 PENDING 重推: {'✓ PASS' if ok else '✗ FAIL (未检测到 PENDING)'}")
                        except OSError:
                            print(f"  [reconnect] 无法读取日志，跳过验证")
                    else:
                        print(f"\n  [reconnect] FAIL: 60s 内未重连")
                else:
                    print(f"\n  [reconnect] SKIP: 30s 内未检测到断开")

            # 模拟 Claude Code 真实节奏：工具间至少 1s（避免 BLE 推送过快导致设备丢包）
            time.sleep(1.0)

        print(f"\n{'='*60}")
        print(f"[sim] 全部 {len(sequence)} 个 hook 事件发送完毕")

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
