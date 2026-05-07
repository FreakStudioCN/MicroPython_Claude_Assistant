#!/usr/bin/env python3
# scripts/demo_v5.py
# v5 架构演示脚本：展示终端审批 + 状态推送

import asyncio
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))
import ble_daemon as d


print("="*60)
print("V5 架构演示：终端审批 + 纯展示模式")
print("="*60)
print()


# ── 模拟环境 ───────────────────────────────────────────────
_sent_wires = []

async def _capture_send(payload):
    _sent_wires.append(dict(payload))
    print(f"[BLE 推送] {json.dumps(payload, ensure_ascii=False)}")

class _MockTransport:
    def connected(self): return True

d._sessions.clear()
d._dirty = False
d._stub = True
d._transport = _MockTransport()
d._send = _capture_send


# ── 演示场景 ───────────────────────────────────────────────
async def demo():
    print("场景 1: 非审批工具（Read）- 立即执行")
    print("-" * 60)

    env1 = {
        "type": "event", "v": 2,
        "event": {
            "kind": "tool_start",
            "tool": "Read",
            "tool_category": "read",
            "summary": "main.py",
            "needs_approval": False,
            "tool_use_id": "t1",
        },
        "generic": {
            "session_id": "demo", "cwd": "/project",
            "hook_event_name": "PreToolUse", "transcript_path": "",
            "permission_mode": "auto",
        }
    }

    resp1 = await d._handle_envelope(env1)
    print(f"[响应] {json.dumps(resp1, ensure_ascii=False)}")
    print(f"[说明] 立即返回 'once'，无需等待审批")
    print()

    await asyncio.sleep(0.5)

    print("\n场景 2: 审批工具（Bash）- 终端审批")
    print("-" * 60)
    print("[说明] v5 架构中，审批在 hook_bridge.py 的终端完成")
    print("[说明] daemon 收到的是已审批的 tool_start，立即返回 'once'")
    print()

    env2 = {
        "type": "event", "v": 2,
        "event": {
            "kind": "tool_start",
            "tool": "Bash",
            "tool_category": "exec",
            "summary": "ls -la",
            "needs_approval": True,  # 即使标记需要审批
            "tool_use_id": "t2",
        },
        "generic": {
            "session_id": "demo", "cwd": "/project",
            "hook_event_name": "PreToolUse", "transcript_path": "",
            "permission_mode": "auto",
        }
    }

    resp2 = await d._handle_envelope(env2)
    print(f"[响应] {json.dumps(resp2, ensure_ascii=False)}")
    print(f"[说明] daemon 不再等待审批，立即返回 'once'")
    print()

    await asyncio.sleep(0.5)

    print("\n场景 3: 工具完成")
    print("-" * 60)

    env3 = {
        "type": "event", "v": 2,
        "event": {
            "kind": "tool_done",
            "tool": "Bash",
            "tool_category": "exec",
            "duration_ms": 150,
            "tool_use_id": "t2",
            "interrupted": False,
        },
        "generic": {
            "session_id": "demo", "cwd": "/project",
            "hook_event_name": "PostToolUse", "transcript_path": "",
            "permission_mode": "auto",
        }
    }

    resp3 = await d._handle_envelope(env3)
    print(f"[响应] {json.dumps(resp3, ensure_ascii=False)}")
    print()

    await asyncio.sleep(0.5)

    print("\n场景 4: 查看最终状态")
    print("-" * 60)

    wire = d._to_device_wire()
    print(f"[Wire 消息] {json.dumps(wire, ensure_ascii=False)}")
    print(f"[说明] 设备收到状态更新，显示对应动画")
    print()

    print("\n" + "="*60)
    print("V5 架构特点总结")
    print("="*60)
    print("✅ 审批在终端完成（hook_bridge.py）")
    print("✅ daemon 不再等待审批，立即返回")
    print("✅ 设备仅展示状态，无审批 UI")
    print("✅ 单向通信，无需心跳")
    print("✅ 代码简化 12%")
    print()


if __name__ == "__main__":
    asyncio.run(demo())
