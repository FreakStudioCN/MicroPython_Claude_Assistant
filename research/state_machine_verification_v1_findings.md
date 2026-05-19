# Daemon 状态机：v1 验证清单——实测前结论 v1.1

**日期**: 2026-05-18
**作者**: ChrisWu132 (with Claude Opus 4.7)
**前置文档**: `research/state_machine_verification_v1.md`（v1 假设清单）
**分支**: `investigate/hook-state-completion-gaps`

---

## 0. 这份文档的作用

v1 doc 列了 5 个假设（A-1 ~ A-5）+ 3 个未 PR 的修复（Fix-2/3/4），全部"等 probe 数据回来再判断"。本次调查发现：**5 个假设里有 4 个（A-1/A-2/A-3/A-4）不用跑实验就能定论**——分别有 Anthropic 官方 docs / GitHub issue / 自家源码作证据。

只剩 A-5（连发 prompt 间隔分布）真的需要实测数据。

**结果：v1 doc §6 决策矩阵需要更新；probe 范围从 11 hook 缩到 2 hook；可以立刻起 PR 修 A-1/A-4 共享的根因。**

---

## 1. 证据来源汇总

| 来源 | 用途 |
|---|---|
| Anthropic 官方 hooks reference (`https://code.claude.com/docs/en/hooks`) | A-1 StopFailure 字段定义 + 8 个 matcher + Stop/StopFailure 互斥语义 |
| GitHub Issue `anthropics/claude-code#33049` | A-3 已知 bug：Task tool subagent 完成不 fire Stop/SubagentStop |
| Anthropic docs（同上）+ 第三方多份分析 | A-2 subagent 内的 PreToolUse / UserPromptSubmit 冒到父进程已确认 |
| `daemon/ble_daemon.py:265, 422` + `hooks/hooks.json` | A-4 cleanup 闸控源码确认 |
| 现有 `scripts/hook_probe.py` | §4 probe 基础设施已就位 |

---

## 2. 各假设结论

### A-1 StopFailure 单独 fire — **CONFIRMED，不用实验**

**官方 hooks reference 原文**：
- `Stop` = 正常结束
- `StopFailure` = turn ends due to API error
- 两者**互斥**（mutually exclusive）
- StopFailure 有 8 个 matcher：`rate_limit` / `authentication_failed` / `oauth_org_not_allowed` / `billing_error` / `invalid_request` / `server_error` / `max_output_tokens` / `unknown`
- StopFailure 不可 block（exit code 2 被忽略，turn 已经结束）

**现状**：`hooks/hooks.json` 没注册 `StopFailure`。任何 API 错误（限流、超 token、计费失败、服务端 500）触发的 turn 结束，daemon 完全收不到信号 → `sess.turn_active = True` 永远不被清。

**修复成本**：normalizer + handler 已存在（`hook_bridge.py:257-271` + `ble_daemon.py:423-426`），只缺 `hooks.json` 加 4 行。

**变化 vs v1 doc**：v1 doc 把"实测会不会单独冒"列为疑点。官方 docs 已经写死语义，疑点不成立。

---

### A-2 subagent 内 hook 冒到父进程 — **CONFIRMED，不用修**

**官方 docs + 多个独立来源原文**：
> Subagent fires UserPromptSubmit and PreToolUse hooks → external tools register a new session

**结论**：Task subagent 内部的 `PreToolUse` 会冒到父进程的 hook 系统。桌宠在 subagent 跑时会看到具体工具名（不是干巴巴的 Task），假设的 bug 不存在。

**变化 vs v1 doc**：v1 doc 把这当成"假设的 bug"，准备实验确认。官方 docs 已经明说，假设不成立。

---

### A-3 SubagentStop 可靠性 — **UPSTREAM 已知 bug，注册它不解决问题**

**GitHub Issue `anthropics/claude-code#33049`**：
> When a subagent spawned via the Agent tool (or TeammateTool) completes and returns results to the parent agent, no Stop hook is fired. However, other lifecycle hooks (UserPromptSubmit, PreToolUse, PostToolUse) fire correctly.

