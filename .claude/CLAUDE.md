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
- **scripts/**：烧录、TTS生成、集成测试工具；图片资源在 `scripts/assets/`
- **tests/**：单元测试和集成测试

## 注意事项

- `device/config.py` 中的 `VARIANT` 和 `BLE_NAME` 两个字段由 `scripts/flash_device.py` 烧录时自动注入，**不要手动修改这两个字段**；其余常量（引脚、灯光参数等）可以手动修改
- `device/assets/` 下的 PCM 文件是 `gen_voice_assets.py` 的生成产物，不要手动编辑
- 用户自定义入口：语音音色 → `gen_voice_assets.py`；面板角色 → `device/character.py`；Logo → `scripts/logo_converter.py`；行为参数 → `device/config.py`

## v5 协议（当前版本）

- 单向推送（PC → 设备），无心跳，无审批
- wire 格式：`{"ss":[{"n":"proj","s":"W","m":"Read: main.py"}]}`
- 状态枚举：`I`（空闲）/ `W`（执行中）/ `P`（待审批提醒）/ `C`（完成）/ `E`（出错）
- 审批由 Claude Code 在终端 UI 完成，设备仅作灯光/语音提醒

## 开发规范

1. 所有 Python 代码遵循 PEP 8
2. 注释和文档字符串使用中文
3. Git commit message 使用中文或英文均可

## 联合测试流程

设备端日志写入 `/log/run.log`（每次启动清空），通过 `config.py` 的 `LOG_ENABLE` 控制：
- `LOG_ENABLE = True`：写设备 flash 文件，供 mpremote 读取分析
- `LOG_ENABLE = False`：走串口输出，正常使用模式

**测试步骤：**

1. 用户手动启动 daemon：
   ```
   python daemon/ble_daemon.py 2>&1 | tee logs/daemon.log
   ```
2. 用户告知 Claude "daemon 已启动"
3. Claude 执行：`python scripts/sim_hooks_v5.py --clock --no-daemon`
4. Claude 读取设备日志（注意冒号前缀）：`mpremote fs cat :/log/run.log`
5. Claude 读取 `logs/daemon.log`，对比两端日志分析问题
6. Claude 修改代码后提示用户重启 daemon，重复上述流程验证

**注意：** BLE（测试通信）和 USB 串口（mpremote 读日志）不冲突，可同时使用。
