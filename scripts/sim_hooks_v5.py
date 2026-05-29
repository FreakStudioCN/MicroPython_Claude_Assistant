#!/usr/bin/env python3
# scripts/sim_hooks_v5.py —— v5 架构手动集成测试
#
# 用途: 模拟 Claude Code 触发 hook 事件，测试 v5 纯展示模式
#       → hook_bridge.py（真实运行）→ ble_daemon.py（真实运行）→ BLE → ESP32
#
# v5 变化（对齐当前 ble_daemon.py）:
#   - C 状态：由 Stop hook 触发（设 completed_until），不再依赖静默期推断
#   - P 状态：由 Notification(permission_prompt) 触发（waiting++），
#             由 stop/user_prompt 清零（tool_done 不清 waiting）
#   - needs_approval 在 hook_bridge 中固定为 False，P 状态不由 PreToolUse 触发
#   - Stop.json fixture 新增，CLOCK_SEQUENCE 中的 Stop 事件已修正为使用它
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
    ("Stop",               "Stop.json",                None),
]

# ── 多 Session 测试序列 ────────────────────────────────────
# 验证多个 Claude Code 实例同时工作
MULTI_SESSION_SEQUENCE = [
    # Session 1: 用户提交 prompt
    ("S1: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "session_1",
        "cwd": "C:\\Users\\user\\Projects\\MyProject",
        "prompt": "Read main.py"
    }),

    # Session 1: 开始读取文件
    ("S1: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "session_1",
        "cwd": "C:\\Users\\user\\Projects\\MyProject",
        "tool_name": "Read",
        "tool_use_id": "toolu_S1_READ1",
        "tool_input": {"file_path": "main.py"}
    }),

    # Session 2: 同时启动（并发场景）
    ("S2: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "session_2",
        "cwd": "C:\\Users\\user\\Projects\\WebApp",
        "prompt": "Run tests"
    }),

    # Session 2: 开始执行命令
    ("S2: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "session_2",
        "cwd": "C:\\Users\\user\\Projects\\WebApp",
        "tool_name": "Bash",
        "tool_use_id": "toolu_S2_BASH1",
        "tool_input": {"command": "pytest"}
    }),

    # Session 3: 第三个实例
    ("S3: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "session_3",
        "cwd": "C:\\Users\\user\\Projects\\DataPipeline",
        "prompt": "Search for TODO"
    }),

    # Session 3: 搜索
    ("S3: PreToolUse(Grep)", "PreToolUse.json", {
        "session_id": "session_3",
        "cwd": "C:\\Users\\user\\Projects\\DataPipeline",
        "tool_name": "Grep",
        "tool_use_id": "toolu_S3_GREP1",
        "tool_input": {"pattern": "TODO"}
    }),

    # Session 1: 完成
    ("S1: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "session_1",
        "cwd": "C:\\Users\\user\\Projects\\MyProject",
        "tool_name": "Read",
        "tool_use_id": "toolu_S1_READ1",
        "tool_response": {"interrupted": False}
    }),

    # Session 2: 完成
    ("S2: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "session_2",
        "cwd": "C:\\Users\\user\\Projects\\WebApp",
        "tool_name": "Bash",
        "tool_use_id": "toolu_S2_BASH1",
        "tool_response": {"interrupted": False}
    }),

    # Session 3: 完成
    ("S3: PostToolUse(Grep)", "PostToolUse.json", {
        "session_id": "session_3",
        "cwd": "C:\\Users\\user\\Projects\\DataPipeline",
        "tool_name": "Grep",
        "tool_use_id": "toolu_S3_GREP1",
        "tool_response": {"interrupted": False}
    }),
    ("S1: Stop", "Stop.json", {"session_id": "session_1", "cwd": "C:\\Users\\user\\Projects\\MyProject"}),
    ("S2: Stop", "Stop.json", {"session_id": "session_2", "cwd": "C:\\Users\\user\\Projects\\WebApp"}),
    ("S3: Stop", "Stop.json", {"session_id": "session_3", "cwd": "C:\\Users\\user\\Projects\\DataPipeline"}),
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
    ("Stop", "Stop.json", {"session_id": "parallel_test"}),
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
    ("Stop", "Stop.json", {"session_id": "error_test"}),
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
    ("Stop", "Stop.json", {"session_id": "interrupt_test"}),
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
    ("Stop", "Stop.json", {"session_id": "web_test"}),
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
    ("Stop", "Stop.json", {"session_id": "long_task"}),
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
    ("Turn3: Stop", "Stop.json", {"session_id": "restart_test"}),
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
    ("Stop", "Stop.json", {"session_id": "mixed_test"}),
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
    ("S1: Stop", "Stop.json", {"session_id": "err_s1"}),
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
    ("Stop", "Stop.json", {"session_id": "rapid_test"}),
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
    ("Stop", "Stop.json", {"session_id": "subagent_test"}),
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
    ("S1: Stop(turn1→C)", "Stop.json", {"session_id": "gui_face_s1"}),
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
    ("S3: Stop(→C)", "Stop.json", {"session_id": "gui_5s_3"}),
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
    ("S1: Stop", "Stop.json", {"session_id": "gui_prio_s1"}),
    ("S3: Stop", "Stop.json", {"session_id": "gui_prio_s3"}),
    # [期望] S1=C, S2=C, S3=C → 脸=绿, 消息块=绿 "S1: Done"
]

