# E2E Wire Test 覆盖审计 v1

**日期**: 2026-05-18
**审计对象**: `tests/e2e_wire_assertions.py` v3（真子进程链路）
**方法**: 自审 + codex consult（codex 跑超未产出 deliverable，但帮我集中读了源码）

---

## §0 头条发现：又一个真实 bug

读源码做 audit 时撞到的，e2e v3 实测也证实了：

**`dizzy_until` 过期不 `_mark_dirty`** —— wire 卡 'E' 不出去，5Hz pusher 因 dedup 不重推。

证据：
- `ble_daemon.py:258-279` `_pusher_tick` 有 `completed_until` 过期 `_mark_dirty` 那段，但**没有 `dizzy_until` 对应那段**
- 之后 line 282 `if _dirty:` 才生成新 wire，否则 5Hz tick 等于空转
- e2e A1 step3 实测 dizzy 过 0.6s 后 latest wire 仍是 'E'——本来 turn_active=True 应推 'W'，但根本没推

这跟"turn_active 不清"、"current_error 不清"都不是一回事。**dizzy 过期那一刻 daemon 静默**，要等任何下一个事件 `_mark_dirty` 才会推。如果用户在 StopFailure 后 idle 不发新 prompt，桌宠**永久** E 灯亮着。

修法两件事都要做：
1. `_pusher_tick` 加 dizzy_until 过期 `_mark_dirty`（参考 line 272-279 completed_until 那段照搬）
2. `_enter_error_state` 加 `sess.turn_active = False`（否则修了 (1) 后 wire 会从 'E' 跳回 'W' 还是卡）

---

## §1 完整状态转移矩阵

**优先级链** (`_session_to_wire` line 170-201)：
```
dizzy_until > now  → E
waiting > 0        → P
tools.running 有项 → W (m=tool:summary)
completed_until > now → C
turn_active        → W (no m)
last_tool_start_ts < 0.4s → W (新工具的 5Hz 抖动保护)
else               → I
```

**转移表** (源 → 目，触发，代码位置)：

