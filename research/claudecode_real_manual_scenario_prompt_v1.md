# Claude Buddy Real Manual Scenario Prompt v1

把本文件中「给 Claude Code 的指令」整段交给 Claude Code 执行。目标是触发真实 Claude Code hooks、真实工具调用、真实用户交互流程，用来观察 Claude Buddy daemon 和硬件显示是否卡在错误状态。

执行前，另开一个终端观察生产 daemon 日志。生产 daemon 已占用 57320 时，不要再用 capture 脚本抢 57320 端口，直接 tail 日志：

```powershell
$log = Join-Path $env:TEMP 'ble_daemon.log'
Get-Content $log -Tail 0 -Wait
```

如果使用隔离端口测试，并且没有生产 daemon 占用目标端口，才使用 capture 脚本：

```powershell
$env:CLAUDE_BUDDY_PORT="57322"
python scripts\capture_real_claude_wire.py --seconds 900 --port 57322 --out .context\manual_interactive_run.jsonl
```

## 给 Claude Code 的指令

你现在是 Claude Buddy 真实 hook 测试执行器。请严格按顺序执行下面所有场景。不要模拟工具调用；该读文件就用真实 Read/LS/Grep/Bash/Edit/Write/AskUserQuestion 等工具。每个场景开始和结束都要在聊天里打印一行 marker，格式必须完全一致：

```text
CBTEST:Sxx:START:<name>
CBTEST:Sxx:END:<name>:<result>
```

不要修改项目源码。允许你只在 `.context/claude_buddy_manual_probe/` 目录里创建、编辑、删除测试文件。

如果某一步需要用户确认权限或回答问题，请正常发起 Claude Code 的真实交互并等待用户选择，不要绕过权限/交互流程。

### S01 no-tool completion

打印：

```text
CBTEST:S01:START:no_tool
```

不要使用任何工具，直接回答：

```text
no-tool-ok
CBTEST:S01:END:no_tool:ok
```

预期观察：daemon/wire 从 `W` 到完成/空闲；普通 `idle_prompt` 不能让 daemon 常驻 `P`。

### S02 single read tool

打印：

```text
CBTEST:S02:START:single_read
```

使用真实 Read 工具读取 `VERSION.txt`。然后回答版本号，并打印：

```text
CBTEST:S02:END:single_read:ok
```

预期观察：`user_prompt -> tool_start -> tool_done -> stop/session_end`，wire 最终不能卡 `W/P/E`。

### S03 multiple tools in one turn

打印：

```text
CBTEST:S03:START:multi_tool
```

依次真实执行：

1. LS 当前目录。
2. Read `README.md` 的前面部分。
3. Bash 执行 `echo claude-buddy-multi-tool-ok`。

然后总结三步结果，并打印：

```text
CBTEST:S03:END:multi_tool:ok
```

预期观察：多次 `tool_start/tool_done` 期间保持 `W`，最后不能提前回 `I`，也不能最终卡 `W`。

### S04 permission write path

打印：

```text
CBTEST:S04:START:permission_write
```

创建目录 `.context/claude_buddy_manual_probe/`，然后写入文件 `.context/claude_buddy_manual_probe/write_probe.txt`，内容为：

```text
write-probe-ok
```

如果 Claude Code 弹出权限确认，请等待用户选择。用户应选择允许。

写完后读取该文件确认内容，然后打印：

```text
CBTEST:S04:END:permission_write:ok
```

预期观察：如果出现 `Notification(permission_prompt)`，wire 应进入 `P`；允许后应继续 `W`，最后不残留 `P`。

### S05 edit path

打印：

```text
CBTEST:S05:START:edit_file
```

编辑 `.context/claude_buddy_manual_probe/write_probe.txt`，在文件末尾追加一行：

```text
edit-probe-ok
```

读取文件确认两行都存在，然后打印：

```text
CBTEST:S05:END:edit_file:ok
```

预期观察：Edit/Write hook 正常成对出现，最后不残留 `W/P/E`。

### S06 failed command / nonzero exit

打印：

```text
CBTEST:S06:START:failed_command
```

真实执行一个会失败的 Bash 命令：

```powershell
cmd /c exit 7
```

不要把这个失败当作测试失败。观察并说明 Claude Code 如何报告该命令，然后打印：

```text
CBTEST:S06:END:failed_command:expected_nonzero
```

预期观察：真实 Claude Code 可能把非零退出仍记录为 `tool_done`，不一定是 `tool_error`。关键是最后不能卡 `W`。