# ── 长消息显示测试序列 ────────────────────────────────────
# 验证 60 字符长消息的跑马灯滚动和历史记录换行
LONG_MESSAGE_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "prompt": "Analyze git history and refactor display code"
    }),
    ("PreToolUse(Bash-1)", "PreToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Bash",
        "tool_use_id": "toolu_LONG_BASH1",
        "tool_input": {"command": "git log --oneline --graph --all --decorate --abbrev-commit --author=user -20"}
    }),
    ("PostToolUse(Bash-1)", "PostToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Bash",
        "tool_use_id": "toolu_LONG_BASH1",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Read-1)", "PreToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Read",
        "tool_use_id": "toolu_LONG_READ1",
        "tool_input": {"file_path": "C:\\Users\\Administrator\\Projects\\MicroPython_Claude_Assistant\\device\\display_renderer.py"}
    }),
    ("PostToolUse(Read-1)", "PostToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Read",
        "tool_use_id": "toolu_LONG_READ1",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Grep-1)", "PreToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Grep",
        "tool_use_id": "toolu_LONG_GREP1",
        "tool_input": {"pattern": "def _update_main.*completed_until.*dizzy_until.*session_dots"}
    }),
    ("PostToolUse(Grep-1)", "PostToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Grep",
        "tool_use_id": "toolu_LONG_GREP1",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Bash-2)", "PreToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Bash",
        "tool_use_id": "toolu_LONG_BASH2",
        "tool_input": {"command": "find . -name '*.py' -type f -exec grep -l 'async def' {} \\; | head -20"}
    }),
    ("PostToolUse(Bash-2)", "PostToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Bash",
        "tool_use_id": "toolu_LONG_BASH2",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Edit-1)", "PreToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Edit",
        "tool_use_id": "toolu_LONG_EDIT1",
        "tool_input": {"file_path": "C:\\Users\\Administrator\\Projects\\MicroPython_Claude_Assistant\\daemon\\ble_daemon.py"}
    }),
    ("PostToolUse(Edit-1)", "PostToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Edit",
        "tool_use_id": "toolu_LONG_EDIT1",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Write-1)", "PreToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Write",
        "tool_use_id": "toolu_LONG_WRITE1",
        "tool_input": {"file_path": "C:\\Users\\Administrator\\Projects\\MicroPython_Claude_Assistant\\tests\\test_long_message.py"}
    }),
    ("PostToolUse(Write-1)", "PostToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Write",
        "tool_use_id": "toolu_LONG_WRITE1",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Glob-1)", "PreToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Glob",
        "tool_use_id": "toolu_LONG_GLOB1",
        "tool_input": {"pattern": "**/*display*renderer*.py"}
    }),
    ("PostToolUse(Glob-1)", "PostToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Glob",
        "tool_use_id": "toolu_LONG_GLOB1",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(Bash-3)", "PreToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Bash",
        "tool_use_id": "toolu_LONG_BASH3",
        "tool_input": {"command": "python -m pytest tests/ -v --tb=short --maxfail=3 --color=yes"}
    }),
    ("PostToolUse(Bash-3)", "PostToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "Bash",
        "tool_use_id": "toolu_LONG_BASH3",
        "tool_response": {"interrupted": False}
    }),
    ("PreToolUse(WebSearch-1)", "PreToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "WebSearch",
        "tool_use_id": "toolu_LONG_WEB1",
        "tool_input": {"url": "https://docs.micropython.org/en/latest/library/lvgl.html#display-rotation"}
    }),
    ("PostToolUse(WebSearch-1)", "PostToolUse.json", {
        "session_id": "long_msg_test",
        "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant",
        "tool_name": "WebSearch",
        "tool_use_id": "toolu_LONG_WEB1",
        "tool_response": {"interrupted": False}
    }),
    ("Stop", "Stop.json", {"session_id": "long_msg_test",
                           "cwd": "C:\\Users\\user\\Projects\\MicroPython_Claude_Assistant"}),
]

# ── 审批通知序列 ──────────────────────────────────────────
# v5 架构：P 状态由 Notification(permission_prompt) 触发（waiting++）
#          waiting 由 stop/user_prompt 清零；tool_done 不清 waiting
#          （hook_bridge 固定 needs_approval=False，PreToolUse 不再触发 P）
APPROVAL_SEQUENCE = [
    # 阶段1: 普通工具 → W 状态
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
    # [期望] W 状态
    ("PostToolUse(Read-safe)", "PostToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_AP_R1",
        "tool_response": {"interrupted": False}
    }),

    # 阶段2: 高风险工具开始 → W，随后 Claude Code 发出审批通知 → P
    ("PreToolUse(Bash-risky)", "PreToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_AP_B1",
        "tool_input": {"command": "rm -rf /tmp/build && git push --force"}
    }),
    # [期望] W 状态（工具开始，审批通知尚未到达）
    ("Notification(permission_prompt→P)", "Notification.json", {
        "session_id": "approval_test",
        "notification_type": "permission_prompt"
    }),
    # [期望] P 状态（waiting=1）
    ("PostToolUse(Bash-approved)", "PostToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_AP_B1",
        "tool_response": {"interrupted": False}
    }),
    # [期望] 仍 P（v5: tool_done 不清 waiting，waiting 由 stop 清零）
    ("Stop(turn1完成→waiting清零)", "Stop.json", {
        "session_id": "approval_test"
    }),
    # [期望] C 状态（waiting=0，completed_until 设置）

    # 阶段3: 高风险工具被拒绝 → E 状态
    ("UserPromptSubmit(turn2)", "UserPromptSubmit.json", {
        "session_id": "approval_test",
        "prompt": "Try again with .env"
    }),
    ("PreToolUse(Write-risky)", "PreToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Write",
        "tool_use_id": "toolu_AP_W1",
        "tool_input": {"file_path": ".env"}
    }),
    ("Notification(permission_prompt)", "Notification.json", {
        "session_id": "approval_test",
        "notification_type": "permission_prompt"
    }),
    # [期望] P 状态
    ("PostToolUseFailure(Write-denied)", "PostToolUseFailure.json", {
        "session_id": "approval_test",
        "tool_name": "Write",
        "tool_use_id": "toolu_AP_W1",
        "error": "User denied the operation"
    }),
    # [期望] E 状态（tool_error 触发 dizzy，3s 后自动消）

    # 阶段4: 两个工具并发，其中触发一次审批通知
    ("UserPromptSubmit(turn3)", "UserPromptSubmit.json", {
        "session_id": "approval_test",
        "prompt": "Multiple ops"
    }),
    ("PreToolUse(Read-safe2)", "PreToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_AP_R2",
        "tool_input": {"file_path": "config.py"}
    }),
    ("PreToolUse(Bash-risky2)", "PreToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_AP_B2",
        "tool_input": {"command": "git push --force origin main"}
    }),
    ("Notification(permission_prompt)", "Notification.json", {
        "session_id": "approval_test",
        "notification_type": "permission_prompt"
    }),
    # [期望] P 状态（waiting=1，两个工具仍在运行）
    ("PostToolUse(Read-done)", "PostToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Read",
        "tool_use_id": "toolu_AP_R2",
        "tool_response": {"interrupted": False}
    }),
    ("PostToolUse(Bash-done)", "PostToolUse.json", {
        "session_id": "approval_test",
        "tool_name": "Bash",
        "tool_use_id": "toolu_AP_B2",
        "tool_response": {"interrupted": False}
    }),
    # [期望] 仍 P（waiting 未清）
    ("Stop(turn3完成→C)", "Stop.json", {
        "session_id": "approval_test"
    }),
    # [期望] C 状态（waiting=0）
]

