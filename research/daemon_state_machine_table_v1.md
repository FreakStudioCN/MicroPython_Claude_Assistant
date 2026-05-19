# Daemon 状态机表 v1

本文档描述 `daemon/ble_daemon.py` 当前生产状态机。这里的“状态”指发送给设备的 wire 状态：

```json
{"ss": [{"n": "project", "s": "W", "m": "Read: README.md"}]}
```

注意：daemon 内部没有单独保存一个 `state` enum。设备状态由每个 `_Session` 的字段实时推导出来：

| 内部字段 | 含义 |
| --- | --- |
| `tools` | 正在运行的工具集合，非空时通常显示 `W`，并带 `m` 工具描述 |
| `waiting` | 等待用户审批/选择/输入的计数，`>0` 时显示 `P` |
| `turn_active` | 从 `UserPromptSubmit` 到 `Stop` / `SessionEnd` 之间的 assistant turn |
| `completed_until` | 完成庆祝 `C` 的截止时间 |
| `dizzy_until` | 错误/失败 `E` 的截止时间 |
| `last_stop_ts` | 最近一次 stop 时间，用来过滤 stop 后乱序 `permission_prompt` |
| `last_activity_ts` | 最近活动时间，用于清理长期 idle session |

## 设备状态定义

| 状态 | 名称 | 设备语义 | 典型来源 |
| --- | --- | --- | --- |
| `I` | Idle | 当前 session 空闲或无可显示活动 | 无工具、无等待、无完成庆祝、无错误、turn 不活跃 |
| `W` | Working | Claude Code 正在处理、思考或运行工具 | `UserPromptSubmit`、`PreToolUse`、工具完成后等待 `Stop` |
| `P` | Pending | 需要用户在 Claude Code 终端里选择/审批/回答 | `Notification(permission_prompt)`、`Notification(elicitation_dialog)`、或 daemon 收到 `needs_approval=True` 的 tool_start |
| `C` | Completed | 本轮完成后的短庆祝态 | `Stop` 或兜底 `SessionEnd` |
| `E` | Error | 工具失败或 assistant turn 失败后的短错误态 | `PostToolUseFailure`、`StopFailure` |

## 显示优先级

`_session_to_wire()` 每次推送时按以下顺序推导显示状态。前面的条件命中后直接返回。

| 优先级 | 条件 | 输出状态 | 备注 |
| --- | --- | --- | --- |
| 1 | `dizzy_until > now` | `E` | 错误态最高优先级，避免被 P/W/C 覆盖 |
| 2 | `waiting > 0` | `P` | 用户交互优先于工具和完成庆祝 |
| 3 | 任一 `tools[*].status == "running"` | `W` + `m` | 工具运行优先于旧 `C`，避免 C 遮挡真实工作 |
| 4 | `completed_until > now` | `C` | 仅当没有错误、等待、运行中工具时显示 |
| 5 | `turn_active == True` | `W` | 无工具时表示 Claude Code 正在思考/整理结果 |
| 6 | `now - last_tool_start_ts < 0.4s` | `W` | 快速工具兜底，保证至少推一次 W |
| 7 | 以上都不满足 | `I` | 空闲 |

## 事件转换表

表里的“现态”是事件到达前的设备可见状态；“次态”是事件处理后下一次 wire 推送的预期状态。由于状态由字段推导，某些行会受优先级和计时器影响。

