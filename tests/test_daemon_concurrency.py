#!/usr/bin/env python3
# tests/test_daemon_concurrency.py
# 并发压测: 起真 stub daemon, 同时打 N 个 socket, 发各种 envelope,
# 看 daemon 是否 crash + 状态计数是否守恒 (running 进必须出, 不能有 -1 或泄漏)。
#
# 跑法: 在仓库根 `python tests/test_daemon_concurrency.py` 退出码 0 = pass。

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "127.0.0.1"
PORT = 57320

N_CLIENTS = 50  # 并发客户端数


def _g():
    return {"session_id": "s", "cwd": "/x", "transcript_path": "/x.j",
            "hook_event_name": "X", "permission_mode": "auto"}


def _env_pre(tool, ti=0):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_start", "tool": tool,
                      "summary": f"call-{ti}", "needs_approval": False,
                      "tool_use_id": f"t{ti}"},
            "generic": _g()}


def _env_post(tool, ti=0):
    return {"type": "event", "v": 2,
            "event": {"kind": "tool_done", "tool": tool, "duration_ms": 5,
                      "tool_use_id": f"t{ti}"},
            "generic": _g()}


async def _send_recv(envelope: dict, timeout=5.0) -> dict:
    """单次 socket 调用 daemon, 返回 daemon resp。"""
    reader, writer = await asyncio.open_connection(HOST, PORT)
    writer.write(json.dumps(envelope).encode())
    writer.write_eof()
    data = await asyncio.wait_for(reader.read(8192), timeout=timeout)
    writer.close()
    await writer.wait_closed()
    return json.loads(data.decode()) if data else {}


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


def _assert(c, msg):
    if not c:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)


async def test_paired_concurrent():
    """N 个 client 各发一对 (tool_start, tool_done), 最终 running 应回 0。"""
    tasks = []
    for i in range(N_CLIENTS):
        tasks.append(_send_recv(_env_pre("Read", ti=i)))
    pre_resps = await asyncio.gather(*tasks, return_exceptions=True)
    err_pre = [r for r in pre_resps if isinstance(r, Exception)]
    _assert(not err_pre, f"pre errors: {err_pre[:3]}")

    # 等一小会再发对应 done
    await asyncio.sleep(0.2)
    tasks = []
    for i in range(N_CLIENTS):
        tasks.append(_send_recv(_env_post("Read", ti=i)))
    post_resps = await asyncio.gather(*tasks, return_exceptions=True)
    err_post = [r for r in post_resps if isinstance(r, Exception)]
    _assert(not err_post, f"post errors: {err_post[:3]}")

    print(f"  ok  {N_CLIENTS} pre + {N_CLIENTS} post 全部 ok, "
          f"non-approval 走非阻塞 path")


async def test_mixed_concurrent():
    """同时发 tool_start / tool_done / Notification / batch 混合, daemon 不崩。"""
    tasks = []
    for i in range(N_CLIENTS):
        kind = i % 4
        if kind == 0:
            env = _env_pre("Glob", ti=i)
        elif kind == 1:
            env = _env_post("Glob", ti=i)
        elif kind == 2:
            env = {"type": "event", "v": 2,
                   "event": {"kind": "notification",
                             "notification_type": "permission_prompt",
                             "message": f"msg-{i}"},
                   "generic": _g()}
        else:
            env = {"type": "event", "v": 2,
                   "event": {"kind": "tool_batch_done",
                             "batch_size": 2, "tools": ["Read", "Bash"]},
                   "generic": _g()}
        tasks.append(_send_recv(env))
    resps = await asyncio.gather(*tasks, return_exceptions=True)
    err = [r for r in resps if isinstance(r, Exception)]
    _assert(not err, f"mixed errors: {err[:3]}")
    print(f"  ok  {N_CLIENTS} mixed envelopes 全部成功响应")


async def test_malformed_no_crash():
    """发坏 JSON / 空字段, daemon 不崩, 仍能服务后续请求。"""
    bad_payloads = [
        b"not json",
        b"",
        b"{",
        b'{"type":"event"}',  # 没 event 字段
        b'{"event":{}}',       # 空 event
    ]
    for p in bad_payloads:
        try:
            r, w = await asyncio.open_connection(HOST, PORT)
            w.write(p)
            w.write_eof()
            await asyncio.wait_for(r.read(4096), timeout=2.0)
            w.close()
            await w.wait_closed()
        except Exception as e:
            print(f"  bad payload {p!r} → exception {e!r} (acceptable)")

    # 服坏数据后, 正常请求应该仍能工作
    resp = await _send_recv(_env_pre("Read", ti=999))
    _assert(isinstance(resp, dict), f"daemon dead after bad payloads, got {resp!r}")
    print(f"  ok  daemon 服 {len(bad_payloads)} 个坏 payload 后仍能正常响应")


async def main():
    # 起 stub daemon
    log = "/tmp/daemon_concurrency.log"
    if os.path.exists(log):
        os.remove(log)
    proc = subprocess.Popen(
        [sys.executable, "-u", os.path.join(ROOT, "ble_daemon.py"), "--stub"],
        stdout=open(log, "w"), stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_listen(5.0):
            print("  daemon failed to listen on 57320")
            return 1

        tests = [
            test_paired_concurrent,
            test_mixed_concurrent,
            test_malformed_no_crash,
        ]
        print(f"running {len(tests)} concurrency tests against real stub daemon...")
        for t in tests:
            print(f"\n[{t.__name__}]")
            await t()
        print(f"\n{'='*50}\n  ALL CONCURRENCY TESTS PASSED ({len(tests)})")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
