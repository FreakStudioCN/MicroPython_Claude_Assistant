# setup_tool/pairing.py — 配对/验证对话框
import tkinter as tk
from tkinter import ttk, messagebox
import sys, os, subprocess, threading, json, time
from pathlib import Path
from typing import Optional, Tuple

ROOT_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "~"))
    else:
        base = Path.home() / ".config"
    return base / "claude-buddy" / "device.json"


def _save_config(name: str, mac: str, method: str):
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"device_name": name, "paired_mac": mac, "method": method}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


class PairingDialog(tk.Toplevel):
    def __init__(self, parent, method: str):
        super().__init__(parent)
        self.title("设备配对")
        self.geometry("500x380")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._method = method
        self._running = False

        ttk.Label(self, text=f"通信方式: {method.upper()}", font=("", 10, "bold")).pack(pady=(10, 4))
        self.log = tk.Text(self, height=12, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=10, pady=4)

        scroll = ttk.Scrollbar(self, orient="vertical", command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=6)

        self.action_btn = ttk.Button(btn_frame, text="开始配对", command=self._start)
        self.action_btn.pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="关闭", command=self.destroy).pack(side="left")

        if method == "usb":
            self._append_log("[信息] USB 配对：验证串口通信...")
        elif method == "ethernet":
            self._append_log("[信息] 以太网配对：验证 TCP 连接...")
        else:
            self._append_log("[信息] BLE 配对：准备扫描 Claude-Buddy 设备...\n"
                             "请确保设备已开机且蓝牙已启用")

    def _append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _start(self):
        if self._running:
            return
        self._running = True
        self.action_btn.configure(state="disabled", text="配对中...")

        if self._method == "usb":
            threading.Thread(target=self._pair_usb, daemon=True).start()
        elif self._method == "ethernet":
            threading.Thread(target=self._pair_ethernet, daemon=True).start()
        else:
            threading.Thread(target=self._pair_ble, daemon=True).start()

    # ── BLE 配对（复用 pair_device.py 扫描逻辑） ────────────────
    def _pair_ble(self):
        import asyncio
        from bleak import BleakScanner

        async def scan():
            self._append_log("[扫描] 正在搜索附近的 Claude-Buddy 设备（5 秒）...")
            devices = await BleakScanner.discover(timeout=5.0)
            found = [(d.name, d.address) for d in devices
                     if d.name and d.name.startswith("Claude-Buddy-")]
            return found

        try:
            devices = asyncio.run(scan())
        except Exception as e:
            self.after(0, self._done, False, f"BLE 扫描失败: {e}")
            return

        if not devices:
            self.after(0, self._done, False, "未发现 Claude-Buddy 设备\n"
                       "请确保设备已开机并运行 main.py")
            return

        self.after(0, self._append_log, f"\n发现 {len(devices)} 个设备：")
        for n, m in devices:
            self.after(0, self._append_log, f"  {n}  (MAC: {m})")

        # 多个设备 → 让用户选择
        if len(devices) > 1:
            selected = self._select_device_dialog(devices)
            if selected is None:
                self.after(0, self._done, False, "用户取消选择")
                return
            name, mac = selected
        else:
            name, mac = devices[0]

        # 保存配置
        p = _save_config(name, mac, "ble")
        self.after(0, self._done, True, f"✓ 已配对: {name}\n  MAC: {mac}\n  配置已保存到:\n  {p}")

    def _select_device_dialog(self, devices: list) -> Optional[Tuple[str, str]]:
        """弹窗让用户选择要配对的设备。返回 (name, mac) 或 None。"""
        result = [None]

        win = tk.Toplevel(self)
        win.title("选择配对设备")
        win.geometry("460x300")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(win, text="发现多个 Claude-Buddy 设备，请选择要配对的一个：",
                  wraplength=420).pack(pady=(10, 4))

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10)
        scroll = ttk.Scrollbar(frame, orient="vertical")
        listbox = tk.Listbox(frame, font=("Consolas", 10),
                             yscrollcommand=scroll.set)
        scroll.configure(command=listbox.yview)
        listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        for i, (n, m) in enumerate(devices):
            listbox.insert(tk.END, f"{n}  ({m})")
        listbox.selection_set(0)
        listbox.focus_set()

        def _confirm():
            sel = listbox.curselection()
            if sel:
                result[0] = devices[sel[0]]
            win.destroy()

        def _cancel():
            win.destroy()

        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", padx=10, pady=8)
        ttk.Button(btn_frame, text="确认", command=_confirm).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="取消", command=_cancel).pack(side="left")

        win.protocol("WM_DELETE_WINDOW", _cancel)
        self.wait_window(win)

        return result[0]

    # ── USB 配对/验证 ───────────────────────────────────────────
    def _pair_usb(self):
        # TODO: 实现 USB 验证逻辑
        self._append_log("[信息] USB 配对功能开发中...")
        time.sleep(1)
        self.after(0, self._done, True, "USB 配对暂为占位，未来通过 mpremote 验证串口通信")

    # ── 以太网配对/验证 ─────────────────────────────────────────
    def _pair_ethernet(self):
        # TODO: 实现以太网验证逻辑
        self._append_log("[信息] 以太网配对功能开发中...")
        time.sleep(1)
        self.after(0, self._done, True, "以太网配对暂为占位，未来通过 TCP 握手验证")

    def _done(self, success: bool, msg: str):
        self._running = False
        self.action_btn.configure(state="disabled")
        self._append_log(f"\n{'✅' if success else '❌'} {msg}")
        if success:
            self.action_btn.configure(text="已完成")
