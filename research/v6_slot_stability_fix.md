# v6 协议：slot 字段修复槽位漂移问题

## 一、问题背景

### 1.1 bug 现象

**复现步骤：**

1. 开启两个以上 Claude Code session，设备端 Tab 显示 S1=projA、S2=projB
2. projA 停止对话，等待 >10 秒
3. daemon 清理 projA，设备端 S1 还原为空槽
4. projA 重新发起对话
5. 观察设备端 projA 出现在哪个 Tab

**预期行为：** projA 重新出现在 S1（原位置）

**实际行为：** projA 填入当前 `_sessions.items()` 插入顺序的第一个空槽，位置不确定

**副作用：** 槽位漂移时，device 端检测到"名字变化"，清空两个槽的历史记录

---

### 1.2 根本原因（两层）

#### daemon 侧

`_sessions` 是普通 dict，无 slot 编号概念。session 超时被 `del _sessions[sid]`，重新激活时 `setdefault` 创建新对象，插入到 dict 末尾。`_to_device_wire()` 按 `items()` 遍历，输出顺序随 dict 插入顺序变化，没有稳定的 slot → sid 映射。

**源码证据：**
- `daemon/ble_daemon.py:87` `_sessions: dict = {}` — 普通 dict，无 slot 概念
- `daemon/ble_daemon.py:209-215` `_to_device_wire` 用 `list(_sessions.items())` 遍历 — wire 顺序 = dict 插入顺序
- `daemon/ble_daemon.py:305-312` cleanup `del _sessions[sid]` — 删后 setdefault 重建会插到 dict 末尾

#### 协议层

wire entry 只有 `{"n", "s", "m"}` 字段，不带 sid 也不带 slot 字段。协议层无归属信息。

**源码证据：**
- `daemon/ble_daemon.py:170-172` wire entry 只有 `{"n", "s", "m"}`
- `device/protocol.py:7, 29-40` 解析侧只读 `n/s/m/t/h`

#### device 侧

`_update_tab(i, sess)` 按数组下标喂，device 端只能盲映射。

**源码证据：**
- `device/display_renderer.py:482-485` `_update_tab(i, sess)` 按数组下标喂

---

### 1.3 影响范围

#### 用户体验影响

1. **方向感混乱**：熟悉的 session 突然出现在不同 Tab 位置
2. **历史记录丢失**：槽位漂移时触发双向互清，两个 session 的对话历史一次性全毁
3. **长按清历史失效**：若 session 恰好在此期间超时重连，槽位也会漂移

#### 历史被清的机制

`device/display_renderer.py:577-580`：

```python
if sess.name != self._slot_names[index]:
    self._histories[index].clear()
    self._containers[index].clean()
    self._slot_names[index] = sess.name
```

slot index 不变、name 变了就清历史。双 session 漂移场景下两个槽都会被擦：

```
初始     : slot_names=[projA, projB]  histories=[A 的对话, B 的对话]
A 超时清 : wire=[projB]           → slot 0 "projA"→"projB" 触发 clear → A 历史没了
A 重连后 : wire=[projB, projA]    → slot 1 "projB"→"projA" 触发 clear → B 历史也没了
```

---

## 二、真实使用场景

### 场景 1：单项目反复沉默重连

**用户行为：**
- 打开 `G:\test` 项目的 Claude Code
- 发起对话，stop 后沉默 >10s
- 再次发起对话

**v5 行为（有 bug）：**
```
t=0   wire: [{"n":"test","s":"W"}]           device: [S1=test]
t=10  wire: [{"n":"test","s":"I"}]           device: [S1=test]
t=20  wire: []                                device: [S1=空]  ← slot_names[0]=""
t=30  wire: [{"n":"test","s":"W"}]           device: [S1=test] ← 名字变化，清历史
```

**影响：** 历史记录被清空，但槽位不漂移（只有一个 session）

---

### 场景 2：多项目同时活跃，其中一个沉默重连

**用户行为：**
- 同时打开 projA 和 projB
- projA stop 后沉默 >10s
- projA 重新发起对话

**v5 行为（有 bug）：**
```
t=0   wire: [projA(W), projB(W)]             device: [S1=projA] [S2=projB]
t=10  wire: [projA(I), projB(W)]             device: [S1=projA] [S2=projB]
t=20  wire: [projB(W)]                       device: [S1=projB] [S2=空]  ← projB 漂到 S1
t=30  wire: [projB(W), projA(W)]             device: [S1=projB] [S2=projA] ← projA 漂到 S2
```

