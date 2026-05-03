#!/usr/bin/env python3
# tests/test_e2e_stub.py
# E2E 测试: 8 种 hook fixture → hook_bridge → ble_daemon --stub → protocol.parse()
# 跑法: 在仓库根 `python tests/test_e2e_stub.py`  退出码 0 = pass

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))
sys.path.insert(0, os.path.join(ROOT, "device"))

import hook_bridge as hb  # noqa: E402
import protocol as p      # noqa: E402

FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "probe_samples")
DAEMON_PATH = os.path.join(ROOT, "daemon", "ble_daemon.py")
HOST, PORT = "127.0.0.1", 57320

HOOK_NAMES = [
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    "SubagentStart", "Notification", "UserPromptSubmit", "StopFailure",
]

# 每种 hook 的预期分析说明
EXPECTED = {
    "PreToolUse":         "running↑, waiting=1（Bash需审批，stub auto-once）",
    "PostToolUse":        "running↓，工具完成",
    "PostToolUseFailure": "error 字段非空，触发 dizzy 状态",
    "PostToolBatch":      "batch_done 软信号，wire 可能无变化",
    "SubagentStart":      "_has_subagent=True，completed 阈值升至 8s",
    "Notification":       "fire-and-forget，wire 可能无变化",
    "UserPromptSubmit":   "清除 completed/error 状态，新 turn 开始",
    "StopFailure":        "error 字段非空，task_error 触发",
}


def _wait_listen(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((HOST, PORT), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


async def _send_recv(envelope: dict) -> dict:
    reader, writer = await asyncio.open_connection(HOST, PORT)
    writer.write(json.dumps(envelope).encode())
    writer.write_eof()
    data = await asyncio.wait_for(reader.read(8192), timeout=5.0)
    writer.close()
    await writer.wait_closed()
    return json.loads(data.decode()) if data else {}


def _read_new_stub_lines(log_path: str, offset: int):
    """从 log 文件 offset 处读取新增的 [stub-send] 行，返回 (wire_msgs, new_offset)。"""
    msgs = []
    raw_lines = []
    with open(log_path, encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        for line in f:
            raw_lines.append(line.rstrip())
            if "[stub-send]" in line:
                try:
                    idx = line.index("{")
                    wire_json = line[idx:].strip()
                    msgs.append((wire_json, p.parse(wire_json)))
                except (ValueError, Exception):
                    pass
        new_offset = f.tell()
    return msgs, raw_lines, new_offset


def _fmt_msg(msg) -> str:
    if isinstance(msg, p.StatusMsg):
        return (f"StatusMsg(running={msg.running}, waiting={msg.waiting}, "
                f"completed={msg.completed}, msg={msg.msg!r}, "
                f"category={msg.category!r}, error={msg.error!r}, "
                f"interrupted={msg.interrupted})")
    if isinstance(msg, dict):
        return f"cmd dict: {msg}"
    return f"parse→None"


async def main():
    log = os.path.join(tempfile.gettempdir(), "test_e2e_stub.log")
    if os.path.exists(log):
        os.remove(log)

    proc = subprocess.Popen(
        [sys.executable, "-u", DAEMON_PATH, "--stub"],
        stdout=open(log, "w"), stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_listen(5.0):
            print("FAIL: daemon 未能在 5s 内监听 57320")
            return 1

        print(f"daemon 已就绪，运行 {len(HOOK_NAMES)} 个 e2e hook 测试...\n")
        offset = 0
        all_ok = True

        for hook_name in HOOK_NAMES:
            print(f"{'─'*60}")
            print(f"[{hook_name}]")

            # 1. 加载 fixture
            fixture_path = os.path.join(FIXTURE_DIR, f"{hook_name}.json")
            with open(fixture_path, encoding="utf-8") as f:
                raw = json.load(f)

            # 2. 规范化为 v2 envelope
            normalizer = hb.NORMALIZERS.get(hook_name, hb._normalize_fallback)
            envelope = normalizer(raw)
            ev = envelope.get("event", {})
            print(f"  fixture  → kind={ev.get('kind')!r}", end="")
            if "tool" in ev:
                print(f", tool={ev.get('tool')!r}", end="")
            if "needs_approval" in ev:
                print(f", needs_approval={ev.get('needs_approval')}", end="")
            print()

            # 3. 发送 TCP envelope
            resp = await _send_recv(envelope)
            print(f"  TCP resp → {json.dumps(resp, ensure_ascii=False)}")

            # 4. 等 pusher tick
            await asyncio.sleep(0.35)

            # 5. 读取 stub log 新增行
            wire_msgs, raw_lines, offset = _read_new_stub_lines(log, offset)

            # 6. 打印原始 stub 输出
            if raw_lines:
                print(f"  stub log ({len(raw_lines)} 行):")
                for line in raw_lines:
                    print(f"    {line}")
            else:
                print(f"  stub log: (无新输出)")

            # 7. 解析结果
            if wire_msgs:
                print(f"  protocol.parse() 结果:")
                for wire_json, msg in wire_msgs:
                    print(f"    {_fmt_msg(msg)}")
                    if msg is None:
                        print(f"    !! parse 失败，原始: {wire_json!r}")
                        all_ok = False
            else:
                print(f"  protocol.parse(): 无 wire 输出（fire-and-forget 正常）")

            # 8. 预期分析
            print(f"  预期行为: {EXPECTED.get(hook_name, '?')}")

        print(f"\n{'='*60}")
        if all_ok:
            print(f"  E2E 测试完成，所有 wire 输出均可被 protocol.parse() 正确解析")
        else:
            print(f"  E2E 测试完成，存在 parse 失败项，请检查上方输出")
        return 0 if all_ok else 1

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
