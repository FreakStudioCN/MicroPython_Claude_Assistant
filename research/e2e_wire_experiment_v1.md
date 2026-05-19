# E2E Wire Assertion Experiment v1

**日期**: 2026-05-18
**分支**: `investigate/hook-state-completion-gaps`
**产物**: `tests/e2e_wire_assertions.py`

---

## 0. 这是什么实验

**测试范围**：`Claude Code hook → hook_bridge → daemon → BLE wire payload` 这一段。

**不测**：设备端（ESP32）渲染——假设设备收到 wire 后会正确显示，已通过双形态 v0.9.0 MVP 在物理设备上 dogfooded 过。这次专门验证 daemon 的状态机生成的 wire 是否符合预期。

**为什么需要**：现有 `tests/test_daemon_state.py` 用 `_capture_send` mock 拦截 wire，覆盖状态机内部逻辑，但绕过了真实 hook envelope normalization (`hook_bridge.py:NORMALIZERS`) 和 TCP 链路。这一层之前没人验过。

---

## 1. 工作原理

```
fixture JSON (tests/fixtures/probe_samples/*.json)
   │
   │ stdin
   ▼
python daemon/hook_bridge.py                # e2e 脚本 spawn
   │
   │ TCP 127.0.0.1:57320
   ▼
python daemon/ble_daemon.py --stub          # e2e 脚本 spawn
   │
   │ _send(payload) → print f"[stub-send] t={ts} {json}"
   ▼
stdout 被 e2e 主线程后台 thread 读 + regex 抓
   │
   ▼
assertions: 检查 wire 里 ss[0].s / m / n 是否符合状态机预期
```

**关键技巧**：daemon 已有 `--stub` 模式（line 113-130 ble_daemon.py），不调真实 BLE 而是把每条 wire `print` 到 stdout。这就是天然 wire-tap，零 daemon 代码改动。

---

## 2. 当前场景（v1）

### SCENARIO BASELINE — 正常 turn 链路
```
UserPromptSubmit  → wire s=W
PreToolUse(Read)  → wire s=W, m='Read: ...'
PostToolUse(Read) → wire s=W (turn still active, tools cleared)
Stop              → wire s=C
等 COMPLETED_HOLD_S (2s) → wire s=I
```
**预期**：PASS。这是当前已工作路径，Fix-1 PR #4 后应稳定。

### SCENARIO A1_STOPFAILURE — StopFailure 不该让 W 卡死
```
UserPromptSubmit → wire s=W
StopFailure      → hook_bridge normalize 为 task_error → daemon 进 dizzy
                   wire s=E（dizzy_until 期间）
等 DIZZY_HOLD_S (3s) → wire s=??
```
**预期 (修前)**：FAIL。`ble_daemon._enter_error_state` (line 243-253) 设了 `dizzy_until`、清了 `completed_until`、设了 `current_error`，但 **没有清 `turn_active`**。结果：
- 立刻：wire `s=E`（current_error 让 `_session_to_wire` 优先级链 return 'E'）
- DIZZY_HOLD_S 过期：dizzy 状态退出，但 `sess.turn_active` 仍是 True → wire 回到 `s=W` → 桌宠永久亮着
- 还有：`_pusher_tick` line 263-269 的 cleanup 闸控 `not s.turn_active`，这个 session 永远不会被清

**预期 (PR-A 修后)**：PASS。task_error handler 加一行 `sess.turn_active = False`，dizzy 过期后回 I（最终 cleanup 也能动）。

---

## 3. 已知约束：不能在 Claude Code 会话里跑

**关键发现**：跑 e2e 时，**Claude Code 自身的 hook 链路会持续干扰**。

每次 Claude Code 调任何 tool（Bash、Edit、Write 等），会按 `~/.claude-buddy/hooks/hooks.json` 触发 hook_bridge.py。如果 hook_bridge 连不上 daemon（57320 没人 listen），它会调 `_spawn_daemon_detached()` 启动 `~/.claude-buddy/.venv/Scripts/claude-buddy-daemon.exe` 抢占端口。

**死循环**：
1. e2e 启动 → 检查端口是否空闲
2. 如果你刚 kill 过 stray daemon，但下一个 Claude Code 工具调用又起一个 → 抢端口失败
3. e2e infra error

**解法**：**在独立 terminal 跑 e2e**（不通过 Claude Code）。普通 PowerShell / Git Bash 直接执行 `python tests/e2e_wire_assertions.py`——没有 hook 链路干扰。

---

## 4. 跑法

### 4.1 单次干净跑（推荐）

打开**独立 PowerShell / Git Bash 终端**（不在 Claude Code 里）：