**影响：** 
- projA 从 S1 漂到 S2
- projB 从 S2 漂到 S1
- 两个 session 的历史都被清空

---

### 场景 3：同一目录多窗口

**用户行为：**
- 在 `G:\test` 目录下打开 3 个 Claude Code 窗口
- 窗口 A、B、C 同时活跃

**v5 行为：**
```
wire: [
  {"n":"test-1167","s":"W"},
  {"n":"test-15d7","s":"W"},
  {"n":"test-2855","s":"W"}
]

device: [S1=test-1167] [S2=test-15d7] [S3=test-2855]
```

**问题：** 如果任意一个窗口沉默重连，会触发槽位重排，所有窗口的历史都可能被清

---

### 场景 4：关窗口再开

**用户行为：**
- 打开 projA，工作一段时间
- 关掉 Claude Code 窗口
- 重新打开 projA 目录的 Claude Code

**v5 行为：**
```
第一次打开：SID = uuid-001 → display_name = "projA"
关窗口
第二次打开：SID = uuid-002 → display_name = "projA"  ← 新 SID
```

**问题：** daemon 认为是两个不同的 session，但 display_name 相同，device 端无法区分

---

## 三、方案设计

### 3.1 方案对比

#### 方案 A：daemon 维护 cwd → slot 映射

**思路：** daemon 增加 `_slot_map: dict[str, int]`（cwd → slot 0-4），cleanup 删除 session 对象时不删映射，重连时按 cwd 找回原槽位。

**优点：**
- daemon 侧改动集中，device/protocol 不动
- cwd 是项目的真正身份，用它做映射 key 合理

**缺点：**
- 同一 cwd 开多个窗口时，它们会抢同一个槽位（后来的覆盖前面的）
- 需要处理槽位满了（5个）之后的 LRU 淘汰逻辑
- cwd 不能真实反映"用户在处理哪个项目"（窗口里可能 cd 到其他目录）

---

#### 方案 B：wire 协议加 slot 字段（槽位编号）

**思路：** wire entry 加 `"slot": 0-4` 字段，daemon 维护 cwd → slot 映射，device 端按 slot 字段而非数组下标映射。

**优点：**
- 语义明确，wire 里直接带槽位信息

**缺点：**
- daemon 还在"管槽位"，违反职责分离原则（daemon 应该推送状态，不是管理 UI 布局）
- 协议改动，daemon 和 device 都要改

---

#### 方案 C：wire 协议加 slot 字段（session 唯一标识）

**思路：** wire entry 加 `"slot": "abc123"` 字段（SID 的 hash 或后 N 位），device 端维护 `slot_id → 槽位编号` 的映射。

**优点：**
- daemon 不管槽位，只管推送状态 + 唯一标识
- 职责清晰：daemon = 状态源，device = 展示层
- 支持同一 cwd 多窗口（不同 SID 不冲突）
- 语义正确（slot 代表 session，不是 project）

**缺点：**
- device 端逻辑变复杂（要维护 slot_id → 槽位映射）
- MicroPython 内存有限，多一层映射表（但 ~100 字节对 8MB RAM 可接受）

---

#### 方案 D：device 侧按名字查找槽位

**思路：** device 端不再盲映射，而是先在 `slot_names` 里找"这个名字之前在哪个槽"。

**优点：**
- 只改 device 端，daemon 不动

**缺点：**
- 治标不治本：daemon 推空 wire 时，device 端 `slot_names` 被清成 `""`，下次重连还是找不到
- 同名冲突（test-1167 vs test-2855）时无法区分

---

### 3.2 最终选择：方案 C（slot = SID 后 8 位）

**理由：**

1. **根因在协议层**：wire 用数组下标隐式表达槽位，但没有归属信息，导致 daemon 和 device 之间没有"这个 session 应该在哪个槽"的共识
2. **SID 是 session 的真正身份**：用户关心的是"哪个对话窗口"，不是"哪个目录"
3. **支持同一 cwd 多窗口**：不同 SID 不冲突，各占一个槽
4. **职责分离**：daemon 只负责推送状态 + 标识，device 负责槽位分配

**slot 字段语义：** session 的稳定唯一标识符（SID 后 8 位），不是槽位编号

