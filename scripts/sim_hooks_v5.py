#!/usr/bin/env python3
# scripts/sim_hooks_v5.py —— v5 架构手动集成测试
#
# 用途: 模拟 Claude Code 触发 hook 事件，测试 v5 纯展示模式
#       → hook_bridge.py（真实运行）→ ble_daemon.py（真实运行）→ BLE → ESP32
#
# v5 变化:
#   - 删除审批等待（所有工具立即返回）
#   - 删除离线风险测试（daemon 不再处理审批）
#   - 删除重连 PENDING 测试（无 PENDING 状态）
#   - 新增多 session 测试
#
# 运行前提: ESP32 已烧录固件并开机，或先手动启动 ble_daemon.py --stub 做无设备测试
#
# 跑法:
#   python scripts/sim_hooks_v5.py             # 自动启动 daemon（需要 ESP32）
#   python scripts/sim_hooks_v5.py --stub      # 自动启动 daemon --stub（无设备）
#   python scripts/sim_hooks_v5.py --no-daemon # daemon 已手动启动，跳过自动启动
#   python scripts/sim_hooks_v5.py --multi-session # 多 session 测试

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

# ── 基本测试序列 ──────────────────────────────────────────
# 验证 v5 核心功能：状态推送、无审批等待
BASIC_SEQUENCE = [
    ("UserPromptSubmit",   "UserPromptSubmit.json",   None),
    ("SubagentStart",      "SubagentStart.json",       None),
    ("PreToolUse(Read)",   "PreToolUse.json",          {
        "tool_name": "Read",
        "tool_use_id": "toolu_SIM_READ1",
        "tool_input": {"file_path": "/etc/hosts"}
    }),
    ("PostToolUse(Read)",  "PostToolUse.json",         {
        "tool_name": "Read",
        "tool_use_id": "toolu_SIM_READ1",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Bash)",   "PreToolUse.json",          {
        "tool_name": "Bash",
        "tool_use_id": "toolu_SIM_BASH1",
        "tool_input": {"command": "ls -la"}
    }),
    ("PostToolUse(Bash)",  "PostToolUse.json",         {
        "tool_name": "Bash",
        "tool_use_id": "toolu_SIM_BASH1",
        "tool_response": {"interrupted": False}
    }),
    ("PostToolBatch",      "PostToolBatch.json",       None),
    ("PostToolUseFailure", "PostToolUseFailure.json",  None),
    ("Notification",       "Notification.json",        None),
    ("StopFailure",        "StopFailure.json",         None),
]

# ── 多 Session 测试序列 ────────────────────────────────────
# 验证多个 Claude Code 实例同时工作
MULTI_SESSION_SEQUENCE = [
    # Session 1: 用户提交 prompt
    ("S1: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "session_1",
        "prompt": "Read main.py"
    }),

    # Session 1: 开始读取文件
    ("S1: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "session_1",
        "tool_name": "Read",
        "tool_use_id": "toolu_S1_READ1",
        "tool_input": {"file_path": "main.py"}
    }),

    # Session 2: 同时启动（并发场景）
    ("S2: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "session_2",
        "prompt": "Run tests"
    }),

    # Session 2: 开始执行命令
    ("S2: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "session_2",
        "tool_name": "Bash",
        "tool_use_id": "toolu_S2_BASH1",
        "tool_input": {"command": "pytest"}
    }),

    # Session 3: 第三个实例
    ("S3: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "session_3",
        "prompt": "Search for TODO"
    }),

    # Session 3: 搜索
    ("S3: PreToolUse(Grep)", "PreToolUse.json", {
        "session_id": "session_3",
        "tool_name": "Grep",
        "tool_use_id": "toolu_S3_GREP1",
        "tool_input": {"pattern": "TODO"}
    }),

    # Session 1: 完成
    ("S1: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "session_1",
        "tool_name": "Read",
        "tool_use_id": "toolu_S1_READ1",
        "tool_response": {"interrupted": False}
    }),

    # Session 2: 完成
    ("S2: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "session_2",
        "tool_name": "Bash",
        "tool_use_id": "toolu_S2_BASH1",
        "tool_response": {"interrupted": False}
    }),

    # Session 3: 完成
    ("S3: PostToolUse(Grep)", "PostToolUse.json", {
        "session_id": "session_3",
        "tool_name": "Grep",
        "tool_use_id": "toolu_S3_GREP1",
        "tool_response": {"interrupted": False}
    }),
]

# ── 并行工具测试序列 ──────────────────────────────────────
# 验证单个 session 中多个工具并行执行
PARALLEL_TOOLS_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "parallel_test",
        "prompt": "Read multiple files"
    }),

    # 同时启动 3 个工具
    ("PreToolUse(Read-1)", "PreToolUse.json", {
        "session_id": "parallel_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_PAR_READ1",
        "tool_input": {"file_path": "file1.py"}
    }),

    ("PreToolUse(Read-2)", "PreToolUse.json", {
        "session_id": "parallel_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_PAR_READ2",
        "tool_input": {"file_path": "file2.py"}
    }),

    ("PreToolUse(Read-3)", "PreToolUse.json", {
        "session_id": "parallel_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_PAR_READ3",
        "tool_input": {"file_path": "file3.py"}
    }),

    # 依次完成
    ("PostToolUse(Read-1)", "PostToolUse.json", {
        "session_id": "parallel_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_PAR_READ1",
        "tool_response": {"interrupted": False}
    }),

    ("PostToolUse(Read-2)", "PostToolUse.json", {
        "session_id": "parallel_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_PAR_READ2",
        "tool_response": {"interrupted": False}
    }),

    ("PostToolUse(Read-3)", "PostToolUse.json", {
        "session_id": "parallel_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_PAR_READ3",
        "tool_response": {"interrupted": False}
    }),

    ("PostToolBatch", "PostToolBatch.json", {
        "session_id": "parallel_test"
    }),
]

