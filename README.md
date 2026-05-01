# MicroPython Claude Assistant

将 Claude Code 的工具执行状态实时可视化为 ESP32 桌宠动画，支持设备端触摸审批。

---

## 项目结构

```
MicroPython_Claude_Assistant/
├── daemon/          # PC 守护进程层
│   ├── ble_daemon.py    # TCP↔BLE 桥接，状态机，推送到设备
│   └── hook_bridge.py   # Claude Code Hook 接收，规范化为 v2 envelope
├── device/          # ESP32 固件层
│   ├── main.py          # 主程序，3 个 asyncio 任务
│   ├── main_mvp.py      # 早期 MVP 版本
│   ├── ble_uart.py      # BLE NUS 驱动，20B MTU 分包
│   ├── display.py       # LVGL + SPI 屏幕 + I2C 触摸屏
│   ├── buddy.py         # 角色动画控制器
│   ├── buddies.py       # ASCII 帧数据（3 角色 × 7 状态）
│   ├── state.py         # 设备状态机（base + 短暂覆盖）
│   ├── protocol.py      # PC↔ESP32 消息协议
│   └── config.py        # 硬件引脚与全局常量
├── scripts/         # 调试工具
│   ├── hook_probe.py    # 一次性 Hook 采样（用完删除）
│   ├── ble_test_send.py # PC 端 BLE 手动测试
│   └── test_recv.py     # ESP32 端 BLE 收发测试
├── tests/           # 单元测试
│   ├── test_daemon_state.py       # daemon 状态机时序测试
│   ├── test_daemon_concurrency.py # daemon 并发压测
│   ├── test_hook_normalize.py     # hook_bridge 规范化测试
│   └── fixtures/probe_samples/    # 8 类真实 Hook payload 样本
└── research/        # 设计文档
    ├── hook_to_device_mapping_v1.md
    └── hook_probe_settings_template.json
```

---

## 架构与数据流

```
Claude Code
  → Hook 事件（PreToolUse / PostToolUse 等）
  → hook_bridge.py（stdin）
  → v2 envelope（TCP 57320）
  → ble_daemon.py（状态机）
  → v2 wire（BLE NUS，20B 分包）
  → ESP32（解析 → 状态更新 → 动画渲染）

反向（审批）：
  ESP32 触摸按钮 → BLE TX → ble_daemon → hook_bridge → Claude Code 执行/拒绝工具
```

---

## PC → ESP32 消息协议（wire 格式）

### 当前版本（v1，6 字段）

```json
{
  "running":   1,
  "waiting":   0,
  "completed": false,
  "msg":       "Bash: ls -la",
  "tokens":    0,
  "prompt":    {"id": "cli-req", "tool": "Bash", "hint": "ls -la"}
}
```

| 字段 | 类型 | 含义 |
|------|------|------|
| `running` | int | 当前正在执行的工具数，>0 设备显示 BUSY |
| `waiting` | int | 等待审批的工具数，>0 设备显示 ATTENTION |
| `completed` | bool | 任务刚完成，触发 CELEBRATE 动画 |
| `msg` | str | 屏幕底部显示文字 |
| `tokens` | int | 本轮 token 数（当前恒为 0，未实现） |
| `prompt` | dict\|null | 非 null 时显示审批界面 |

### 规划版本（v2，9 字段）

在 v1 基础上新增 3 个字段：

```json
{
  "running":     1,
  "waiting":     0,
  "completed":   false,
  "msg":         "Bash: ls -la",
  "tokens":      0,
  "prompt":      null,
  "category":    "exec",
  "error":       "",
  "interrupted": false
}
```

| 新增字段 | 类型 | 来源 | 含义 |
|---------|------|------|------|
| `category` | str | `tool_category` | 工具类别：exec/edit/read/web/agent/other/""，设备可据此区分显示 |
| `error` | str | `error_msg` / `error` | 最近一次错误原文（截断 80 字），DIZZY 状态下显示具体原因 |
| `interrupted` | bool | `is_interrupt` | true = 用户主动 Ctrl+C，设备跳过 DIZZY 直接回 IDLE |

**不加 `agent_depth` 的原因**：SubagentStart hook 没有对应的 SubagentStop 被观测到触发，深度只能增不能减，数值不可靠。改为 daemon 内部维护 `_has_subagent` 标志，影响 `completed` 推断阈值（有子 Agent 时从 4s 延长到 8s），不暴露到 wire。

---

## hook_bridge 发出的 8 类事件

### 1. tool_start（PreToolUse）
```json
{
  "kind": "tool_start",
  "tool": "Bash",
  "tool_category": "exec",
  "summary": "ls -la /src",
  "needs_approval": true,
  "tool_use_id": "toolu_xxx"
}
```
- 唯一有阻塞语义的 hook：`needs_approval=true` 时等 daemon 回 `once/deny`
- `APPROVAL_TOOLS = {Bash, Write, Edit}`