# ── 闹钟版语音测试序列（--clock）────────────────────────────
# 目标：验证 LightRenderer 语音触发时序
#   1. 正常触发：W → C，全链路 DeepSeek+TTS 跑通
#   2. busy 丢弃：连续快速 C/E/C，第二三个被正确丢弃
#   3. 等待审批触发：W → P，验证 P 状态语音
#   4. 3 session 并发：其中一个 C，验证只触发一次语音
#
# 间隔 0.5s 模拟真实 Claude Code 频率（比普通测试更激进）
CLOCK_SEQUENCE = [
    # ── 场景1：正常完成触发语音 ──────────────────────────────
    ("S1: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "clock_s1",
        "cwd": "C:\\Projects\\danke_ai",
        "prompt": "帮我整理代码"
    }, 3.0),
    ("S1: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "clock_s1",
        "cwd": "C:\\Projects\\danke_ai",
        "tool_name": "Read",
        "tool_use_id": "toolu_CLK_R1",
        "tool_input": {"file_path": "main.py"}
    }, 3.0),
    ("S1: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "clock_s1",
        "cwd": "C:\\Projects\\danke_ai",
        "tool_name": "Read",
        "tool_use_id": "toolu_CLK_R1",
        "tool_response": {"interrupted": False}
    }, 3.0),
    ("S1: Stop(完成)", "Stop.json", {
        "session_id": "clock_s1",
        "cwd": "C:\\Projects\\danke_ai",
    }, 15.0),  # 等语音播完

    # ── 场景2：快速 C→E→C，验证 busy 丢弃 ───────────────────
    ("S2: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "clock_s2",
        "cwd": "C:\\Projects\\danke_ai",
        "prompt": "运行测试"
    }, 3.0),
    ("S2: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "clock_s2",
        "cwd": "C:\\Projects\\danke_ai",
        "tool_name": "Bash",
        "tool_use_id": "toolu_CLK_B1",
        "tool_input": {"command": "pytest tests/"}
    }, 3.0),
    ("S2: Stop(完成→触发)", "Stop.json", {
        "session_id": "clock_s2",
        "cwd": "C:\\Projects\\danke_ai",
    }, 0.5),  # 故意快速，测试覆盖
    ("S2: PostToolUseFailure(busy丢弃)", "PostToolUseFailure.json", {
        "session_id": "clock_s2",
        "cwd": "C:\\Projects\\danke_ai",
    }, 0.5),  # 故意快速
    ("S2: Stop(再次完成→丢弃)", "Stop.json", {
        "session_id": "clock_s2",
        "cwd": "C:\\Projects\\danke_ai",
    }, 15.0),  # 等语音播完

    # ── 场景3：等待审批触发语音 ──────────────────────────────
    ("S3: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "clock_s3",
        "cwd": "C:\\Projects\\danke_ai",
        "prompt": "删除临时文件"
    }, 3.0),
    ("S3: PreToolUse(Bash/危险)", "PreToolUse.json", {
        "session_id": "clock_s3",
        "cwd": "C:\\Projects\\danke_ai",
        "tool_name": "Bash",
        "tool_use_id": "toolu_CLK_B2",
        "tool_input": {"command": "rm -rf /tmp/cache"}
    }, 3.0),
    ("S3: Notification(等待审批)", "Notification.json", {
        "session_id": "clock_s3",
        "cwd": "C:\\Projects\\danke_ai",
    }, 15.0),  # 等语音播完
    ("S3: PostToolUse(审批后完成)", "PostToolUse.json", {
        "session_id": "clock_s3",
        "cwd": "C:\\Projects\\danke_ai",
        "tool_name": "Bash",
        "tool_use_id": "toolu_CLK_B2",
        "tool_response": {"interrupted": False}
    }, 5.0),

    # 场景3 完成后 Stop → C 状态（waiting 由 stop 清零）
    ("S3: Stop(审批完成)", "Stop.json", {
        "session_id": "clock_s3",
        "cwd": "C:\\Projects\\danke_ai",
    }, 5.0),

    # ── 场景4：3 session 并发，只有一个 C ────────────────────
    ("S4a: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "clock_s4a",
        "cwd": "C:\\Projects\\proj_a",
        "prompt": "分析日志"
    }, 1.0),
    ("S4b: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "clock_s4b",
        "cwd": "C:\\Projects\\proj_b",
        "prompt": "生成报告"
    }, 1.0),
    ("S4c: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "clock_s4c",
        "cwd": "C:\\Projects\\proj_c",
        "prompt": "更新依赖"
    }, 5.0),
    ("S4a: Stop(完成)", "Stop.json", {
        "session_id": "clock_s4a",
        "cwd": "C:\\Projects\\proj_a",
    }, 15.0),
    ("S4b: PostToolUseFailure(出错)", "PostToolUseFailure.json", {
        "session_id": "clock_s4b",
        "cwd": "C:\\Projects\\proj_b",
    }, 15.0),
    ("S4c: Stop(完成)", "Stop.json", {
        "session_id": "clock_s4c",
        "cwd": "C:\\Projects\\proj_c",
    }, 5.0),
]

# ── GUI: S_PENDING 在优先级中高于 S_WORKING ──────────────
# 验证 _dominant_state 中 E > P > W > C 顺序
GUI_PENDING_PRIORITY_SEQUENCE = [
    ("S1: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "gui_pp_s1", "prompt": "Long running task"
    }),
    ("S1: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "gui_pp_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_PP_B1", "tool_input": {"command": "build.sh"}
    }),
    # S1=W 状态
    ("S2: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "gui_pp_s2", "prompt": "Risky operation"
    }),
    ("S2: PreToolUse(Bash-risky)", "PreToolUse.json", {
        "session_id": "gui_pp_s2", "tool_name": "Bash",
        "tool_use_id": "toolu_PP_B2", "tool_input": {"command": "rm -rf /tmp"}
    }),
    ("S2: Notification(permission_prompt→P)", "Notification.json", {
        "session_id": "gui_pp_s2", "notification_type": "permission_prompt"
    }),
    # [期望] S1=W, S2=P → dominant=P → 脸黄色闪烁，消息块=黄 "S2: Pending"
    ("S1: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "gui_pp_s1", "tool_name": "Bash",
        "tool_use_id": "toolu_PP_B1", "tool_response": {"interrupted": False}
    }),
    ("S1: Stop", "Stop.json", {"session_id": "gui_pp_s1"}),
    ("S2: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "gui_pp_s2", "tool_name": "Bash",
        "tool_use_id": "toolu_PP_B2", "tool_response": {"interrupted": False}
    }),
    ("S2: Stop", "Stop.json", {"session_id": "gui_pp_s2"}),
]

# ── GUI: P → E 状态转换 ───────────────────────────────────
# 验证审批拒绝后 P 直接跳 E，脸色从黄变红
GUI_PENDING_ERROR_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "gui_pe", "prompt": "Deploy to prod"
    }),
    ("PreToolUse(Bash-risky)", "PreToolUse.json", {
        "session_id": "gui_pe", "tool_name": "Bash",
        "tool_use_id": "toolu_PE_B1", "tool_input": {"command": "deploy.sh --prod"}
    }),
    ("Notification(permission_prompt→P)", "Notification.json", {
        "session_id": "gui_pe", "notification_type": "permission_prompt"
    }),
    # [期望] P 状态，脸黄色
    ("PostToolUseFailure(denied→E)", "PostToolUseFailure.json", {
        "session_id": "gui_pe", "tool_name": "Bash",
        "tool_use_id": "toolu_PE_B1", "error": "User denied"
    }),
    # [期望] E 状态，脸红色，选项卡闪烁
    ("StopFailure", "StopFailure.json", {"session_id": "gui_pe"}),
]

