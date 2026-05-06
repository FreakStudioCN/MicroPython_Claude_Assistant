# Hook → 下位机协议映射 v1

**日期**: 2026-04-27
**目的**: 决定哪些 hook 事件 / 字段值得传给 BLE 设备，怎么传
**前置**: 现状只用了 `PreToolUse` / `PostToolUse` / `Stop` 三个事件，从 `tool_input` 只取了 `command` / `path`

> **实施状态（2026-05-03）**：本文档的 v2 协议草案已落地。wire 格式已升级为 9 字段（running/waiting/completed/msg/tokens/prompt/category/error/interrupted），ble_daemon 状态机已重构为基于 `_tools` 字典的 v2 实现。"落地优先序"三步已全部完成。详见 README.md v2.0 变更记录。
>
> **实施状态（2026-05-04）**：wire 进一步升级为 v3 sessions 数组格式（`{"v":2,"sessions":[{id,running,waiting,...}]}`）。ble_daemon 全局状态机拆分为 per-session `_Session` 对象，支持多 Claude Code 实例并发，修复 approval 竞争问题。protocol.py 新增 `SessionStatus` / `MultiSessionMsg`，`parse()` 向后兼容旧 v2 格式。详见 README.md v3.0 变更记录。

---

## TL;DR：v2 协议建议加 4 类信号

| 优先级 | 新增信号 | 来源 hook | 设备语义 |
|--------|--------|-----------|--------|
| P0 | **idle 闲置信号** | Notification (idle_prompt) | 进入氛围/呼吸动画，桌宠睡眠 |
| P0 | **task complete 仪式感** | Stop | 庆祝动画 + 输出摘要短句 |
| P1 | **session 启停** | SessionStart / SessionEnd | 上电/落幕仪式动画 |
| P1 | **token 用量** | PostToolUse (tool_response) | 副屏数字 / 进度条 |
| P2 | **tool 类型预告** | PreToolUse 全工具扩展 | 不同工具不同图标（编辑/搜索/网页） |

---

## 现状盘点

`hook_bridge.py` 现在传给 daemon 的：
- `pre`: 工具名 + 一行 hint
- `post`: 工具名 + 是否成功（bool）
- `stop`: 仅信号

`daemon._send` 转成 BLE 的字段：
- `running` / `waiting` / `completed` / `msg` / `tokens`（保留位）/ `prompt`

→ 信息密度只用了 hook 事件能力的 30%。

---

## Hook 事件全集（subagent 报告，分置信度）

### 高置信（标准事件，常见文档反复提及）

| 事件 | 触发时机 | 当前用了？ |
|------|--------|----------|
| PreToolUse | 工具执行前 | ✓ |
| PostToolUse | 工具执行后 | ✓ |
| Stop | Claude 完成回复 | ✓（仅信号） |
| UserPromptSubmit | 用户提交 prompt | ✗ |
| Notification | 通知发送时（含 idle / 等待审批） | ✗ |
| SessionStart | session 启动（startup/resume/clear/compact） | ✗ |
| SessionEnd | session 结束 | ✗ |
| PreCompact | 上下文压缩前 | ✗ |
| SubagentStop | Subagent 完成 | ✗ |

### 中置信（subagent 报告，需查官方文档确认）

PostToolUseFailure / TaskCreated / TaskCompleted / WorktreeCreate / WorktreeRemove / FileChanged / CwdChanged / ConfigChange / InstructionsLoaded / UserPromptExpansion / PermissionRequest / PermissionDenied / Elicitation / ElicitationResult / TeammateIdle / PostCompact / StopFailure / SubagentStart / PostToolBatch

⚠️ **使用前必须官方文档复核**。subagent 一次列了 26 个，怀疑有混入未发布或废弃事件。

---

## 通用字段（每个 hook 都能拿到）

```json
{
  "session_id": "abc123",
  "cwd": "/path/to/project",
  "hook_event_name": "...",
  "transcript_path": "/path/to/transcript.jsonl",
  "permission_mode": "auto|default|acceptEdits|dontAsk|bypassPermissions"
}
```

**对设备有用的**：
- `cwd` → 基于项目名切换"主题色"或显示项目缩写
- `permission_mode` → 显示当前 Claude 是不是放飞自我（bypassPermissions 时设备亮红警告灯）

---

## 各事件 stdin 字段（subagent 报告）

### PreToolUse / PostToolUse