# ── 错误处理测试序列 ──────────────────────────────────────
# 验证错误状态和恢复
ERROR_HANDLING_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "error_test",
        "prompt": "Test error handling"
    }),

    ("PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "error_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_ERR_BASH1",
        "tool_input": {"command": "invalid_command"}
    }),

    # 工具执行失败
    ("PostToolUseFailure", "PostToolUseFailure.json", {
        "session_id": "error_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_ERR_BASH1",
        "error": "Command not found"
    }),

    # 用户重新提交（清除错误状态）
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "error_test",
        "prompt": "Try again"
    }),

    ("PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "error_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_ERR_BASH2",
        "tool_input": {"command": "ls"}
    }),

    ("PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "error_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_ERR_BASH2",
        "tool_response": {"interrupted": False}
    }),
]


# ── 工具中断测试序列 ──────────────────────────────────────
# 验证 interrupted=True 的状态处理
INTERRUPTED_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "interrupt_test",
        "prompt": "Run long command"
    }),
    ("PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "interrupt_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_INT_BASH1",
        "tool_input": {"command": "sleep 60"}
    }),
    # 用户中断
    ("PostToolUse(Bash-interrupted)", "PostToolUse.json", {
        "session_id": "interrupt_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_INT_BASH1",
        "tool_response": {"interrupted": True}
    }),
    # 中断后重新提交
    ("UserPromptSubmit(retry)", "UserPromptSubmit.json", {
        "session_id": "interrupt_test",
        "prompt": "Try shorter command"
    }),
    ("PreToolUse(Bash-retry)", "PreToolUse.json", {
        "session_id": "interrupt_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_INT_BASH2",
        "tool_input": {"command": "echo done"}
    }),
    ("PostToolUse(Bash-retry)", "PostToolUse.json", {
        "session_id": "interrupt_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_INT_BASH2",
        "tool_response": {"interrupted": False}
    }),
]

# ── Web 工具测试序列 ──────────────────────────────────────
# 验证 web 类工具的状态显示
WEB_TOOLS_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "web_test",
        "prompt": "Research the topic"
    }),
    ("PreToolUse(WebSearch)", "PreToolUse.json", {
        "session_id": "web_test",
        "tool_name": "WebSearch",
        "tool_use_id": "toolu_WEB_SEARCH1",
        "tool_input": {"query": "MicroPython BLE tutorial"}
    }),
    ("PostToolUse(WebSearch)", "PostToolUse.json", {
        "session_id": "web_test",
        "tool_name": "WebSearch",
        "tool_use_id": "toolu_WEB_SEARCH1",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(WebFetch)", "PreToolUse.json", {
        "session_id": "web_test",
        "tool_name": "WebFetch",
        "tool_use_id": "toolu_WEB_FETCH1",
        "tool_input": {"url": "https://docs.micropython.org/en/latest/"}
    }),
    ("PostToolUse(WebFetch)", "PostToolUse.json", {
        "session_id": "web_test",
        "tool_name": "WebFetch",
        "tool_use_id": "toolu_WEB_FETCH1",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Read-result)", "PreToolUse.json", {
        "session_id": "web_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_WEB_READ1",
        "tool_input": {"file_path": "notes.md"}
    }),
    ("PostToolUse(Read-result)", "PostToolUse.json", {
        "session_id": "web_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_WEB_READ1",
        "tool_response": {"interrupted": False}
    }),
]

