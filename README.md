# MicroPython Claude Assistant

将 Claude Code 的工具执行状态实时可视化为 ESP32 桌宠动画（v5 纯展示模式）。

---

## 项目结构

```
MicroPython_Claude_Assistant/
├── daemon/          # PC 守护进程层
│   ├── ble_daemon.py    # TCP↔BLE 桥接，状态机，推送到设备
│   ├── hook_bridge.py   # Claude Code Hook 接收，规范化为 v2 envelope
│   ├── transport.py     # BLE 连接管理（bleak）
│   └── risk_config.py   # 风险分级配置（v5 已废弃，保留供参考）
├── device/          # ESP32 固件层
│   ├── main.py              # 主程序入口（BLE + LVGL 渲染）
│   ├── display_renderer.py  # LVGL 屏幕渲染器（主界面 + Sessions + Config）
│   ├── character.py         # 角色形象接口（可替换）
│   ├── logo_data.py         # 像素风 Claude Logo 数据
│   ├── transport.py         # BLE NUS 驱动，20B MTU 分包
│   ├── protocol.py          # PC↔ESP32 消息协议解析
│   └── config.py            # 硬件引脚与全局常量
├── scripts/         # 调试工具
│   ├── sim_hooks_v5.py  # v5 手动集成测试（支持 --stub 无设备测试）
│   ├── flash_device.py  # ESP32 固件烧录脚本
│   └── logo_converter.py # Logo 图片转换工具
├── tests/           # 单元测试
│   ├── test_protocol.py           # protocol 单元测试（21 用例）
│   ├── test_daemon_state.py       # daemon 状态机时序测试（18 用例）
│   ├── test_hook_normalize.py     # hook_bridge 规范化测试
│   ├── test_e2e_stub.py           # E2E 联动测试（无需设备，--stub 模式）
│   ├── test_daemon_concurrency.py # daemon 并发压测
│   └── fixtures/probe_samples/    # 8 类真实 Hook payload 样本
└── research/        # 设计文档
    ├── protocol_v5_display_only.md
    └── v5_implementation_summary.md
```

---

## 架构与数据流

```
Claude Code
  → Hook 事件（PreToolUse / PostToolUse 等）
  → hook_bridge.py（stdin）
  → v2 envelope（TCP 57320）
  → ble_daemon.py（状态机）
  → v5 wire（BLE NUS，20B 分包，1-5 chunks）
  → ESP32（解析 → 状态更新 → 动画渲染）

v5 变化：
  - 删除设备审批（审批在终端完成）
  - 删除心跳机制（单向推送）
  - 消息长度扩展到 60 字符（跑马灯滚动）
```

---

## 设备离线时的分层审批策略（v4 已废弃）

> **注意**: v5 版本已删除设备审批功能，所有审批在终端完成。以下内容仅供历史参考。

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

### 当前版本（v5，纯展示模式）

#### 状态推送

```json
{
  "ss": [
    {
      "n": "MyProject",
      "s": "W",
      "m": "Bash: git log --oneline --graph --all --decorate --abbrev"
    }
  ]
}
```

#### 字段说明

| 字段 | 类型 | 含义 | 长度限制 |
|------|------|------|---------|
| `ss` | array | Sessions 数组，包含所有活跃 session | - |
| `n` | str | Session 显示名称（项目名） | ≤12 字符 |
| `s` | str | 状态枚举：I/W/E/C/P | 1 字符 |
| `m` | str | 消息文本（工具 + 描述） | ≤60 字符 |

#### 状态枚举

| 值 | 含义 | 设备动画 |
|----|------|---------|
| `I` | Idle — 空闲 | 呼吸动画 |
| `W` | Working — 执行中 | 忙碌动画 |
| `P` | Pending — 等待审批 | 闪烁提示（终端审批） |
| `C` | Completed — 完成 | 庆祝动画（持续 2s） |
| `E` | Error — 出错 | 错误动画（持续 3s） |

#### BLE 传输

- **MTU**: 20 字节/chunk（BLE NUS 标准）
- **常规消息**: 1-3 chunks（≤60 字节）
- **长消息**: 4-5 chunks（≤100 字节）
- **推送频率**: 5Hz（200ms 间隔）

#### 设备显示

- **主界面消息块**: 60 字符，跑马灯滚动（LVGL SCROLL_CIRCULAR）
- **Sessions 历史记录**: 自动换行，完整显示
- **Session 选项卡**: 显示项目名或工具名（6 字符截断）

`sessions` 数组只含活跃 session（有工具运行，或近 10s 内有活动）。多个 Claude Code 实例并发时每个 session 独立出现在数组中。

**关于 v5 变化**：
- 删除设备端触摸审批（审批在终端完成）
- 删除心跳机制（单向推送）
- 消息长度从 15 字符扩展到 60 字符
- Session 显示名称基于项目目录名（自动去重）

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
- v5 变化：`needs_approval` 字段保留但不再阻塞，hook_bridge 始终立即返回 `{}`
- 审批由 Claude Code 在终端完成，设备仅显示 PENDING 状态提醒
- `APPROVAL_TOOLS = {Bash, Write, Edit}`（仅用于设备端状态显示）

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

# daemon 状态机时序测试（18 组，无需设备）
python tests/test_daemon_state.py

# E2E 联动测试：8 种 hook → daemon --stub → protocol（无需设备）
python tests/test_e2e_stub.py

# daemon 并发压测（3 组，会启动真实 stub daemon）
python tests/test_daemon_concurrency.py

# v5 手动集成测试：模拟完整 turn，需要 ESP32 或加 --stub
python scripts/sim_hooks_v5.py --stub          # 无设备
python scripts/sim_hooks_v5.py                 # 真设备（需 ESP32 已开机）
python scripts/sim_hooks_v5.py --long-message  # 长消息测试（60 字符）
python scripts/sim_hooks_v5.py --all           # 全部测试序列（约 6 分钟）
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
| v5.0 | 2026-05-06 | 删除设备审批，改为纯展示模式；删除心跳机制；wire 简化为 ss 数组（n/s/m 三字段）；daemon 代码减少 32%；新增 sim_hooks_v5.py 集成测试；test_daemon_state 扩充至 18 用例 |
| v5.1 | 2026-05-12 | 消息长度从 15 字符扩展到 60 字符；主界面消息块添加跑马灯滚动（LVGL SCROLL_CIRCULAR）；Sessions 历史记录自动换行；新增长消息测试序列（LONG_MESSAGE_SEQUENCE，9 个工具 / 17 个事件）；BLE 传输从 3 chunks 增加到 5 chunks（长消息） |