**GitHub Issue `anthropics/claude-code#7881`**：
> Multiple subagents share the same session_id. SubagentStop hook cannot identify which specific subagent has just completed.

**结论**：
- SubagentStop 在 Task tool 启动的 subagent 上**根本不 fire**（上游 bug）
- 即使 fire 也分不清是哪个子 agent
- 我们注册它 = 安慰剂

**Workaround**（Anthropic docs 列了，hook #14/#15）：用 `TaskCreated` / `TaskCompleted` 替代 SubagentStop。这两个 hook 在父进程的 Task tool 生命周期上 fire，不依赖 subagent 内部信号。

**变化 vs v1 doc**：v1 doc 准备"实验看 SubagentStop 会不会冒"。已知不会冒，没必要实验。如果要做 subagent 完成的桌宠反馈，应用 `TaskCompleted` 而不是 `SubagentStop`。

---

### A-4 turn_active 永久卡死 — **CONFIRMED 源码层面真实路径**

**源码闸控链**：
1. `ble_daemon.py:262-269` — 长期无活动 session 清理**闸控在 `not s.turn_active`**
   ```python
   for sid in [k for k, s in list(_sessions.items())
               if not s.tools
               and not s.turn_active           # ← 这一行
               and s.last_activity_ts > 0
               and now - s.last_activity_ts > SESSION_CLEANUP_S
               ...]:
       del _sessions[sid]
   ```
2. `ble_daemon.py:419-422` — `turn_active = False` 唯一写入点是 `stop` handler
3. `ble_daemon.py:415` — `turn_active = True` 写入点是 `user_prompt` handler

**结论：只要 Stop hook 不 fire，turn_active 永远不被清，cleanup 永远不动这个 session，wire 永远显示 W**。

**Stop 不 fire 的真实代码路径**：
- **路径 1（A-1 同根因）**：API 错误 → Claude Code fire StopFailure 而不是 Stop → 我们没注册 StopFailure → daemon 完全错过结束信号
- **路径 2**：Claude Code 进程被 kill / Ctrl+C → 既不 fire Stop 也不 fire StopFailure
- **路径 3**：hook 子进程崩 / stdin 截断（payload 超过 1MB `MAX_STDIN_BYTES` 限）/ JSON 解析失败 → `hook_bridge.py:257-271` 不通知 daemon → daemon 错过结束信号

**变化 vs v1 doc**：v1 doc 把这当成"纯防御性论点，可能从来不发生"。源码 review 表明路径 1 几乎一定会撞到（任何用户达到限流都触发），路径 2/3 偶发但确定存在。

**修复**：
- 一阶段（共享 A-1 修复）：注册 StopFailure，handler 里 `sess.turn_active = False` + 进 error 状态
- 二阶段（兜底路径 2/3）：`_pusher_tick` 每秒检查 `now - sess.last_activity_ts > MAX_TURN_DURATION_S`（建议 600s），强制清 `turn_active = False`

---

### A-5 连发 prompt 间隔分布 — **NEEDS-EXPERIMENT，唯一剩下要跑数据的**

源码层面无法回答"用户在 stop 后 < 2s 内连发新 prompt 的概率"。这是行为数据问题。

**这条决定 Fix-2/3/4 是修真问题还是修空气**：
- > 20% → 修 Fix-2/3/4
- < 5% → commit `6e81675` 撤掉

**probe 需求**：只需要钩 `UserPromptSubmit` 和 `Stop`（外加 `StopFailure` 顺便验证 A-1 的实战触发率）。3 个 hook 够了，不需要 v1 doc §4.3 列的 11 个。

---

## 3. 对 v1 doc §4 probe 设计的修订