**冲突概率：** 16^8 = 42 亿种可能，5 个 session 同时活跃冲突概率 < 0.000001%

---

## 四、v6 协议定义

### 4.1 wire 格式

**PC → 设备（多 session wire）：**

```json
{
  "ss": [
    {"n": "test-1167", "s": "W", "m": "Bash: ls", "slot": "cd501167"},
    {"n": "test-15d7", "s": "I",                  "slot": "fa8715d7"},
    {"n": "MicroPy_",  "s": "C",                  "slot": "4cd50979"}
  ]
}
```

---

### 4.2 字段说明

| 字段 | 类型 | 必填 | 含义 | 示例 | v5 → v6 变化 |
|------|------|------|------|------|-------------|
| `n` | string | 是 | session 显示名 | `"test-1167"` | 不变 |
| `s` | string | 是 | 状态码 | `"W"/"P"/"C"/"E"/"I"` | 不变 |
| `m` | string | 否 | 工具名+摘要（仅 W 状态） | `"Bash: ls"` | 不变 |
| `slot` | string | **是** | **session 唯一标识（SID 后 8 位）** | `"cd501167"` | **新增** |

**状态码枚举（不变）：**
- `I`：空闲
- `W`：执行中
- `P`：待审批提醒
- `C`：完成
- `E`：出错

---

### 4.3 与 v5 的差异

| 对比项 | v5 | v6 |
|--------|----|----|
| wire 顺序依赖 | ✅ 依赖数组下标 | ❌ 不依赖，可乱序 |
| 归属信息 | ❌ 无 | ✅ slot 字段 |
| 槽位稳定性 | ❌ dict 顺序变就漂移 | ✅ 同一 SID 永远同一槽 |
| 历史误清 | ❌ 漂移时双向互清 | ✅ 不漂移就不误清 |
| 同目录多窗口 | ⚠️ 能显示但会漂移 | ✅ 各占一槽，不漂移 |
| 向后兼容 | - | ✅ 老 device 忽略 slot 字段 |

---

### 4.4 slot 字段计算规则

**daemon 侧：**

```python
def _get_slot_id(sid: str) -> str:
    """从 SID 提取后 8 位作为 slot 标识"""
    compact_sid = sid.replace("-", "")  # 去掉连字符
    return compact_sid[-8:] if len(compact_sid) >= 8 else compact_sid
```

**示例：**

```
SID: 27f7bc8f-cc50-409b-95e3-14b498641167
去连字符: 27f7bc8fcc50409b95e314b498641167
后 8 位: cd501167
```

---

## 五、实现细节

### 5.1 daemon 侧改动

#### 修改文件：`daemon/ble_daemon.py`

**改动点 1：修改 `_session_to_wire()` 函数**

```python
def _session_to_wire(sid: str, sess: _Session) -> dict:
    now = time.time()
    result = {"n": sess.display_name or "?"}
    
    # 新增：计算 slot（SID 后 8 位）
    compact_sid = sid.replace("-", "")
    result["slot"] = compact_sid[-8:] if len(compact_sid) >= 8 else compact_sid
    
    # 原有状态判断逻辑（不变）
    if sess.dizzy_until > now:
        result["s"] = "E"
        return result
    if sess.waiting > 0:
        result["s"] = "P"
        return result
    for t in sess.tools.values():
        if t["status"] == "running":
            summary = t.get("summary", "")[:50]
            m = f"{t['tool']}: {summary}" if summary else t["tool"]
            result["s"] = "W"
            result["m"] = m[:60]
            return result
    if sess.completed_until > now:
        result["s"] = "C"
        return result
    if sess.turn_active:
        result["s"] = "W"
        return result
    if sess.last_tool_start_ts > 0 and (now - sess.last_tool_start_ts) < 0.4:
        result["s"] = "W"
        return result
    result["s"] = "I"
    return result
```

**改动量：** 3 行代码

**改动点 2：`_to_device_wire()` 不需要改**

原有逻辑已经调用 `_session_to_wire()`，会自动带上 slot 字段。

---

### 5.2 device 侧改动

#### 改动文件 1：`device/protocol.py`

**修改 `SessionStatus` 类：**

