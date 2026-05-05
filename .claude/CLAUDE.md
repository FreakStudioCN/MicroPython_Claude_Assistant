# MicroPython Claude Assistant 项目说明

## 语言偏好

**请始终使用中文回复**，除非用户明确要求使用英文。

## 项目简介

这是一个将 Claude Code 的工具执行状态实时可视化为 ESP32 桌宠动画的项目，支持设备端触摸审批。

## 架构

- **daemon/**: PC 端守护进程（BLE 桥接、状态机、风险分级）
- **device/**: ESP32 固件（BLE 通信、LVGL 渲染、触摸审批）
- **scripts/**: 调试和测试工具
- **tests/**: 单元测试和集成测试

## 开发规范

1. 所有 Python 代码遵循 PEP 8
2. 注释和文档字符串使用中文
3. Git commit message 使用中文或英文均可
4. 测试覆盖率保持在 80% 以上

## 风险分级

设备离线时根据操作风险自动决策：
- **safe**: 只读操作（Read/Glob/Grep/WebFetch/WebSearch）→ 自动批准
- **normal**: 可逆写操作（普通 Bash/Write/Edit）→ 自动批准
- **critical**: 破坏性操作（git push --force/rm -rf/关键路径修改）→ CLI 提示

详见 `daemon/risk_config.py`。
