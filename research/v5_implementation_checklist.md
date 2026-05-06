# V5 架构实施清单（上位机部分）

**目标**: 删除设备端审批，改为终端审批 + 纯展示推送

---

## 📋 需要修改的文件清单

### 核心修改（3 个文件）

| 文件 | 当前行数 | 修改类型 | 预计变化 |
|------|---------|---------|---------|
| `daemon/ble_daemon.py` | 601 行 | 🔴 重大修改 | -270 行 |
| `daemon/hook_bridge.py` | 328 行 | 🟡 中等修改 | +50 行 |
| `daemon/transport.py` | 219 行 | 🟡 中等修改 | -80 行 |

### 可选修改（2 个文件）

| 文件 | 当前行数 | 修改类型 | 预计变化 |
|------|---------|---------|---------|
| `daemon/risk_config.py` | 67 行 | 🟢 保留/简化 | 0 或 -67 行 |
| `daemon/pair_device.py` | 144 行 | 🟢 保留不变 | 0 行 |

---

## 🔴 文件 1: `daemon/ble_daemon.py` (重大修改)

### 修改内容

#### 1. 删除审批相关代码（-270 行）

```python
# ── 删除的常量 ──
APPROVAL_TIMEOUT_S = 60           # 删除
MIN_PENDING_RESEND_S = 1.0        # 删除
MAX_PENDING_RESENDS = 5           # 删除
POST_PING_COOLDOWN_S = 0.3        # 删除

# ── 删除的 _Session 字段 ──
class _Session:
    def __init__(self):
        # 删除：
        # self.approval_queue: list = []
        # self.decision_event: Optional[asyncio.Event] = None
        # self.decision_value: Optional[str] = None
        # self.approval_in_progress: bool = False
        # self.pending_resend_count: int = 0

# ── 删除的全局变量 ──
_last_pending_send_ts = 0.0       # 删除

# ── 删除的函数 ──
def _on_transport_recv(msg: dict):
    # 删除整个函数（处理设备审批响应）
    pass

def _resolve_pending_approvals_on_offline():
    # 删除整个函数（离线审批处理）
    pass

def _update_pending_send_ts():
    # 删除整个函数
    pass

# ── 删除 _pusher_tick 中的重发逻辑 ──
async def _pusher_tick(last_pushed_wire):
    # 删除：
    # has_pending = any(sess.approval_queue for sess in _sessions.values())
    # pending_resend_due = ...
    # if pending_resend_due:
    #     ...
```

#### 2. 简化 `_handle_envelope` 函数（-150 行）

```python
async def _handle_envelope(env: dict) -> dict:
    """处理 hook 事件，推送状态到设备（不等待审批）"""
    session_id = env.get("generic", {}).get("session_id", "") or "default"
    sess = _sessions.setdefault(session_id, _Session())

    event = env.get("event") or {}
    kind = event.get("kind", "")
    now = time.time()

    if kind == "tool_start":
        tool = event.get("tool", "")
        tool_use_id = event.get("tool_use_id", "")
        category = event.get("tool_category", "")
        summary = event.get("summary", "")

        # 记录工具状态
        sess.tools[tool_use_id] = {
            "tool": tool,
            "category": category,
            "summary": summary,
            "status": "running",
            "ts": now,
        }
        sess.last_activity_ts = now

        # 推送状态到设备（不等待）
        _mark_dirty()
        
        # 立即返回（不阻塞）
        return {"decision": "once"}

    if kind == "tool_done":
        tool_use_id = event.get("tool_use_id", "")
        if tool_use_id in sess.tools:
            del sess.tools[tool_use_id]
        sess.last_activity_ts = now
        _mark_dirty()
        return {"ok": True}

    if kind == "tool_error":
        tool_use_id = event.get("tool_use_id", "")
        error_msg = event.get("error_msg", "")
        if tool_use_id in sess.tools:
            del sess.tools[tool_use_id]
        _enter_error_state(sess, now, hard_reset=False, error_msg=error_msg, is_interrupt=False)
        return {"ok": True}

    # ... 其他事件处理保持不变 ...

    return {"ok": True}
```