# ── 长任务测试序列 ────────────────────────────────────────
# 模拟复杂重构任务：读多文件 → 编辑 → 测试
LONG_TASK_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "long_task",
        "prompt": "Refactor the authentication module"
    }),
    ("SubagentStart", "SubagentStart.json", {"session_id": "long_task"}),
    ("PreToolUse(Read-1)", "PreToolUse.json", {
        "session_id": "long_task", "tool_name": "Read",
        "tool_use_id": "toolu_LT_R1", "tool_input": {"file_path": "auth/login.py"}
    }),
    ("PostToolUse(Read-1)", "PostToolUse.json", {
        "session_id": "long_task", "tool_name": "Read",
        "tool_use_id": "toolu_LT_R1", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Read-2)", "PreToolUse.json", {
        "session_id": "long_task", "tool_name": "Read",
        "tool_use_id": "toolu_LT_R2", "tool_input": {"file_path": "auth/session.py"}
    }),
    ("PostToolUse(Read-2)", "PostToolUse.json", {
        "session_id": "long_task", "tool_name": "Read",
        "tool_use_id": "toolu_LT_R2", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Glob)", "PreToolUse.json", {
        "session_id": "long_task", "tool_name": "Glob",
        "tool_use_id": "toolu_LT_GLOB", "tool_input": {"pattern": "auth/**/*.py"}
    }),
    ("PostToolUse(Glob)", "PostToolUse.json", {
        "session_id": "long_task", "tool_name": "Glob",
        "tool_use_id": "toolu_LT_GLOB", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Grep)", "PreToolUse.json", {
        "session_id": "long_task", "tool_name": "Grep",
        "tool_use_id": "toolu_LT_GREP", "tool_input": {"pattern": "def authenticate"}
    }),
    ("PostToolUse(Grep)", "PostToolUse.json", {
        "session_id": "long_task", "tool_name": "Grep",
        "tool_use_id": "toolu_LT_GREP", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Edit-1)", "PreToolUse.json", {
        "session_id": "long_task", "tool_name": "Edit",
        "tool_use_id": "toolu_LT_E1", "tool_input": {"file_path": "auth/login.py"}
    }),
    ("PostToolUse(Edit-1)", "PostToolUse.json", {
        "session_id": "long_task", "tool_name": "Edit",
        "tool_use_id": "toolu_LT_E1", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Edit-2)", "PreToolUse.json", {
        "session_id": "long_task", "tool_name": "Edit",
        "tool_use_id": "toolu_LT_E2", "tool_input": {"file_path": "auth/session.py"}
    }),
    ("PostToolUse(Edit-2)", "PostToolUse.json", {
        "session_id": "long_task", "tool_name": "Edit",
        "tool_use_id": "toolu_LT_E2", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Write)", "PreToolUse.json", {
        "session_id": "long_task", "tool_name": "Write",
        "tool_use_id": "toolu_LT_W1", "tool_input": {"file_path": "auth/utils.py"}
    }),
    ("PostToolUse(Write)", "PostToolUse.json", {
        "session_id": "long_task", "tool_name": "Write",
        "tool_use_id": "toolu_LT_W1", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Bash-test)", "PreToolUse.json", {
        "session_id": "long_task", "tool_name": "Bash",
        "tool_use_id": "toolu_LT_BASH", "tool_input": {"command": "pytest auth/"}
    }),
    ("PostToolUse(Bash-test)", "PostToolUse.json", {
        "session_id": "long_task", "tool_name": "Bash",
        "tool_use_id": "toolu_LT_BASH", "tool_response": {"interrupted": False}
    }),
    ("PostToolBatch", "PostToolBatch.json", {"session_id": "long_task"}),
]

# ── 同一 Session 多轮对话序列 ─────────────────────────────
# 验证同一 session 多次 user_prompt 的状态重置
SESSION_RESTART_SEQUENCE = [
    ("Turn1: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "restart_test", "prompt": "Read config"
    }),
    ("Turn1: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "restart_test", "tool_name": "Read",
        "tool_use_id": "toolu_RS_R1", "tool_input": {"file_path": "config.py"}
    }),
    ("Turn1: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "restart_test", "tool_name": "Read",
        "tool_use_id": "toolu_RS_R1", "tool_response": {"interrupted": False}
    }),
    # 第二轮对话（同一 session）
    ("Turn2: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "restart_test", "prompt": "Now update the config"
    }),
    ("Turn2: PreToolUse(Edit)", "PreToolUse.json", {
        "session_id": "restart_test", "tool_name": "Edit",
        "tool_use_id": "toolu_RS_E1", "tool_input": {"file_path": "config.py"}
    }),
    ("Turn2: PostToolUse(Edit)", "PostToolUse.json", {
        "session_id": "restart_test", "tool_name": "Edit",
        "tool_use_id": "toolu_RS_E1", "tool_response": {"interrupted": False}
    }),
    # 第三轮对话
    ("Turn3: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "restart_test", "prompt": "Run tests to verify"
    }),
    ("Turn3: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "restart_test", "tool_name": "Bash",
        "tool_use_id": "toolu_RS_B1", "tool_input": {"command": "pytest -v"}
    }),
    ("Turn3: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "restart_test", "tool_name": "Bash",
        "tool_use_id": "toolu_RS_B1", "tool_response": {"interrupted": False}
    }),
]

# ── 混合工具类型序列 ──────────────────────────────────────
# 覆盖所有 5 个工具类别：read/edit/exec/web/agent
MIXED_TOOLS_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "mixed_test", "prompt": "Full stack task"
    }),
    ("SubagentStart", "SubagentStart.json", {"session_id": "mixed_test"}),
    ("PreToolUse(Glob)", "PreToolUse.json", {
        "session_id": "mixed_test", "tool_name": "Glob",
        "tool_use_id": "toolu_MX_GLOB", "tool_input": {"pattern": "src/**/*.py"}
    }),
    ("PostToolUse(Glob)", "PostToolUse.json", {
        "session_id": "mixed_test", "tool_name": "Glob",
        "tool_use_id": "toolu_MX_GLOB", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(WebSearch)", "PreToolUse.json", {
        "session_id": "mixed_test", "tool_name": "WebSearch",
        "tool_use_id": "toolu_MX_WS", "tool_input": {"query": "best practices"}
    }),
    ("PostToolUse(WebSearch)", "PostToolUse.json", {
        "session_id": "mixed_test", "tool_name": "WebSearch",
        "tool_use_id": "toolu_MX_WS", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "mixed_test", "tool_name": "Read",
        "tool_use_id": "toolu_MX_R1", "tool_input": {"file_path": "src/main.py"}
    }),
    ("PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "mixed_test", "tool_name": "Read",
        "tool_use_id": "toolu_MX_R1", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Edit)", "PreToolUse.json", {
        "session_id": "mixed_test", "tool_name": "Edit",
        "tool_use_id": "toolu_MX_E1", "tool_input": {"file_path": "src/main.py"}
    }),
    ("PostToolUse(Edit)", "PostToolUse.json", {
        "session_id": "mixed_test", "tool_name": "Edit",
        "tool_use_id": "toolu_MX_E1", "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "mixed_test", "tool_name": "Bash",
        "tool_use_id": "toolu_MX_B1", "tool_input": {"command": "python -m pytest"}
    }),
    ("PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "mixed_test", "tool_name": "Bash",
        "tool_use_id": "toolu_MX_B1", "tool_response": {"interrupted": False}
    }),
    ("PostToolBatch", "PostToolBatch.json", {"session_id": "mixed_test"}),
]