# ── GUI: 快速工具（< 0.4s）兜底显示 ─────────────────────
# 验证 last_tool_start_ts 兜底：工具名在消息块中至少显示 0.4s
GUI_FAST_TOOL_SEQUENCE = [
    ("UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "gui_ft", "prompt": "Quick reads"
    }),
] + [
    (f"PreToolUse(Read-{i})", "PreToolUse.json", {
        "session_id": "gui_ft", "tool_name": "Read",
        "tool_use_id": f"toolu_FT_R{i}", "tool_input": {"file_path": f"f{i}.py"}
    })
    for i in range(1, 5)
] + [
    (f"PostToolUse(Read-{i})", "PostToolUse.json", {
        "session_id": "gui_ft", "tool_name": "Read",
        "tool_use_id": f"toolu_FT_R{i}", "tool_response": {"interrupted": False}
    })
    for i in range(1, 5)
] + [
    ("Stop", "Stop.json", {"session_id": "gui_ft"}),
]

# ── GUI: C 状态后新一轮不清除 completed_until ────────────
# 验证 UserPromptSubmit 不会提前清除 C 状态显示
GUI_C_NEW_TURN_SEQUENCE = [
    ("Turn1: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "gui_cnt", "prompt": "First task"
    }),
    ("Turn1: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "gui_cnt", "tool_name": "Read",
        "tool_use_id": "toolu_CNT_R1", "tool_input": {"file_path": "a.py"}
    }),
    ("Turn1: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "gui_cnt", "tool_name": "Read",
        "tool_use_id": "toolu_CNT_R1", "tool_response": {"interrupted": False}
    }),
    ("Turn1: Stop(→C)", "Stop.json", {"session_id": "gui_cnt"}),
    # [期望] C 状态，脸绿色
    # 立即发起第二轮（completed_until 窗口内）
    ("Turn2: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "gui_cnt", "prompt": "Second task"
    }),
    # [期望] 仍显示 C（completed_until 未过期），不应立即变灰
    ("Turn2: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "gui_cnt", "tool_name": "Bash",
        "tool_use_id": "toolu_CNT_B1", "tool_input": {"command": "pytest"}
    }),
    # [期望] 现在变 W（有新工具运行）
    ("Turn2: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "gui_cnt", "tool_name": "Bash",
        "tool_use_id": "toolu_CNT_B1", "tool_response": {"interrupted": False}
    }),
    ("Turn2: Stop", "Stop.json", {"session_id": "gui_cnt"}),
]

# ── GUI: 同 cwd 旧 session 退休 ──────────────────────────
# 验证同一 cwd 新 session 启动时，旧 session（P 状态）被移除
GUI_SESSION_RETIRE_SEQUENCE = [
    ("Old: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "gui_sr_old",
        "cwd": "C:\\Projects\\shared_proj",
        "prompt": "Old task"
    }),
    ("Old: Notification(→P)", "Notification.json", {
        "session_id": "gui_sr_old",
        "cwd": "C:\\Projects\\shared_proj",
        "notification_type": "permission_prompt"
    }),
    # [期望] old session P 状态
    ("New: UserPromptSubmit(同cwd→旧session退休)", "UserPromptSubmit.json", {
        "session_id": "gui_sr_new",
        "cwd": "C:\\Projects\\shared_proj",
        "prompt": "New task same dir"
    }),
    # [期望] old session 被移除，new session 出现（W 或 I）
    ("New: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "gui_sr_new",
        "cwd": "C:\\Projects\\shared_proj",
        "tool_name": "Read",
        "tool_use_id": "toolu_SR_R1", "tool_input": {"file_path": "main.py"}
    }),
    ("New: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "gui_sr_new",
        "cwd": "C:\\Projects\\shared_proj",
        "tool_name": "Read",
        "tool_use_id": "toolu_SR_R1", "tool_response": {"interrupted": False}
    }),
    ("New: Stop", "Stop.json", {
        "session_id": "gui_sr_new",
        "cwd": "C:\\Projects\\shared_proj"
    }),
]

# ── GUI: 显示名 basename 冲突加后缀 ──────────────────────
# 验证两个不同路径但 basename 相同时，daemon 加数字后缀区分
GUI_DISPLAY_NAME_CONFLICT_SEQUENCE = [
    ("S1: UserPromptSubmit(proj_a/main)", "UserPromptSubmit.json", {
        "session_id": "gui_dnc_s1",
        "cwd": "C:\\Projects\\proj_a",
        "prompt": "Task in proj_a"
    }),
    ("S2: UserPromptSubmit(proj_b/main)", "UserPromptSubmit.json", {
        "session_id": "gui_dnc_s2",
        "cwd": "C:\\Projects\\proj_b",
        "prompt": "Task in proj_b"
    }),
    # [期望] S1 显示 "proj_a", S2 显示 "proj_b"（basename 不同，无冲突）
    # 制造 basename 冲突：两个 session 都在名为 "myapp" 的目录
    ("S3: UserPromptSubmit(a/myapp)", "UserPromptSubmit.json", {
        "session_id": "gui_dnc_s3",
        "cwd": "C:\\Users\\alice\\myapp",
        "prompt": "Alice task"
    }),
    ("S4: UserPromptSubmit(b/myapp)", "UserPromptSubmit.json", {
        "session_id": "gui_dnc_s4",
        "cwd": "C:\\Users\\bob\\myapp",
        "prompt": "Bob task"
    }),
    # [期望] S3="myapp", S4="myapp2"（或类似后缀）
    ("S1: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "gui_dnc_s1", "tool_name": "Read",
        "tool_use_id": "toolu_DNC_R1", "tool_input": {"file_path": "a.py"}
    }),
    ("S3: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "gui_dnc_s3", "tool_name": "Read",
        "tool_use_id": "toolu_DNC_R2", "tool_input": {"file_path": "b.py"}
    }),
    ("S1: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "gui_dnc_s1", "tool_name": "Read",
        "tool_use_id": "toolu_DNC_R1", "tool_response": {"interrupted": False}
    }),
    ("S3: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "gui_dnc_s3", "tool_name": "Read",
        "tool_use_id": "toolu_DNC_R2", "tool_response": {"interrupted": False}
    }),
    ("S1: Stop", "Stop.json", {"session_id": "gui_dnc_s1"}),
    ("S2: Stop", "Stop.json", {"session_id": "gui_dnc_s2"}),
    ("S3: Stop", "Stop.json", {"session_id": "gui_dnc_s3"}),
    ("S4: Stop", "Stop.json", {"session_id": "gui_dnc_s4"}),
]