#### 3. 简化 `_to_device_wire` 函数（保持不变）

```python
def _to_device_wire() -> dict:
    """构造 v5 wire 消息（与 v4 格式相同）"""
    now = time.time()
    active = []
    for sid, sess in list(_sessions.items()):
        has_tools = bool(sess.tools)
        recently = sess.last_activity_ts > 0 and (now - sess.last_activity_ts) < SESSION_ACTIVE_TIMEOUT_S
        special = sess.completed_until > now or sess.dizzy_until > now
        if has_tools or recently or special:
            active.append(_session_to_wire(sid, sess))
    return {"ss": active}
```

#### 4. 删除心跳相关代码（-60 行）

```python
# 删除整个 _heartbeat_loop 函数
# 删除 _on_transport_connect 中的心跳逻辑
# 删除 _on_transport_disconnect 中的心跳逻辑
```

---

## 🟡 文件 2: `daemon/hook_bridge.py` (中等修改)

### 修改内容

#### 1. 新增终端审批函数（+50 行）

```python
def _terminal_approval(event: dict) -> str:
    """
    在终端显示审批提示，等待用户输入
    
    返回: "once" | "session" | "deny"
    """
    tool = event.get("tool", "")
    summary = event.get("summary", "")
    risk = event.get("risk_level", "normal")
    
    # 推送提醒到设备（单向，不等待响应）
    try:
        _notify_device_pending(tool, summary)
    except Exception:
        pass  # 设备离线不影响审批
    
    # 在终端显示审批提示
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"⚠️  APPROVAL REQUIRED", file=sys.stderr)
    print(f"Tool: {tool}", file=sys.stderr)
    print(f"Command: {summary}", file=sys.stderr)
    print(f"Risk: {risk}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"[y] Approve once  [s] Approve session  [n] Deny: ", 
          file=sys.stderr, end="")
    sys.stderr.flush()
    
    try:
        choice = input().strip().lower()
        decision_map = {"y": "once", "s": "session", "n": "deny"}
        return decision_map.get(choice, "deny")
    except (EOFError, KeyboardInterrupt):
        return "deny"


def _notify_device_pending(tool: str, summary: str):
    """推送 PENDING 状态到设备（单向通知）"""
    envelope = {
        "type": "event",
        "v": 2,
        "event": {
            "kind": "tool_pending",  # 新增事件类型
            "tool": tool,
            "summary": summary[:20],
        },
        "generic": {
            "session_id": "",
            "cwd": "",
            "hook_event_name": "PreToolUse",
            "transcript_path": "",
            "permission_mode": "",
        }
    }
    
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5) as s:
            s.sendall(json.dumps(envelope).encode("utf-8"))
            # 不等待响应，立即关闭
    except Exception:
        pass  # 忽略错误
```

#### 2. 修改 `main()` 函数（+10 行）

```python
def main():
    raw = sys.stdin.read(MAX_STDIN_BYTES).strip()
    if not raw:
        print(json.dumps({}))
        return
    
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({}))
        return

    hook = event.get("hook_event_name", "")
    normalize = NORMALIZERS.get(hook, _normalize_fallback)
    envelope = normalize(event)

    # 判断是否需要审批
    if hook == "PreToolUse":
        tool = event.get("tool_name", "")
        if tool in APPROVAL_TOOLS:
            # 终端审批（阻塞等待用户输入）
            decision = _terminal_approval({
                "tool": tool,
                "summary": _hint_from_tool_input(event.get("tool_input", {})),
                "risk_level": _classify_risk(tool, event.get("tool_input", {}))
            })
            
            if decision == "deny":
                print(json.dumps({
                    "decision": "block",
                    "reason": "Denied by user"
                }))
            else:
                print(json.dumps({}))
            return

    # 非审批工具，推送状态到 daemon
    _call_daemon(envelope)
    print(json.dumps({}))
```

#### 3. 删除风险分级相关代码（可选）

```python
# 如果不需要在终端显示风险等级，可以删除：
# - _classify_risk() 函数
# - CRITICAL_PATHS / CRITICAL_BASH_PATTERNS 常量
# - risk_config.py 导入
```