```json
{
  "tool_name": "Bash|Write|Edit|Read|Glob|Grep|WebFetch|WebSearch|...",
  "tool_input": { ... },          // PreToolUse
  "tool_response": { ... }        // PostToolUse（含 exit_code、output、token usage 等）
}
```

**Bash tool_input**: `command` / `description` / `timeout` / `run_in_background`
**Edit tool_input**: `file_path` / `old_string` / `new_string` / `replace_all`
**Write tool_input**: `file_path` / `content`
**Read tool_input**: `file_path` / `limit` / `offset`
**Grep tool_input**: `pattern` / `glob` / `type` / `output_mode`
**WebFetch tool_input**: `url` / `prompt`

### Notification

```json
{
  "notification_type": "permission_prompt|idle_prompt|...",
  "notification_content": "..."
}
```

**关键**：`idle_prompt` 是"用户走神 / Claude 等了好久"的信号。设备用这个进氛围动画。

### Stop

```json
{
  "response": "Claude 最后输出的文本",
  "turn_number": 42
}
```

**关键**：可以截前 20 字显示在设备屏幕，给用户"看一眼就知道刚干了啥"的反馈。

### SessionStart

```json
{
  "source": "startup|resume|clear|compact",
  "model": "claude-sonnet-4-6"
}
```

**关键**：source 区分能做不同动画（新开 vs 续上 vs 清空）。

### SessionEnd

```json
{
  "session_end_reason": "clear|resume|logout|prompt_input_exit|bypass_permissions_disabled|other"
}
```

---

## v2 协议草案（PC → 设备）

在现有 `{running, waiting, completed, msg, tokens, prompt}` 基础上扩展：

```json
{
  "type": "state|event|cmd",         // 三类消息分通道
  "v": 2,                             // 协议版本

  // 状态类（持续性，后到覆盖前到）
  "state": {
    "running": 0,
    "waiting": 0,
    "idle": false,                   // 新：来自 Notification idle_prompt
    "session_active": true,          // 新：来自 SessionStart/End
    "permission_mode": "default",    // 新：bypassPermissions 时设备警示
    "project": "claudehardware",     // 新：cwd 末段
    "tokens_used": 12345             // 新：累计
  },

  // 事件类（瞬时，触发一次动画）
  "event": {
    "kind": "tool_start|tool_done|task_complete|session_start|session_end|notify",
    "tool": "Bash",                  // 可选
    "tool_category": "exec|edit|read|web",  // 新：粗分类，设备只认大类
    "summary": "ran tests, passed",  // 短句（≤40 字）
    "success": true
  },

  // 命令类（保留：name/owner/unpair + prompt 审批）
  "cmd": { ... }
}
```

### 分类的好处

设备端原来在 if/else 里写死"Bash 是绿灯、Edit 是蓝灯"。改成 `tool_category` 后：
- `exec` → Bash / 命令执行类
- `edit` → Write / Edit / NotebookEdit
- `read` → Read / Glob / Grep
- `web` → WebFetch / WebSearch
- `agent` → Task / Subagent
- `other` → 兜底

设备只看 5 个分类，新增工具不用改固件。

---

## "怎么传"的工程问题

### 1. BLE MTU 限制
- 现在 20B/chunk，长 payload 慢且容易粘包
- v2 协议字段更多 → payload 变长 → **要么压字段名（`r/w/c/m/t`），要么协商更大 MTU（BLE 5 支持 247B）**

### 2. 节流（throttle）
- Claude 一秒能发 N 次 PostToolUse → daemon 必须做节流，否则 BLE 队列堵
- 建议：**状态类合并发送（最多 5Hz）**，事件类不合并（每个都发）

### 3. 哪些不该传
- `tool_input.content`（Write 全文件内容）→ 太长，只传文件名
- `tool_response.output` 全文 → 同上，截 40 字
- `transcript_path` 路径 → 设备用不上

### 4. 反向通道扩展
现在设备只能回 `permission`。可加：
- `pause` / `resume` → 通过 hook exit code 2 阻止下一个工具
- `mute` → 临时关掉一类事件推送（用户睡觉/会议）

---

## 落地优先序

1. **先验证 hook 事件实际可用性**：写一个 `hook_probe.py`，挂上所有可疑事件名，dump 到日志，跑一个真实 session 看哪些真的触发
2. **再决定 v2 协议字段**：基于实际能拿到的数据，不基于 subagent 转述
3. **最后改 daemon + 设备固件**