### S07 real tool error

打印：

```text
CBTEST:S07:START:tool_error
```

尝试读取一个不存在的文件：

```text
.context/claude_buddy_manual_probe/THIS_FILE_SHOULD_NOT_EXIST_12345.txt
```

不要创建它。说明错误是预期的，然后打印：

```text
CBTEST:S07:END:tool_error:expected_error
```

预期观察：wire 可以短暂进入 `E`，但错误态结束后必须离开 `E`，不能一直停在 `E`。

### S08 single-choice question

打印：

```text
CBTEST:S08:START:single_choice
```

现在必须用 Claude Code 的真实用户交互能力向用户提出一个单选题。题目和选项必须完全如下：

```text
请选择一个测试分支，只能选一个：
A. alpha
B. beta
C. gamma
```

等待用户回答。用户建议回答：`B`。

收到用户回答后，不要使用工具，只复述用户选择，并打印：

```text
CBTEST:S08:END:single_choice:<user_answer>
```

预期观察：如果 Claude Code 使用 `AskUserQuestion` 或发出 `Notification(permission_prompt)` / `elicitation_dialog`，daemon 应进入 `P`。用户回答后，`tool_done` 或下一次 `UserPromptSubmit` 应清掉 `P`。

### S09 multi-select question

打印：

```text
CBTEST:S09:START:multi_select
```

现在必须用 Claude Code 的真实用户交互能力向用户提出一个多选题。题目和选项必须完全如下：

```text
请选择要覆盖的边缘情况，可以多选：
A. permission
B. error
C. long_think
D. interrupt
```

明确告诉用户可以回答多个字母，例如：`A,C`。

等待用户回答。用户建议回答：`A,C`。

收到用户回答后，不要使用工具，只复述用户选择，并打印：

```text
CBTEST:S09:END:multi_select:<user_answer>
```

重点观察：记录是否触发 `AskUserQuestion` / `Notification(permission_prompt)` / `elicitation_dialog`，daemon/wire 是否进入 `P`，用户选择后是否清掉 `P`。如果只有 `Notification(idle_prompt)`，daemon 应忽略它，不能因此常驻 `P`。

### S10 long think after tool

打印：

```text
CBTEST:S10:START:long_think_after_tool
```

先真实 Read `VERSION.txt`。读完后不要再调用工具，进行至少 20 秒的纯文本推理等待，然后回答：

```text
long-think-after-tool-ok
CBTEST:S10:END:long_think_after_tool:ok
```

预期观察：`tool_done` 到 `stop/session_end` 之间超过 cleanup 时间后，最后仍不能卡 `W`，也不能丢完成态。

### S11 back-to-back turns inside same session

打印：

```text
CBTEST:S11:START:back_to_back
```

先 Read `VERSION.txt`，回答一句 `first-turn-ok`。然后不要退出会话，继续执行下一段：再 LS `.context/claude_buddy_manual_probe/`，回答一句 `second-turn-ok`。最后打印：

```text
CBTEST:S11:END:back_to_back:ok
```

预期观察：同一个 Claude Code session 连续两轮不能发生旧的 `C/P` 闪回覆盖新的 `W/tool`。

### S12 cleanup

打印：

```text
CBTEST:S12:START:cleanup
```

删除 `.context/claude_buddy_manual_probe/write_probe.txt`。如果目录为空，也可以删除 `.context/claude_buddy_manual_probe/`。

打印：

```text
CBTEST:S12:END:cleanup:ok
```

### S13 normal idle should not become P

打印：

```text
CBTEST:S13:START:idle_no_p
```

不要使用工具，直接回答：

```text
idle-finished-ok
CBTEST:S13:END:idle_no_p:ok
```

回答后保持会话打开，等待至少 20 秒，不要主动继续发消息。

预期观察：如果 Claude Code 发 `Notification(idle_prompt)`，daemon 应打印 `idle_prompt ignored`，wire 不应常驻 `P`。

### S14 two questions back to back

打印：

```text
CBTEST:S14:START:double_question
```

连续提出两个真实用户交互问题。第一个：

```text
第一题：请选择环境，只能选一个：
A. dev
B. staging
C. prod
```

等待用户回答。用户建议回答：`A`。

收到第一个回答后，立刻提出第二个：

```text
第二题：请选择确认动作，只能选一个：
A. continue
B. pause
C. abort
```

等待用户回答。用户建议回答：`A`。

最后复述两个答案，并打印：

```text
CBTEST:S14:END:double_question:<answer1>,<answer2>
```