---

## 🟡 文件 3: `daemon/transport.py` (中等修改)

### 修改内容

#### 1. 删除心跳相关代码（-60 行）

```python
class BleTransport(Transport):
    def __init__(self):
        self._client = None
        self._connected = False
        # 删除：
        # self._device_online = False
        # self._last_pong_ts = 0.0
        # self._last_ping_ts = 0.0
        
        self._rx_buf = ""
        self._send_lock = None

    async def start(self, on_recv, on_connect, on_disconnect):
        self._on_recv = on_recv
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._send_lock = asyncio.Lock()
        
        # 只保留连接循环
        await self._connect_loop()
        
        # 删除：
        # await asyncio.gather(
        #     self._connect_loop(),
        #     self._heartbeat_loop(),
        # )

    def connected(self) -> bool:
        """BLE 是否已连接"""
        return self._connected

    # 删除整个函数：
    # def device_online(self) -> bool:
    #     return self._device_online

    # 删除整个函数：
    # async def _heartbeat_loop(self):
    #     ...
```

#### 2. 简化 `_on_ble_notify` 回调（-20 行）

```python
def _on_ble_notify(self, sender, data: bytearray):
    """处理 BLE 接收数据"""
    self._rx_buf += data.decode(errors="ignore")
    while "\n" in self._rx_buf:
        line, self._rx_buf = self._rx_buf.split("\n", 1)
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            
            # 删除 pong 处理：
            # if msg.get("ack") == "pong":
            #     self._last_pong_ts = time.time()
            #     if not self._device_online:
            #         print("[heartbeat] device back online")
            #         self._device_online = True
            #     continue
            
            # 直接转发所有消息
            if self._on_recv:
                self._on_recv(msg)
        except Exception:
            pass
```

---

## 🟢 文件 4: `daemon/risk_config.py` (可选修改)

### 选项 A: 保留（用于终端审批显示）

```python
# 保持不变，用于在终端显示风险等级
CRITICAL_PATHS = {...}
CRITICAL_BASH_PATTERNS = [...]
SAFE_TOOLS = {...}
APPROVAL_TOOLS = {...}
```

### 选项 B: 删除（如果不需要风险分级）

```python
# 完全删除文件
# hook_bridge.py 中使用固定的 APPROVAL_TOOLS 列表
```

---

## 🟢 文件 5: `daemon/pair_device.py` (保持不变)

**无需修改**，配对功能与审批无关。

---

## 📊 修改统计

| 文件 | 当前行数 | 删除 | 新增 | 修改后 | 变化 |
|------|---------|------|------|--------|------|
| `ble_daemon.py` | 601 | -270 | +0 | ~331 | -45% |
| `hook_bridge.py` | 328 | -20 | +70 | ~378 | +15% |
| `transport.py` | 219 | -80 | +0 | ~139 | -37% |
| `risk_config.py` | 67 | 0 | +0 | 67 | 0% |
| `pair_device.py` | 144 | 0 | +0 | 144 | 0% |
| **总计** | **1359** | **-370** | **+70** | **~1059** | **-22%** |

---

## 🔧 实施步骤

### 阶段 1: 准备工作（1 小时）

1. **备份当前代码**
   ```bash
   git checkout -b v5-display-only
   git add -A
   git commit -m "backup: v4 审批版本"
   ```

2. **创建测试环境**
   ```bash
   # 复制 daemon 目录用于对比
   cp -r daemon daemon_v4_backup
   ```

---

### 阶段 2: 修改 `transport.py`（30 分钟）

**优先级**: 🔴 高（基础依赖）

1. 删除 `_heartbeat_loop()` 函数
2. 删除 `device_online()` 方法
3. 删除 `_on_ble_notify` 中的 pong 处理
4. 简化 `start()` 函数

**测试**:
```bash
python daemon/transport.py  # 确保无语法错误
```

---

### 阶段 3: 修改 `ble_daemon.py`（2 小时）

**优先级**: 🔴 高（核心逻辑）

