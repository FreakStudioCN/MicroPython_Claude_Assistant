# Claude Buddy / Claude Code real-test findings, 2026-05-19

这份文档记录 2026-05-18 到 2026-05-19 这轮真实 Claude Code + 真实 hook + 生产 daemon 日志验证的结论。目标是进入生产测试前，分清哪些行为已经证实正常，哪些只是上游/用户交互导致的现象，哪些仍然需要补测。

## 测试对象

- daemon: `daemon/ble_daemon.py`
- hook bridge: `daemon/hook_bridge.py`
- hooks: `hooks/hooks.json`
- 生产 daemon 端口: `127.0.0.1:57320`
- 日志: `%TEMP%\ble_daemon.log`
- Claude Code 执行提示词: `research/claudecode_real_manual_scenario_prompt_v1.md`

## 本轮已验证的真实行为

### 1. 普通完成不会被 idle_prompt 拉成 P

真实日志里出现过：

```text
[req v2] ... kind='stop'
[req v2] ... kind='notification'
[idle] ... idle_prompt ignored
```

结论：普通 Claude Code 完成后的 `Notification(idle_prompt)` 不再被当成审批/选择题。daemon 会忽略它，避免“Claude 已经完成但硬件显示 P”。

### 2. 选择题会通过 AskUserQuestion 进入 P

真实链路反复出现：

```text
kind='tool_start'
wire: W, m='AskUserQuestion'
kind='notification'
[approval] ... permission_prompt, waiting=1
wire: P
```

结论：选择题/交互题当前通过 `AskUserQuestion` + `Notification(permission_prompt)` 表示等待用户。硬件显示 `P` 是正确预期。

### 3. 用户回答后 P 能清掉

旧 session `690609a5-0efb-4dda-bc75-5a64ad1029d3` 中多次出现：

```text
permission_prompt, waiting=1
...
kind='tool_done'
```

后续 wire 从 `P` 回到 `W` 或完成流程。结论：至少在正常回答路径里，daemon 能收到 `tool_done` 并清掉 `waiting`。

### 4. 重启后出现“同样题目”不等于旧 P 残留

本轮用户中途重启 Claude Code。日志显示：

```text
old session 690609... kind='session_end'
new session 362c9b4b... kind='user_prompt'
new session 362c9b4b... tool_start AskUserQuestion
new session 362c9b4b... permission_prompt, waiting=1
```

结论：重启后 Claude Code 按同一份 md 又执行到同样的选择题，会产生新的 `AskUserQuestion` 和新的 `P`。这不是 daemon 没清旧题，而是新的真实等待。

### 5. 工具错误 E 会过期

真实日志里 `Read` 不存在文件后出现 `tool_error -> E`，随后回到 `W`。单测也覆盖 `dizzy_until` 过期 dirty 标记。

结论：当前 `E` 不再永久卡住。

### 6. long-think 后 stop 不丢 C

单测覆盖 `tool_done` 后长时间纯思考，再收到 `stop` 时仍推 `C`，并最终回 `I`。

结论：之前“Claude 其实完了但状态不对”的一个核心路径已经修复。

## 本轮没有证明为 bug 的现象

### 当前 P

最新 session `362c9b4b-6de7-4b4d-a128-43fd2fb798d3` 最后停在：

```text
6730 kind='tool_start'
6731-6763 wire W, m='AskUserQuestion'
6764 kind='notification'
6765 permission_prompt, waiting=1
6766+ wire P
```

没有后续 `tool_done` / `stop` / `session_end`。用户确认当时 Claude Code 确实停在选择题。

结论：这个 `P` 合理。它不是“已经回答仍不清”。

## 仍需生产前补测的风险

### R1. 选择题期间直接关闭 Claude Code

如果 Claude Code 在 `AskUserQuestion` 运行中被关闭，可能没有 `tool_done`。当前 stale 清理只 retire `waiting > 0` 且没有 running tools、没有 active turn 的旧 session。若旧 session 仍保留 running `AskUserQuestion` tool，则可能不会被 retire。

需要真实测试：`P` 时直接关闭 Claude Code，然后同 cwd 重新打开并发新 prompt。

### R2. 多 Claude Code 同 cwd 并发

同一个目录开两个 Claude Code，一个在 `P`，另一个开始新 prompt。这个场景下旧 `P` 如果真是活跃等待，不应被误删；但如果旧进程已死，就不能长期残留。

需要真实测试：同 cwd 两终端，一个选择题等待，另一个跑无工具/读文件。

### R3. 多 Claude Code 不同 cwd 并发

daemon wire 支持 `ss` 多 session 数组。需要确认硬件/daemon 在两个目录同时工作时能显示两个 session，而不是互相覆盖或名称混淆。

### R4. 同 basename 不同 cwd

`_generate_display_name()` 对同 basename 不同 cwd 会加 suffix。需要确认设备端能显示可区分名称，不发生 UI 截断导致无法区分。

### R5. daemon 重启期间 Claude Code 正在 P/W

daemon 是内存状态机。daemon 重启后不会自动知道 Claude Code 当前仍在等待用户或工作中，直到下一次 hook 事件到来。

这是生产重要限制，不一定是 bug，但要决定是否接受。

### R6. BLE 断连/重连期间状态不变

代码设计是 `_send` 失败不更新 `last_pushed_wire`，BLE 重连后状态相同也应重推。需要实机验证：硬件断电/离开再回来时，仍能收到当前 `P/W/C/I`。

### R7. 中断长命令 / StopFailure

真实用户中断和 API 错误可能走 `StopFailure`、`tool_error`、`task_error`、`session_end` 的不同组合。需要真实操作而不是模拟。

## 生产前建议 gate

进入生产测试前至少满足：

1. 单 terminal 全流程 S01-S25 无最终卡 `W/P/E`。
2. 选择题回答后每次都能看到 `tool_done` 或明确结束事件。
3. P 中关闭 Claude Code 后，同 cwd 新 prompt 不出现旧 session 永久残留。
4. 两个 Claude Code 同时运行时，wire `ss` 数组符合预期。
5. daemon 重启行为被明确记录：要么接受“重启丢当前状态”，要么后续做恢复机制。
6. BLE 断连/重连后状态能重新推送。

## 操作建议

生产前不要只看屏幕，需要同时保留三样东西：

```powershell
$log = Join-Path $env:TEMP 'ble_daemon.log'
Get-Content $log -Tail 0 -Wait
```

- Claude Code 屏幕截图或文字记录：当前是否真的停在选择题/审批。
- `%TEMP%\ble_daemon.log`：判断 hook 是否发来了 `tool_done` / `stop` / `session_end`。
- 如果使用隔离测试端口，再保留 `.context/*.jsonl` wire capture。

判断规则：

- UI 正在等用户，daemon 显示 `P`：正确。
- UI 已回答并继续执行，但日志没有 `tool_done`：优先怀疑 Claude Code hook 没发完成事件。
- UI 已结束，日志有 `stop/session_end`，daemon 仍 `P/W/E`：daemon bug。
- daemon 重启后状态丢失：当前架构预期限制，需要产品层决定是否接受。