预期观察：两次 `AskUserQuestion` 都应进入 `P`，第一次清掉后第二次能再次进入 `P`，不能残留旧 P 或跳过第二次 P。

### S15 question then immediate tool

打印：

```text
CBTEST:S15:START:question_then_tool
```

先提出一个真实单选问题：

```text
是否继续执行读取测试？
A. yes
B. no
```

等待用户回答。用户建议回答：`A`。

如果用户回答 `A` 或 `yes`，立即真实 Read `VERSION.txt`，然后打印：

```text
CBTEST:S15:END:question_then_tool:ok
```

预期观察：`P -> tool_done/answer -> W(Read) -> done` 顺序清晰，不能在 Read 时还显示旧 `P`。

### S16 invalid answer recovery

打印：

```text
CBTEST:S16:START:invalid_answer
```

提出一个真实单选问题：

```text
请选择一个合法选项：
A. red
B. green
C. blue
```

等待用户回答。用户第一轮请故意回答：`Z`。

如果收到非法回答，指出非法，并再次提出同一个问题，等待用户第二轮回答。用户第二轮建议回答：`B`。

最后打印：

```text
CBTEST:S16:END:invalid_answer:<final_answer>
```

预期观察：非法回答后的第二次询问应再次触发等待状态，不能卡住第一次 `P`。

### S17 user says none / cancel-like answer

打印：

```text
CBTEST:S17:START:none_cancel_answer
```

提出一个真实多选问题：

```text
请选择至少一个模块，或者输入 none 表示不选择：
A. ui
B. daemon
C. device
D. docs
```

等待用户回答。用户建议回答：`none`。

收到 `none` 后，不要使用工具，确认用户取消选择，并打印：

```text
CBTEST:S17:END:none_cancel_answer:none
```

预期观察：取消式答案后应清掉 `P`，普通 `idle_prompt` 不应重新把它拉回 `P`。

### S18 stale P across new session

打印：

```text
CBTEST:S18:START:stale_p_setup
```

提出一个真实用户交互问题：

```text
请先不要回答这个问题，用来测试旧 P 清理：
A. keep_waiting
B. answer_now
```

等待用户回答。如果用户没有回答而直接关闭/重启 Claude Code，会留下一个旧等待 session。这个场景需要人工配合：用户可以在问题出现后直接关闭当前 Claude Code，然后在同一目录重新打开 Claude Code，再执行任意新 prompt。

如果用户选择继续在当前会话完成测试，则回答 `B`，然后打印：

```text
CBTEST:S18:END:stale_p_setup:answered
```

预期观察：如果用户关闭旧会话再同目录打开新会话，新的 `UserPromptSubmit` 应 retire 旧 waiting session，不能出现同名旧 `P` 和新 `W/P` 同时显示。

### S19 deny permission path

打印：

```text
CBTEST:S19:START:deny_permission
```

尝试写入文件 `.context/claude_buddy_manual_probe/deny_probe.txt`，内容为：

```text
deny-probe-should-not-exist
```

如果 Claude Code 弹出权限确认，用户这次必须选择拒绝/No/Deny。不要改用别的办法绕过拒绝。拒绝后说明工具是否被取消、是否产生错误，然后打印：

```text
CBTEST:S19:END:deny_permission:denied
```

预期观察：拒绝前如果出现 `permission_prompt` 应进入 `P`；拒绝后必须离开 `P`。如果 Claude Code 产生 `tool_error` 或 `StopFailure`，daemon 可以短暂 `E`，但不能最终卡 `P/E/W`。

### S20 long unanswered question

打印：

```text
CBTEST:S20:START:long_unanswered_question
```

提出一个真实单选问题：

```text
请等待至少 45 秒再回答，用来测试 P 是否能稳定保持：
A. waited
B. answer_immediately
```

等待用户回答。用户必须先等至少 45 秒，然后回答：`A`。

收到回答后打印：

```text
CBTEST:S20:END:long_unanswered_question:<user_answer>
```

预期观察：等待期间应稳定保持 `P`，不能被 cleanup 清掉；用户回答后必须清掉 `P`，回到 `W` 或完成态。

### S21 interrupt long command

打印：

```text
CBTEST:S21:START:interrupt_long_command
```

真实执行一个长命令：

```powershell
powershell -NoProfile -Command "Start-Sleep -Seconds 120; Write-Output should-not-reach"
```

命令开始后，用户需要在 Claude Code UI 里用真实中断方式停止本轮，例如按 Esc / Ctrl+C / Stop，按当前 Claude Code 支持的方式操作。中断后说明 Claude Code 返回了什么状态，然后打印：