```powershell
# 进 repo
cd "C:\Users\Haipeng Wu\Desktop\claudehardware\MicroPython_Claude_Assistant"

# 干掉所有 stray daemon
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*claude-buddy-daemon*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 跑
python tests\e2e_wire_assertions.py
```

预期输出（修前）：
```
== e2e wire assertions ==
starting daemon --stub ...
daemon ready

[SCENARIO BASELINE]  expect wire: W → W(m=Read) → W → C → I
  PASS  wire emitted after UserPromptSubmit: True
  PASS  step1 UserPromptSubmit → s=W: 'W'
  PASS  step2 PreToolUse(Read) → s=W: 'W'
  PASS  step2 wire m contains 'Read': True
  PASS  step3 PostToolUse(Read) → s=W (turn still active): 'W'
  PASS  step4 Stop → s=C: 'C'
  PASS  step5 after COMPLETED_HOLD_S → s=I: 'I'

[SCENARIO A1_STOPFAILURE]  expect wire: W → E → I (NOT回 W)
  PASS  wire emitted after UserPromptSubmit: True
  PASS  step1 UserPromptSubmit → s=W: 'W'
  PASS  step2 StopFailure → s=E (dizzy active): 'E'
  observation after dizzy expires: s='W'
  FAIL  step3 after DIZZY_HOLD_S → s should NOT be W (got 'W')
  FAIL  step3 strict: after dizzy → s=I (turn_active cleared)

============================================================
SUMMARY
  PASS  BASELINE  (7 ok, 0 fail)
  FAIL  A1_STOPFAILURE  (3 ok, 2 fail)
============================================================
```

### 4.2 在 Claude Code 里跑（不推荐，仅作 emergency）

如果非得在 Claude Code 会话里跑：

1. **临时禁 plugin**：编辑 `~/.claude/settings.json` 把 `"claude-buddy-bridge@claude-buddy": true` 改 `false`
2. **重启 Claude Code**（让 settings 重新加载）
3. 跑 `python tests/e2e_wire_assertions.py`
4. 改回 `true`、重启

更暴力的方式：临时 mv `~/.claude-buddy/hooks/hooks.json` → 跑 → mv 回。改 settings 不重启可能不生效。

### 4.3 选项

```
python tests/e2e_wire_assertions.py --only baseline    # 只跑 BASELINE
python tests/e2e_wire_assertions.py --only a1          # 只跑 A1
python tests/e2e_wire_assertions.py --keep-daemon-out  # 保留 daemon stdout 全文 → .context/e2e_daemon_stdout.txt
```

---

## 5. 退出码

- `0` — 所有断言 PASS
- `1` — 任意断言 FAIL（预期：修前 A1 应 FAIL）
- `2` — infra error（端口被占、fixture 缺失、daemon 启不来）

---

## 6. 不在本实验范围

- **A-4 turn_active 兜底 (MAX_TURN_DURATION_S)**：需要 daemon 接受 env var 覆盖（默认 600s 不适合 e2e）。先用单测 mock 时间 cover。
- **A-5 连发 prompt 间隔分布**：行为数据问题，不是 daemon 状态机 bug。继续走 `scripts/hook_probe.py` + `analyze_probe.py` 自然累积。
- **Fix-2/3/4 (`6e81675`)**：依赖 A-5 数据出结论后再做。
- **真实 BLE 链路 + ESP32 设备**：当前 `sim_hooks_v5.py` 不带 `--stub` 时能跑，但本实验只验 daemon。

---

## 7. TDD 流程（实验如何引导修复）

1. **现在**：跑 `python tests/e2e_wire_assertions.py` → BASELINE PASS、A1_STOPFAILURE FAIL → 这就是"failing test case"
2. **PR-A 修复**：在 `daemon/ble_daemon.py:_enter_error_state` 加 `sess.turn_active = False`；在 `hooks/hooks.json` 注册 StopFailure
3. **重跑 e2e** → 两个场景都应 PASS
4. **PR-B (A-4 兜底)**：单测覆盖，e2e 不动
5. **未来扩展**：probe 数据回来后，加 A-5 / Fix-2 场景验证

---

## 8. 相关引用

- 上层方法论：`research/state_machine_verification_v1.md` + `state_machine_verification_v1_findings.md`
- daemon 源码：`daemon/ble_daemon.py` (state machine), `daemon/hook_bridge.py` (normalizer)
- daemon 常量：line 53-58 ble_daemon.py（COMPLETED_HOLD_S、DIZZY_HOLD_S、PUSH_INTERVAL_S）
- fixture：`tests/fixtures/probe_samples/*.json`
