# setup_tool/gui.py — Tkinter 主窗口
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sys, os, json, tempfile

from setup_tool import worker

ROOT_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class App(tk.Tk):
    """Claude Buddy 烧录配置工具主窗口"""

    def __init__(self):
        super().__init__()
        self.title("Claude Buddy 烧录配置工具")
        self.geometry("740x680")
        self.minsize(680, 600)
        self.resizable(True, True)

        # ── 配置状态变量 ──────────────────────────────────────────
        self.variant       = tk.StringVar(value="panel")
        self.character     = tk.StringVar(value="claude")
        self.comm_method   = tk.StringVar(value="ble")
        self.port          = tk.StringVar()
        self.flash_firmware = tk.BooleanVar(value=False)
        self.firmware_path  = tk.StringVar()
        self.wipe          = tk.BooleanVar(value=False)
        self.generate_voice = tk.BooleanVar(value=False)

        # clock 专有
        self.voice_enable  = tk.BooleanVar(value=True)
        self.voice_speed   = tk.DoubleVar(value=1.0)

        # 高级参数
        self.fps           = tk.IntVar(value=20)
        self.heartbeat     = tk.IntVar(value=30)
        self.log_enable    = tk.BooleanVar(value=True)
        self.brightness    = tk.IntVar(value=80)

        # UI 状态
        self._is_flashing = False
        self._worker: worker.FlashWorker = None
        self._flash_success    = False
        self._custom_char_src  = None
        self._custom_char_label = None

        # ── 菜单栏 ──────────────────────────────────────────────
        self._build_menu()

        # ── 主布局 ──────────────────────────────────────────────
        self._build_ui()
        self._refresh_ports()
        self._scan_firmware()
        self._update_voice_status()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ═══════════════════════════════════════════════════════════════
    #  UI 构建
    # ═══════════════════════════════════════════════════════════════

    def _build_menu(self):
        menubar = tk.Menu(self)

        config_menu = tk.Menu(menubar, tearoff=0)
        config_menu.add_command(label="保存配置...", command=self._save_config)
        config_menu.add_command(label="加载配置...", command=self._load_config)
        config_menu.add_separator()
        config_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="配置", menu=config_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.config(menu=menubar)

    def _show_about(self):
        messagebox.showinfo(
            "关于 Claude Buddy 烧录配置工具",
            "Claude Buddy 烧录配置工具  v0.9\n\n"
            "将 Claude Code 的工具执行状态实时可视化为\n"
            "ESP32 桌宠设备的一键配置工具。\n\n"
            "项目地址：\n"
            "  https://github.com/FreakStudioCN/\n"
            "  MicroPython_Claude_Assistant\n\n"
            "问题反馈与定制：\n"
            "  10696531183@qq.com\n\n"
            "技术支持：\n"
            "  联系开发者时请附上日志文件截图"
        )

    def _save_config(self):
        """保存当前所有配置到 JSON 文件。"""
        data = {
            "variant":        self.variant.get(),
            "character":      self.character.get(),
            "comm_method":    self.comm_method.get(),
            "flash_firmware": self.flash_firmware.get(),
            "firmware_path":  self.firmware_path.get(),
            "wipe":           self.wipe.get(),
            "generate_voice": self.generate_voice.get(),
            "voice_enable":   self.voice_enable.get(),
            "voice_speed":    self.voice_speed.get(),
            "fps":            self.fps.get(),
            "heartbeat":      self.heartbeat.get(),
            "log_enable":     self.log_enable.get(),
            "brightness":     self.brightness.get(),
        }
        path = filedialog.asksaveasfilename(
            title="保存配置",
            defaultextension=".json",
            filetypes=[("配置文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._append_log(f"[信息] 配置已保存: {path}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _load_config(self):
        """从 JSON 文件加载配置。"""
        path = filedialog.askopenfilename(
            title="加载配置",
            filetypes=[("配置文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取配置文件:\n{e}")
            return

        try:
            self.variant.set(data.get("variant", "panel"))
            self._on_variant_change()
            self.character.set(data.get("character", "claude"))
            self.comm_method.set(data.get("comm_method", "ble"))
            self.flash_firmware.set(data.get("flash_firmware", False))
            self.firmware_path.set(data.get("firmware_path", ""))
            self.wipe.set(data.get("wipe", False))
            self.generate_voice.set(data.get("generate_voice", False))
            self.voice_enable.set(data.get("voice_enable", True))
            self.voice_speed.set(data.get("voice_speed", 1.0))
            self.fps.set(data.get("fps", 20))
            self.heartbeat.set(data.get("heartbeat", 30))
            self.log_enable.set(data.get("log_enable", True))
            self.brightness.set(data.get("brightness", 80))
            self._append_log(f"[信息] 配置已加载: {path}")
        except Exception as e:
            messagebox.showerror("加载失败", f"配置解析错误:\n{e}")

    def _build_ui(self):
        # 可滚动区域
        canvas = tk.Canvas(self, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        inner = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # 跟随窗口宽度动态调整
        def _on_canvas_resize(e):
            canvas.itemconfig(inner, width=e.width)
        canvas.bind("<Configure>", _on_canvas_resize)

        # 鼠标滚轮
        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        main = scroll_frame

        # ── 流程引导 ──────────────────────────────────────────────
        guide = ttk.Frame(main)
        guide.pack(fill="x", padx=10, pady=(6, 2))
        ttk.Label(guide, text="操作步骤：", font=("", 9, "bold")).pack(side="left")
        for s, t in [("1", "选硬件"), ("2", "连接设备"), ("3", "调参数"), ("4", "开始烧录")]:
            ttk.Label(guide, text=f"  {s}.{t}  →", foreground="#555", font=("", 9)).pack(side="left")
        ttk.Label(guide, text="  完成!", foreground="green", font=("", 9, "bold")).pack(side="left")

        # ── ① 硬件配置 ──────────────────────────────────────────
        f1 = ttk.LabelFrame(main, text="① 硬件配置", padding=8)
        f1.pack(fill="x", padx=10, pady=(10, 2))
        f1.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(f1, text="硬件形态:").grid(row=row, column=0, sticky="w", padx=(0, 8))
        rb_frame = ttk.Frame(f1)
        rb_frame.grid(row=row, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(rb_frame, text="Clock (ESP32-C3)  灯光+语音", variable=self.variant,
                        value="clock", command=self._on_variant_change).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(rb_frame, text="Panel (ESP32-S3)  屏幕+动画", variable=self.variant,
                        value="panel", command=self._on_variant_change).pack(side="left")
        row += 1

        ttk.Label(f1, text="面板角色:").grid(row=row, column=0, sticky="w", padx=(0, 8))
        self.char_combo = ttk.Combobox(f1, textvariable=self.character, state="readonly", width=32)
        custom_exists = os.path.isfile(os.path.join(ROOT_DIR, "device", "char_custom.py"))
        char_values = ["claude", "cat", "robot", "ghost",
                       "among_us", "creeper", "kirby", "pikachu"]
        if custom_exists:
            char_values.append("自定义")
        self.char_combo["values"] = tuple(char_values)
        self.char_combo.grid(row=row, column=1, sticky="ew")
        self.char_label = ttk.Label(f1, text="仅 Panel 可选", foreground="gray")
        self.char_label.grid(row=row, column=2, sticky="w", padx=(6, 0))
        self.char_preview_btn = ttk.Button(f1, text="预览", command=self._preview_character)
        self.char_preview_btn.grid(row=row, column=3, padx=(4, 2))
        self.char_info_btn = self._info_btn(f1, "面板角色是什么？",
                       "面板角色是设备屏幕上显示的小动画形象。\n\n"
                       "目前有 8 种预设角色可选：\n"
                       "  claude    — Claude Logo\n"
                       "  cat       — 橘猫\n"
                       "  robot     — 机器人\n"
                       "  ghost     — 幽灵\n"
                       "  among_us  — Among Us 船员\n"
                       "  creeper   — Minecraft 苦力怕\n"
                       "  kirby     — 星之卡比\n"
                       "  pikachu   — 皮卡丘\n\n"
                       "选好后重新烧录即可生效。")
        self.char_info_btn.grid(row=row, column=4, padx=(2, 0))
        self.import_char_btn = ttk.Button(f1, text="导入自定义角色...",
                                          command=self._import_custom_char)
        self.import_char_btn.grid(row=row, column=5, padx=(2, 0))
        row += 1

        # 自定义角色引导
        self.custom_frame = ttk.Frame(f1)
        self.custom_frame.grid(row=row, column=0, columnspan=5, sticky="w", pady=(2, 0))
        ttk.Label(self.custom_frame, text="想用自己画的角色？", foreground="gray",
                  font=("", 9)).pack(side="left", padx=(0, 4))
        ttk.Button(self.custom_frame, text="查看自定义教程",
                   command=self._show_custom_char_guide).pack(side="left")
        self.view_char_btn = ttk.Button(self.custom_frame, text="预览自定义角色",
                                        command=self._view_custom_char)
        self.view_char_btn.pack(side="left", padx=(4, 0))
        if not custom_exists:
            self.view_char_btn.pack_forget()
        row += 1

        # 语音预设（通用）
        self.voice_frame = ttk.Frame(f1)
        self.voice_frame.grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(self.voice_frame, text="语音预设:").pack(side="left", padx=(0, 8))
        self.voice_label = ttk.Label(self.voice_frame, text="bv701 ✓ 已就绪", foreground="green")
        self.voice_label.pack(side="left", padx=(0, 8))
        ttk.Button(self.voice_frame, text="生成语音文件", command=self._gen_voice).pack(side="left")
        row += 1

        # 固件文件
        ttk.Label(f1, text="固件文件:").grid(row=row, column=0, sticky="w", padx=(0, 8))
        self.fw_combo = ttk.Combobox(f1, textvariable=self.firmware_path, state="readonly", width=48)
        self.fw_combo.grid(row=row, column=1, sticky="ew")
        ttk.Button(f1, text="浏览...", command=self._browse_firmware).grid(row=row, column=2, padx=(6, 0))
        row += 1

        # ── ② 设备连接 ──────────────────────────────────────────
        f2 = ttk.LabelFrame(main, text="② 设备连接", padding=8)
        f2.pack(fill="x", padx=10, pady=4)
        f2.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(f2, text="串口:").grid(row=row, column=0, sticky="w", padx=(0, 8))
        self.port_combo = ttk.Combobox(f2, textvariable=self.port, state="readonly", width=32)
        self.port_combo.grid(row=row, column=1, sticky="ew")
        ttk.Button(f2, text="↻ 刷新", command=self._refresh_ports).grid(row=row, column=2, padx=(6, 0))
        row += 1

        ttk.Label(f2, text="通信方式:").grid(row=row, column=0, sticky="w", padx=(0, 8))
        cm_frame = ttk.Frame(f2)
        cm_frame.grid(row=row, column=1, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Radiobutton(cm_frame, text="BLE 蓝牙 (已实现)", variable=self.comm_method,
                        value="ble").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(cm_frame, text="USB 串口 (开发中)", variable=self.comm_method,
                        value="usb").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(cm_frame, text="以太网 (开发中)", variable=self.comm_method,
                        value="ethernet").pack(side="left")
        row += 1

        ttk.Checkbutton(f2, text="烧录底层 MicroPython 固件（首次使用需勾选）",
                        variable=self.flash_firmware).grid(row=row, column=0, columnspan=4, sticky="w")
        ttk.Label(f2, text="  ︎烧录 = 把程序通过 USB 线写入芯片；固件 = 设备最底层的「操作系统」，首次才需烧录",
                  foreground="gray", font=("", 9)).grid(
            row=row+1, column=0, columnspan=4, sticky="w", padx=(20, 0))
        row += 2

        self.wipe_cb = ttk.Checkbutton(f2, text="清空设备文件系统（危险操作 — 不可恢复）",
                                       variable=self.wipe)
        self.wipe_cb.grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1

        # ── ③ 其他设置 ──────────────────────────────────────────
        f3 = ttk.LabelFrame(main, text="③ 其他设置", padding=8)
        f3.pack(fill="x", padx=10, pady=4)

        def _row(parent):
            """创建一个水平行容器（pack 布局，无 grid 干扰）。"""
            r = ttk.Frame(parent)
            r.pack(fill="x", pady=1)
            return r

        r = _row(f3)
        ttk.Label(r, text="画面流畅度:").pack(side="left")
        ttk.Spinbox(r, from_=1, to=60, textvariable=self.fps, width=6).pack(side="left", padx=(4, 0))
        ttk.Label(r, text="数字越大越流畅，但耗电也越快  (1-60)", foreground="gray").pack(
            side="left", padx=(8, 0))

        r = _row(f3)
        ttk.Label(r, text="断线检测:").pack(side="left")
        ttk.Spinbox(r, from_=5, to=120, textvariable=self.heartbeat, width=6).pack(side="left", padx=(4, 0))
        ttk.Label(r, text="超过这个秒数没收到消息，就认为设备断开了  (5-120)", foreground="gray").pack(
            side="left", padx=(8, 0))

        r = _row(f3)
        ttk.Checkbutton(r, text="记录运行日志（勾选后设备会保存日志，方便排查问题）",
                        variable=self.log_enable).pack(side="left")

        # ── 通用语音参数 ──────────────────────────────────────────
        r = _row(f3)
        ttk.Checkbutton(r, text="启用语音提醒（设备会说话告诉你状态变化）",
                        variable=self.voice_enable).pack(side="left")

        r = _row(f3)
        ttk.Label(r, text="语音语速:").pack(side="left")
        ttk.Spinbox(r, from_=0.5, to=2.0, increment=0.1,
                    textvariable=self.voice_speed, width=6).pack(side="left", padx=(4, 0))
        ttk.Label(r, text="数字越大说话越快  (0.5-2.0)", foreground="gray").pack(
            side="left", padx=(8, 0))

        # ── clock 专属参数 ──────────────────────────────────────
        self._clock_section = ttk.Frame(f3)

        ttk.Separator(self._clock_section, orient="horizontal").pack(fill="x", pady=4)

        self.clock_hint = ttk.Label(self._clock_section, text="以下为闹钟版专属设置：", foreground="#888")
        self.clock_hint.pack(anchor="w")

        r = _row(self._clock_section)
        ttk.Label(r, text="灯光亮度:").pack(side="left")
        scale_frame = ttk.Frame(r)
        scale_frame.pack(side="left", padx=(4, 0))
        self.adv_brightness = ttk.Scale(scale_frame, from_=1, to=100, variable=self.brightness,
                                        orient="horizontal", length=120)
        self.adv_brightness.pack(side="left")
        self.adv_brightness_val = ttk.Label(scale_frame, textvariable=self.brightness, width=3)
        self.adv_brightness_val.pack(side="left", padx=(4, 0))
        ttk.Label(r, text="值越大越亮", foreground="gray").pack(side="left", padx=(8, 0))

        self._update_clock_controls()

        # ── ④ 操作按钮 ──────────────────────────────────────────
        f4 = ttk.Frame(main, padding=8)
        f4.pack(fill="x", padx=10, pady=4)

        self.flash_btn = ttk.Button(f4, text="▶  开始烧录", command=self._start_flash)
        self.flash_btn.pack(pady=4)

        # ── ⑤ 烧录进度 ──────────────────────────────────────────
        f5 = ttk.LabelFrame(main, text="④ 烧录进度", padding=8)
        f5.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.progress = ttk.Progressbar(f5, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 4))

        log_frame = ttk.Frame(f5)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=10, state="disabled",
                                wrap="word", font=("Consolas", 9))
        scroll_log = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll_log.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll_log.pack(side="right", fill="y")

        self.status_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(f5, textvariable=self.status_var,
                                      foreground="gray", font=("", 9))
        self.status_label.pack(anchor="w", pady=(4, 0))

        self.post_btn_frame = ttk.Frame(f5)
        self.post_btn_frame.pack(fill="x", pady=(4, 0))

        self.pair_btn = ttk.Button(self.post_btn_frame, text="🔗 配对设备",
                                   command=self._open_pairing, state="disabled")
        self.pair_btn.pack(side="left", padx=(0, 8))

        self.restart_btn = ttk.Button(self.post_btn_frame, text="↻ 重新开始",
                                      command=self._reset_ui, state="disabled")
        self.restart_btn.pack(side="left", padx=(0, 8))

        self.log_btn = ttk.Button(self.post_btn_frame, text="📄 读取设备日志",
                                  command=self._read_device_log, state="disabled")
        self.log_btn.pack(side="left")

        # ── 烧录完成后的操作指引（默认隐藏） ──────────────────────
        self.after_flash_frame = ttk.LabelFrame(f5, text="烧录完成后的操作", padding=8)

        af = self.after_flash_frame
        steps = [
            "1. 等待设备重启（约 10 秒）",
            "2. 设备启动完成后，再点击上方「配对设备」进行蓝牙配对",
            "3. 配对成功后，安装 PC 端上位机插件（说明待补充）",
        ]
        for s in steps:
            ttk.Label(af, text=s, font=("", 9)).pack(anchor="w")
        self._info_btn(af, "PC 端插件是什么？",
                       "PC 端插件是连接 Claude Code 和你设备的桥梁。\n\n"
                       "安装后，Claude Code 的执行状态会自动推送\n"
                       "到你的 ESP32 设备上，实现实时可视化。\n\n"
                       "安装方式（待补充详细链接）：\n"
                       "  claude plugin install claude-buddy\n\n"
                       "或参照项目 README.md 中「安装部署」章节。"
        ).pack(anchor="w", pady=(4, 0))

    # ═══════════════════════════════════════════════════════════════
    #  帮助方法
    # ═══════════════════════════════════════════════════════════════

    def _info_btn(self, parent, title, text):
        """创建一个 ⓘ 按钮，点击弹出帮助说明。"""
        btn = ttk.Button(parent, text="ⓘ", width=2,
                         command=lambda: messagebox.showinfo(title, text))
        return btn

    def _preview_character(self):
        """生成并显示角色预览图（直接调用，无需子进程）。"""
        from scripts.preview_character import build_grid, load_custom_char, ALL_CHARS, ALL_STATES

        char = self.character.get()
        is_custom = char.startswith("自定义")

        tmp = tempfile.gettempdir()
        out = os.path.join(tmp, f"claude_buddy_preview_{char}.png")

        try:
            if is_custom:
                custom_py = os.path.join(ROOT_DIR, "device", "char_custom.py")
                if not os.path.isfile(custom_py):
                    messagebox.showerror("错误", "未找到自定义角色文件 device/char_custom.py，请重新导入")
                    return
                name, cls = load_custom_char(custom_py)
                ALL_CHARS[name] = cls
                build_grid([name], ALL_STATES, 4, out)
            else:
                build_grid([char], ALL_STATES, 4, out)
            os.startfile(out)
        except Exception as e:
            messagebox.showerror("预览失败", str(e))

    def _import_custom_char(self):
        """导入自定义角色 .py 文件。"""
        path = filedialog.askopenfilename(
            title="选择自定义角色文件",
            filetypes=[("Python 文件", "*.py"), ("所有文件", "*.*")],
            initialdir=os.path.join(ROOT_DIR, "device"),
        )
        if not path:
            return

        # 简单校验：检查是否包含 build/tick 方法
        try:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
        except Exception as e:
            messagebox.showerror("读取失败", f"无法读取文件:\n{e}")
            return

        has_build = "def build" in src
        has_tick = "def tick" in src
        if not has_build or not has_tick:
            msg = "所选文件不是有效的角色类。\n\n角色类必须包含以下方法：\n"
            if not has_build:
                msg += "  • build()   — 构建界面元素\n"
            if not has_tick:
                msg += "  • tick()    — 每帧动画回调\n"
            messagebox.showerror("校验失败", msg)
            return

        # 复制到 device/char_custom.py
        dst = os.path.join(ROOT_DIR, "device", "char_custom.py")
        try:
            import shutil
            shutil.copy2(path, dst)
        except Exception as e:
            messagebox.showerror("复制失败", f"无法复制文件到 device/:\n{e}")
            return

        # 更新 ComboBox
        values = list(self.char_combo["values"])
        src_name = os.path.splitext(os.path.basename(path))[0]
        label = f"自定义 ({src_name})"
        # 替换已有的自定义项
        values = [v for v in values if not v.startswith("自定义")]
        values = list(values) + [label]
        self.char_combo["values"] = values
        self.character.set(label)

        # 记住原始路径（用于预览）
        self._custom_char_src = path

        self._append_log(f"[信息] 已导入自定义角色: {path} → {dst}")
        self.view_char_btn.pack(side="left", padx=(4, 0))
        messagebox.showinfo("导入成功",
            f"已将角色文件导入到设备目录。\n\n"
            f"来源: {os.path.basename(path)}\n"
            f"烧录时角色设为「自定义」即可使用。")

    def _view_custom_char(self):
        """预览已导入的自定义角色。"""
        from scripts.preview_character import build_grid, load_custom_char, ALL_CHARS, ALL_STATES
        custom_py = os.path.join(ROOT_DIR, "device", "char_custom.py")
        if not os.path.isfile(custom_py):
            messagebox.showerror("错误", "自定义角色文件不存在，请重新导入")
            self.view_char_btn.pack_forget()
            return
        tmp = tempfile.gettempdir()
        out = os.path.join(tmp, "claude_buddy_preview_custom.png")
        try:
            name, cls = load_custom_char(custom_py)
            ALL_CHARS[name] = cls
            build_grid([name], ALL_STATES, 4, out)
            os.startfile(out)
        except Exception as e:
            messagebox.showerror("预览失败", str(e))

    def _show_custom_char_guide(self):
        """显示自定义角色的操作指引。"""
        messagebox.showinfo(
            "如何自定义面板角色？",
            "🎨 推荐方式：让 Claude Code 帮你生成\n\n"
            "在项目中运行以下提示词：\n"
            "  「读取 device/character.py 看一下角色基类，\n"
            "    参照 char_cat.py 的风格帮我写一个\n"
            "    自定义角色，保存为 char_mychar.py」\n\n"
            "Claude 会理解角色结构并为你生成新角色。\n"
            "生成后修改 config.py 中的 CHARACTER 字段\n"
            "即可生效。\n\n"
            "──────────────\n"
            "⚠️ 自定义角色限制：\n"
            "  • 屏幕分辨率 320×240，角色建议不超过 120px\n"
            "  • 动画帧率 20fps，不要做复杂计算\n"
            "  • 只能用 lvgl 基本图形（矩形、圆、线）\n"
            "  • 不能加载外部图片（MicroPython 不支持）\n"
            "  • 总代码量建议控制在 200 行以内\n\n"
            "参考模板：\n"
            f"  {ROOT_DIR}\\device\\character.py\n"
            "预设角色示例：\n"
            f"  {ROOT_DIR}\\device\\char_cat.py\n"
            f"  {ROOT_DIR}\\device\\char_robot.py"
        )

    # ═══════════════════════════════════════════════════════════════
    #  交互回调
    # ═══════════════════════════════════════════════════════════════

    def _on_variant_change(self):
        v = self.variant.get()
        is_panel = v == "panel"
        is_clock = v == "clock"

        # 角色相关控件
        method = "grid" if is_panel else "grid_remove"
        self.char_combo.configure(state="normal" if is_panel else "disabled")
        self.char_label.configure(foreground="black" if is_panel else "gray")
        for w in (self.char_preview_btn, self.char_info_btn, self.import_char_btn,
                  self.custom_frame):
            getattr(w, method)()

        # 更新语音状态（两种形态都有）
        self._update_voice_status()

        # clock 专属控件（灯光亮度）
        self._update_clock_controls()

    def _update_clock_controls(self):
        if self.variant.get() == "clock":
            self._clock_section.pack(fill="x", pady=(4, 0))
        else:
            self._clock_section.pack_forget()

    def _refresh_ports(self):
        ports = worker.scan_ports()
        self.port_combo["values"] = ports
        if ports:
            current = self.port.get()
            if current not in ports:
                self.port.set(ports[0])
        else:
            self.port.set("")
        self._update_flash_btn()

    def _scan_firmware(self):
        files = worker.scan_firmware_files()
        fw_dir = os.path.join(ROOT_DIR, "firmware")
        full_paths = [os.path.join(fw_dir, f) for f in files]
        self.fw_combo["values"] = full_paths
        if full_paths and not self.firmware_path.get():
            self.firmware_path.set(full_paths[0])

    def _browse_firmware(self):
        path = filedialog.askopenfilename(
            title="选择 MicroPython 固件 (.bin)",
            filetypes=[("固件文件", "*.bin"), ("所有文件", "*.*")],
            initialdir=os.path.join(ROOT_DIR, "firmware"),
        )
        if path:
            self.firmware_path.set(path)

    def _update_voice_status(self):
        presets = worker.scan_voice_presets()
        total = sum(presets.values())
        # 期望: clock 版需要 18 个文件 (7 种状态 × 1~4 个)
        expected = 18
        if total >= expected:
            status = "green"
            text = f"已就绪 ({total} 个 PCM 文件)"
        elif total > 0:
            status = "orange"
            text = f"部分就绪 ({total}/{expected} 个文件)"
        else:
            status = "red"
            text = "未生成语音文件"
        self.voice_label.configure(text=text, foreground=status)

    def _gen_voice(self):
        """在 Toplevel 模态对话框中打开语音生成工具。"""
        from scripts.gen_voice_assets import App
        app = App(master=None)
        app.transient(self)
        app.grab_set()
        self.wait_window(app)

    def _update_flash_btn(self):
        if self._is_flashing:
            return
        has_port = bool(self.port.get())
        self.flash_btn.configure(state="normal" if has_port else "disabled")

    # ═══════════════════════════════════════════════════════════════
    #  烧录流程
    # ═══════════════════════════════════════════════════════════════

    def _collect_params(self) -> dict:
        raw_char = self.character.get()
        # "自定义 (xxx)" → "custom"
        character = "custom" if raw_char.startswith("自定义") else raw_char
        p = {
            "port":          self.port.get(),
            "variant":       self.variant.get(),
            "character":     character if self.variant.get() == "panel" else "claude",
            "comm_method":   self.comm_method.get(),
            "flash_firmware": self.flash_firmware.get(),
            "firmware_path": self.firmware_path.get() or None,
            "wipe":          self.wipe.get(),
            "generate_voice": self.generate_voice.get(),
            "voice_enable":  self.voice_enable.get(),
            "voice_speed":   self.voice_speed.get(),
            # 高级参数覆盖
            "FPS":               self.fps.get(),
            "HEARTBEAT_TIMEOUT": self.heartbeat.get(),
            "LOG_ENABLE":        self.log_enable.get(),
            "LIGHT_CONNECT_BRIGHTNESS": self.brightness.get(),
        }
        return p

    def _preflight_check(self) -> tuple[bool, str]:
        """烧录前预检：串口、固件、依赖工具。"""
        # 1. 串口
        port = self.port.get()
        if not port:
            return False, "请先选择串口设备（② 设备连接 → 串口）"
        available = worker.scan_ports()
        if port not in available:
            return False, f"串口 {port} 不在可用列表中，请检查设备连接后点击「↻ 刷新」"

        # 2. 固件文件
        if self.flash_firmware.get():
            fw = self.firmware_path.get()
            if not fw:
                return False, "已勾选「烧录底层固件」，请选择固件文件"
            if not os.path.isfile(fw):
                return False, f"固件文件不存在:\n{fw}"

        # 3. esptool
        if self.flash_firmware.get():
            try:
                import esptool  # noqa: F401
            except ImportError:
                return False, "缺少 esptool，请运行: pip install esptool"

        # 4. mpremote
        try:
            import mpremote  # noqa: F401
        except ImportError:
            return False, "缺少 mpremote，请运行: pip install mpremote"

        # 5. 固件路径有效性（手动浏览时）
        fw_manual = self.firmware_path.get()
        if fw_manual and not os.path.isfile(fw_manual):
            return False, f"固件文件路径无效:\n{fw_manual}"

        return True, ""

    def _start_flash(self):
        if self._is_flashing:
            self._cancel_flash()
            return

        # ── 烧录前预检 ─────────────────────────────────────────────
        ok, msg = self._preflight_check()
        if not ok:
            messagebox.showerror("预检失败", msg)
            return

        if self.wipe.get():
            ok = messagebox.askyesno("确认", "清空文件系统会删除设备上所有文件，确认继续？")
            if not ok:
                self.wipe.set(False)
                return

        self._is_flashing = True
        self._flash_success = False
        self.flash_btn.configure(text="■  取消")
        self.progress["value"] = 0
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")
        self.status_var.set("正在烧录...")
        self.status_label.configure(foreground="blue")
        self.pair_btn.configure(state="disabled")
        self.restart_btn.configure(state="disabled")
        self.log_btn.configure(state="disabled")
        self.after_flash_frame.pack_forget()

        params = self._collect_params()
        self._worker = worker.FlashWorker()
        self._worker.run(
            params=params,
            on_log=lambda t: self.after(0, self._append_log, t),
            on_progress=lambda v: self.after(0, self._set_progress, v),
            on_done=lambda ok, msg: self.after(0, self._flash_done, ok, msg),
        )

    def _set_progress(self, v: int):
        self.progress["value"] = v

    def _cancel_flash(self):
        if self._worker:
            self._worker.cancel()
        self._append_log("[取消] 用户取消烧录")
        self._flash_done(False, "已取消")

    def _append_log(self, text: str):
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        tag = "normal"
        if text.startswith("[错误]"):
            tag = "error"
        elif text.startswith("[警告]"):
            tag = "warn"
        elif text.startswith("✓"):
            tag = "success"
        self.log_text.tag_configure("error", foreground="red")
        self.log_text.tag_configure("warn", foreground="darkorange")
        self.log_text.tag_configure("success", foreground="green")
        self.log_text.insert(tk.END, f"[{ts}] {text}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _flash_done(self, success: bool, message: str):
        self._is_flashing = False
        self._flash_success = success

        if success:
            self.flash_btn.configure(text="✓  重新开始", state="normal")
            self.status_var.set(f"✅ 烧录完成 — {message}")
            self.status_label.configure(foreground="green")
            self.pair_btn.configure(state="normal")
            self.log_btn.configure(state="normal")
            self.after_flash_frame.pack(fill="x", pady=(6, 0))
        else:
            self.flash_btn.configure(text="↻  重新开始", state="normal")
            self.status_var.set(f"❌ {message}")
            self.status_label.configure(foreground="red")

        self.progress["value"] = 100 if success else 0
        self.restart_btn.configure(state="normal")
        self._update_flash_btn()

    def _reset_ui(self):
        self._is_flashing = False
        self._flash_success = False
        self.progress["value"] = 0
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")
        self.status_var.set("就绪")
        self.status_label.configure(foreground="gray")
        self.flash_btn.configure(text="▶  开始烧录", state="normal")
        self.pair_btn.configure(state="disabled")
        self.restart_btn.configure(state="disabled")
        self.log_btn.configure(state="disabled")
        self.after_flash_frame.pack_forget()
        self._update_flash_btn()
        self._refresh_ports()

    # ═══════════════════════════════════════════════════════════════
    #  日志读取 & 配对
    # ═══════════════════════════════════════════════════════════════

    def _read_device_log(self):
        """读取设备日志（直接调用，无需子进程）。"""
        try:
            from scripts.read_device_log import read_logs
            port = self.port.get()
            output = read_logs(port=port) or "(无输出)"
        except Exception as e:
            output = f"读取失败: {e}"

        # 弹出对话框显示日志
        win = tk.Toplevel(self)
        win.title("设备日志")
        win.geometry("700x500")
        win.transient(self)
        win.grab_set()
        txt = tk.Text(win, wrap="word", font=("Consolas", 9))
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("1.0", output)
        txt.configure(state="disabled")
        scroll = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        scroll.pack(side="right", fill="y")
        txt.configure(yscrollcommand=scroll.set)
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 8))

    def _open_pairing(self):
        from setup_tool.pairing import PairingDialog
        PairingDialog(self, self.comm_method.get())

    def _on_close(self):
        if self._is_flashing and self._worker:
            self._worker.cancel()
        self.destroy()