```text
CBTEST:S21:END:interrupt_long_command:interrupted
```

预期观察：中断时可能出现 `StopFailure`、`tool_error`、`task_error` 或普通 `stop`。daemon 不能长期卡 `W`，错误态如果出现也必须过期。

### S22 exit after W, before normal stop

打印：

```text
CBTEST:S22:START:exit_during_work
```

先真实 Read `VERSION.txt`，读完后开始输出一段至少 30 秒的纯文本推理等待。用户需要在这 30 秒内关闭当前 Claude Code 会话或输入 `/exit`，不要等它自然完成。

如果用户没有及时退出而本场景自然继续了，则打印：

```text
CBTEST:S22:END:exit_during_work:not_exited
```

预期观察：这是为了验证 `W` 状态下 Claude Code 退出时是否有 `SessionEnd` 或其他结束 hook。如果没有结束 hook，旧 `W` 可能残留；重新打开同目录 Claude Code 后应重点观察是否出现旧 `W` 和新 session 混在一起。

### S23 restart after stale W

这个场景只在 S22 用户真的关闭/退出后执行。用户在同一目录重新打开 Claude Code，然后要求 Claude Code 继续执行本文件，从 S23 开始。

打印：

```text
CBTEST:S23:START:restart_after_stale_w
```

不要使用工具，直接回答：

```text
restart-after-stale-w-ok
CBTEST:S23:END:restart_after_stale_w:ok
```

预期观察：新的 `UserPromptSubmit` 到来后，硬件/daemon 不应同时显示旧 session 的 `W` 和新 session 的状态。如果旧 `W` 还在，这是一个需要修的真实边缘 bug。

### S24 empty answer recovery

打印：

```text
CBTEST:S24:START:empty_answer_recovery
```

提出一个真实单选问题：

```text
请选择一个选项；第一轮请直接回车或发送空答案：
A. one
B. two
C. three
```

等待用户第一轮空答案。如果 Claude Code UI 不允许空答案，用户可以输入一个空白或 ` `。收到空/空白后，指出答案为空，并再次提出同一个问题。用户第二轮建议回答：`C`。

最后打印：

```text
CBTEST:S24:END:empty_answer_recovery:<final_answer>
```

预期观察：空答案后的第二次询问应再次进入 `P`；最终回答后清掉 `P`。

### S25 rapid question then normal idle

打印：

```text
CBTEST:S25:START:rapid_question_idle
```

快速连续执行：

1. 提出真实单选问题：

```text
最后确认是否结束测试：
A. finish
B. keep_running
```

2. 等待用户回答。用户建议回答：`A`。
3. 收到回答后不要再使用工具，立刻打印：

```text
CBTEST:S25:END:rapid_question_idle:<user_answer>
```

打印完成后保持会话打开至少 30 秒，不要主动继续发消息。

预期观察：用户回答后应离开 `P`；随后如果 Claude Code 发 `idle_prompt`，daemon 应忽略它，不能又从完成/空闲回到 `P`。

### S26 P while second Claude Code starts in same cwd

这个场景需要人工开第二个 Claude Code。除 S31/S32 明确要求外，开第二个 Claude Code 时不要重启 daemon，也不要重装插件；生产期望是多个 Claude Code 窗口自然共用同一个 daemon。

在当前 Claude Code 里打印：

```text
CBTEST:S26:START:same_cwd_second_claude_setup
```

提出一个真实单选问题，但用户暂时不要回答：

```text
S26 主窗口问题：请暂时不要回答，保持这个 Claude Code 停在 P：
A. hold
B. release
```

当问题出现后，用户在同一个项目目录再打开第二个 Claude Code，给第二个 Claude Code 发送：

```text
CBTEST:S26B:START:same_cwd_second_claude
不要使用工具，直接回答 same-cwd-second-ok，然后打印 CBTEST:S26B:END:same_cwd_second_claude:ok
```

第二个 Claude Code 完成后，用户回到当前 Claude Code，回答 `B`。当前 Claude Code 收到回答后打印：

```text
CBTEST:S26:END:same_cwd_second_claude_setup:released
```

预期观察：同 cwd 两个 Claude Code 并发时，daemon 可能短时间出现两个 session。主窗口真实等待时显示 `P` 是合理的；第二个窗口不能把主窗口活跃 `P` 错误清掉。主窗口回答后必须离开 `P`。

### S27 close while P, then restart same cwd