# ── 多 Session 同时出错序列 ───────────────────────────────
# 验证多个 session 同时处于错误状态
MULTI_SESSION_ERROR_SEQUENCE = [
    ("S1: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "err_s1", "prompt": "Deploy to prod"
    }),
    ("S2: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "err_s2", "prompt": "Run migration"
    }),
    ("S1: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "err_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_MSE_B1", "tool_input": {"command": "deploy.sh"}
    }),
    ("S2: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "err_s2", "tool_name": "Bash",
        "tool_use_id": "toolu_MSE_B2", "tool_input": {"command": "migrate.sh"}
    }),
    # 两个 session 同时失败
    ("S1: PostToolUseFailure", "PostToolUseFailure.json", {
        "session_id": "err_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_MSE_B1", "error": "Connection refused"
    }),
    ("S2: PostToolUseFailure", "PostToolUseFailure.json", {
        "session_id": "err_s2", "tool_name": "Bash",
        "tool_use_id": "toolu_MSE_B2", "error": "Database locked"
    }),
    # S1 恢复
    ("S1: UserPromptSubmit(retry)", "UserPromptSubmit.json", {
        "session_id": "err_s1", "prompt": "Retry deploy"
    }),
    ("S1: PreToolUse(Bash-retry)", "PreToolUse.json", {
        "session_id": "err_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_MSE_B3", "tool_input": {"command": "deploy.sh --retry"}
    }),
    ("S1: PostToolUse(Bash-retry)", "PostToolUse.json", {
        "session_id": "err_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_MSE_B3", "tool_response": {"interrupted": False}
    }),
    # S2 仍在错误状态，S1 完成
    ("S2: StopFailure", "StopFailure.json", {"session_id": "err_s2"}),
]

# ── 快速连续工具序列 ──────────────────────────────────────
# 验证高频工具调用（模拟 Claude 快速读取多文件）
RAPID_FIRE_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "rapid_test", "prompt": "Analyze entire codebase"
    }),
] + [
    (f"PreToolUse(Read-{i})", "PreToolUse.json", {
        "session_id": "rapid_test", "tool_name": "Read",
        "tool_use_id": f"toolu_RF_R{i}",
        "tool_input": {"file_path": f"src/module_{i}.py"}
    })
    for i in range(1, 9)
] + [
    (f"PostToolUse(Read-{i})", "PostToolUse.json", {
        "session_id": "rapid_test", "tool_name": "Read",
        "tool_use_id": f"toolu_RF_R{i}",
        "tool_response": {"interrupted": False}
    })
    for i in range(1, 9)
] + [
    ("PostToolBatch", "PostToolBatch.json", {"session_id": "rapid_test"}),
]

# ── Subagent 嵌套序列 ─────────────────────────────────────
# 验证 subagent 场景下的状态显示
SUBAGENT_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "subagent_test", "prompt": "Use agents to analyze"
    }),
    ("SubagentStart(agent-1)", "SubagentStart.json", {
        "session_id": "subagent_test", "agent_id": "agent_001", "agent_type": "Explore"
    }),
    ("PreToolUse(Read-by-agent)", "PreToolUse.json", {
        "session_id": "subagent_test", "tool_name": "Read",
        "tool_use_id": "toolu_SA_R1", "tool_input": {"file_path": "README.md"}
    }),
    ("PostToolUse(Read-by-agent)", "PostToolUse.json", {
        "session_id": "subagent_test", "tool_name": "Read",
        "tool_use_id": "toolu_SA_R1", "tool_response": {"interrupted": False}
    }),
    ("SubagentStart(agent-2)", "SubagentStart.json", {
        "session_id": "subagent_test", "agent_id": "agent_002", "agent_type": "general-purpose"
    }),
    ("PreToolUse(Bash-by-agent2)", "PreToolUse.json", {
        "session_id": "subagent_test", "tool_name": "Bash",
        "tool_use_id": "toolu_SA_B1", "tool_input": {"command": "git log --oneline -10"}
    }),
    ("PostToolUse(Bash-by-agent2)", "PostToolUse.json", {
        "session_id": "subagent_test", "tool_name": "Bash",
        "tool_use_id": "toolu_SA_B1", "tool_response": {"interrupted": False}
    }),
    ("Notification", "Notification.json", {"session_id": "subagent_test"}),
]

