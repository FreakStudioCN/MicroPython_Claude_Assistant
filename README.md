# MicroPython Claude Assistant

将 Claude Code 的工具执行状态实时可视化为 ESP32 桌宠动画，支持设备端触摸审批。

---

## 项目结构

```
MicroPython_Claude_Assistant/
├── daemon/          # PC 守护进程层
│   ├── ble_daemon.py    # TCP↔BLE 桥接，状态机，推送到设备
│   ├── hook_bridge.py   # Claude Code Hook 接收，规范化为 v2 envelope
│   └── risk_config.py   # 设备离线时的风险分级配置（可编辑）
├── device/          # ESP32 固件层
│   ├── main.py          # 调试测试用（BLE 收发 + 串口 print，无 UI）
│   ├── main_mvp.py      # 完整版（LVGL 渲染 + 触摸审批 + 状态机动画）
│   ├── ble_uart.py      # BLE NUS 驱动，20B MTU 分包
│   ├── display.py       # LVGL + SPI 屏幕 + I2C 触摸屏
│   ├── buddy.py         # 角色动画控制器
│   ├── buddies.py       # ASCII 帧数据（3 角色 × 7 状态）
│   ├── state.py         # 设备状态机（base + 短暂覆盖）
│   ├── protocol.py      # PC↔ESP32 消息协议
│   └── config.py        # 硬件引脚与全局常量
├── scripts/         # 调试工具
│   ├── sim_hooks.py     # 手动集成测试：模拟完整 turn 的 hook 触发链路
│   ├── hook_probe.py    # 一次性 Hook 采样（用完删除）
│   ├── ble_test_send.py # PC 端 BLE 手动测试
│   └── test_recv.py     # ESP32 端 BLE 收发测试
├── tests/           # 单元测试
│   ├── test_protocol.py           # protocol 单元测试（21 用例）
│   ├── test_daemon_state.py       # daemon 状态机时序测试（14 用例）
│   ├── test_offline_approval.py   # 设备离线审批策略测试（7 用例）
│   ├── test_daemon_concurrency.py # daemon 并发压测
│   ├── test_hook_normalize.py     # hook_bridge 规范化测试
│   ├── test_e2e_stub.py           # E2E 联动测试（无需设备，--stub 模式）
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

心跳（设备在线检测）：
  ble_daemon 每 10s 发 ping → ESP32 立即回 pong → 30s 无响应判定离线
```

---

## 设备离线时的分层审批策略

当设备离线（30s 无心跳响应）时，根据操作风险等级自动决策：

| 风险等级 | 操作类型 | 离线行为 |
|---------|---------|---------|
| **safe** | Read / Glob / Grep / WebFetch / WebSearch | 自动批准 |
| **normal** | 普通 Bash / Write / Edit | 自动批准 |
| **critical** | Git 破坏性操作 / rm -rf / 关键路径修改 | CLI 提示用户（未实现） |

### 风险分级规则（可自定义）

编辑 `daemon/risk_config.py` 自定义风险规则：

- **CRITICAL_PATHS**：写入这些路径视为 critical（如 `.git/config`, `.env`, `credentials.json`）
- **CRITICAL_BASH_PATTERNS**：包含这些模式的 Bash 命令视为 critical（如 `git push --force`, `rm -rf`, `dd if=`）
- **SAFE_TOOLS**：始终视为 safe 的只读工具
- **APPROVAL_TOOLS**：需要审批的工具列表

**设计理念**：设备离线不应阻塞工作流，但破坏性操作必须确认。fail-open 保证便利性，风险分级保证安全性。

---

## PC → ESP32 消息协议（wire 格式）

### 当前版本（v3，sessions 数组 + 心跳）

#### 状态推送

```json
{
  "v": 2,
  "sessions": [
    {
      "id":          "SESSION-",
      "running":     1,
      "waiting":     0,
      "completed":   false,
      "msg":         "Bash: ls -la",
      "category":    "exec",
      "error":       "",
      "interrupted": false,
      "prompt":      {"id": "toolu_xxx", "tool": "Bash", "hint": "ls -la"}
    }
  ]
}
```

#### 心跳

```json
// PC → 设备（每 10s）
{"cmd": "ping", "ts": 1234567890.123}

// 设备 → PC（立即响应）
{"ack": "pong", "ts": 1234567890.456}
```