第 1 步是阻塞的。**写代码前先做这个**。

---

## 来源

同 install_mechanism_v1.md，subagent 转述 docs.claude.com / code.claude.com 文档。所有事件 / 字段在落地前必须自己打开官方文档复核一次。

---

---

## v4 精简 wire 协议（当前实现，2026-05-06）

### 设计动机

v3 sessions 数组每个 session 有 9 个字段（running/waiting/completed/msg/tokens/prompt/category/error/interrupted），单条 wire 约 120-200B，BLE 需要 6-10 个 20B chunk。v4 改为单字母状态枚举，把 payload 压到 20-60B，1-4 个 chunk。

### PC → 设备（状态推送）

```json
{"ss": [{"s": "W", "m": "Read: /etc/hosts"}]}
```

`ss` 数组每个元素代表一个活跃 session：

| 字段 | 类型 | 含义 | 何时出现 |
|------|------|------|---------|
| `s`  | str  | 状态枚举（见下表） | 必填 |
| `m`  | str  | 工具简短描述（≤16字） | 仅 `s="W"` |
| `t`  | str  | 工具名（Bash/Write/…） | 仅 `s="P"` |
| `h`  | str  | 审批提示文本（≤80字） | 仅 `s="P"` |

状态枚举：

| 值 | 含义 | 设备动画 |
|----|------|---------|
| `I` | Idle — 空闲 | 呼吸/待机 |
| `W` | Working — 工具执行中 | 忙碌动画 |
| `P` | Pending — 等待审批 | 闪烁提示 |
| `C` | Completed — 任务完成 | 庆祝动画 |
| `E` | Error — 出错 | 错误动画 |

典型 wire 大小对比：

| 状态 | 示例 payload | 大小 | BLE chunks |
|------|-------------|------|-----------|
| Idle | `{"ss":[{"s":"I"}]}` | 21B | 2 |
| Working | `{"ss":[{"s":"W","m":"Read: /etc/hosts"}]}` | 45B | 3 |
| Pending | `{"ss":[{"s":"P","t":"Bash","h":"rm -rf /tmp"}]}` | 62B | 4 |

### 设备 → PC（审批决策）

```json
{"d": "once", "n": 0}
```

| 字段 | 含义 |
|------|------|
| `d`  | 决策：`once`（批准一次）/ `session`（本 session 全批）/ `deny`（拒绝） |
| `n`  | ss 数组下标，标识哪个 session（当前 daemon 端未使用，预留多 session 路由） |

hook_bridge 将 daemon 返回的 `"deny"` 转换为 Claude Code 协议的 `"block"`：

```python
# hook_bridge.py
if hook == "PreToolUse" and resp.get("decision") == "deny":
    print(json.dumps({"decision": "block", "reason": "Denied by hardware buddy"}))
```

### 设备端解析（protocol.py）

```python
class SessionStatus:
    def __init__(self, d: dict):
        s = d.get("s", "I")
        self.running   = 1 if s == "W" else 0
        self.waiting   = 1 if s == "P" else 0
        self.completed = s == "C"
        self.error     = "!" if s == "E" else ""
        self.msg       = d.get("m", "")
        self.prompt    = {"tool": d.get("t",""), "hint": d.get("h",""), "id": ""} if s == "P" else None
```

### 已知限制

1. **多 session 审批路由未实现**：daemon `_on_transport_recv` 忽略 `n` 字段，决策广播给所有等待 session；设备端 `render_task` 只处理 ss[0] 的审批（`break` 后跳过其余）。单 session 场景无影响。
2. **错误文本不传输**：`s="E"` 时不携带具体错误信息，设备只知道"有错误"，不知道错误内容。设计取舍：节省 BLE 带宽。

---

## 变更记录

| 版本 | 日期 | 内容 |
|------|------|------|
| v1.0 | 2026-04-27 | 初始设计文档，v2 协议草案，落地优先序 |
| v1.1 | 2026-05-03 | 追加实施状态说明：v2 协议已落地，三步优先序已全部完成 |
| v1.2 | 2026-05-04 | 追加实施状态说明：v3 sessions 数组格式已落地，per-session 状态机已实现 |
| v1.3 | 2026-05-06 | 追加实施状态说明：v4 精简 wire 已落地，BLE chunks 从 9-16 降至 1-4 |