这个场景需要人工关闭 Claude Code。

打印：

```text
CBTEST:S27:START:close_while_p
```

提出一个真实单选问题：

```text
S27 请不要回答。看到这个问题后，直接关闭当前 Claude Code 或 Ctrl+C 退出：
A. keep_waiting
B. answer_now
```

用户看到问题后不要回答，直接关闭当前 Claude Code。然后在同一目录重新打开 Claude Code，发送：

```text
CBTEST:S27B:START:restart_after_close_while_p
不要使用工具，直接回答 restart-after-p-close-ok，然后打印 CBTEST:S27B:END:restart_after_close_while_p:ok
```

预期观察：这是生产前重点风险。旧 P 如果没有 `tool_done/session_end`，可能残留。重新打开同 cwd 后，不能长期显示旧 `P` 和新 session 状态混在一起。如果旧 `P` 一直不消失，记录为 bug。

### S28 two Claude Code in different cwd

这个场景需要人工开第二个 Claude Code，并且第二个 Claude Code 工作目录不能是当前项目目录。

在当前 Claude Code 打印：

```text
CBTEST:S28:START:different_cwd_main
```

当前窗口真实执行：

```powershell
powershell -NoProfile -Command "Start-Sleep -Seconds 45; Write-Output main-different-cwd-done"
```

命令运行期间，用户在另一个目录，例如 `C:\tmp\claude_buddy_second_cwd`，打开第二个 Claude Code，发送：

```text
CBTEST:S28B:START:different_cwd_second
真实执行 Bash/PowerShell 命令：powershell -NoProfile -Command "Start-Sleep -Seconds 20; Write-Output second-different-cwd-done"
完成后打印 CBTEST:S28B:END:different_cwd_second:ok
```

当前窗口命令完成后打印：

```text
CBTEST:S28:END:different_cwd_main:ok
```

预期观察：daemon wire 的 `ss` 应同时包含两个 session。两个目录的显示名应可区分；一个完成不应把另一个正在运行的 `W` 清掉。

### S29 same basename, different cwd

这个场景需要人工准备两个同名目录，例如：

```powershell
New-Item -ItemType Directory -Force C:\tmp\cb_a\repo
New-Item -ItemType Directory -Force C:\tmp\cb_b\repo
```

分别在 `C:\tmp\cb_a\repo` 和 `C:\tmp\cb_b\repo` 打开两个 Claude Code。两个窗口都发送类似命令：

```text
CBTEST:S29:START:same_basename
真实执行：powershell -NoProfile -Command "Start-Sleep -Seconds 30; Write-Output same-basename-ok"
完成后打印 CBTEST:S29:END:same_basename:ok
```

预期观察：daemon 对同 basename 不同 cwd 应使用带 suffix 的 display name，例如 `repo` / `repo-xxxx` 或等价可区分形式。硬件端不能因为截断而看起来完全相同。

### S30 BLE disconnect/reconnect while active

这个场景需要真实硬件。

打印：

```text
CBTEST:S30:START:ble_reconnect_active
```

先提出真实单选问题：

```text
S30 请保持这个问题不回答，然后关闭/断开 Claude Buddy 硬件 10 秒，再重新连接：
A. reconnected
B. skip
```

用户断开硬件再连接后，回答 `A`。收到回答后打印：

```text
CBTEST:S30:END:ble_reconnect_active:reconnected
```

预期观察：断连期间 daemon 可以打印 send skipped；重连后应重新推当前状态，不能因为 wire 相同被 dedup 吞掉。

### S31 daemon restart while P

这个场景需要人工重启 daemon。

打印：

```text
CBTEST:S31:START:daemon_restart_while_p
```

提出真实单选问题：

```text
S31 请保持这个问题不回答，然后重启 Claude Buddy daemon：
A. daemon_restarted
B. skip
```

用户在问题出现后重启 daemon。重启后观察硬件/日志，再回到 Claude Code 回答 `A`。收到回答后打印：

```text
CBTEST:S31:END:daemon_restart_while_p:answered_after_restart
```

预期观察：daemon 是内存状态机，重启后可能丢失当前 P，直到下一个 hook 事件到来。这是当前架构的生产限制；需要记录是否可接受。

### S32 daemon restart while W

打印：

```text
CBTEST:S32:START:daemon_restart_while_w
```

真实执行长命令：

```powershell
powershell -NoProfile -Command "Start-Sleep -Seconds 90; Write-Output daemon-restart-while-w-done"
```

