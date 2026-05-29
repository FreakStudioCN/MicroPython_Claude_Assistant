# MicroPython Claude Assistant 项目说明

## 语言偏好

**请始终使用中文回复**，除非用户明确要求使用英文。

## 项目简介

这是一个将 Claude Code 的工具执行状态实时可视化为 ESP32 桌宠设备的项目。v0.9.0 MVP，双形态可用。

## 硬件形态

- **clock**：ESP32-C3 + WS2812×2 + MAX98357A 扬声器，灯光状态 + 豆包TTS语音播报
- **panel**：ESP32-S3 + ST7789 2.4寸屏 + LVGL，小人动画 + 多session历史记录

## 项目文档

完整架构、安装部署、协议说明、测试指南见 **README.md**（项目根目录）。

## 架构

- **daemon/**：PC 端守护进程（BLE 桥接、状态机、5Hz 推送）
- **device/**：ESP32 固件（BLE 通信、灯光/语音/屏幕渲染，纯展示，无审批）
  - `state.py`：wire 状态码常量（`S_IDLE/S_WORKING/S_PENDING/S_DONE/S_ERROR`）+ `sess_state()` + 状态转换逻辑
  - `config.py`：所有可调参数（引脚、BLE、I2S、灯光帧数/时间、显示参数）
  - `rotating_logger.py`：循环轮转日志模块（4文件×150行，MicroPython/CPython 通用）
- **scripts/**：烧录、TTS生成、集成测试工具；图片资源在 `scripts/assets/`
- **tests/**：单元测试和集成测试

## 注意事项

- `device/config.py` 中的 `VARIANT` 和 `BLE_NAME` 两个字段由 `scripts/flash_device.py` 烧录时自动注入，**不要手动修改这两个字段**；其余常量（引脚、灯光参数等）可以手动修改
- `device/config.py` 中的 `LOG_ENABLE`、`LOG_MAX_FILES`、`LOG_LINES_PER_FILE` 可按需调整（日志开关与轮转参数）
- `device/assets/` 下的 PCM 文件是 `gen_voice_assets.py` 的生成产物，不要手动编辑
- 用户自定义入口：语音音色 → `gen_voice_assets.py`；面板角色 → `/create-character` skill（推荐）或 `device/character.py`；Logo → `scripts/logo_converter.py`；行为参数 → `device/config.py`

## v6 协议（当前版本）

- 单向推送（PC → 设备），无心跳，无审批
- wire 格式：`{"ss":[{"n":"proj","s":"W","m":"Read: main.py","slot":"cd501167"}]}`
- `slot` 字段：session 唯一标识（SID 去连字符后取后 8 位），device 端用于槽位稳定映射
- 状态枚举：`I`（空闲）/ `W`（执行中）/ `P`（待审批提醒）/ `C`（完成）/ `E`（出错）
- 审批由 Claude Code 在终端 UI 完成，设备仅作灯光/语音提醒

## 开发规范

1. 所有 Python 代码遵循 PEP 8
2. 注释和文档字符串使用中文
3. Git commit message 使用中文或英文均可

## 依赖管理

- PC 端依赖统一在 `pyproject.toml` 的 `dependencies` 字段维护，安装：`pip install -e .`
- 可选依赖（`mpy-cross`）在 `[project.optional-dependencies] dev` 中，安装：`pip install -e ".[dev]"`
- `device/` 是 MicroPython 固件，**不参与依赖扫描**
- 新增第三方包时，在 `scripts/update_deps.py` 的 `_IMPORT_TO_PKG` 加映射，然后运行 `python scripts/update_deps.py`
- 通过命令行调用但无 `import` 的工具包（如 `mpremote`、`esptool`）在 `_FORCED_DEPS` 列表中维护
- pre-commit hook 会自动扫描并更新 `pyproject.toml`；首次克隆后运行 `python scripts/install_hooks.py` 安装

## 联合测试流程

**两种场景，日志路径不同：**

### 场景一：真实设备（ESP32 + BLE）

设备端日志写入 `/log/run_0.log`～`/log/run_3.log`（4文件×150行循环轮转），通过 `config.py` 的 `LOG_ENABLE` 控制：
- `LOG_ENABLE = True`：写设备 flash 轮转文件，供 mpremote 读取分析
- `LOG_ENABLE = False`：走串口输出，正常使用模式

1. 用户手动启动 daemon（日志自动写到 `logs/daemon.log`）：
   ```
   python daemon/ble_daemon.py
   ```
2. 用户告知 Claude "daemon 已启动"
3. Claude 执行：`python scripts/sim_hooks_v5.py --clock --no-daemon`
4. Claude 读取设备日志：`python scripts/read_device_log.py`
5. Claude 读取 `logs/daemon.log`，对比两端日志分析问题
6. Claude 修改代码后提示用户重启 daemon，重复上述流程验证

**注意：** BLE（测试通信）和 USB 串口（mpremote 读日志）不冲突，可同时使用。

#### C/P 粘滞状态测试（真机）

验证 C/P 状态不被 I 立刻覆盖，需要在无其他活跃 session 的纯净环境下运行：

1. **关闭所有 Claude Code 窗口**（避免真实 session 干扰）
2. 启动 daemon：`python daemon/ble_daemon.py`
3. 运行粘滞测试：`python scripts/sim_hooks_v5.py --no-daemon --sticky-state`
4. 观察 ESP32 屏幕 logo 颜色变化：
   - **场景 1（C 粘滞）**：Stop 后 logo 变绿（C），2 秒后 daemon 推 I，logo 应保持绿色不变灰（粘滞 5s）
   - **场景 2（C 粘滞解除）**：粘滞 C 期间新任务到来，logo 从绿变蓝（W）
   - **场景 3（P 粘滞）**：审批通知后 logo 变紫（P），Stop 后变绿（C），daemon 推 I 后应保持绿色（粘滞）

### 场景二：模拟设备（PC 端 sim_device，无需 ESP32）

daemon 以 `--tcp-device` 模式连接 PC 端 `sim_device`，日志统一在 `scripts/sim_device/logs/`：
- `scripts/sim_device/logs/daemon.log`：daemon 日志（`--tcp-device` 时自动写此路径）
- `scripts/sim_device/logs/sim_device_N.log`：sim_device 轮转日志（N=0~3）

**需要三个终端分别启动（顺序不可错）：**

终端 1（先启动 sim_device，它会循环重试连接 daemon:57321）：
```
python -m scripts.sim_device
```

终端 2（sim_device 启动后启动 daemon，sim_device 会自行重连）：
```
python daemon/ble_daemon.py --tcp-device
```

终端 3（daemon 就绪后发送测试事件）：
```
python scripts/sim_hooks_v5.py --no-daemon --skip-ble-check
```

运行完毕后直接读日志文件分析：
```
python scripts/read_sim_log.py        # 读取所有轮转日志（按时间顺序）
python scripts/read_sim_log.py --tail 50  # 只看最后50行
```

#### C/P 粘滞状态测试（sim_device）

验证粘滞逻辑。**注意：Claude Code 自身运行测试命令也会创建 session（terminal-1.1 始终 W），因此 global dominant 级粘滞（`sticky dominant`、`sticky msg`）在 CC 运行时无法触发——这属于正常现象。** 可验证的是 per-dot/slot 级粘滞行为：

1. **关闭所有 Claude Code 窗口**（减少干扰，但正在运行测试的 CC 自身仍会产生 session）
2. 按上述三终端流程启动 sim_device + daemon
3. 终端 3 运行：`python scripts/sim_hooks_v5.py --no-daemon --skip-ble-check --sticky-state`
4. 观察终端 1（sim_device）的实时输出：
   - 标题行 `dominant:` 显示当前聚合状态（通常为 W，因为 CC 自身 session 活跃）
   - 各 slot 行颜色变化反映 per-dot 粘滞
5. 读日志验证：
   ```
   python scripts/read_sim_log.py --tail 80 | grep -E "sticky (dot|hold|broken|dominant|msg)"
   ```
   - `sticky dot[N] ... raw=I prev=C -> sticky=C` — per-dot 粘滞生效
   - `sticky hold: N empty dot(s) kept at dominant=P` — 空槽粘滞保持
   - `sticky broken: ... -> ... (sticky was holding ...)` — W/E 打破粘滞
   - `sticky dominant: raw=I -> C (keep C against I)` — global 粘滞（需纯净环境）
   - `sticky msg: show ... as ... (all sessions gone, sticky hold)` — 消息块粘滞（需纯净环境）

## 日志规范

- **不做 stdout 重定向**：`print()` 是终端实时渲染用的（含 ANSI 转义码），写进文件是乱码，无分析价值，不需要捕获
- **logging 模块自动写轮转文件**：`sim_device` 的结构化日志自动写到 `scripts/sim_device/logs/sim_device_N.log`，运行完后用 `python scripts/read_sim_log.py` 读取分析
- **日志文件紧邻产生它的模块**：`logs/` 目录不应存在于项目根，所有测试日志统一放 `scripts/sim_device/logs/`
- **正确分析流程**：运行 → 等结束 → `python scripts/read_sim_log.py` → 分析，无需任何重定向操作