# ── GUI 主界面状态转换序列 ────────────────────────────────
# 验证脸部颜色（I=灰/W=蓝/E=红/C=绿）、眼睛眨眼（W时启动）、圆点颜色、选项卡闪烁
GUI_FACE_TRANSITIONS_SEQUENCE = [
    # 阶段1: 开始工作 → 脸蓝色、眼睛眨眼、S1圆点蓝色
    ("S1: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "gui_face_s1", "prompt": "Analyze code"
    }),
    ("S1: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "gui_face_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_GF_B1", "tool_input": {"command": "pytest --tb=short"}
    }),
    # [期望] 脸=蓝, 眼睛眨眼, S1圆点=蓝, 消息块=蓝 "Bash: ..."
    # 阶段2: 完成 → 脸绿色、眼睛停止、消息块绿色
    ("S1: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "gui_face_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_GF_B1", "tool_response": {"interrupted": False}
    }),
    ("S1: PostToolBatch", "PostToolBatch.json", {"session_id": "gui_face_s1"}),
    # [期望] 脸=绿, 眼睛静止, S1圆点=绿, 消息块=绿 "Done"
    # 阶段3: 第二轮工作 → 再次蓝色
    ("S1: Turn2-UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "gui_face_s1", "prompt": "Fix the bug"
    }),
    ("S1: PreToolUse(Edit)", "PreToolUse.json", {
        "session_id": "gui_face_s1", "tool_name": "Edit",
        "tool_use_id": "toolu_GF_E1", "tool_input": {"file_path": "main.py"}
    }),
    # [期望] 脸=蓝, 眼睛眨眼, 消息块=蓝 "Edit: ..."
    # 阶段4: 工具失败 → 脸红色、选项卡闪烁
    ("S1: PostToolUseFailure", "PostToolUseFailure.json", {
        "session_id": "gui_face_s1", "tool_name": "Edit",
        "tool_use_id": "toolu_GF_E1", "error": "File not found"
    }),
    # [期望] 脸=红, 眼睛静止, S1圆点=红, 消息块=红 "Error", S1选项卡闪烁
    # 阶段5: StopFailure → 脸灰色
    ("S1: StopFailure", "StopFailure.json", {"session_id": "gui_face_s1"}),
    # [期望] 脸=灰, 消息块="Idle"
]

# ── GUI 五 Session 同时活跃序列 ──────────────────────────
# 验证5个圆点同时显示不同颜色：S1=蓝/S2=红/S3=绿/S4=蓝/S5=灰
GUI_5SESSIONS_SEQUENCE = [
    # 依次启动 5 个 session
    ("S1: Start", "UserPromptSubmit.json", {"session_id": "gui_5s_1", "prompt": "Task 1"}),
    ("S2: Start", "UserPromptSubmit.json", {"session_id": "gui_5s_2", "prompt": "Task 2"}),
    ("S3: Start", "UserPromptSubmit.json", {"session_id": "gui_5s_3", "prompt": "Task 3"}),
    ("S4: Start", "UserPromptSubmit.json", {"session_id": "gui_5s_4", "prompt": "Task 4"}),
    ("S5: Start", "UserPromptSubmit.json", {"session_id": "gui_5s_5", "prompt": "Task 5"}),
    # S1: 工作中（蓝色圆点）
    ("S1: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "gui_5s_1", "tool_name": "Read",
        "tool_use_id": "toolu_5S_R1", "tool_input": {"file_path": "a.py"}
    }),
    # S2: 失败（红色圆点、选项卡闪烁）
    ("S2: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "gui_5s_2", "tool_name": "Bash",
        "tool_use_id": "toolu_5S_B1", "tool_input": {"command": "bad_cmd"}
    }),
    ("S2: PostToolUseFailure", "PostToolUseFailure.json", {
        "session_id": "gui_5s_2", "tool_name": "Bash",
        "tool_use_id": "toolu_5S_B1", "error": "Command not found"
    }),
    # S3: 完成（绿色圆点）
    ("S3: PreToolUse(Grep)", "PreToolUse.json", {
        "session_id": "gui_5s_3", "tool_name": "Grep",
        "tool_use_id": "toolu_5S_G1", "tool_input": {"pattern": "TODO"}
    }),
    ("S3: PostToolUse(Grep)", "PostToolUse.json", {
        "session_id": "gui_5s_3", "tool_name": "Grep",
        "tool_use_id": "toolu_5S_G1", "tool_response": {"interrupted": False}
    }),
    ("S3: PostToolBatch", "PostToolBatch.json", {"session_id": "gui_5s_3"}),
    # S4: 工作中（蓝色圆点）
    ("S4: PreToolUse(WebSearch)", "PreToolUse.json", {
        "session_id": "gui_5s_4", "tool_name": "WebSearch",
        "tool_use_id": "toolu_5S_WS1", "tool_input": {"query": "MicroPython LVGL"}
    }),
    # S5: 空闲（灰色圆点，无工具调用）
    # [期望] 5圆点: 蓝/红/绿/蓝/灰, 脸=红（E优先）, 消息块=红 S2错误
    # 清理：恢复 S1、S4
    ("S1: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "gui_5s_1", "tool_name": "Read",
        "tool_use_id": "toolu_5S_R1", "tool_response": {"interrupted": False}
    }),
    ("S4: PostToolUse(WebSearch)", "PostToolUse.json", {
        "session_id": "gui_5s_4", "tool_name": "WebSearch",
        "tool_use_id": "toolu_5S_WS1", "tool_response": {"interrupted": False}
    }),
]