### 2. tool_done（PostToolUse）
```json
{
  "kind": "tool_done",
  "tool": "Bash",
  "tool_category": "exec",
  "duration_ms": 1234,
  "tool_use_id": "toolu_xxx"
}
```

### 3. tool_error（PostToolUseFailure）
```json
{
  "kind": "tool_error",
  "tool": "Bash",
  "tool_category": "exec",
  "error_msg": "command not found...",
  "is_interrupt": false,
  "duration_ms": 500,
  "tool_use_id": "toolu_xxx"
}
```

### 4. tool_batch_done（PostToolBatch）
```json
{
  "kind": "tool_batch_done",
  "batch_size": 3,
  "tools": ["Read", "Glob", "Grep"]
}
```
- 并行工具整批完成的信号，daemon 可作为 task_complete 的强信号

### 5. subagent_start（SubagentStart）
```json
{
  "kind": "subagent_start",
  "agent_id": "agent_xxx",
  "agent_type": "Explore"
}
```
- 只有 Start，无 Stop（SubagentStop 未被观测到触发）

### 6. notification（Notification）
```json
{
  "kind": "notification",
  "notification_type": "permission_prompt",
  "message": "Claude Code needs your attention"
}
```
- 实测只见过 `permission_prompt` 类型，与 `waiting` 语义重叠

### 7. user_prompt（UserPromptSubmit）
```json
{
  "kind": "user_prompt",
  "prompt": "继续"
}
```
- turn 开始的强信号，prompt 文字截断 80 字

### 8. task_error（StopFailure）
```json
{
  "kind": "task_error",
  "error": "unknown",
  "last_assistant_message": "API Error: Stream idle timeout..."
}
```
- 整个 assistant turn 崩溃（API 超时 / stream 中断等）

---

## hook_bridge 当前已知问题

### 问题 1：PostToolUse 中 `interrupted` 字段被遗漏

fixture 实测 `PostToolUse` 的 `tool_response` 中存在 `interrupted` 字段：
```json
"tool_response": {
  "interrupted": false,
  ...
}
```
但 `_normalize_post_tool` 完全没有提取它，导致"工具正常返回但实际是被中断的"这种情况无法识别。当前 `is_interrupt` 只从 `PostToolUseFailure` 中提取，覆盖不完整。

**修复方向**：`_normalize_post_tool` 中补充提取 `event.get("tool_response", {}).get("interrupted", False)`。

### 问题 2：`_hint_from_tool_input` 回退可能暴露敏感内容

当 `tool_input` 中没有任何已知 key（command/file_path/pattern/url/description）时，回退为：
```python
return str(tool_input)[:80]
```
这会把完整的 dict 序列化后截断显示，可能包含环境变量、密钥路径等敏感信息。

**修复方向**：回退改为返回空串 `""`。

### 问题 3：截断长度不统一

| 字段 | 截断长度 |
|------|---------|
| `summary`（hint） | 80 字 |
| `prompt` | 80 字 |
| `error_msg` | 200 字 |
| `message` | 200 字 |
| `error` / `last_assistant_message` | 200 字 |

设备屏幕实际只能显示约 20-30 字，200 字的截断传到 BLE 浪费带宽。建议统一为 80 字。

### 问题 4：NotebookEdit 不在 APPROVAL_TOOLS 中

`NotebookEdit` 归类为 `edit`，但不需要审批，与 `Write`/`Edit` 行为不一致。这是设计选择，但未在代码中说明原因。

---

## 运行测试

```bash
# hook_bridge 规范化测试（6 组，无需设备）
python tests/test_hook_normalize.py

# daemon 状态机时序测试（7 组，无需设备）
python tests/test_daemon_state.py

# daemon 并发压测（3 组，会启动真实 stub daemon）
python tests/test_daemon_concurrency.py
```

---

## 部署

### PC 端
```bash
pip install bleak
python daemon/ble_daemon.py          # 正常模式（需要 ESP32）
python daemon/ble_daemon.py --stub   # stub 模式（无设备测试）
```

`C:\Users\<用户>\.claude\settings.json` 中注册 hook：
```json
{
  "hooks": {
    "PreToolUse":  [{"hooks": [{"type": "command", "command": "python G:/MicroPython_Claude_Assistant/daemon/hook_bridge.py"}]}],
    "PostToolUse": [{"hooks": [{"type": "command", "command": "python G:/MicroPython_Claude_Assistant/daemon/hook_bridge.py"}]}],
    "Stop":        [{"hooks": [{"type": "command", "command": "python G:/MicroPython_Claude_Assistant/daemon/hook_bridge.py"}]}]
  }
}
```

### ESP32 端
将 `device/` 目录下所有文件烧录到 ESP32，重启后自动运行。
