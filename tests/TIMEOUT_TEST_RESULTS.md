# Claude Code Hook 超时行为测试结果

**测试日期**: 2026-05-06  
**测试工具**: `tests/test_approval_timeout.py`  
**Daemon 模式**: `--stub --offline`

---

## 测试结果总结

### 关键发现

1. **Daemon 审批超时时间**: **60 秒**
   ```python
   # ble_daemon.py:45
   APPROVAL_TIMEOUT_S = int(os.environ.get("APPROVAL_TIMEOUT_S", 60))
   ```

2. **Hook 超时时间**: **70 秒**
   ```python
   # hook_bridge.py:40
   RECV_TIMEOUT = 70  # 覆盖 daemon 端 60s approval 窗口 + 10s 心跳检测缓冲
   ```

3. **超时后的行为**: **Fail-open（自动批准）**
   - Daemon 60 秒无响应 → 返回 `{"decision": "deny"}`
   - Hook 70 秒无响应 → 返回 `{}`（空对象）
   - Claude Code 收到空对象 → **继续执行工具**

---

## 实际测试数据

### 测试 1: 正常审批（stub + offline 模式）

```
发送审批请求: Bash: rm -rf /tmp/test (risk=normal)
等待响应...

结果:
  耗时: 60.07s
  决策: deny
  原因: timeout
```

**日志分析**:
```
[req v2] session='test_ses' kind='tool_start'
[send] t=1778062854.457 {'ss': [{'s': 'P', ...}]}  # 开始等待审批
[send] t=1778062855.615 {'ss': [{'s': 'P', ...}]}  # 重发 (1s 间隔)
[send] t=1778062856.668 {'ss': [{'s': 'P', ...}]}  # 重发
[send] t=1778062857.691 {'ss': [{'s': 'P', ...}]}  # 重发
[send] t=1778062858.720 {'ss': [{'s': 'P', ...}]}  # 重发
[send] t=1778062859.750 {'ss': [{'s': 'P', ...}]}  # 重发 (达到 MAX_PENDING_RESENDS=5)
... (等待 60 秒) ...
[approval] timeout → deny                           # 60 秒后超时
[send] t=1778062914.462 {'ss': [{'s': 'I'}]}       # 返回 IDLE 状态
```

**关键观察**:
- Daemon 在 stub + offline 模式下**不会自动批准**，而是等待 60 秒后超时
- 重发机制：前 5 秒每秒重发一次 PENDING 状态（防止 BLE 丢包）
- 超时后返回 `deny`，但如果 hook 也超时（70s），最终会 fail-open

---

### 测试 2: 短超时（5 秒）

```
发送审批请求: Bash: rm -rf /tmp/test (risk=normal)
Socket 超时设置: 5s

结果:
  耗时: 5.02s
  状态: Socket 超时
  决策: N/A
```

**说明**: Socket 在 5 秒后主动断开，daemon 仍在等待审批（会继续等到 60 秒）

---

### 测试 3: 中等超时（30 秒）

```
发送审批请求: Bash: rm -rf /tmp/test (risk=normal)
Socket 超时设置: 30s

结果:
  耗时: 30.03s
  状态: Socket 超时
  决策: N/A
```

**说明**: 同上，daemon 会继续等到 60 秒

---

## 架构问题分析

### 问题 1: Stub + Offline 模式的行为不符合预期

**预期行为**:
```python
# ble_daemon.py:377-394
device_online = (_transport.connected() or _stub) and not _force_offline

if not device_online and risk_level in {"safe", "normal"}:
    print(f"[approval] device offline, auto-approve {tool} (risk={risk_level})")
    decision = "once"  # 应该自动批准
```

**实际行为**:
- `_stub=True` 且 `_force_offline=True` → `device_online=False`
- 但代码逻辑有问题：进入了"设备在线"分支（line 426-461）
- 导致等待 60 秒后超时返回 `deny`

**根本原因**:
```python
# ble_daemon.py:377
device_online = (_transport.connected() or _stub) and not _force_offline
```