# ── v6 槽位稳定性测试序列 ──────────────────────────────────
# 验证 v6 协议：session 沉默 >10s 后重连，槽位不漂移，历史不清空
V6_SLOT_STABILITY_SEQUENCE = [
    # Session A 和 B 同时启动
    ("A: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "27f7bc8f-cc50-409b-95e3-14b498641167",  # 固定 SID
        "cwd": "G:\\test",
        "prompt": "Read test.py"
    }),
    ("A: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "27f7bc8f-cc50-409b-95e3-14b498641167",
        "cwd": "G:\\test",
        "tool_name": "Read",
        "tool_use_id": "toolu_A_READ1",
        "tool_input": {"file_path": "test.py"}
    }),
    ("A: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "27f7bc8f-cc50-409b-95e3-14b498641167",
        "cwd": "G:\\test",
        "tool_name": "Read",
        "tool_use_id": "toolu_A_READ1",
        "tool_response": {"interrupted": False}
    }),
    ("A: Stop", "Stop.json", {
        "session_id": "27f7bc8f-cc50-409b-95e3-14b498641167",
        "cwd": "G:\\test"
    }),

    ("B: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "9432b9ea-7c5c-4ed0-9275-e301da4e2855",
        "cwd": "G:\\MicroPython_Claude_Assistant",
        "prompt": "List files"
    }),
    ("B: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "9432b9ea-7c5c-4ed0-9275-e301da4e2855",
        "cwd": "G:\\MicroPython_Claude_Assistant",
        "tool_name": "Bash",
        "tool_use_id": "toolu_B_BASH1",
        "tool_input": {"command": "ls"}
    }),
    ("B: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "9432b9ea-7c5c-4ed0-9275-e301da4e2855",
        "cwd": "G:\\MicroPython_Claude_Assistant",
        "tool_name": "Bash",
        "tool_use_id": "toolu_B_BASH1",
        "tool_response": {"interrupted": False}
    }),
    ("B: Stop", "Stop.json", {
        "session_id": "9432b9ea-7c5c-4ed0-9275-e301da4e2855",
        "cwd": "G:\\MicroPython_Claude_Assistant"
    }),

    # 等待 12 秒（让 A 超时被清理）
    ("WAIT 12s for A cleanup", None, {"sleep": 12}),

    # A 重新活跃（同一个 SID）
    ("A: UserPromptSubmit(reconnect)", "UserPromptSubmit.json", {
        "session_id": "27f7bc8f-cc50-409b-95e3-14b498641167",  # 同一个 SID
        "cwd": "G:\\test",
        "prompt": "Read another file"
    }),
    ("A: PreToolUse(Read-2)", "PreToolUse.json", {
        "session_id": "27f7bc8f-cc50-409b-95e3-14b498641167",
        "cwd": "G:\\test",
        "tool_name": "Read",
        "tool_use_id": "toolu_A_READ2",
        "tool_input": {"file_path": "main.py"}
    }),
    ("A: PostToolUse(Read-2)", "PostToolUse.json", {
        "session_id": "27f7bc8f-cc50-409b-95e3-14b498641167",
        "cwd": "G:\\test",
        "tool_name": "Read",
        "tool_use_id": "toolu_A_READ2",
        "tool_response": {"interrupted": False}
    }),
    ("A: Stop", "Stop.json", {
        "session_id": "27f7bc8f-cc50-409b-95e3-14b498641167",
        "cwd": "G:\\test"
    }),
]

# ── v6 同 cwd 多窗口序列 ──────────────────────────────────
# 验证同一 cwd 下 3 个不同 SID 各占独立槽位，互不冲突
V6_SAME_CWD_MULTI_SEQUENCE = [
    ("W1: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "cwd": "G:\\test", "prompt": "Window 1"
    }),
    ("W1: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "cwd": "G:\\test", "tool_name": "Read",
        "tool_use_id": "toolu_W1_R1", "tool_input": {"file_path": "a.py"}
    }),
    ("W2: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "bbbbbbbb-0000-0000-0000-000000000002",
        "cwd": "G:\\test", "prompt": "Window 2"
    }),
    ("W2: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "bbbbbbbb-0000-0000-0000-000000000002",
        "cwd": "G:\\test", "tool_name": "Bash",
        "tool_use_id": "toolu_W2_B1", "tool_input": {"command": "ls"}
    }),
    ("W3: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "cccccccc-0000-0000-0000-000000000003",
        "cwd": "G:\\test", "prompt": "Window 3"
    }),
    ("W3: PreToolUse(Grep)", "PreToolUse.json", {
        "session_id": "cccccccc-0000-0000-0000-000000000003",
        "cwd": "G:\\test", "tool_name": "Grep",
        "tool_use_id": "toolu_W3_G1", "tool_input": {"pattern": "TODO"}
    }),
    # [期望] S1=test(W1) S2=test(W2) S3=test(W3)，三个槽各自独立
    ("W1: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "cwd": "G:\\test", "tool_name": "Read",
        "tool_use_id": "toolu_W1_R1", "tool_response": {"interrupted": False}
    }),
    ("W1: Stop", "Stop.json", {"session_id": "aaaaaaaa-0000-0000-0000-000000000001", "cwd": "G:\\test"}),
    # W1 沉默 12s 后重连，验证回到原槽位
    ("WAIT 12s for W1 cleanup", None, {"sleep": 12}),
    ("W1: UserPromptSubmit(reconnect)", "UserPromptSubmit.json", {
        "session_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "cwd": "G:\\test", "prompt": "Window 1 reconnect"
    }),
    ("W1: PreToolUse(Read-2)", "PreToolUse.json", {
        "session_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "cwd": "G:\\test", "tool_name": "Read",
        "tool_use_id": "toolu_W1_R2", "tool_input": {"file_path": "b.py"}
    }),
    ("W1: Stop", "Stop.json", {"session_id": "aaaaaaaa-0000-0000-0000-000000000001", "cwd": "G:\\test"}),
    # [期望] W1 回到 S1（原槽位），无 session changed，历史保留
    ("W2: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "bbbbbbbb-0000-0000-0000-000000000002",
        "cwd": "G:\\test", "tool_name": "Bash",
        "tool_use_id": "toolu_W2_B1", "tool_response": {"interrupted": False}
    }),
    ("W2: Stop", "Stop.json", {"session_id": "bbbbbbbb-0000-0000-0000-000000000002", "cwd": "G:\\test"}),
    ("W3: PostToolUse(Grep)", "PostToolUse.json", {
        "session_id": "cccccccc-0000-0000-0000-000000000003",
        "cwd": "G:\\test", "tool_name": "Grep",
        "tool_use_id": "toolu_W3_G1", "tool_response": {"interrupted": False}
    }),
    ("W3: Stop", "Stop.json", {"session_id": "cccccccc-0000-0000-0000-000000000003", "cwd": "G:\\test"}),
]