| # | 源 | 触发 hook / 事件 | 目标 | 写入点 |
|---|---|---|---|---|
| 1 | I/* | user_prompt | W (turn_active=True) | ble_daemon.py:407-416 |
| 2 | W | tool_start (无 needs_approval) | W (m=tool) | line 326-353 |
| 3 | W | tool_start (needs_approval=True) | P (waiting++) | line 346-348 |
| 4 | W | tool_done (tool 删, 仍 turn_active) | W (no m) | line 355-373 |
| 5 | P | tool_done (last needs_approval tool) | W 或 I | line 360-362 |
| 6 | W/P/* | tool_error | E (dizzy 3s) | line 375-387 → _enter_error_state |
| 7 | W/* | stop | C (completed 2s) | line 420-428 |
| 8 | W/* | StopFailure→task_error | E (dizzy 3s) | line 431-433 → _enter_error_state |
| 9 | C/W | notification permission_prompt | P (waiting++) | line 394-404 |
| 10 | C | time>completed_until | I | line 272-279 (_mark_dirty) |
| 11 | C | tool_start | W (清 completed_until) | line 351 |
| 12 | E | user_prompt | W (清 current_error+设 turn_active) | line 407-416 |
| 13 | **E** | **time>dizzy_until** | **应到 W/I，实际卡 E** | **bug: 无 _mark_dirty** |
| 14 | * | subagent_start | * (set has_subagent flag) | line 436 |
| 15 | * | tool_batch_done | * (touch last_activity_ts) | line 389 |
| 16 | I | timeout SESSION_CLEANUP_S(10s) | session 删 | line 263-270 |
| 17 | * | unknown (fallback) | no-op | line 439-440 |

**dedup 层** (`_pusher_tick` line 282-291)：
- `if wire != last_pushed_wire` 才 _send
- `_mark_dirty()` 是唯一让 dedup 失效的触发
- 没人 `_mark_dirty` 时 wire 不重生成

---

## §2 e2e v3 实际覆盖

### scenario BASELINE 实际验证的转移
- #1: I→W (UserPromptSubmit, step1) ✓
- #2: W→W tool_start Read (step2) ✓
- #4: W→W tool_done (step3) ✓
- #7: W→C stop (step4) ✓
- #10: C→I time>completed_until (step5) ✓

5/17 转移覆盖。

### scenario A1_STOPFAILURE 实际验证的转移
- #1: I→W (UserPromptSubmit, step1) ✓
- #8: W→E task_error (step2) ✓
- #13: E→? dizzy expire ← **故意 FAIL 暴露 bug**

2 个新转移 + 1 个 bug 暴露。

**总覆盖：17 个转移里 6 个**（35%）。

---

## §3 e2e v3 漏掉的（严重性 × 生产频率排序）

| 转移 | 严重性 | 生产频率 | 备注 |
|---|---|---|---|
| #3 W→P (tool_start needs_approval) | HIGH | DAILY | needs_approval 当前 hard-code False (hook_bridge.py:147)，实际 P 状态走的是 #9 path。但 daemon 仍接受 #3 path——可能死代码 |
| #9 W/C→P (notification permission_prompt) | **CRITICAL** | DAILY | 每次 Bash/Edit 都触发 P，**完全没测** |
| #5 P→W/I (tool_done after approval) | HIGH | DAILY | P 状态退出路径，关联 #9 |
| #6 *→E (tool_error / PostToolUseFailure) | HIGH | WEEKLY | 比 task_error 更常见，**没测** |
| #11 C→W (tool_start during celebration) | MEDIUM | DAILY | 连发 prompt + 新 turn 立刻起工具 |
| #12 E→W (user_prompt clears error) | MEDIUM | DAILY | 用户撞到 StopFailure 后 retry |
| #14 subagent_start has_subagent | LOW | WEEKLY | 仅影响内部 flag |
| #15 tool_batch_done | LOW | RARELY | 上游可能 deprecated |
| #16 session cleanup timeout | MEDIUM | DAILY | 长 session 自然清理路径 |
| **interrupted=True tool_done** | HIGH | WEEKLY | ESC 中断常见 |
| **多工具并发** | MEDIUM | DAILY | 多个 tool_use_id 同时 running |
| **多 session 并发** | MEDIUM | RARELY | 多 Claude Code 同时跑 |
| **连发 prompt < COMPLETED_HOLD_S** | HIGH | DAILY | A-5 决策依赖此 |
| **dizzy dedup bug (#13)** | **CRITICAL** | API 错误时 100% | §0 新发现 |

---

## §4 e2e v3 vs 真实环境的抽象差距

| 维度 | e2e v3 | 真实环境 | 差距影响 |
|---|---|---|---|
| daemon 二进制 | repo 源 (`daemon/ble_daemon.py`) | `~/.claude-buddy/.venv/Scripts/claude-buddy-daemon.exe` | 版本可能漂移，未验证一致 |
| hook 触发 | e2e 主动 spawn `python hook_bridge.py` 灌 stdin | Claude Code 按 hooks.json fire `pythonw hook_bridge.py` | pythonw vs python（窗口闪不同），但语义同 |
| envelope schema | `tests/fixtures/probe_samples/*.json` 是 probe 实采 | 真 Claude Code 可能加新字段 | 当前看 schema 稳定，但未来不保证 |
| BLE 传输 | `--stub` 完全跳过 | 真 BleakError 重连、payload 拆分、丢包 | 不在 daemon 状态机范围，但 `_send` 返回值 dedup 路径在两处有副作用（line 287-291）|
| 5Hz pusher_task 调度 | 真 async + time.sleep | 真，但系统 swap/慢盘可能拖慢 | 一般无影响 |
| `_spawn_daemon_detached` 自启 (hook_bridge.py:306-370) | 完全没碰（e2e 自己起 daemon） | 真生产路径 | 这条路径有 uv 解析、Windows DETACHED_PROCESS 等，未测 |
| 多 Claude Code 实例 | 单 session | 用户跑多个 Claude Code 同时连一个 daemon | wire ss[] 多元素，no test |
| daemon 进程意外退出 | 不测 | A-4 边界路径 | 单测可补 |
| hook 子进程崩 / stdin 截断 | 不测 | A-4 边界路径 | 单测可补 |
| `MAX_ENVELOPE_BYTES=64KB` 上限 | 不测 | 长 tool_response 可能撞 | 单测可补 |
| `MAX_STDIN_BYTES=1MB` (hook_bridge) | 不测 | 长 Bash output 可能撞 | 单测可补 |
| 生产 daemon 57320 在跑时 | 隔离 57321 不影响 | 用户日常状态 | e2e 端口隔离已解决 |

---

## §5 Top-5 优先级补测（按 severity × frequency）

1. **dizzy expire dedup** (#13, §0 头条 bug)
   - 场景：UserPrompt → StopFailure → 等 DIZZY_HOLD_S + 1s → assert wire emitted 数 > step2 时的数（即 dizzy 过期触发了新 wire）
   - 修后预期：wire 数++ 且 s != E

2. **P 状态完整生命周期** (#9, #5)
   - 场景：UserPrompt → notification(permission_prompt) → wire s=P → Stop → wire s=C
   - 现在 e2e 完全没碰 P，但 P 是日常每次 Bash/Edit 都走

3. **tool_error → E → 恢复** (#6, #12)
   - 场景：UserPrompt → PreToolUse(Bash) → PostToolUseFailure → wire s=E → UserPrompt → wire s=W
   - 比 task_error 频率高一个量级

4. **多工具并发**
   - 场景：UserPrompt → PreToolUse(t1) + PreToolUse(t2) → wire m 应反映其中一个 running → PostToolUse(t1) → wire 仍 W (t2 还在) → PostToolUse(t2) → 全清
   - 真实生产 Claude 4.5+ 经常并发 Read/Grep

5. **连发 prompt (Fix-2/3/4 决策)**
   - 场景：UserPrompt → Stop → wait 1.5s (< COMPLETED_HOLD_S) → UserPrompt → wire 应该立刻 W 还是延续 C？
   - 这关系到 v1_findings.md §A-5 决定 Fix-2/3/4 命运

---

## §6 测试方法本身的问题

诚实评估：

**对的**：
- 真子进程链路（v3）覆盖了 hook_bridge + TCP + state machine + async pusher + dedup 这条主链路
- 端口隔离 (CLAUDE_BUDDY_PORT) 解决了和生产 daemon 冲突，能在 Claude Code 会话里直接跑
- 用 fixture 真 envelope 不是合成数据
- stub 模式跳 BLE 是正确取舍（设备端假设无 bug）

**可改进**：
- 每个 scenario start/stop daemon 慢（每场 ~7s），加场景后总时长会涨。考虑：scenario 之间用唯一 session_id + 用 latest_session_by_id 而不是 ss[0]，daemon 单次起跑全部场景。
- `latest_session()` 取 ss[0] 是脆弱断言。Scenario 之间 session 残留虽然每个 scenario 重启 daemon 解决了，但**单场景内**如果状态泄漏到下一步也抓不出来。应改成 `latest_session_by_n(display_name)` 精确匹配。
- 没有 wire 数量断言（"应该推 N 条 wire"）。dedup bug 这种"少推一条"的问题，靠状态对比抓不到，要看 wire 总数。
- fixture 是历史 probe 采的，schema 漂移会让 e2e 给假阳性。需要定期 re-snapshot。
- 不在 CI 跑——本地手动跑，未自动化。

**架构层 honest take**：
- e2e 测 daemon 不测设备。如果将来设备端 firmware 出 bug（wire 渲染错），e2e 不知道。需要单独的设备端测试（已有 `sim_hooks_v5.py` 不带 --stub 跑真设备）。
- 测试 daemon 不测 plugin runtime（`~/.claude-buddy/`）。如果安装版漂移于 repo 版，用户实际体验 ≠ e2e 结果。需要"装机一致性"测试或 release pipeline。

---

## §7 行动建议

立即：
1. PR-A 修两件事：dizzy_until 过期 _mark_dirty + _enter_error_state 清 turn_active。e2e A1 应 PASS
2. 加 §5 top-5 场景到 e2e（顺序按列表）

中期：
3. 改 `latest_session()` 用 display_name 精确匹配，扫除潜在脆弱断言
4. 把 e2e 接进 CI（GitHub Actions 跑 Windows runner？需要确认）

未做：
5. plugin runtime 一致性测试（release 流程层面）
6. 真 BLE 设备端 e2e（已有手工 `sim_hooks_v5.py` 路径，但没自动化）