# ── GUI 消息块优先级切换序列 ──────────────────────────────
# 验证主界面消息块 E > W > C > I 的动态切换
GUI_PRIORITY_SEQUENCE = [
    # 建立初始状态：S1=W, S2=C, S3=E → 脸红色，消息块显示 S3
    ("S1: Start+Work", "UserPromptSubmit.json", {
        "session_id": "gui_prio_s1", "prompt": "Ongoing task"
    }),
    ("S1: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "gui_prio_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_PR_B1", "tool_input": {"command": "long_job.sh"}
    }),
    ("S2: Start+Done", "UserPromptSubmit.json", {
        "session_id": "gui_prio_s2", "prompt": "Finished task"
    }),
    ("S2: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "gui_prio_s2", "tool_name": "Read",
        "tool_use_id": "toolu_PR_R1", "tool_input": {"file_path": "done.py"}
    }),
    ("S2: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "gui_prio_s2", "tool_name": "Read",
        "tool_use_id": "toolu_PR_R1", "tool_response": {"interrupted": False}
    }),
    ("S2: PostToolBatch", "PostToolBatch.json", {"session_id": "gui_prio_s2"}),
    ("S3: Start+Error", "UserPromptSubmit.json", {
        "session_id": "gui_prio_s3", "prompt": "Failed task"
    }),
    ("S3: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "gui_prio_s3", "tool_name": "Bash",
        "tool_use_id": "toolu_PR_B2", "tool_input": {"command": "fail.sh"}
    }),
    ("S3: PostToolUseFailure", "PostToolUseFailure.json", {
        "session_id": "gui_prio_s3", "tool_name": "Bash",
        "tool_use_id": "toolu_PR_B2", "error": "Script failed"
    }),
    # [期望] S1=W, S2=C, S3=E → 脸=红, 消息块=红 "S3: Error"
    # 消除 S3 错误，S1/S3 均在工作
    ("S3: Recover", "UserPromptSubmit.json", {
        "session_id": "gui_prio_s3", "prompt": "Retry"
    }),
    ("S3: PreToolUse(Bash-retry)", "PreToolUse.json", {
        "session_id": "gui_prio_s3", "tool_name": "Bash",
        "tool_use_id": "toolu_PR_B3", "tool_input": {"command": "retry.sh"}
    }),
    # [期望] S1=W, S2=C, S3=W → 脸=蓝, 消息块=蓝 "S1: Bash:..."（第一个W）
    # 解决 S1
    ("S1: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "gui_prio_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_PR_B1", "tool_response": {"interrupted": False}
    }),
    # [期望] S1=C, S2=C, S3=W → 脸=蓝, 消息块=蓝 "S3: Bash:..."
    # 解决 S3
    ("S3: PostToolUse(Bash-retry)", "PostToolUse.json", {
        "session_id": "gui_prio_s3", "tool_name": "Bash",
        "tool_use_id": "toolu_PR_B3", "tool_response": {"interrupted": False}
    }),
    ("S3: PostToolBatch", "PostToolBatch.json", {"session_id": "gui_prio_s3"}),
    # [期望] S1=C, S2=C, S3=C → 脸=绿, 消息块=绿 "S1: Done"
]

# ── 审批通知序列 ──────────────────────────────────────────
# 验证 needs_approval=True 时设备显示 PENDING（黄色），审批完成后恢复
# 注意：hook_bridge 立即返回 {}，审批由 Claude Code 终端完成
#       设备端只做通知用，本序列验证 PENDING 状态的推送与恢复
APPROVAL_SEQUENCE = [
    # 阶段1: 普通工具（无审批）→ 设备显示 WORKING（蓝色）
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "approval_test",
        "prompt": "Delete temp files and push to remote"
    }),
    ("PreToolUse(Read-safe)", "PreToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_AP_R1",
        "tool_input": {"file_path": "README.md"}
    }),
    # [期望] 设备: WORKING 蓝色，消息块 "Read: README.md"
    ("PostToolUse(Read-safe)", "PostToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_AP_R1",
        "tool_response": {"interrupted": False}
    }),

    # 阶段2: 需要审批的 Bash 命令 → 设备显示 PENDING（黄色）
    ("PreToolUse(Bash-critical)", "PreToolUse_critical_bash.json", {
        "session_id": "approval_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_AP_B1",
        "tool_input": {"command": "rm -rf /tmp/build && git push --force"}
    }),
    # [期望] 设备: PENDING 黄色，提醒用户去终端审批
    # 模拟用户在终端批准（PostToolUse 表示工具已执行完成）
    ("PostToolUse(Bash-critical-approved)", "PostToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_AP_B1",
        "tool_response": {"interrupted": False}
    }),
    # [期望] 设备: 恢复 IDLE（灰色）

    # 阶段3: 需要审批的 Write 操作 → 再次 PENDING
    ("PreToolUse(Write-critical)", "PreToolUse_critical_write.json", {
        "session_id": "approval_test",
        "tool_name": "Write",
        "tool_use_id": "toolu_AP_W1",
        "tool_input": {"file_path": ".env"}
    }),
    # [期望] 设备: PENDING 黄色
    # 模拟用户在终端拒绝（PostToolUseFailure 表示工具被拒绝/失败）
    ("PostToolUseFailure(Write-denied)", "PostToolUseFailure.json", {
        "session_id": "approval_test",
        "tool_name": "Write",
        "tool_use_id": "toolu_AP_W1",
        "error": "User denied the operation"
    }),
    # [期望] 设备: ERROR 红色（工具失败），随后恢复 IDLE

    # 阶段4: 多个审批工具并发 → PENDING 计数正确
    ("PreToolUse(Bash-1)", "PreToolUse_critical_bash.json", {
        "session_id": "approval_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_AP_B2",
        "tool_input": {"command": "git push --force origin main"}
    }),
    ("PreToolUse(Edit-critical)", "PreToolUse_critical_edit.json", {
        "session_id": "approval_test",
        "tool_name": "Edit",
        "tool_use_id": "toolu_AP_E1",
        "tool_input": {"file_path": ".git/config"}
    }),
    # [期望] 设备: PENDING 黄色（waiting=2）
    ("PostToolUse(Bash-1-done)", "PostToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_AP_B2",
        "tool_response": {"interrupted": False}
    }),
    # [期望] 设备: 仍然 PENDING（waiting=1，还有 Edit 未完成）
    ("PostToolUse(Edit-done)", "PostToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Edit",
        "tool_use_id": "toolu_AP_E1",
        "tool_response": {"interrupted": False}
    }),
    # [期望] 设备: 恢复 IDLE（waiting=0）
]

