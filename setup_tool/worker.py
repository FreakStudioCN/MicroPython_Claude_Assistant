# setup_tool/worker.py — 后台烧录线程（不修改 flash_device.py 一行）
import sys, os, re, time, threading, subprocess, serial.tools.list_ports
from typing import Callable, Optional
from scripts import flash_device as fd

ROOT_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── stdout 捕获器 ─────────────────────────────────────────────────────
class _Capture:
    def __init__(self, callback: Callable[[str], None]):
        self._cb = callback
    def write(self, text: str):
        text = text.strip()
        if text:
            self._cb(text)
    def flush(self):
        pass


# ── 配置覆盖 ───────────────────────────────────────────────────────────
_OVERRIDE_MAP = {
    "FPS": int, "HEARTBEAT_TIMEOUT": int, "LOG_ENABLE": bool,
    "LIGHT_CONNECT_BRIGHTNESS": int, "LIGHT_IDLE_MAX_V": int,
    "VOICE_WORK_MIN_S": int, "VOICE_WORK_MAX_S": int,
    "VOICE_IDLE_MIN_S": int, "VOICE_IDLE_MAX_S": int,
}

def _apply_config_overrides(config_text: str, params: dict) -> str:
    for key, typ in _OVERRIDE_MAP.items():
        if key in params:
            val = params[key]
            if typ == bool:
                formatted = f"{key} = {str(val)}"
            elif typ == str:
                formatted = f'{key} = "{val}"'
            else:
                formatted = f"{key} = {val}"
            config_text = re.sub(
                rf'^{key}\s*=.*$', formatted,
                config_text, flags=re.MULTILINE,
            )
    return config_text


# ── 串口工具 ──────────────────────────────────────────────────────────
def scan_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]

def auto_select_port(exclude: Optional[str] = None) -> str:
    ports = scan_ports()
    if exclude:
        ports = [p for p in ports if p != exclude]
    if not ports:
        raise RuntimeError("未检测到可用串口设备")
    return ports[0]


# ── 固件扫描 ──────────────────────────────────────────────────────────
def scan_firmware_files() -> list[str]:
    d = os.path.join(ROOT_DIR, "firmware")
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".bin"))


# ── 语音预设扫描 ──────────────────────────────────────────────────────
_VOICE_STATES = {"startup","connect","disconnect","working","pending","done","error","idle"}

def scan_voice_presets() -> dict[str, int]:
    d = os.path.join(ROOT_DIR, "device", "assets")
    if not os.path.isdir(d):
        return {}
    counts: dict[str, int] = {}
    for f in os.listdir(d):
        if not f.endswith(".pcm"):
            continue
        parts = f.replace(".pcm", "").split("-")
        for i, p in enumerate(parts):
            if p in _VOICE_STATES:
                voice = "-".join(parts[:i])
                counts[voice] = counts.get(voice, 0) + 1
                break
        else:
            counts[f] = counts.get(f, 0) + 1
    return counts