命令运行期间，用户重启 daemon。命令完成后打印：

```text
CBTEST:S32:END:daemon_restart_while_w:ok
```

预期观察：daemon 重启会丢掉内存状态。命令完成时如果 Claude Code 后续 hook 仍能发到新 daemon，应恢复到合理状态；如果重启期间没有任何后续 hook，硬件可能不知道当前仍在工作。

### S33 simultaneous W and P

这个场景需要两个 Claude Code。

当前 Claude Code 打印：

```text
CBTEST:S33:START:simultaneous_w_main
```

当前窗口真实执行长命令：

```powershell
powershell -NoProfile -Command "Start-Sleep -Seconds 60; Write-Output simultaneous-w-main-done"
```

命令运行期间，用户在第二个 Claude Code 中发送：

```text
CBTEST:S33B:START:simultaneous_p_second
提出真实单选题：
S33 第二窗口问题：
A. yes
B. no
等待用户回答 A，然后打印 CBTEST:S33B:END:simultaneous_p_second:A
```

当前窗口命令结束后打印：

```text
CBTEST:S33:END:simultaneous_w_main:ok
```

预期观察：wire `ss` 应同时能表达一个 `W` 和一个 `P`。第二窗口回答后 `P` 清掉，但主窗口 `W` 不能被清掉。

### S34 rapid terminal churn

这个场景需要人工快速开关 Claude Code。

连续三次在同一目录打开新的 Claude Code，每次只发送：

```text
CBTEST:S34:START:rapid_terminal_churn
不要使用工具，直接回答 churn-ok，然后打印 CBTEST:S34:END:rapid_terminal_churn:ok
```

每次看到回答完成后立即退出该 Claude Code，再开下一次。

预期观察：daemon 不应积累多个旧 idle session 长期显示。旧完成态最多短暂 `C`，最终应回到空闲或只显示当前活跃 session。

### S35 production soak mini-run

这个场景用于最后 5-10 分钟小型 soak。

打开两个 Claude Code：

1. 主项目目录：反复执行 Read、LS、短 Bash、选择题。
2. 第二目录：每 30 秒执行一个短 Bash 或无工具回答。

主项目目录可以发送：

```text
CBTEST:S35:START:production_soak_main
连续执行 5 轮：每轮 Read VERSION.txt、LS 当前目录、提出一个 A/B 选择题并等待用户回答 A。每轮打印 CBTEST:S35:ROUND:<n>:ok。最后打印 CBTEST:S35:END:production_soak_main:ok
```

第二目录可以发送：

```text
CBTEST:S35B:START:production_soak_second
连续执行 5 轮：每轮 Bash echo soak-second-<n>，间隔约 30 秒。最后打印 CBTEST:S35B:END:production_soak_second:ok
```

预期观察：长时间运行中不能出现永久 `W/P/E`。多 session 数组不能持续增长。硬件断连后重连应恢复当前状态。

### Final summary

最后输出完整总结，包含：

1. 每个 `CBTEST` 场景是否执行。
2. 哪些步骤触发了 `AskUserQuestion`。
3. 哪些步骤触发了 `Notification(permission_prompt)` / `elicitation_dialog`。
4. S08/S09/S14/S15/S16/S17/S20/S24/S25/S26/S30/S31/S33/S35 的用户回答。
5. 是否有任何步骤中断、被拒绝、工具失败、或 UI 异常。
6. S19 是否真实拒绝了权限。
7. S21 是否真实中断了长命令。
8. S22/S23 是否观察到退出后旧 `W` 残留。
9. S26-S35 的多 Claude Code / daemon 重启 / BLE 重连场景是否执行，以及是否看到旧 session 残留。

## 执行后分析

如果使用 capture 脚本，跑完后停止 capture，然后执行：

```powershell
python scripts\analyze_real_wire_capture.py .context\manual_interactive_run.jsonl --expect-event user_prompt --expect-event tool_start --expect-event tool_done --expect-stop --no-stuck-final
```

如果使用生产 daemon 日志监听并且选择题复现问题，请保留：

```text
%TEMP%\ble_daemon.log
```

并记录 S08/S09/S14-S18 时屏幕上看到的 Claude Code UI 行为。

多 Claude Code / 生产前场景还需要额外记录：

```text
S26-S35 每个窗口的 cwd
每个窗口的 Claude Code session 是否重启过
daemon 是否重启过，以及重启的大致时间
硬件是否断连/重连过，以及重连后第一个显示状态
如果出现两个以上 session，记录 wire 中每个 n/s/m
```