| v1 doc §4 项 | 修订建议 |
|---|---|
| §4.1 文件结构 | 不写 `scripts/probe_all_hooks.py` — `scripts/hook_probe.py` 已经存在且覆盖 95% 规格 |
| §4.2 规格 | 现有 `hook_probe.py` 唯一差异：路径用下划线 `~/.claude_buddy/probe.jsonl`，v1 doc 写连字符 `~/.claude-buddy/probe_full.jsonl`。**统一用下划线**（daemon 实际用的也是 `.claude_buddy/`）。新增分析时直接读这个文件 |
| §4.3 钩 11 个事件 | 缩到 **3 个：`UserPromptSubmit` + `Stop` + `StopFailure`**。其他 8 个对应的假设已经在源码 / docs 层面定论 |
| §4.4 启动方式 | 不变。临时把 hooks 指向 probe，跑日常工作，再恢复 |
| §4.5 分析脚本 | 简化为只算 A-5：`(UserPromptSubmit_ts - 上一个 Stop_ts) < COMPLETED_HOLD_S(2s)` 的占比。顺便统计 StopFailure 真实触发次数（实战频率，不是决策依据） |

---

## 4. 修订后的决策矩阵

| 假设 | 结论方式 | 决策 |
|---|---|---|
| A-1 StopFailure | 官方 docs CONFIRMED | **立刻 PR**：`hooks.json` 注册 StopFailure |
| A-2 subagent hook 冒父 | 官方 docs CONFIRMED | 不修，删 v1 doc §A-2 / 实验 E1 |
| A-3 SubagentStop | upstream issue CONFIRMED 不可靠 | 不注册 SubagentStop；如要做 subagent 完成反馈，研究 `TaskCreated`/`TaskCompleted` |
| A-4 turn_active 卡死 | 源码 CONFIRMED 真实路径 | **立刻 PR**：StopFailure handler 清 turn_active + 加 `MAX_TURN_DURATION_S=600` 兜底 |
| A-5 连发 prompt 间隔 | NEEDS-EXPERIMENT | 跑缩小版 probe（3 hook，自然工作期累积一周）→ 根据占比决定 Fix-2/3/4 |

---

## 5. 行动清单（按依赖排序）

1. **PR-A**：`hooks.json` 注册 `StopFailure`。改 daemon `_stop_failure` handler，让它清 `turn_active` + 进 error 状态展示用户。理由：A-1 + A-4 共享根因，一次修两件事。
2. **PR-B**：daemon `_pusher_tick` 加 `MAX_TURN_DURATION_S = 600` 兜底，强制清 stale `turn_active`。理由：A-4 的进程 kill / hook 崩这类边界路径。代价：5Hz tick 里多一次时间比较。
3. **Probe（轻量）**：把 `scripts/hook_probe.py` 通过临时 `hooks/probe_hooks.json` 钩 3 个事件，跑 3-7 天，分析连发 prompt 间隔，决定 Fix-2/3/4 命运。
4. **删空假设**：v1 doc 的 A-2/A-3 章节标"已 docs CONFIRMED 不需要实验"，避免后人重复调查。

PR-A 和 PR-B 都不依赖 probe 数据，可以现在做。Fix-2/3/4 一组等 probe 出数。

---

## 6. 已知局限

- **codex consult 在本仓不可用**：今天 codex 0.130.0 在 Windows + 中文 daemon 仓上挂掉两次——PowerShell GBK 编码把中文源文件读成 mojibake，codex 看到的是乱码；同时 Windows sandbox policy 拒绝了它的 `Get-Content -LiteralPath '...'` 行号化命令。本次 review 100% 来自 Anthropic docs + GitHub issues + 我自己读源码。如果要让 codex 起作用，需要先在 PowerShell 里 `chcp 65001` + `$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8`，且 codex 沙箱不拒绝那条命令。
- **A-5 数据采集仍要时间**：probe 自然累积需要日常工作覆盖正常使用 pattern，至少 3-7 天才能下 > 20% / < 5% 的判断。

---

## 7. 关联引用

- v1 doc：`research/state_machine_verification_v1.md`
- 实验分支 commit：`6e81675 fix(daemon): 修 _session_to_wire 优先级 + C 庆祝边界`（Fix-2/3/4 候选，等 A-5 数据）
- 已 PR Fix-1：`8e5832f` → [PR #4](https://github.com/FreakStudioCN/MicroPython_Claude_Assistant/pull/4)
- 现有 probe：`scripts/hook_probe.py`
