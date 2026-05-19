# Daemon 状态机：待实测验证清单 v1

**日期**: 2026-05-18
**作者**: ChrisWu132 (with Claude Opus 4.7)
**当前 PR**: [FreakStudioCN#4](https://github.com/FreakStudioCN/MicroPython_Claude_Assistant/pull/4) — 只含 Fix-1
**实验分支**: `investigate/hook-state-completion-gaps`（experimental commit `6e81675` 保留，未 push）

---

## 0. 文档目的

记录"daemon 漏报已完成"调查中，**没有进 PR 的所有候选问题**——已写好但搁置的修复 + 没写代码的假设——以及每条对应的**真实环境实测方案**。等 probe 数据回来后再判断保留 / 撤销 / 扩展。

**进 PR 的（不在本文档讨论范围）**：Fix-1 `_session_to_wire` 优先级——tools 提到 turn_active 之前，让 W 状态保留 `m` 字段。源码可证 + 触发条件（任何调工具的 turn）几乎 100% 在生产成立，不需要 probe 即可上线。

---

## 1. 已写代码但未 PR 的修复（Fix-2/3/4）

这三块在 `investigate/hook-state-completion-gaps` 分支 commit `6e81675` 里，单测全绿 + codex 三轮复审通过，但**没有 probe 数据证明它们解决的是真问题**。逻辑层面成立 ≠ 真实环境会触发。

### Fix-2：`user_prompt` 不再清 `completed_until`

**修法**：删 `ble_daemon.py:402` 那一行 `sess.completed_until = 0.0`。

**想解决的问题**：用户上一轮 stop 后立刻发新 prompt（"接着改"），原代码立即清掉 `completed_until`，C 庆祝动画只跑了 < 200ms 就被切走，桌宠"庆祝感"被秒杀。

**真实性疑点**：
- 用户在 stop 后 < 2s 内连发新 prompt 的概率是多少？依赖人类输入节奏，**纯脑补**没数据
- 如果实际平均间隔 > 2s，C 一直能跑完，这个 fix 是修空气

**怎么验证**：
- 跑 probe（见 §4）记 `stop` 时间戳和下一个 `user_prompt` 时间戳
- 计算 `(prompt - stop) < COMPLETED_HOLD_S(2s)` 的占比
- 阈值：占比 > 20% → 修；< 5% → 不修

### Fix-3：`completed_until` 检查降到 `waiting/tools` 之后

**修法**：`_session_to_wire` 优先级链里 C 从位置 2 移到位置 4（在 waiting / tools 之后）。

**想解决的问题**：codex 一轮 P2 review 发现——Fix-2 让 C 跨 turn 存活后，如果新 turn 在 C 期间启动工具，wire 优先 return C 把工具名盖住。需要让真实活动优先于"庆祝"。

**依赖关系**：**只有 Fix-2 触发时才需要 Fix-3**。如果 Fix-2 不上（即 user_prompt 仍清 completed_until），新 turn 启动时 completed_until 已经是 0，C 不会盖工具，Fix-3 完全是死代码。

**怎么验证**：跟 Fix-2 一同——Fix-2 不修则 Fix-3 自动不修。

### Fix-4：`tool_start` / `notification(permission_prompt)` 主动清 `completed_until`

**修法**：在两个 handler 里加 `sess.completed_until = 0.0`。

**想解决的问题**：codex 二轮 P2 review 发现——Fix-2 + Fix-3 后还有间隙：新 turn 启工具 → tool_done → tools 空了 → completed_until 仍 > now → wire 闪回旧 C。需要在工具/审批一冒头就抛弃旧 C。

**依赖关系**：同样只有 Fix-2 触发时才需要。

**怎么验证**：跟 Fix-2 一同。

### `test_daemon_state.py::test_user_prompt_clears_completed` 改写

随 Fix-2 一起的测试翻转——把老断言"user_prompt 清 completed_until"改成新 contract"user_prompt 保留，tool_start 清"。

**依赖**：跟 Fix-2 同生共死。Fix-2 不上则不动这个测试（虽然其底层依赖的静默期推断已经被 upstream `3fcba73` 删了，测试自身在 pre-fix 状态本来就跑不通——但那是另一个 issue，不是这次该改的范围）。

---

## 2. 没写代码、纯假设的问题（plan §10B A-1 ~ A-5）

这五条在 plan `quirky-doodling-nygaard.md` §10B 列了，**没动一行代码**——属于"我感觉可能有 bug，但我也没观察过真实行为"。

### A-1：`StopFailure` 真的会触发，且 `Stop` 不会同时也来？

**假设的 bug**：Claude Code API rate limit / 流式断开 / billing error 时，触发 `StopFailure` 而不触发 `Stop`。我们 `hooks.json` 没注册 `StopFailure` → daemon 永远收不到 → `turn_active` 永远 True → 桌宠 W 灯永久卡。

**疑点**：
- `StopFailure` 实测会不会单独冒？还是 `Stop` + `StopFailure` 都冒？
- Anthropic 限流频率到底多高？如果一周才一次撞，影响很小

**怎么验证**：
- 跑 probe 全量钩，等一次自然 rate limit（或人为制造：长 prompt 引爆 max_output_tokens）
- 看 jsonl 里 `StopFailure` 字段 + 是否同时有 `Stop`
- 决策：单独冒 → 注册 `StopFailure`；只 `Stop` 冒 → 不注册

**修起来成本**：normalizer + handler 已存在（`hook_bridge.py:257-271` + `ble_daemon.py:423-426`），只要 `hooks.json` 加 4 行。

### A-2：Task subagent 内部的工具调用是否冒到父进程 hook？

**假设的 bug**：用户起 Task subagent 后，子 agent 跑的工具不冒到父进程，桌宠 30s~几分钟只显示干巴巴的 `Task` 不知道在干啥。

**疑点**：
- subagent 是不是隔离进程？还是共用父的 hook 系统？
- 即便分离，Claude Code 是否给父进程 forward 子 agent 的 hook 事件？

**怎么验证**：
- 跑 probe，用 Task 起一个 Explore agent 找几个文件
- 看 jsonl 里 subagent 期间有没有冒 `PreToolUse(Read)` 之类的事件
- 决策：冒了 → 没问题，不修；不冒 → 进 §A-3

### A-3：`SubagentStop` 真的会触发？且产品上值不值得展示？

**假设的 bug**：subagent 完成时桌宠没有任何反馈信号，用户感觉"卡了"。

**疑点 1（会不会冒）**：docs 列了但 changelog v2.1.143 从没提过——可能像 PostToolBatch 一样名存实亡。

**疑点 2（产品上值不值得）**：即使冒了，subagent 完成是不是用户感知层面的"completed"？还是只是主 turn 内部的中间步骤？这是产品决策不是 bug。

**怎么验证**：
- A-2 同一次实验顺手看 `SubagentStop` 字段
- 决策：冒了 + 产品需要 → 注册 + 加 handler；冒了但产品不需要 → 不修；不冒 → 文档注明

### A-4：`turn_active` 真的会永久卡死？

**假设的 bug**：Claude Code 进程崩了 / hook 子进程被杀 / TCP 断 / stdin 截断时，`Stop` 信号丢失 → `turn_active` 永久 True → cleanup 不动 → 桌宠 W 灯永久亮。需要 `MAX_TURN_DURATION_S = 600` 兜底。

**疑点**：
- 实际有没有 user 遇到？没有 GitHub issue、没有日志告警
- 纯防御性论点，可能从来不发生

**怎么验证**：
- 跑 probe + 故意制造异常（kill claude code 进程中途，看后续重启行为）
- 长时间挂 daemon 监听日志，统计 `turn_active=True` 持续 > 5min 的频率
- 决策：撞上 → 加 600s 兜底；不撞 → 标 TODO 等真实告警

### A-5：用户连发 prompt 的实际间隔分布

**和 Fix-2/3/4 直接相关**。见 §1 Fix-2 "怎么验证"。

---

## 3. 待补 hook 注册（不依赖假设的明确 todo）

这几条**源码可证有问题**，但优先级低，等 probe 数据回来后顺手补：

| hook | 状态 | 修起来 | 何时改 |
|---|---|---|---|
| `PostToolBatch` | 上轮已确认 docs/changelog 矛盾，疑似 deprecated。normalizer/handler 是死代码 | 不动 | 拿到 probe 数据若证实不冒 → 删 normalizer + handler；若证实冒 → 加注册 |
| `SubagentStart` | normalizer + handler 已写，hook 未注册（A-2/A-3 同步评估） | hooks.json +4 行 | 跟 A-2/A-3 结论一起 |
| `StopFailure` | normalizer + handler 已写，hook 未注册（A-1） | hooks.json +4 行 | 跟 A-1 结论一起 |
| `SubagentStop` | normalizer 和 handler 都没写 | 写 + 注册 | 跟 A-2/A-3 结论一起 |
| `SessionStart` / `SessionEnd` | 完全没接 | 写 + 注册（如果做开机/关机仪式） | 产品层决策，不在 daemon 范围 |
| `Notification` 多子类型 | 当前只识别 `permission_prompt`；`idle_prompt` 完全丢弃 | 协议 v6 改动 | 拿到 idle_prompt 实测后立项 |

---

## 4. Probe 基础设施设计

目标：把 daemon **不动**，挂一套独立的 probe 钩子捕获**真实 hook 时序**到 jsonl。

### 4.1 文件结构

```
scripts/probe_all_hooks.py        # 独立 probe 脚本（不依赖 hook_bridge.py）
hooks/probe_hooks.json            # 钩 11 个目标事件
research/probe_logs/              # 日志归档：probe_full_YYYYMMDD.jsonl
scripts/analyze_probe.py          # 离线分析：算各假设的触发率
```

### 4.2 `scripts/probe_all_hooks.py` 规格

输入：stdin 一个 JSON envelope（Claude Code hook 标准）。
处理：附加 `_probe_received_at = time.time()`，整行 append 到 `~/.claude-buddy/probe_full.jsonl`。
输出：stdout 空 dict `{}`（不阻塞 Claude Code）。退出码 0。

实现要点：
- 不引用 `daemon/hook_bridge.py`——完全独立，不影响生产
- 用 `pythonw`（Windows 防闪窗，跟生产 hooks.json 一致）
- 文件 append 用 buffering=1 + flush，crash 不丢数据
- 文件大小自动 rotate（超过 50MB 切下一个 .1.jsonl）

### 4.3 `hooks/probe_hooks.json` 范围

钩**和当前假设直接相关**的 11 个事件（不撒网钩 26 个，目标导向）：

| 事件 | 目的 |
|---|---|
| `UserPromptSubmit` | 对照组，看时序基线 |
| `PreToolUse` / `PostToolUse` / `PostToolUseFailure` | 同上 + 验证 A-2（subagent 工具是否冒到父） |
| `Notification` | 对照组 + idle_prompt 子类型出现率 |
| `Stop` | 对照组 + 验证 A-1（StopFailure 时是否也冒） |
| `StopFailure` | 验证 A-1 |
| `SubagentStart` / `SubagentStop` | 验证 A-2 / A-3 |
| `SessionStart` / `SessionEnd` | 顺手看，开机仪式后用 |

不钩：`PostToolBatch`（已确认放弃）、`PermissionRequest` / `Denied` / `Elicitation` / `Compact` / `Worktree` / `Task` / `Cwd` / `File` / `Config` / `Instructions` / `TeammateIdle` / `Setup` / `UserPromptExpansion`——等需要时单独立项。

### 4.4 启动方式

- 临时把这个 plugin 的 hooks 配置（或者用户级 `~/.claude/settings.json`）指向 `probe_hooks.json`，不动生产 daemon
- 用户日常工作时自然累积数据
- 跑完实验恢复原 hooks.json

### 4.5 分析脚本 `scripts/analyze_probe.py`

读 `probe_full.jsonl`，输出针对每个假设的统计：

```
A-1 StopFailure：
  Stop 事件数: 234
  StopFailure 事件数: 3
  StopFailure 之后 5s 内有 Stop 的占比: 0/3 → StopFailure 单独冒，需要注册

A-2 subagent 内 hook：
  含 Task 工具的 turn 数: 12
  Task 期间冒 PreToolUse 的 turn 数: 0 → subagent 完全隔离

A-5 连发 prompt 间隔：
  (UserPromptSubmit - 上一个 Stop) < 2s 的占比: 47/123 (38%)
  → Fix-2 是真问题，修！
```

---

## 5. 实验矩阵（plan §11.2 拷贝过来）

| # | 场景 | 操作 | 关心的 hook | 假设验证 |
|---|---|---|---|---|
| E1 | Task subagent 全程 | 用 Task 起一个 Explore agent 找几个文件 | 主进程是否收 subagent 内的 PreToolUse / PostToolUse / SubagentStart / SubagentStop | A-2 / A-3 |
| E2 | API 限流 / 错误 | 等一次自然 rate limit 或人为制造 max_output_tokens | StopFailure 是否冒、Stop 是否也冒 | A-1 |
| E3 | ESC 中断 tool | 起 sleep 60 Bash，1s 后 ESC | PostToolUse(interrupted=true) 后 Stop 是否冒 | A-4 侧面 |
| E4 | 连续两条 prompt | Stop 后 < 2s 立刻发下一条 | 看时间戳分布 | A-5 / Fix-2 |
| E5 | 进程被杀 | turn 中途 kill claude code 进程，重启后再发 prompt | 是否有"清理"事件 / 旧 session 状态 | A-4 |
| E6 | 长 turn | 让 Claude 跑 30+ 工具的大任务 | 全程时序，看 hook 丢失/延迟 | A-4 侧面 |

E1 / E4 / E6 可以挂在背景跑日常工作自然收集，E2 / E3 / E5 需要主动制造场景。

---

## 6. 决策矩阵

| 假设 | 实验 | 结果 → 决策 |
|---|---|---|
| A-1 StopFailure 单独冒 | E2 | True → PR 加 `StopFailure` 注册；False → 文档注明，放弃 |
| A-2 subagent 内 hook 冒父进程 | E1 | True → A-3 进入产品讨论；False → A-3 默认"需要" |
| A-3 SubagentStop 冒 | E1 | True + 产品需要 → PR 加注册 + handler；False → 删 normalizer 死代码 |
| A-4 turn_active 卡死 | E5 / E6 | 撞上 → PR 加 600s 兜底；不撞 → 标 TODO |
| A-5 / Fix-2 连发 prompt | E4 | > 20% 触发 → PR Fix-2/3/4 一组；< 5% → 放弃，删 commit `6e81675` |

---

## 7. 不在本文档范围

- v6 协议改动（多 Notification 子类型 / idle_prompt 新状态枚举）
- SessionStart/End 仪式动画
- PreCompact/PostCompact 长思考动画
- PermissionRequest/Denied 接入
- daemon 持久化（重启状态恢复）

这些等实验数据 + 产品决策齐了再单独立项。

---

## 8. 维护

- 每次实验结束 → `research/probe_logs/EX_YYYYMMDD.jsonl` 归档
- 每个假设有结论 → 更新本文档第 6 节决策状态
- 全部假设有结论 → 关闭实验分支 `investigate/hook-state-completion-gaps`，把要 PR 的 fix 拆成独立分支按 gitdev.md 提

---

## 9. 关键文件引用

- 实验分支 commit：`6e81675 fix(daemon): 修 _session_to_wire 优先级 + C 庆祝边界`
- 当前已 PR Fix-1：`8e5832f`（在 `fix-T0-wire-w-loses-tool-name` 分支）→ [PR #4](https://github.com/FreakStudioCN/MicroPython_Claude_Assistant/pull/4)
- 上层 plan：`~/.claude/plans/quirky-doodling-nygaard.md` §10A/§10B/§11
- 上游修复历史：`33c6eff` turn_active 引入、`3fcba73` Stop hook 接入、`977a4a5` cleanup 保护 C
