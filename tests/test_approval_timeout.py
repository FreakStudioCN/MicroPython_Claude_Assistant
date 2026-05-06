#!/usr/bin/env python3
"""
直接测试审批超时行为

模拟 hook_bridge 发送审批请求到 daemon，观察超时行为
"""

import json
import socket
import time
import sys

HOST = "127.0.0.1"
PORT = 57320

def test_approval_with_timeout(test_name, wait_for_response=True, timeout=None):
    """
    测试审批请求

    Args:
        test_name: 测试名称
        wait_for_response: 是否等待响应
        timeout: 超时时间（秒），None 表示使用默认
    """
    print(f"\n{'='*60}")
    print(f"测试: {test_name}")
    print(f"{'='*60}")

    # 构造审批请求
    envelope = {
        "type": "event",
        "v": 2,
        "event": {
            "kind": "tool_start",
            "tool": "Bash",
            "tool_category": "exec",
            "summary": "rm -rf /tmp/test",
            "needs_approval": True,
            "tool_use_id": f"test_{int(time.time())}",
            "risk_level": "normal"
        },
        "generic": {
            "session_id": "test_session",
            "cwd": "/test",
            "hook_event_name": "PreToolUse",
            "transcript_path": "",
            "permission_mode": "default"
        }
    }

    try:
        print(f"发送审批请求...")
        print(f"  工具: {envelope['event']['tool']}")
        print(f"  命令: {envelope['event']['summary']}")
        print(f"  风险: {envelope['event']['risk_level']}")

        start_time = time.time()

        with socket.create_connection((HOST, PORT), timeout=2) as s:
            if timeout:
                s.settimeout(timeout)
            else:
                s.settimeout(75)  # 默认 75 秒

            # 发送请求
            s.sendall(json.dumps(envelope).encode("utf-8"))
            s.shutdown(socket.SHUT_WR)

            if wait_for_response:
                print(f"等待响应...")
                print(f"  (daemon 会等待设备审批，stub 模式会自动批准)")
                print(f"  (离线模式会根据风险等级自动决策)")

                # 接收响应
                buf = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk

                elapsed = time.time() - start_time

                if buf:
                    resp = json.loads(buf.decode("utf-8"))
                    print(f"\n收到响应 (耗时 {elapsed:.2f}s):")
                    print(f"  决策: {resp.get('decision', 'once')}")
                    if resp.get('decision') == 'deny':
                        print(f"  原因: {resp.get('reason', 'N/A')}")

                    return elapsed, resp
                else:
                    print(f"\n无响应 (超时 {elapsed:.2f}s)")
                    return elapsed, None
            else:
                print(f"不等待响应，立即返回")
                return 0, None

    except socket.timeout as e:
        elapsed = time.time() - start_time
        print(f"\nSocket 超时 (耗时 {elapsed:.2f}s)")
        print(f"  这意味着 daemon 在 {elapsed:.2f}s 内没有返回响应")
        return elapsed, None

    except ConnectionRefusedError:
        print(f"\n连接被拒绝 - daemon 未运行")
        print(f"请先启动: python daemon/ble_daemon.py --stub --offline")
        return 0, None

    except Exception as e:
        print(f"\n错误: {e}")
        return 0, None


def main():
    print("Claude Code 审批超时测试")
    print("="*60)

    # 检查 daemon 是否运行
    try:
        with socket.create_connection((HOST, PORT), timeout=1):
            print("daemon 正在运行")
    except:
        print("错误: daemon 未运行")
        print("请先启动: python daemon/ble_daemon.py --stub --offline")
        return

    # 测试场景
    tests = [
        ("场景 1: 正常审批（stub 模式自动批准）", True, None),
        ("场景 2: 短超时（5秒）", True, 5),
        ("场景 3: 中等超时（30秒）", True, 30),
    ]

    results = []

    for test_name, wait, timeout in tests:
        elapsed, resp = test_approval_with_timeout(test_name, wait, timeout)
        results.append((test_name, elapsed, resp))
        time.sleep(1)  # 间隔 1 秒

    # 总结
    print(f"\n{'='*60}")
    print("测试总结")
    print(f"{'='*60}")

    for test_name, elapsed, resp in results:
        status = "成功" if resp else "超时"
        decision = resp.get('decision', 'N/A') if resp else 'N/A'
        print(f"\n{test_name}")
        print(f"  耗时: {elapsed:.2f}s")
        print(f"  状态: {status}")
        print(f"  决策: {decision}")

    print(f"\n{'='*60}")
    print("结论:")
    print(f"{'='*60}")
    print("1. daemon 在 stub + offline 模式下会立即自动批准 normal 风险的操作")
    print("2. 如果设备在线且需要审批，daemon 会等待最多 60 秒")
    print("3. hook_bridge 的 RECV_TIMEOUT=70s 覆盖了 daemon 的 60s 审批窗口")
    print("4. 如果超过 70 秒，hook 会超时并返回空（fail-open，允许执行）")


if __name__ == "__main__":
    main()