# ── 后台工作线程 ──────────────────────────────────────────────────────
class FlashWorker:
    def __init__(self):
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def cancel(self):
        self._cancel.set()

    def run(self, params: dict,
            on_log: Callable[[str], None],
            on_progress: Callable[[int], None],
            on_done: Callable[[bool, str], None]):
        self._thread = threading.Thread(
            target=self._run_impl,
            args=(params, on_log, on_progress, on_done),
            daemon=True,
        )
        self._thread.start()

    def _patch(self):
        import builtins
        self._patches: dict[str, object] = {}
        if hasattr(fd, "select_com_port"):
            self._patches["select_com_port"] = fd.select_com_port
            fd.select_com_port = self._auto_select
        ob = builtins.input
        self._patches["builtins_input"] = ob
        builtins.input = self._auto_input

    def _unpatch(self):
        import builtins
        for name, original in self._patches.items():
            if name == "builtins_input":
                builtins.input = original
            elif hasattr(fd, name):
                setattr(fd, name, original)
        self._patches = {}

    def _auto_select(self) -> str:
        return auto_select_port()

    def _auto_input(self, prompt="") -> str:
        return ""

    # ── 主流程 ─────────────────────────────────────────────────────
    def _run_impl(self, params, on_log, on_progress, on_done):
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _Capture(on_log)
        sys.stderr = _Capture(on_log)

        try:
            self._patch()
            fd._COM_PORT = params["port"]

            # 0 — 语音生成（可选，两种形态都支持）
            if params.get("generate_voice"):
                on_log("[信息] 调用 gen_voice_assets.py 生成语音文件...")
                self._gen_voice(on_log)

            # 1 — 烧录底层固件（可选）
            if params.get("flash_firmware"):
                on_progress(5)
                on_log("[信息] 烧录 MicroPython 底层固件...")
                self._do_flash_firmware(params["variant"], params.get("firmware_path"))

            # 2 — 清空文件系统（可选）
            if params.get("wipe"):
                on_progress(15)
                fd.wipe_device("2")

            # 3 — 读 MAC → BLE_NAME
            on_progress(25)
            mac_suffix = fd.get_mac_address("3")

            # 4 — 生成 config.py
            on_progress(35)
            config = fd.generate_config(mac_suffix, params["variant"],
                                        params["character"], "4")
            config = _apply_config_overrides(config, params)

            # 5 — 安装 aioble
            on_progress(45)
            fd.install_libs("5")

            # 6 — 上传固件文件
            on_progress(55)
            fd.upload_firmware(config, "6", wiped=params.get("wipe", False),
                               character=params["character"])

            # 7 — 重启
            on_progress(95)
            fd.reset_device("7")

            on_progress(100)
            name = f"Claude-Buddy-{mac_suffix}"
            on_done(True, name)

        except SystemExit as e:
            msg = str(e) or "设备通信失败"
            if "mpremote" in msg or "串口" in msg or "连接" in msg:
                msg += "\n\n请尝试：\n1. 重新插拔 USB 线\n2. 按设备 RST 键重启\n3. 点击「↻ 刷新」重新选择串口"
            on_done(False, msg)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(tb)  # 完整堆栈会显示在日志区
            on_done(False, f"意外错误: {e}\n\n请重新插拔 USB 线后重试，详细信息见上方日志")
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            self._unpatch()

    # ── 底层固件烧录（绕过 input() 调用） ────────────────────────
    def _do_flash_firmware(self, variant: str, firmware_path: Optional[str] = None):
        prefix = {"clock": "claude-buddy-clock-", "panel": "claude-buddy-panel-"}[variant]
        d = os.path.join(ROOT_DIR, "firmware")
        if firmware_path and os.path.isfile(firmware_path):
            bin_path = firmware_path
        else:
            candidates = [f for f in os.listdir(d) if f.startswith(prefix) and f.endswith(".bin")]
            if not candidates:
                raise RuntimeError(f"firmware/ 下未找到 {prefix}*.bin")
            bin_path = os.path.join(d, sorted(candidates)[-1])

        port = fd._COM_PORT
        print(f"  固件: {os.path.basename(bin_path)}")
        print(f"  串口: {port}")
        import esptool

        if variant == "panel":
            esptool_args = [
                "--chip", "esp32s3", "--port", port, "--baud", "460800",
                "--before", "default_reset", "--after", "hard_reset",
                "write_flash", "--flash_mode", "dio",
                "--flash_size", "16MB", "--flash_freq", "80m",
                "--erase-all", "0x0", bin_path,
            ]
        else:
            esptool_args = [
                "--chip", "esp32c3", "--port", port, "--baud", "460800",
                "write_flash", "--erase-all", "-z", "0x0", bin_path,
            ]

        try:
            esptool.main(esptool_args)
        except SystemExit as e:
            if e.code != 0:
                raise RuntimeError("esptool 烧录失败，请检查串口连接后重试")

        # 等待设备重启 → 检测串口（设备通常重启后复用同一串口）
        old_port = fd._COM_PORT
        print("固件烧录完成！设备正在重启...")
        time.sleep(3)

        # 先试原串口（设备重启后通常复用同一端口）
        for attempt in range(30):
            try:
                result = subprocess.run(
                    ["mpremote", "connect", old_port, "exec", "print('ready')"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and 'ready' in result.stdout:
                    print(f"  设备就绪 ✓  (串口: {old_port})")
                    return
            except Exception:
                pass
            time.sleep(1)
            if attempt % 10 == 9:
                print(f"  等待设备就绪... ({attempt//10 + 1}/3)")

        # 原串口不行 → 扫描新端口
        print("  原串口未就绪，尝试检测新串口...")
        for i in range(30):
            ports = scan_ports()
            candidates = [p for p in ports if p != old_port]
            for np in candidates:
                try:
                    result = subprocess.run(
                        ["mpremote", "connect", np, "exec", "print('ready')"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if result.returncode == 0 and 'ready' in result.stdout:
                        fd._COM_PORT = np
                        print(f"  新串口: {np} ✓")
                        return
                except Exception:
                    pass
            time.sleep(1)

        raise RuntimeError(
            "设备重连超时（60 秒）。\n"
            "请尝试：\n"
            "  1. 按设备 RST 键重启\n"
            "  2. 重新插拔 USB 线\n"
            "  3. 点击「↻ 刷新」手动选择串口后重试"
        )

    # ── 调用语音生成（仅限主线程，worker 线程中提示用户使用按钮） ──
    def _gen_voice(self, on_log):
        on_log("[提示] 语音生成需在主界面操作，请点击「生成语音文件」按钮。")