| 现态 | 转换条件 / hook | daemon 内部动作 | 次态 | 备注 |
| --- | --- | --- | --- | --- |
| 无 session | 任意已识别 hook 带新 `session_id` | 创建 `_Session`，首次根据 `cwd` 生成 `display_name` | 取决于事件 | 同 basename 多 session 会追加 session suffix |
| `I` | `UserPromptSubmit` -> `user_prompt` | `turn_active=True`，清 `waiting/current_error/current_interrupted`，更新 activity | `W` | 新 turn 开始 |
| `C` | `UserPromptSubmit` -> `user_prompt`，且没有新工具/等待 | `turn_active=True`，不清 `completed_until` | `C`，到期后 `W` | 保留完成庆祝，避免连发 prompt 截断 C |
| `P` | `UserPromptSubmit` -> `user_prompt` | 清 `waiting`，`turn_active=True` | `W` 或 `C` | 用于用户继续输入后离开旧等待态 |
| `P` 的旧 session | 新 session 同 `cwd` 收到 `user_prompt`，旧 session 无工具且 `turn_active=False` | 删除旧 stale waiting session | 新 session 为 `W` | 修复重启 Claude Code 后旧 P 残留 |
| 任意 | `PreToolUse` -> `tool_start`，`needs_approval=False` | 写入 `tools[tool_use_id]`，`last_tool_start_ts=now`，清 `completed_until` | `W` + `m` | 真实工具活动优先于旧 C |
| 任意 | `PreToolUse` -> `tool_start`，`needs_approval=True` | 写入工具并 `waiting += 1` | `P` | v5 当前主要 P 来源不是这里，但 daemon 仍支持 |
| `W` | `PostToolUse` -> `tool_done`，仍有其他 running tools | 删除完成的 tool，更新 activity | `W` + `m` | 显示下一个仍在运行的工具 |
| `W` | `PostToolUse` -> `tool_done`，所有 tools 已空且 `turn_active=True` | 清 `waiting/current_error`，保留 `turn_active` | `W` | 工具完成后 Claude 可能仍在生成回答，不能提前 `I` |
| `P` | `PostToolUse` -> `tool_done`，审批 tool 完成 | 对应 `waiting -= 1`，删除 tool；若 tools 空则 `waiting=0` | 通常 `W` | 等待结束但 turn 还没 stop |
| 任意 | `PostToolUseFailure` -> `tool_error`，非 interrupt | 删除对应 tool，进入错误态 | `E`，到期后按剩余字段推导 | `dizzy_until = now + 3s` |
| 任意 | `PostToolUseFailure` -> `tool_error`，`is_interrupt=True` | 删除对应 tool，记录 interrupt，但不设置 dizzy hold | 通常 `W` / `I` | 用户中断不强制显示 E |
| 任意 | `Notification(permission_prompt)` | 若不在 stop 后 1s 乱序窗口：`waiting=1`，清 `completed_until` | `P` | 真实选择题/审批的主要路径 |
| 任意 | `Notification(elicitation_dialog)` | `waiting=1`，清 `completed_until` | `P` | Claude Code 主动询问用户输入 |
| 任意 | `Notification(idle_prompt)` | 只打印日志，不改状态 | 不变 | idle 不是选择题，不能让设备常驻 P |
| `W` / `P` / `I` | `Stop` -> `stop`，当前不在错误态 | 清 `tools/waiting`，`turn_active=False`，设置 `completed_until=now+2s` | `C` | 正常完成 |
| `E` | `Stop` -> `stop`，`dizzy_until > now` 或仍有 `current_error` | 清 `tools/waiting/turn_active`，不设置 C | `E` 到期后 `I` | 错误结束不庆祝 |
| 任意 | `SessionEnd` -> `session_end`，距离最近 `Stop < 10s` | 忽略 | 不变 | 防止 Stop 后重复庆祝 |
| `W` / `P` / `I` | `SessionEnd` -> `session_end`，没有近期 Stop 且不在错误态 | 清 `tools/waiting/turn_active`，设置 `completed_until=now+2s` | `C` | Stop 缺失时的完成兜底 |
| 任意 | `StopFailure` -> `task_error` | `turn_active=False`，`waiting=0`，清 tools，进入错误态 | `E` | assistant turn 失败 |
| 任意 | `PostToolBatch` -> `tool_batch_done` | 只更新 activity 并 mark dirty | 按当前字段推导 | 批量工具完成信号本身不改变状态 |
| 任意 | `SubagentStart` -> `subagent_start` | `has_subagent=True` | 不变 | 当前只记录，不影响 wire |
| 任意 | 未识别 hook -> `unknown` | 忽略 | 不变 | fail-open |

## 计时器转换表

这些转换不是由 hook 直接触发，而是在 `_pusher_tick()` 中根据时间推进。

| 现态 | 转换条件 | daemon 内部动作 | 次态 | 备注 |
| --- | --- | --- | --- | --- |
| `C` | `completed_until <= now` | mark dirty，等待下一次推送 | 按字段推导，通常 `I` 或 `W` | 如果 `turn_active=True`，C 到期后会显示 W |
| `E` | `dizzy_until <= now` | 清 `dizzy_until/current_error/current_interrupted`，mark dirty | 按字段推导，通常 `I` | 错误态不能永久卡住 |
| `I` / 已完成旧 session | `not tools`、`not turn_active`、`completed_until/dizzy_until` 均到期，且 `now-last_activity_ts > 10s` | 删除 session | 从 `ss` 数组消失 | 清理长期无活动 session |
| 任意 | BLE 断连导致 `_send()` 返回 false | 不更新 `last_pushed_wire` | 设备未收到新状态 | 重连后同一 wire 也会重推，避免 dedup 吞掉 |

## 关键不变量

| 不变量 | 原因 |
| --- | --- |
| `E` 必须能自动退出 | `dizzy_until` 到期后清错误字段，避免生产卡 E |
| `P` 只能来自真实用户交互 | `idle_prompt` 不得进入 P，否则 Claude 完成后可能假等待 |
| 工具运行时必须优先于 `C` 和 `turn_active` | 设备 panel 需要看到工具名 `m`，真实活动不能被庆祝遮挡 |
| `Stop` / `SessionEnd` 必须清 `tools/waiting/turn_active` | 完成后不能继续 W/P |
| `UserPromptSubmit` 不清 `completed_until` | 连发 prompt 时保留完整 C 动画 |
| 新 turn 的 `tool_start` / 真实等待会清 `completed_until` | 一旦有真实活动，旧 C 必须让位 |
| 同 cwd 旧 waiting session 可被新 session 回收 | Claude Code 重启后旧 P 不能长期残留 |

## 当前已知边界

| 场景 | 当前行为 | 是否接受 |
| --- | --- | --- |
| daemon 进程重启 | 内存状态丢失，直到下一次 hook 到来才恢复可见状态 | 当前架构限制，生产测试需记录 |
| Claude Code 在 P/W 中被强杀且没有后续 hook | 旧状态可能留到 cleanup 或同 cwd 新 prompt 回收 | 需要真实测试覆盖 |
| 多 Claude Code 同 cwd 同 basename | display name 使用 suffix 区分 | 当前 PR 已覆盖 |
| BLE 断连后重连 | `_send` 失败不更新 dedup 基准，重连后可重推同状态 | 需实机验证 |