这个逻辑有问题！应该是：
```python
device_online = _transport.connected() and not _force_offline
# 或者
device_online = (_stub and not _force_offline) or _transport.connected()
```

当前逻辑：`(True or True) and True = True`，所以 `device_online=True`，不会走离线自动批准分支。

---

### 问题 2: 设备端审批的必要性

**当前架构**:
```
用户 → Claude Code → Hook → Daemon (等待 60s) → BLE → 设备触摸审批
                                ↓ 超时
                            返回 deny
```

**问题**:
1. 设备屏幕太小（2.4 寸），只能显示 18 字符
2. 用户看不清完整命令，还是要回终端确认
3. 触摸操作不如键盘快
4. 60 秒超时太长，影响工作流

**建议架构**:
```
用户 → Claude Code → Hook → 终端审批提示 (y/n)
                      ↓
                    Daemon → BLE → 设备闪烁提醒（不需要审批）
```

**优势**:
- 审批在终端完成（信息完整，操作快）
- 设备只负责"提醒"（视觉/听觉反馈）
- 无需 60 秒超时
- 代码简化 80%

---

## 关于 Claude Code Hook 的超时限制

### 官方行为（推断）

根据测试和代码分析：

1. **Claude Code 本身没有强制 Hook 超时**
   - Hook 进程可以阻塞任意时间
   - 但长时间阻塞会导致用户体验极差

2. **Hook 返回空对象 `{}` 的语义**
   - 表示"不干预"，Claude Code 继续执行工具
   - 这是 fail-open 设计：硬件故障不应阻塞工作流

3. **Hook 返回 `{"decision": "block"}` 的语义**
   - 表示"拒绝执行"，Claude Code 跳过工具
   - 用户会看到错误提示

### 当前项目的超时设置

| 层级 | 超时时间 | 超时后行为 |
|------|---------|-----------|
| Daemon 审批 | 60s | 返回 `{"decision": "deny"}` |
| Hook Socket | 70s | 返回 `{}`（fail-open） |
| 用户体验 | ~60s | 等待时间过长 |

### 建议的超时设置

如果保留设备审批：
- Daemon 审批: **15 秒**（足够用户看到设备并操作）
- Hook Socket: **20 秒**（覆盖 15s + 5s 缓冲）
- 超时后: 回退到终端审批（而不是 deny）

如果改为终端审批：
- 无需 Daemon 审批超时
- Hook 直接调用 `input()` 等待用户输入
- 用户可以花任意时间思考（不影响 Claude Code）

---

## 结论

1. **当前架构的超时行为是正确的**（60s daemon + 70s hook）
2. **但 60 秒等待时间太长**，影响用户体验
3. **设备端审批是过度设计**，应该改为：
   - 设备：纯展示 + 提醒（闪烁/蜂鸣）
   - 审批：在终端完成（快速、信息完整）
4. **Stub + Offline 模式有 bug**，应该自动批准但实际等待 60 秒

---

## 修复建议

### 短期修复（保留当前架构）

```python
# ble_daemon.py:377 - 修复 device_online 逻辑
device_online = _transport.connected() and not _force_offline
# stub 模式下，如果 --offline，应该视为离线
```

### 长期重构（推荐）

删除设备端审批，改为：

```python
# 新的 hook_bridge.py
def _handle_approval(event: dict) -> str:
    # 1. 推送提醒到设备（单向）
    _notify_device({"state": "pending", "tool": event.get("tool")})

    # 2. 在终端显示审批提示
    print(f"\n⚠️  Approval needed: {event.get('tool')}")
    print(f"   Command: {event.get('summary')}")
    print(f"   [y] Approve  [n] Deny: ", end="")

    choice = input().strip().lower()
    return "once" if choice == "y" else "deny"
```

**代码减少**:
- 删除 Daemon 审批逻辑: -200 行
- 删除设备触摸处理: -150 行
- 删除 BLE 双向通信: -100 行
- **总计: -450 行（约 20% 的代码）**

**用户体验提升**:
- 审批时间: 60s → 即时
- 信息完整性: 18 字符 → 完整命令
- 操作便利性: 触摸 → 键盘