`sessions` 数组只含活跃 session（有工具运行，或近 10s 内有活动）。多个 Claude Code 实例并发时每个 session 独立出现在数组中。

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | str | session_id 前 8 字符 |
| `running` | int | 当前正在执行的工具数，>0 设备显示 WORKING |
| `waiting` | int | 等待审批的工具数，>0 设备显示 PENDING |
| `completed` | bool | 任务刚完成，触发 CELEBRATE 动画（持续 2s） |
| `msg` | str | 屏幕底部显示文字 |
| `category` | str | 工具类别：exec/edit/read/web/agent/other/"" |
| `error` | str | 最近一次错误原文（截断 80 字），ERROR 状态下显示 |
| `interrupted` | bool | true = 用户主动 Ctrl+C，设备跳过 ERROR 直接回 IDLE |
| `prompt` | dict\|null | 非 null 时显示审批界面，含 `id/tool/hint` |

**关于 `agent_depth` 未暴露的原因**：SubagentStart hook 没有对应的 SubagentStop 被观测到触发，深度只能增不能减，数值不可靠。改为 daemon 内部维护 `has_subagent` 标志，影响 `completed` 推断阈值（有子 Agent 时从 4s 延长到 8s），不暴露到 wire。

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

> 以下 4 个问题已在 v2.0 中全部修复，保留记录供参考。

### ~~问题 1：PostToolUse 中 `interrupted` 字段被遗漏~~（已修复）

`_normalize_post_tool` 补充提取 `event.get("tool_response", {}).get("interrupted", False)`，现在"工具正常返回但实际是被中断"可正确识别。

### ~~问题 2：`_hint_from_tool_input` 回退可能暴露敏感内容~~（已修复）

无已知 key 时回退改为返回空串 `""`，不再序列化完整 dict。

### ~~问题 3：截断长度不统一~~（已修复）

所有文本字段（error_msg / message / error / last_assistant_message / prompt）统一截断为 80 字。

### 问题 4：NotebookEdit 不在 APPROVAL_TOOLS 中（设计选择）

`NotebookEdit` 归类为 `edit`，但不需要审批。原因已在 `hook_bridge.py` 中注释说明：notebook 编辑危险性低于直接文件写入，且 cell 输出可在 Claude Code UI 中直接查看，无需额外硬件确认。

---

## 运行测试

```bash
# protocol 单元测试（21 用例，无需设备）
python tests/test_protocol.py

# hook_bridge 规范化测试（7 组，无需设备）
python tests/test_hook_normalize.py

# daemon 状态机时序测试（14 组，无需设备）
python tests/test_daemon_state.py

# E2E 联动测试：8 种 hook → daemon --stub → protocol（无需设备）
python tests/test_e2e_stub.py

# daemon 并发压测（3 组，会启动真实 stub daemon）
python tests/test_daemon_concurrency.py

# 手动集成测试：模拟完整 turn，需要 ESP32 或加 --stub
python scripts/sim_hooks.py --stub     # 无设备
python scripts/sim_hooks.py            # 真设备（需 ESP32 已开机）
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

---

## 变更记录

| 版本 | 日期 | 内容 |
|------|------|------|
| v1.0 | 2026-04-27 | 初始版本：v1 wire（6字段），hook_bridge 接收层，daemon 状态机，基础测试 |
| v2.0 | 2026-05-03 | wire 升级为 v2（9字段，+category/error/interrupted）；ble_daemon 状态机重构为 _tools 字典；修复 4 个 hook_bridge 已知问题；新增 test_protocol（17用例）、test_e2e_stub（E2E联动，无需设备）；daemon 状态机测试扩充至 13 用例 |
| v3.0 | 2026-05-04 | wire 升级为 v3（sessions 数组）；ble_daemon 全局状态 → per-session _Session，修复多实例并发 approval 竞争；protocol 新增 SessionStatus / MultiSessionMsg；test_protocol 扩充至 21 用例，test_daemon_state 扩充至 14 用例；新增 sim_hooks.py 手动集成测试；Windows 进程清理修复 |
| v4.0 | 2026-05-04 | 设备断联处理：心跳机制（ping/pong 每 10s，30s 无响应判定离线）+ 分层 fail-open（safe/normal 自动批准，critical 预留 CLI 提示）；新增 risk_config.py 可编辑风险规则；hook_bridge 添加 risk_level 字段；新增 test_offline_approval（7 用例）；全部测试通过（49 用例） |