# ── 所有序列（--all 模式）────────────────────────────────
ALL_SEQUENCES = [
    (BASIC_SEQUENCE,               "基本功能测试"),
    (MULTI_SESSION_SEQUENCE,       "多 Session 测试"),
    (PARALLEL_TOOLS_SEQUENCE,      "并行工具测试"),
    (ERROR_HANDLING_SEQUENCE,      "错误处理测试"),
    (INTERRUPTED_SEQUENCE,         "工具中断测试"),
    (WEB_TOOLS_SEQUENCE,           "Web 工具测试"),
    (LONG_TASK_SEQUENCE,           "长任务测试"),
    (SESSION_RESTART_SEQUENCE,     "多轮对话测试"),
    (MIXED_TOOLS_SEQUENCE,         "混合工具测试"),
    (MULTI_SESSION_ERROR_SEQUENCE, "多 Session 错误测试"),
    (RAPID_FIRE_SEQUENCE,              "快速连续工具测试"),
    (SUBAGENT_SEQUENCE,                "Subagent 嵌套测试"),
    (APPROVAL_SEQUENCE,                "审批通知测试"),
    (GUI_FACE_TRANSITIONS_SEQUENCE,    "GUI 脸部状态转换测试"),
    (GUI_5SESSIONS_SEQUENCE,           "GUI 五 Session 并发测试"),
    (GUI_PRIORITY_SEQUENCE,            "GUI 消息块优先级测试"),
]


def _wait_listen(timeout=8.0) -> bool:
    """等待 daemon 监听端口"""
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
    """等待 BLE 连接（非 stub 模式）"""
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
                    print()
                return True
        except OSError:
            pass
        time.sleep(1.0)
        dots += 1
        print(f"\r[sim] 等待 ESP32 BLE 连接... {dots}s", end="", flush=True)
    print()
    return False


def _load_fixture(filename: str, patch) -> dict:
    """加载 fixture 文件并应用 patch"""
    path = os.path.join(FIXTURE_DIR, filename)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if patch:
        data.update(patch)
    return data