```python
class SessionStatus:
    """v6 wire 中单个 session 的状态。"""
    def __init__(self, d: dict):
        s = d.get("s", S_IDLE)
        self.name        = d.get("n", "?")
        self.slot        = d.get("slot", "")  # 新增：slot 标识（SID 后 8 位）
        self.running     = 1 if s == S_WORKING else 0
        self.waiting     = 1 if s == S_PENDING else 0
        self.completed   = s == S_DONE
        self.error       = "!" if s == S_ERROR else ""
        self.interrupted = False
        self.msg         = d.get("m", "")
        self.prompt      = {"tool": d.get("t", ""), "hint": d.get("h", ""), "id": ""} if s == S_PENDING else None
```

**改动量：** 1 行代码

---

#### 改动文件 2：`device/display_renderer.py`

**改动点 1：`__init__()` 新增槽位映射表**

```python
class DisplayRenderer:
    def __init__(self):
        # ... 原有初始化代码 ...
        
        # 新增：slot_id → 槽位编号 的映射
        self._slot_assignments: dict = {}  # slot_id(str) → slot_index(int)
```

---

**改动点 2：重写 `render()` 方法**

```python
async def render(self, msg):
    if msg is None or isinstance(msg, dict):
        return
    
    # 标记哪些槽位被更新了
    slot_updated = [False] * MAX_SESSIONS
    
    for sess in msg.sessions:
        slot_id = sess.slot
        if not slot_id:  # 向后兼容：无 slot 字段时跳过
            continue
        
        # 查找或分配槽位
        if slot_id in self._slot_assignments:
            slot_index = self._slot_assignments[slot_id]
        else:
            # 新 slot_id，分配第一个空槽
            slot_index = self._find_empty_slot()
            self._slot_assignments[slot_id] = slot_index
            _log.info("slot[%d] assigned to slot_id=%s", slot_index, slot_id)
        
        # 更新槽位
        self._update_tab(slot_index, sess)
        self._update_history(slot_index, sess)
        slot_updated[slot_index] = True
        
        # 状态跳变检测 → 触发语音
        cur = _sess_state(sess)
        prev = self._prev_states.get(sess.name)
        if cur != prev:
            self._push_voice_history(sess, cur)
            if cur in (S_DONE, S_ERROR, S_PENDING):
                await self._voice.trigger(self._history, sess, cur)
            self._prev_states[sess.name] = cur
    
    # 清空未更新的槽（wire 里没有的 session）
    for i in range(MAX_SESSIONS):
        if not slot_updated[i]:
            self._update_tab(i, None)
    
    self._update_main()
```

---

**改动点 3：新增 `_find_empty_slot()` 方法**

```python
def _find_empty_slot(self) -> int:
    """找第一个空槽，满了就 LRU 淘汰。"""
    # 找空槽
    for i in range(MAX_SESSIONS):
        if self._slot_names[i] == "":
            return i
    
    # 满了，淘汰最久未更新的槽（简单实现：返回 0）
    # TODO: 可以改成真正的 LRU
    _log.warning("all slots full, reusing slot 0")
    # 清理被淘汰槽的映射
    for slot_id, idx in list(self._slot_assignments.items()):
        if idx == 0:
            del self._slot_assignments[slot_id]
            break
    return 0
```

---

#### 改动文件 3：`device/light_renderer.py`

**不需要改动**

clock 版本只遍历 `msg.sessions` 计算 dominant 状态，不关心槽位。

---

### 5.3 向后兼容策略

#### 老 device 收到新 wire（带 slot）

```python
# protocol.py
self.slot = d.get("slot", "")  # 默认空字符串

# 老 render() 按数组下标映射，忽略 slot 字段
# 行为退化成 v5，但不会崩溃
```

#### 新 device 收到老 wire（无 slot）

```python
# render() 里检测
if not slot_id:  # slot 为空字符串
    continue  # 跳过，不处理
```

**建议：** 同时升级 daemon 和 device，避免兼容性问题。

---

### 5.4 改动汇总

| 文件 | 改动类型 | 改动量 | panel 需要 | clock 需要 |
|------|---------|--------|-----------|-----------|
| `daemon/ble_daemon.py` | 修改 `_session_to_wire()` | +3 行 | ✅ | ✅ |
| `device/protocol.py` | `SessionStatus` 加 `self.slot` | +1 行 | ✅ | ✅ |
| `device/display_renderer.py` | 重写 `render()`，新增 `_find_empty_slot()` | ~60 行 | ✅ | ❌ |
| `device/light_renderer.py` | 不需要改 | 0 行 | ❌ | ❌ |

**总改动量：** daemon 3 行，device ~62 行
