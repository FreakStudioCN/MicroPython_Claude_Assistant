# 桥接装机机制调研 v1

> Historical note: this research has been superseded by `plugin_plan/PLAN.md` v2.3.
> In particular, V1 now treats the setup skill as independently distributed, the plugin as hook delivery only, and Windows install as a native PowerShell path rather than a Bash/POSIX path.

**日期**: 2026-04-27
**目的**: 决定 v1 量产版怎么把 `hook_bridge.py` + `ble_daemon.py` 装到用户机器
**来源**: 派 claude-code-guide subagent 查 docs.claude.com / code.claude.com 文档

---

## 结论先行

**Plugin 机制能解决 60-70% 的装机问题，但有一个真实瓶颈：机器重启后 daemon 不会自启。**

推荐路径：**Plugin 兜底 hook 配置 + 文档引导用户做 OS 层开机自启**。不要指望 plugin 完整接管 daemon 生命周期。

---

## 三档交付方式对比

| 方式 | 用户操作 | 适用阶段 | 风险 |
|------|--------|--------|------|
| A. 手动 README | pip install + 改 settings.json + 自起 daemon | FreakStudio 首发 dev demo | 普通用户劝退 |
| B. 一键安装器（PyInstaller + 系统服务注册） | 双击运行 | KS 量产保底方案 | 工程量大，跨平台分别打包 |
| C. Claude Code Plugin | `/plugin marketplace add` + `/plugin install` | KS 量产首选（如可行） | 重启不自启需 workaround |

---

## Claude Code Plugin 机制（核心调研）

### 1. Plugin 能直接携带 hooks 配置 ✅

- Plugin manifest (`.claude-plugin/plugin.json`) 支持 `hooks` 字段
- 可指向外部 JSON 文件（`"hooks": "./hooks/hooks.json"`）或内联
- 用户装 plugin 后 hooks 自动注册，卸载时自动清理（无 settings.json 残留）

**对我们的意义**：用户不用自己编辑 `settings.json`，`hook_bridge.py` 调用方式由 plugin 声明。

### 2. 分发方式灵活，无审核 ✅

支持的安装源：
- GitHub repo: `/plugin marketplace add owner/repo`
- 任意 Git URL: `/plugin marketplace add https://gitlab.com/.../plugins.git`
- 本地路径
- 远程 URL

**对我们的意义**：可以走自家 GitHub 私有/公开仓库分发，不用申请上架。

### 3. Plugin 文件结构（subagent 报告，**首次落地前需对照官方文档复核**）

```
my-plugin/
├── .claude-plugin/plugin.json       ← manifest
├── hooks/hooks.json                 ← hook 配置
├── bin/                             ← 可执行文件，声称自动加 $PATH
├── monitors/monitors.json           ← 后台进程声明（subagent 称 v2.1.105+ 支持）
├── skills/                          ← skills（不需要）
└── agents/                          ← subagents（不需要）
```

**Manifest 主字段（subagent 报告）**：
```json
{
  "name": "claude-buddy-bridge",
  "version": "1.0.0",
  "hooks": "./hooks/hooks.json",
  "monitors": "./monitors.json",
  "bin": "./bin/"
}
```

⚠️ **需亲手验证的字段**：`monitors`、`bin` 自动 PATH、`${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_DATA}` 变量名。这几个是 subagent 报告的，我不能凭它的转述当事实，**写代码前必须照官方 plugin reference 文档自行验证**。

### 4. Daemon 启动方案（这是真问题）

#### 4.1 Plugin 不提供 postinstall hook

没有"装上 plugin 那一刻执行一次"的机制。所以无法用 plugin 安装时自动配开机自启。

#### 4.2 SessionStart hook 是退路

每次 Claude Code 启动 session 时触发：
```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "bash ${CLAUDE_PLUGIN_ROOT}/bin/ensure_daemon.sh"
      }]
    }]
  }
}
```

`ensure_daemon.sh` 干的事：
1. 检查 pid 文件，若 daemon 还活着直接返回
2. 否则 `nohup python ble_daemon.py &` 拉起
3. 写 pid 文件

**缺点**：
- 用户**重启机器 + 没开 Claude Code** → 设备一直黑屏
- 多 session 并发要靠 pid 锁防重启

#### 4.3 真正的开机自启只能让用户做 OS 层

- macOS: launchd plist
- Windows: 任务计划程序 / 服务
- Linux: systemd user unit

**这部分必须有引导文档**，靠 plugin 不够。

### 5. 依赖（bleak）打包

Plugin 不能打包 Python 依赖。两条路：
- A. SessionStart hook 检查并 `pip install bleak` 到隔离 venv（`${CLAUDE_PLUGIN_DATA}/venv`）
- B. 用 PyInstaller 把 daemon 编译成单文件 exe，放 `bin/`，绕过 Python 环境

**推荐 B**：用户不用装 Python，体验更接近"硬件配套软件"。

---

## v1 装机流程草案（待评审）

```
用户买到设备 → 打开包装看到一张卡片
                ↓
        卡片印 GitHub repo URL + 二维码
                ↓
用户复制粘贴： /plugin marketplace add freakstudio/claude-buddy
              /plugin install claude-buddy
                ↓
        Plugin 装好，hook 自动注册
                ↓
        SessionStart 触发 ensure_daemon.sh
        → 首次运行做：BLE 配对引导 + 注册系统服务
        → 后续运行做：检查 daemon 活着没
                ↓
        BLE 设备自动连上，开始推送状态
```

**第一次运行的引导界面**（可能需要单独的 GUI 或终端 wizard）：
1. 扫描可见 BLE 设备
2. 让用户选自己的（或扫包装上的二维码自动配）
3. 写入 owner / device name
4. 注册系统服务（请求管理员权限）

---

## 待确认问题

下次开 Claude Code 实际装一个 demo plugin 验证：

1. `monitors/monitors.json` 是否真的存在？版本要求？
2. `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_DATA}` 变量名是否准确？
3. SessionStart hook 是否能拉起后台进程并存活到 session 结束之后？
4. Plugin 卸载时除了 hooks 还会清理什么？daemon 需不需要手动停？
5. `/plugin marketplace add` 私有 repo 用什么鉴权（SSH key？GH token？）
6. 跨平台行为差异（Windows 下 bash 脚本怎么办？要不要写 .bat / .ps1 双份）

---

## 来源（subagent 转述，使用前自行核对）

- https://code.claude.com/docs/en/plugins.md
- https://code.claude.com/docs/en/plugins-reference.md
- https://code.claude.com/docs/en/discover-plugins.md
- https://code.claude.com/docs/en/hooks.md

⚠️ 注意：subagent 给的域名是 `code.claude.com`，但官方主域是 `docs.claude.com`。两个域名是否都生效、哪个是规范，**亲自打开浏览器确认一次**再外发。
