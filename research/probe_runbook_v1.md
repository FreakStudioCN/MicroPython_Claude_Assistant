# Probe Runbook v1 — 跑 A-5 数据采集

**日期**: 2026-05-18
**为何只剩 A-5**: 见 `state_machine_verification_v1_findings.md` §4 决策矩阵——A-1/A-2/A-3/A-4 已经在 docs/源码层定论，只剩 A-5 真的要数据。

---

## 1. 一键启用 probe

probe 用法不动生产 daemon——把 plugin 的 hooks 临时指向 `hooks/probe_hooks.json` 就行。

### 方案 A：临时切 plugin hooks（推荐）

如果你这台机用 `~/.claude/settings.json` 或 `.claude-plugin/plugin.json` 加载了 `MicroPython_Claude_Assistant/hooks/hooks.json`，先备份生产 hooks，然后切到 probe：

```powershell
cd "C:\Users\Haipeng Wu\Desktop\claudehardware\MicroPython_Claude_Assistant"
Copy-Item hooks\hooks.json hooks\hooks.json.bak
Copy-Item hooks\probe_hooks.json hooks\hooks.json
```

启用后：

- 桌宠**不再亮灯**（因为 hooks 指向了 probe，daemon 不收事件）——这是预期的，probe 模式是 read-only 采数据。
- 每次 turn 会在 `~/.claude_buddy/probe.jsonl` append 一条 envelope（每 turn 至少 2 条：UserPromptSubmit + Stop / StopFailure）。

### 方案 B：用户级 settings.json 单独装载 probe（保留生产 daemon 同时工作）

如果想 **probe + 生产 daemon 并行**（桌宠继续亮灯，同时采数据），编辑 `~/.claude/settings.json` 加一组 hooks 指向 `scripts/hook_probe.py`，不动 plugin 的 `hooks.json`。Claude Code 会两组都 fire。

⚠ 警告：这样 probe 会双倍记录所有 hook payload（生产 daemon 和 probe 各收一份），但 hook_probe.py 是 read-only 不影响主流程。

---

## 2. 跑多久

| 目标占比 | 需要的最小配对数 | 估算日历时间 |
|---|---|---|
| 决策"修真问题"（≥ 20%） | 100+ 配对 | 1-3 天日常工作（每天 30-50 个 turn） |
| 决策"修空气"（≤ 5%）   | 200+ 配对 | 3-7 天 |
| 灰区（5-20%）           | 越多越好    | 持续采到稳定为止 |

中途随时跑 analyzer 看进度，不需要等结束。

---

## 3. 分析

```powershell
cd "C:\Users\Haipeng Wu\Desktop\claudehardware\MicroPython_Claude_Assistant"
python scripts\analyze_probe.py
```

输出会给：
- StopFailure 自然触发频率（顺便看，跟 A-1 修复决策已无关）
- Stop → UserPromptSubmit 间隔分布柱状图
- < 2s 占比 → 直接给"修 / 撤" 建议

样例输出（虚构数据示意）：

```
A-5 / Fix-2/3/4 连发 prompt 间隔（阈值 2.0s = COMPLETED_HOLD_S）
  配对数 (Stop → UserPromptSubmit): 187
  间隔 < 2.0s 的占比: 71/187 = 38.0%

  分布:
       <0.5s:   12  █████
       <1s :   28  █████████████
       <2s :   31  ██████████████
       <5s :   42  ████████████████████
       <10s:   28  █████████████
       <30s:   31  ██████████████
       <60s:   12  █████
       <5min:   3
      >=5min:    0

--- 决策 ---
  ✅ 占比 38.0% ≥ 20% → Fix-2/3/4 修真问题，建议 PR commit 6e81675
```

---

## 4. 恢复生产

```powershell
cd "C:\Users\Haipeng Wu\Desktop\claudehardware\MicroPython_Claude_Assistant"
Copy-Item hooks\hooks.json.bak hooks\hooks.json -Force
Remove-Item hooks\hooks.json.bak
```

数据归档：

```powershell
$today = Get-Date -Format "yyyyMMdd"
New-Item -ItemType Directory -Force research\probe_logs | Out-Null
Move-Item ~\.claude_buddy\probe.jsonl research\probe_logs\A5_$today.jsonl
```

---

## 5. 安全 reminders

`hook_probe.py` 头注释提醒过：probe.jsonl 会原样落盘 Bash command / Write content / Read 路径。**采完立刻归档+清原日志，不要提交到任何 repo**。

```powershell
# 确认没把 probe.jsonl 误加进 git
git status
# 应该看到 probe.jsonl 在 .claude_buddy/ 不在 repo 内
```

---

## 6. 不要做的事

- 不要为了"凑数据"故意制造短间隔 prompt——会污染样本。自然工作流即可。
- 不要把 probe 留过 7 天——磁盘 50MB cap 后会停写。
- 不要在 probe 期间做 review/调试 daemon 本身——probe 数据要反映"用户正常使用"。
- 不要因为 StopFailure 没在 probe 期间出现就推翻 A-1 修复决策——A-1 是 docs CONFIRMED，触发频率只影响 priority，不影响是否修。

---

## 7. 一图流总结

```
findings 已结论：A-1 / A-2 / A-3 / A-4  →  起 PR 修 A-1+A-4 根因（不依赖 probe）
              ↓
probe 采 A-5：UserPromptSubmit / Stop / StopFailure 三个 hook → 跑 3-7 天
              ↓
analyzer 出占比：≥20% → 修 Fix-2/3/4    /    ≤5% → 撤 commit 6e81675
```