# ── v6 15 session 综合压力测试 ────────────────────────────
# 验证槽位分配、沉默重连、满槽处理的完整流程
V6_COMPREHENSIVE_SEQUENCE = [
    # === 阶段 1：填满 5 个槽 ===
    *[
        (f"S{i}: UserPromptSubmit", "UserPromptSubmit.json", {
            "session_id": f"comp-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}", "prompt": f"Task {i}"
        })
        for i in range(1, 6)
    ],
    *[
        (f"S{i}: PreToolUse(Read)", "PreToolUse.json", {
            "session_id": f"comp-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}", "tool_name": "Read",
            "tool_use_id": f"toolu_C_R{i}", "tool_input": {"file_path": "main.py"}
        })
        for i in range(1, 6)
    ],
    # [期望] slot[0~4] 各占一个 session (proj1~proj5)

    # === 阶段 2：proj3 沉默 >10s ===
    *[
        (f"S{i}: PostToolUse", "PostToolUse.json", {
            "session_id": f"comp-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}", "tool_name": "Read",
            "tool_use_id": f"toolu_C_R{i}", "tool_response": {"interrupted": False}
        })
        for i in range(1, 6)
    ],
    *[
        (f"S{i}: Stop", "Stop.json", {
            "session_id": f"comp-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}"
        })
        for i in range(1, 6)
    ],
    ("WAIT 12s for proj3 cleanup", None, {"sleep": 12}),
    # [期望] wire 里只剩 4 个 session，slot[2] 显示空闲但映射表还记得 proj3

    # === 阶段 3：6~10 号 session 来了（5 个新 session） ===
    *[
        (f"S{i}: UserPromptSubmit", "UserPromptSubmit.json", {
            "session_id": f"comp-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}", "prompt": f"Task {i}"
        })
        for i in range(6, 11)
    ],
    # [期望] proj6 占 slot[2]（proj3 的位置），proj7~10 被跳过（槽满）

    # === 阶段 4：proj3 重连 ===
    ("S3: UserPromptSubmit(reconnect)", "UserPromptSubmit.json", {
        "session_id": "comp-0003-0000-0000-0000-000000000003",
        "cwd": "G:\\proj3", "prompt": "proj3 reconnect"
    }),
    # [期望] proj3 发现 slot[2] 被 proj6 占了，找不到空槽，被跳过

    # === 阶段 5：proj1 沉默 >10s ===
    ("S1: Stop", "Stop.json", {
        "session_id": "comp-0001-0000-0000-0000-000000000001",
        "cwd": "G:\\proj1"
    }),
    ("WAIT 12s for proj1 cleanup", None, {"sleep": 12}),
    # [期望] slot[0] 显示空闲，映射表还记得 proj1

    # === 阶段 6：11~15 号 session 来了 ===
    *[
        (f"S{i}: UserPromptSubmit", "UserPromptSubmit.json", {
            "session_id": f"comp-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}", "prompt": f"Task {i}"
        })
        for i in range(11, 16)
    ],
    # [期望] proj11 占 slot[0]（proj1 的位置），proj12~15 被跳过

    # === 阶段 7：proj1 重连 ===
    ("S1: UserPromptSubmit(reconnect)", "UserPromptSubmit.json", {
        "session_id": "comp-0001-0000-0000-0000-000000000001",
        "cwd": "G:\\proj1", "prompt": "proj1 reconnect"
    }),
    # [期望] proj1 发现 slot[0] 被 proj11 占了，找不到空槽，被跳过

    # === 阶段 8：全部 stop ===
    *[
        (f"S{i}: Stop", "Stop.json", {
            "session_id": f"comp-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}"
        })
        for i in [2, 4, 5, 6, 11]  # 当前活跃的 5 个
    ],

    # === 阶段 9：多窗口审批 + 新 session + 报错 ===
    # 先让 proj7 和 proj8 进入 W 状态
    ("S7: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "comp-0007-0000-0000-0000-000000000007",
        "cwd": "G:\\proj7", "prompt": "Task 7 restart"
    }),
    ("S8: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "comp-0008-0000-0000-0000-000000000008",
        "cwd": "G:\\proj8", "prompt": "Task 8 restart"
    }),
    ("S7: PreToolUse(Bash-risky)", "PreToolUse.json", {
        "session_id": "comp-0007-0000-0000-0000-000000000007",
        "cwd": "G:\\proj7", "tool_name": "Bash",
        "tool_use_id": "toolu_C_B7", "tool_input": {"command": "rm -rf /tmp/build"}
    }),
    ("S8: PreToolUse(Bash-risky)", "PreToolUse.json", {
        "session_id": "comp-0008-0000-0000-0000-000000000008",
        "cwd": "G:\\proj8", "tool_name": "Bash",
        "tool_use_id": "toolu_C_B8", "tool_input": {"command": "git push --force"}
    }),
    # proj7 和 proj8 触发审批通知 → P
    ("S7: Notification(permission_prompt→P)", "Notification.json", {
        "session_id": "comp-0007-0000-0000-0000-000000000007",
        "notification_type": "permission_prompt"
    }),
    ("S8: Notification(permission_prompt→P)", "Notification.json", {
        "session_id": "comp-0008-0000-0000-0000-000000000008",
        "notification_type": "permission_prompt"
    }),
    # [期望] S7=P, S8=P，dominant=P
    # 新 session 在两个 P 状态期间到达
    ("S9: UserPromptSubmit(新session在P期间)", "UserPromptSubmit.json", {
        "session_id": "comp-0009-0000-0000-0000-000000000009",
        "cwd": "G:\\proj9", "prompt": "New task during approval"
    }),
    ("S9: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "comp-0009-0000-0000-0000-000000000009",
        "cwd": "G:\\proj9", "tool_name": "Bash",
        "tool_use_id": "toolu_C_B9", "tool_input": {"command": "echo hello"}
    }),
    # [期望] S7=P, S8=P, S9=W，dominant=P
    # proj9 报错
    ("S9: PostToolUseFailure(报错)", "PostToolUseFailure.json", {
        "session_id": "comp-0009-0000-0000-0000-000000000009",
        "tool_name": "Bash", "tool_use_id": "toolu_C_B9",
        "error": "Permission denied"
    }),
    # [期望] S7=P, S8=P, S9=E，dominant=E（E > P）
    # 解决审批：Stop 清零 waiting → C
    ("S7: Stop(审批完成→C)", "Stop.json", {
        "session_id": "comp-0007-0000-0000-0000-000000000007",
        "cwd": "G:\\proj7"
    }),
    ("S8: Stop(审批完成→C)", "Stop.json", {
        "session_id": "comp-0008-0000-0000-0000-000000000008",
        "cwd": "G:\\proj8"
    }),
    ("S9: StopFailure", "StopFailure.json", {
        "session_id": "comp-0009-0000-0000-0000-000000000009"
    }),
    # [期望] S7=C, S8=C, S9=E → dominant=E
]