1. 删除审批相关常量
2. 删除 `_Session` 中的审批字段
3. 简化 `_handle_envelope` 函数
4. 删除 `_on_transport_recv` 函数
5. 删除 `_resolve_pending_approvals_on_offline` 函数
6. 简化 `_pusher_tick` 函数

**测试**:
```bash
python daemon/ble_daemon.py --stub --offline
# 观察是否正常启动
```

---

### 阶段 4: 修改 `hook_bridge.py`（1 小时）

**优先级**: 🟡 中（用户交互）

1. 新增 `_terminal_approval()` 函数
2. 新增 `_notify_device_pending()` 函数
3. 修改 `main()` 函数

**测试**:
```bash
# 模拟 PreToolUse hook
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls"}}' | python daemon/hook_bridge.py
# 应该显示终端审批提示
```

---

### 阶段 5: 集成测试（1 小时）

1. **启动 daemon**
   ```bash
   python daemon/ble_daemon.py --stub --offline
   ```

2. **模拟 hook 调用**
   ```bash
   python scripts/sim_hooks.py --stub
   ```

3. **验证行为**
   - ✅ 非审批工具立即执行
   - ✅ 审批工具显示终端提示
   - ✅ 状态正确推送到设备（stub 模式打印）
   - ✅ 无心跳消息

---

### 阶段 6: 清理和文档（30 分钟）

1. **删除无用代码**
   ```bash
   # 搜索并删除注释掉的代码
   grep -r "# 删除" daemon/
   ```

2. **更新文档**
   - 更新 `README.md`
   - 更新 `research/hook_to_device_mapping_v1.md`

3. **提交代码**
   ```bash
   git add -A
   git commit -m "feat: v5 纯展示模式 - 删除设备审批和心跳"
   ```

---

## ⚠️ 注意事项

### 1. 向后兼容

如果需要保留 v4 兼容性：

```python
# ble_daemon.py 添加环境变量开关
APPROVAL_MODE = os.environ.get("APPROVAL_MODE", "terminal")  # "terminal" | "device"

if APPROVAL_MODE == "device":
    # 使用旧的设备审批逻辑
    pass
else:
    # 使用新的终端审批逻辑
    pass
```

### 2. 测试覆盖

需要更新的测试文件：
- `tests/test_daemon_state.py` - 删除审批相关测试
- `tests/test_offline_approval.py` - 删除整个文件
- `tests/test_e2e_stub.py` - 更新审批流程测试

### 3. 设备端兼容

设备端仍可解析 v5 协议（与 v4 格式相同），只需：
- 删除审批 UI
- 删除 pong 响应
- 保留状态解析和动画渲染

---

## ✅ 验收标准

### 功能验收

- [ ] 非审批工具（Read/Glob/Grep）立即执行
- [ ] 审批工具（Bash/Write/Edit）显示终端提示
- [ ] 终端审批 y/n 正确响应
- [ ] 状态正确推送到设备（stub 模式可见）
- [ ] 无 ping/pong 消息
- [ ] 多 session 支持正常

### 性能验收

- [ ] daemon 启动时间 < 1s
- [ ] 审批响应时间 < 100ms（用户输入后）
- [ ] BLE 流量减少 > 30%
- [ ] CPU 占用降低 > 10%

### 代码质量

- [ ] 无语法错误
- [ ] 无 lint 警告
- [ ] 代码减少 > 300 行
- [ ] 测试通过率 100%

---

## 📝 总结

**需要修改的文件**（上位机）:
1. 🔴 `daemon/ble_daemon.py` - 删除审批逻辑（-270 行）
2. 🟡 `daemon/hook_bridge.py` - 新增终端审批（+50 行）
3. 🟡 `daemon/transport.py` - 删除心跳（-80 行）
4. 🟢 `daemon/risk_config.py` - 可选保留（0 行）
5. 🟢 `daemon/pair_device.py` - 保持不变（0 行）

**总工作量**: 约 **5 小时**（含测试）

**预期收益**:
- 代码减少 22%（-300 行）
- 审批速度提升 ∞（60s → 即时）
- 架构复杂度降低 40%