def _send_hook(label: str, fixture_json: bytes) -> tuple[str, float]:
    """
    发送 hook 事件到 hook_bridge.py
    返回 (stdout输出, 耗时秒数)
    """
    t0 = time.time()
    proc = subprocess.Popen(
        [sys.executable, HOOK_BRIDGE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # v5: 无审批等待，5 秒超时足够
    try:
        stdout, stderr = proc.communicate(input=fixture_json, timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()

    elapsed = time.time() - t0

    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    if err:
        print(f"  [stderr] {err}")
    return out, elapsed


def _verify_response_time(elapsed: float, label: str) -> bool:
    """验证响应时间（v5 应该 < 1 秒）"""
    if elapsed > 1.0:
        print(f"  ⚠️  SLOW: {elapsed:.2f}s (expected < 1s)")
        return False
    return True


def _run_sequence(sequence, test_name, no_cooldown):
    """运行单个测试序列，返回慢响应数量"""
    print(f"\n[sim] {test_name}：发送 {len(sequence)} 个 hook 事件")
    print(f"{'─'*60}")
    slow_count = 0
    for label, filename, patch in sequence:
        print(f"\n[{label}]")
        raw = _load_fixture(filename, patch)
        fixture_json = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        try:
            out, elapsed = _send_hook(label, fixture_json)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
        try:
            result = json.loads(out) if out else {}
        except json.JSONDecodeError:
            result = {"raw": out}
        status = "OK" if result == {} else f"UNEXPECTED: {result}"
        print(f"  fixture  : {filename}{' + patch' if patch else ''}")
        print(f"  response : {result}  [{status}]  ({elapsed:.2f}s)")
        if not _verify_response_time(elapsed, label):
            slow_count += 1
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"[sim] {test_name} 完成，共 {len(sequence)} 个事件")
    if slow_count:
        print(f"[sim] ⚠️  {slow_count} 个响应超过 1 秒")
    else:
        print(f"[sim] ✓ 所有响应时间正常（< 1s）")

    if not no_cooldown:
        print(f"[sim] 等待 session 清除（11s）...")
        time.sleep(11)
    return slow_count


def _run_all(args, sequences):
    """运行全部测试序列"""
    total = len(sequences)
    total_events = sum(len(s) for s, _ in sequences)
    est_secs = total_events * 0.5 + total * 11
    print(f"\n[sim] 全量测试：{total} 个序列，{total_events} 个事件，预计 {est_secs:.0f}s")
    print(f"{'='*60}")

    total_slow = 0
    for i, (sequence, test_name) in enumerate(sequences, 1):
        print(f"\n[sim] [{i}/{total}] {test_name}")
        total_slow += _run_sequence(sequence, test_name, no_cooldown=False)

    print(f"\n{'='*60}")
    print(f"[sim] 全量测试完成：{total} 个序列，{total_events} 个事件")
    if total_slow:
        print(f"[sim] ⚠️  共 {total_slow} 个慢响应")
    else:
        print(f"[sim] ✓ 全部通过")


def main():
    parser = argparse.ArgumentParser(description="v5 架构集成测试")
    parser.add_argument("--stub", action="store_true",
                        help="以 --stub 模式启动 daemon（无设备测试）")
    parser.add_argument("--no-daemon", action="store_true",
                        help="daemon 已手动启动，跳过自动启动")
    parser.add_argument("--skip-ble-check", action="store_true",
                        help="跳过 BLE 连接检测")
    parser.add_argument("--multi-session", action="store_true",
                        help="多 session 测试")
    parser.add_argument("--parallel-tools", action="store_true",
                        help="并行工具测试")
    parser.add_argument("--error-handling", action="store_true",
                        help="错误处理测试")
    parser.add_argument("--interrupted", action="store_true", help="工具中断测试")
    parser.add_argument("--web-tools", action="store_true", help="Web 工具测试")
    parser.add_argument("--long-task", action="store_true", help="长任务测试")
    parser.add_argument("--session-restart", action="store_true", help="多轮对话测试")
    parser.add_argument("--mixed-tools", action="store_true", help="混合工具测试")
    parser.add_argument("--multi-session-error", action="store_true", help="多 Session 错误测试")
    parser.add_argument("--rapid-fire", action="store_true", help="快速连续工具测试")
    parser.add_argument("--subagent", action="store_true", help="Subagent 嵌套测试")
    parser.add_argument("--gui-face", action="store_true", help="GUI 脸部状态转换测试")
    parser.add_argument("--gui-5sessions", action="store_true", help="GUI 五 Session 并发测试")
    parser.add_argument("--gui-priority", action="store_true", help="GUI 消息块优先级测试")
    parser.add_argument("--approval", action="store_true", help="审批通知测试（PENDING 状态）")
    parser.add_argument("--all", action="store_true", help="运行全部序列（约 6 分钟）")
    parser.add_argument("--no-cooldown", action="store_true",
                        help="跳过序列结束后的 session 清除等待")
    args = parser.parse_args()

    # ── 1. 启动 daemon ────────────────────────────────────
    import tempfile
    LOG_PATH = os.path.join(tempfile.gettempdir(), "ble_daemon.log")

    daemon_proc = None
    if not args.no_daemon:
        if _wait_listen(timeout=1.0):
            print("[sim] daemon 已在运行，跳过自动启动")
        else:
            cmd = [sys.executable, "-u", BLE_DAEMON, "--log", LOG_PATH]
            if args.stub:
                cmd.append("--stub")
            daemon_proc = subprocess.Popen(cmd)
            print(f"[sim] 启动 daemon (stub={args.stub})，日志: {LOG_PATH}")
            if not _wait_listen(timeout=8.0):
                print("[sim] FAIL: daemon 未能在 8s 内监听 57320，退出")
                if daemon_proc:
                    daemon_proc.terminate()
                sys.exit(1)
            print(f"[sim] daemon 就绪")
    else:
        if not _wait_listen(timeout=1.0):
            print("[sim] WARN: --no-daemon 但 57320 不可达")

    # ── 2. 等待 BLE 连接（非 stub 模式）────────────────────
    if not args.stub and not args.skip_ble_check:
        print(f"[sim] 等待 ble_daemon 连上 ESP32（日志: {LOG_PATH}）...")
        if not _wait_ble_connected(LOG_PATH, timeout=90.0):
            print("[sim] FAIL: 90s 内未检测到 BLE 连接，退出")
            if daemon_proc:
                daemon_proc.terminate()
            sys.exit(1)
        print(f"\n[sim] BLE 已连接，开始发送事件")

    # ── 3. 选择测试序列 ───────────────────────────────────
    if args.all:
        # 运行全部序列
        _run_all(args, ALL_SEQUENCES)
        return

    if args.multi_session:
        sequence, test_name = MULTI_SESSION_SEQUENCE, "多 Session 测试"
    elif args.parallel_tools:
        sequence, test_name = PARALLEL_TOOLS_SEQUENCE, "并行工具测试"
    elif args.error_handling:
        sequence, test_name = ERROR_HANDLING_SEQUENCE, "错误处理测试"
    elif args.interrupted:
        sequence, test_name = INTERRUPTED_SEQUENCE, "工具中断测试"
    elif args.web_tools:
        sequence, test_name = WEB_TOOLS_SEQUENCE, "Web 工具测试"
    elif args.long_task:
        sequence, test_name = LONG_TASK_SEQUENCE, "长任务测试"
    elif args.session_restart:
        sequence, test_name = SESSION_RESTART_SEQUENCE, "多轮对话测试"
    elif args.mixed_tools:
        sequence, test_name = MIXED_TOOLS_SEQUENCE, "混合工具测试"
    elif args.multi_session_error:
        sequence, test_name = MULTI_SESSION_ERROR_SEQUENCE, "多 Session 错误测试"
    elif args.rapid_fire:
        sequence, test_name = RAPID_FIRE_SEQUENCE, "快速连续工具测试"
    elif args.subagent:
        sequence, test_name = SUBAGENT_SEQUENCE, "Subagent 嵌套测试"
    elif args.gui_face:
        sequence, test_name = GUI_FACE_TRANSITIONS_SEQUENCE, "GUI 脸部状态转换测试"
    elif args.gui_5sessions:
        sequence, test_name = GUI_5SESSIONS_SEQUENCE, "GUI 五 Session 并发测试"
    elif args.gui_priority:
        sequence, test_name = GUI_PRIORITY_SEQUENCE, "GUI 消息块优先级测试"
    elif args.approval:
        sequence, test_name = APPROVAL_SEQUENCE, "审批通知测试"
    else:
        sequence, test_name = BASIC_SEQUENCE, "基本功能测试"

    # ── 4. 运行序列 ───────────────────────────────────────
    try:
        _run_sequence(sequence, test_name, args.no_cooldown)
    finally:
        if daemon_proc is not None:
            print("\n[sim] 终止 daemon")
            daemon_proc.terminate()
            try:
                daemon_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()


if __name__ == "__main__":
    main()