# ── C/P 粘滞测试序列 ─────────────────────────────────────
# 验证：C 状态不被 I 立刻覆盖；P 状态不被 I 立刻覆盖；粘滞被新 W 正确解除
# 注意：本测试需要在无其他活跃 session 的环境下运行（关闭所有 Claude Code 窗口）
STICKY_STATE_SEQUENCE = [
    # === 场景 1：C 粘滞（单 session 纯净环境）===
    # W → Stop(→C 2s) → daemon 推 I → [期望 dominant 粘滞保持 C]
    ("sticky-C: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000001",
        "cwd": "G:\\sticky_c", "prompt": "sticky C test"
    }),
    ("sticky-C: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000001",
        "cwd": "G:\\sticky_c", "tool_name": "Read",
        "tool_use_id": "toolu_SC_R1", "tool_input": {"file_path": "main.py"}
    }),
    # [期望] slot[0]=W, dominant=W, logo=蓝色
    ("sticky-C: PostToolUse(Read)", "PostToolUse.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000001",
        "cwd": "G:\\sticky_c", "tool_name": "Read",
        "tool_use_id": "toolu_SC_R1", "tool_response": {"interrupted": False}
    }),
    ("sticky-C: Stop(→C)", "Stop.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000001",
        "cwd": "G:\\sticky_c"
    }),
    # [期望] slot[0]=C, dominant=C, logo=绿色（C 持续 2s）
    ("WAIT 1s: C状态观察", None, {"sleep": 1}),
    # [期望] 1s 后 logo 仍是绿色
    ("WAIT 2s: daemon推I，粘滞应保持C", None, {"sleep": 2}),
    # [期望] daemon 推 I 后，dominant 粘滞保持 C，logo 仍是绿色（不变灰）
    ("WAIT 2s: 粘滞C持续观察", None, {"sleep": 2}),
    # [期望] logo 仍是绿色
    ("WAIT 8s: 等待session从wire消失", None, {"sleep": 8}),
    # [期望] slot[0] 释放，dominant=I（无粘滞），logo=灰色

    # === 场景 2：C 粘滞被新 W 解除 ===
    ("sticky-C-release: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000002",
        "cwd": "G:\\sticky_c2", "prompt": "task 1"
    }),
    ("sticky-C-release: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000002",
        "cwd": "G:\\sticky_c2", "tool_name": "Bash",
        "tool_use_id": "toolu_SC_B2", "tool_input": {"command": "echo task1"}
    }),
    ("sticky-C-release: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000002",
        "cwd": "G:\\sticky_c2", "tool_name": "Bash",
        "tool_use_id": "toolu_SC_B2", "tool_response": {"interrupted": False}
    }),
    ("sticky-C-release: Stop(→C)", "Stop.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000002",
        "cwd": "G:\\sticky_c2"
    }),
    # [期望] dominant=C, logo=绿色
    ("WAIT 3s: C粘滞生效", None, {"sleep": 3}),
    # [期望] logo 仍是绿色（粘滞中）
    # 新任务到来，应解除粘滞
    ("sticky-C-release: 新任务到来", "UserPromptSubmit.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000003",
        "cwd": "G:\\sticky_c3", "prompt": "new task releases sticky"
    }),
    ("sticky-C-release: PreToolUse(Read)", "PreToolUse.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000003",
        "cwd": "G:\\sticky_c3", "tool_name": "Read",
        "tool_use_id": "toolu_SC_R3", "tool_input": {"file_path": "test.py"}
    }),
    # [期望] dominant=W（粘滞被 W 解除），logo=蓝色
    ("sticky-C-release: PostToolUse", "PostToolUse.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000003",
        "cwd": "G:\\sticky_c3", "tool_name": "Read",
        "tool_use_id": "toolu_SC_R3", "tool_response": {"interrupted": False}
    }),
    ("sticky-C-release: Stop", "Stop.json", {
        "session_id": "sticky-c-0000-0000-0000-000000000003",
        "cwd": "G:\\sticky_c3"
    }),
    ("WAIT 12s: 清场", None, {"sleep": 12}),

    # === 场景 3：P 粘滞 ===
    ("sticky-P: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "sticky-p-0000-0000-0000-000000000004",
        "cwd": "G:\\sticky_p", "prompt": "sticky P test"
    }),
    ("sticky-P: PreToolUse(Bash-risky)", "PreToolUse.json", {
        "session_id": "sticky-p-0000-0000-0000-000000000004",
        "cwd": "G:\\sticky_p", "tool_name": "Bash",
        "tool_use_id": "toolu_SP_B4", "tool_input": {"command": "rm -rf /important"}
    }),
    ("sticky-P: Notification(permission_prompt→P)", "Notification.json", {
        "session_id": "sticky-p-0000-0000-0000-000000000004",
        "notification_type": "permission_prompt"
    }),
    # [期望] dominant=P, logo=紫色
    ("WAIT 2s: P状态观察", None, {"sleep": 2}),
    # [期望] logo 仍是紫色
    # Stop 清零 waiting → C
    ("sticky-P: Stop(审批完成→C)", "Stop.json", {
        "session_id": "sticky-p-0000-0000-0000-000000000004",
        "cwd": "G:\\sticky_p"
    }),
    # [期望] dominant=C, logo=绿色
    ("WAIT 3s: daemon推I，粘滞应保持C", None, {"sleep": 3}),
    # [期望] daemon 推 I 后，dominant 粘滞保持 C，logo 仍是绿色
    ("WAIT 2s: 粘滞C持续观察", None, {"sleep": 2}),
    # [期望] logo 仍是绿色
]

# ── v6 满槽淘汰序列 ───────────────────────────────────────
# 验证 5 槽全满时第 6 个 session 触发 slot 0 淘汰
V6_SLOT_OVERFLOW_SEQUENCE = [
    # 依次填满 5 个槽（SID 末尾 8 位各不相同：0000000i）
    *[
        (f"S{i}: UserPromptSubmit", "UserPromptSubmit.json", {
            "session_id": f"overflow-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}", "prompt": f"Task {i}"
        })
        for i in range(1, 6)
    ],
    *[
        (f"S{i}: PreToolUse(Read)", "PreToolUse.json", {
            "session_id": f"overflow-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}", "tool_name": "Read",
            "tool_use_id": f"toolu_OF_R{i}", "tool_input": {"file_path": "main.py"}
        })
        for i in range(1, 6)
    ],
    # [期望] 5 个槽全满：slot[0~4] 各有一个 session
    # 第 6 个 session 来了 → 触发淘汰 slot 0（proj1 被挤走）
    ("S6: UserPromptSubmit", "UserPromptSubmit.json", {
        "session_id": "overflow-0006-0000-0000-0000-000000000006",
        "cwd": "G:\\proj6", "prompt": "Task 6 triggers eviction"
    }),
    ("S6: PreToolUse(Bash)", "PreToolUse.json", {
        "session_id": "overflow-0006-0000-0000-0000-000000000006",
        "cwd": "G:\\proj6", "tool_name": "Bash",
        "tool_use_id": "toolu_OF_B6", "tool_input": {"command": "echo overflow"}
    }),
    # [期望] log: "all slots full, evicting slot_id=000000000001 from slot[0]"
    # slot[0] 被 proj6 占据，proj1 历史清空
    *[
        (f"S{i}: PostToolUse(Read)", "PostToolUse.json", {
            "session_id": f"overflow-{i:04d}-0000-0000-0000-{i:012d}",
            "cwd": f"G:\\proj{i}", "tool_name": "Read",
            "tool_use_id": f"toolu_OF_R{i}", "tool_response": {"interrupted": False}
        })
        for i in range(1, 6)
    ],
    ("S6: PostToolUse(Bash)", "PostToolUse.json", {
        "session_id": "overflow-0006-0000-0000-0000-000000000006",
        "cwd": "G:\\proj6", "tool_name": "Bash",
        "tool_use_id": "toolu_OF_B6", "tool_response": {"interrupted": False}
    }),
    *[(f"S{i}: Stop", "Stop.json", {
        "session_id": f"overflow-{i:04d}-0000-0000-0000-{i:012d}", "cwd": f"G:\\proj{i}"
    }) for i in range(1, 6)],
    ("S6: Stop", "Stop.json", {
        "session_id": "overflow-0006-0000-0000-0000-000000000006", "cwd": "G:\\proj6"
    }),
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
    (LONG_MESSAGE_SEQUENCE,            "长消息显示测试"),
    (APPROVAL_SEQUENCE,                "审批通知测试"),
    (CLOCK_SEQUENCE,                   "闹钟版语音测试"),
    (GUI_FACE_TRANSITIONS_SEQUENCE,    "GUI 脸部状态转换测试"),
    (GUI_5SESSIONS_SEQUENCE,           "GUI 五 Session 并发测试"),
    (GUI_PRIORITY_SEQUENCE,            "GUI 消息块优先级测试"),
    (GUI_PENDING_PRIORITY_SEQUENCE,    "GUI Pending 优先级测试"),
    (GUI_PENDING_ERROR_SEQUENCE,       "GUI Pending→Error 转换测试"),
    (GUI_FAST_TOOL_SEQUENCE,           "GUI 快速工具兜底测试"),
    (GUI_C_NEW_TURN_SEQUENCE,          "GUI C 状态新轮次测试"),
    (GUI_SESSION_RETIRE_SEQUENCE,      "GUI Session 退休测试"),
    (GUI_DISPLAY_NAME_CONFLICT_SEQUENCE, "GUI 显示名冲突测试"),
    (V6_SLOT_STABILITY_SEQUENCE,         "v6 槽位稳定性测试"),
    (V6_SAME_CWD_MULTI_SEQUENCE,         "v6 同cwd多窗口测试"),
    (V6_SLOT_OVERFLOW_SEQUENCE,          "v6 满槽淘汰测试"),
    (V6_COMPREHENSIVE_SEQUENCE,          "v6 15 session 综合压力测试"),
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
    # 长消息测试使用更长间隔，便于观察跑马灯滚动
    default_interval = 2.5 if "长消息" in test_name else (7.0 if "闹钟" in test_name else (1.0 if "粘滞" in test_name else 0.5))
    for item in sequence:
        label, filename, patch = item[0], item[1], item[2]
        interval = item[3] if len(item) > 3 else default_interval
        print(f"\n[{label}]")
        # sleep 事件：filename=None，patch 含 sleep 字段
        if filename is None:
            secs = patch.get("sleep", 0) if patch else 0
            if secs:
                print(f"  sleeping {secs}s...")
                time.sleep(secs)
            continue
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
        time.sleep(interval)

    print(f"\n{'='*60}")
    print(f"[sim] {test_name} 完成，共 {len(sequence)} 个事件")
    if slow_count:
        print(f"[sim] ⚠️  {slow_count} 个响应超过 1 秒")
    else:
        print(f"[sim] OK 所有响应时间正常（< 1s）")

    if not no_cooldown:
        cooldown = 13 if "粘滞" in test_name else 11
        print(f"[sim] 等待 session 清除（{cooldown}s）...")
        time.sleep(cooldown)
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
    parser.add_argument("--long-message", action="store_true", help="长消息显示测试（60 字符）")
    parser.add_argument("--clock", action="store_true",
                        help="闹钟版语音测试（TTS 触发时序、busy 丢弃、3 session 并发）")
    parser.add_argument("--approval", action="store_true", help="审批通知测试（PENDING 状态）")
    parser.add_argument("--gui-pending-priority", action="store_true", help="GUI Pending 优先级测试（P > W）")
    parser.add_argument("--gui-pending-then-error", action="store_true", help="GUI Pending→Error 转换测试")
    parser.add_argument("--gui-fast-tool", action="store_true", help="GUI 快速工具兜底测试（< 0.4s）")
    parser.add_argument("--gui-c-then-new-turn", action="store_true", help="GUI C 状态新轮次测试")
    parser.add_argument("--gui-session-retire", action="store_true", help="GUI Session 退休测试（同 cwd）")
    parser.add_argument("--gui-display-name-conflict", action="store_true", help="GUI 显示名冲突测试")
    parser.add_argument("--v6-slot-stability", action="store_true", help="v6 槽位稳定性测试（沉默重连不漂移）")
    parser.add_argument("--v6-same-cwd-multi", action="store_true", help="v6 同cwd多窗口测试（不同SID各占独立槽）")
    parser.add_argument("--v6-slot-overflow", action="store_true", help="v6 满槽淘汰测试（第6个session触发淘汰）")
    parser.add_argument("--v6-comprehensive", action="store_true", help="v6 15 session 综合压力测试")
    parser.add_argument("--sticky-state", action="store_true", help="C/P 粘滞状态测试")
    parser.add_argument("--all", action="store_true", help="运行全部序列（约 6 分钟）")
    parser.add_argument("--no-cooldown", action="store_true",
                        help="跳过序列结束后的 session 清除等待")
    args = parser.parse_args()

    # ── 1. 启动 daemon ────────────────────────────────────
    _sim_log_dir = os.path.join(ROOT, "scripts", "sim_device", "logs")
    os.makedirs(_sim_log_dir, exist_ok=True)
    LOG_PATH = os.path.join(_sim_log_dir, "daemon.log")

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
    elif args.long_message:
        sequence, test_name = LONG_MESSAGE_SEQUENCE, "长消息显示测试"
    elif args.clock:
        sequence, test_name = CLOCK_SEQUENCE, "闹钟版语音测试"
    elif args.approval:
        sequence, test_name = APPROVAL_SEQUENCE, "审批通知测试"
    elif args.gui_pending_priority:
        sequence, test_name = GUI_PENDING_PRIORITY_SEQUENCE, "GUI Pending 优先级测试"
    elif args.gui_pending_then_error:
        sequence, test_name = GUI_PENDING_ERROR_SEQUENCE, "GUI Pending→Error 转换测试"
    elif args.gui_fast_tool:
        sequence, test_name = GUI_FAST_TOOL_SEQUENCE, "GUI 快速工具兜底测试"
    elif args.gui_c_then_new_turn:
        sequence, test_name = GUI_C_NEW_TURN_SEQUENCE, "GUI C 状态新轮次测试"
    elif args.gui_session_retire:
        sequence, test_name = GUI_SESSION_RETIRE_SEQUENCE, "GUI Session 退休测试"
    elif args.gui_display_name_conflict:
        sequence, test_name = GUI_DISPLAY_NAME_CONFLICT_SEQUENCE, "GUI 显示名冲突测试"
    elif args.v6_slot_stability:
        sequence, test_name = V6_SLOT_STABILITY_SEQUENCE, "v6 槽位稳定性测试"
    elif args.v6_same_cwd_multi:
        sequence, test_name = V6_SAME_CWD_MULTI_SEQUENCE, "v6 同cwd多窗口测试"
    elif args.v6_slot_overflow:
        sequence, test_name = V6_SLOT_OVERFLOW_SEQUENCE, "v6 满槽淘汰测试"
    elif args.v6_comprehensive:
        sequence, test_name = V6_COMPREHENSIVE_SEQUENCE, "v6 15 session 综合压力测试"
    elif args.sticky_state:
        sequence, test_name = STICKY_STATE_SEQUENCE, "C/P 粘滞状态测试"
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
