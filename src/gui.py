import base64
import csv
import ctypes
import fnmatch
import os
import queue
import shutil
import struct
import sys
import time
import winreg
import zlib
from datetime import date, datetime, timedelta
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from ctypes import wintypes
from PIL import Image
import pystray
if __package__:
    from .database import (
        create_database,
        delete_all_activities,
        delete_activities_for_period,
        get_activities_for_period,
        get_daily_totals,
        get_setting,
        get_summary_for_period,
        log_activity,
        set_setting,
    )
    from .tracker import (
        get_active_window_info,
        get_idle_time,
        get_system_sleep_time,
    )
else:
    from database import (
        create_database,
        delete_all_activities,
        delete_activities_for_period,
        get_activities_for_period,
        get_daily_totals,
        get_setting,
        get_summary_for_period,
        log_activity,
        set_setting,
    )
    from tracker import (
        get_active_window_info,
        get_idle_time,
        get_system_sleep_time,
    )


def resource_path(relative_path: str) -> str:
    """Gets absolute path to resources, working both in dev mode and inside PyInstaller .exe."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def _resolve_executable_path(app_name: str, executable_path: str) -> str:
    if executable_path and os.path.isfile(executable_path):
        return executable_path

    executable_name = os.path.basename(executable_path or app_name)
    path = shutil.which(executable_name)
    if path:
        return path

    registry_path = (
        "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"
        + executable_name
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY, 0):
            try:
                with winreg.OpenKey(
                    hive, registry_path, 0, winreg.KEY_READ | view
                ) as key:
                    path, _ = winreg.QueryValueEx(key, None)
                if os.path.isfile(path):
                    return path
            except OSError:
                continue
    return ""


def _windows_startup_command() -> str:
    if getattr(sys, "frozen", False):
        executable = sys.executable
        arguments = "--minimized"
    else:
        executable = os.path.splitext(sys.executable)[0] + "w.exe"
        if not os.path.isfile(executable):
            executable = sys.executable
        arguments = f'"{os.path.abspath(__file__)}" --minimized'
    return f'"{executable}" {arguments}'


def _set_windows_startup(enabled: bool):
    run_key_path = "Software\\Microsoft\\Windows\\CurrentVersion\\Run"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        run_key_path,
        0,
        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key, "TimeTracker", 0, winreg.REG_SZ, _windows_startup_command()
            )
        else:
            try:
                winreg.DeleteValue(key, "TimeTracker")
            except FileNotFoundError:
                pass


def _load_minute_setting(key: str, default: int) -> int:
    try:
        minutes = int(get_setting(key, str(default)))
    except ValueError:
        return default
    return minutes if 0 <= minutes <= 1440 else default


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    chunk = chunk_type + data
    return (
        struct.pack(">I", len(data))
        + chunk
        + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    )


def _extract_executable_icon(executable_path: str) -> bytes | None:
    class ShellFileInfo(ctypes.Structure):
        _fields_ = [
            ("hIcon", wintypes.HANDLE),
            ("iIcon", ctypes.c_int),
            ("dwAttributes", wintypes.DWORD),
            ("szDisplayName", wintypes.WCHAR * 260),
            ("szTypeName", wintypes.WCHAR * 80),
        ]

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader)]

    width = height = 20
    file_info = ShellFileInfo()
    shell_get_file_info = ctypes.windll.shell32.SHGetFileInfoW
    shell_get_file_info.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ShellFileInfo),
        wintypes.UINT,
        wintypes.UINT,
    ]
    shell_get_file_info.restype = ctypes.c_size_t
    if not shell_get_file_info(
        executable_path,
        0,
        ctypes.byref(file_info),
        ctypes.sizeof(file_info),
        0x100,
    ) or not file_info.hIcon:
        return None

    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.DrawIconEx.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.HANDLE,
        wintypes.UINT,
    ]
    user32.DrawIconEx.restype = wintypes.BOOL
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.DestroyIcon.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    screen_dc = user32.GetDC(None)
    memory_dc = None
    bitmap = None
    old_bitmap = None
    try:
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            return None

        bitmap_info = BitmapInfo(
            BitmapInfoHeader(
                ctypes.sizeof(BitmapInfoHeader),
                width,
                -height,
                1,
                32,
                0,
                width * height * 4,
                0,
                0,
                0,
                0,
            )
        )
        pixel_data = ctypes.c_void_p()
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(BitmapInfo),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        gdi32.CreateDIBSection.restype = wintypes.HANDLE
        bitmap = gdi32.CreateDIBSection(
            memory_dc,
            ctypes.byref(bitmap_info),
            0,
            ctypes.byref(pixel_data),
            None,
            0,
        )
        if not bitmap or not pixel_data.value:
            return None

        old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
        ctypes.memset(pixel_data, 255, width * height * 4)
        if not user32.DrawIconEx(
            memory_dc, 0, 0, file_info.hIcon, width, height, 0, None, 3
        ):
            return None

        bgra = ctypes.string_at(pixel_data, width * height * 4)
        rgba = bytearray(width * height * 4)
        for offset in range(0, len(bgra), 4):
            rgba[offset : offset + 4] = bytes(
                (bgra[offset + 2], bgra[offset + 1], bgra[offset], 255)
            )

        scanlines = b"".join(
            b"\0" + rgba[row * width * 4 : (row + 1) * width * 4]
            for row in range(height)
        )
        return (
            b"\x89PNG\r\n\x1a\n"
            + _png_chunk(
                b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0)
            )
            + _png_chunk(b"IDAT", zlib.compress(scanlines))
            + _png_chunk(b"IEND", b"")
        )
    finally:
        if old_bitmap:
            gdi32.SelectObject(memory_dc, old_bitmap)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)
        ctypes.windll.user32.DestroyIcon(file_info.hIcon)


class TimeTrackerGUI:

    def __init__(self, root):
        self.root = root
        self.root.title("Time Tracker")
        self.root.geometry("940x720")
        self.root.minsize(760, 600)

        icon_path = resource_path(os.path.join("assets", "app_icon.ico"))
        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)

        # Guarantee database exists
        create_database()

        # State tracking
        self.is_tracking = False
        self.current_app = ""
        self.current_title = ""
        self.current_executable_path = ""
        self.current_activity_excluded = False
        self.resume_after_idle = False
        self.track_after_id = None
        self.start_time = 0.0
        self.last_check_time = 0.0
        self.sleep_time = 0.0
        self.app_images: dict[str, tk.PhotoImage | None] = {}
        self.tray_icon = None
        self.tray_actions = queue.Queue()
        self._closing = False
        self.idle_threshold_minutes = _load_minute_setting(
            "idle_threshold_minutes", 5
        )
        self.excluded_apps_var = tk.StringVar(value=get_setting("excluded_apps"))
        self.excluded_titles_var = tk.StringVar(
            value=get_setting("excluded_titles")
        )
        self.daily_goal_minutes = _load_minute_setting(
            "daily_goal_minutes", 240
        )
        saved_theme = get_setting("theme", "Dark")
        self.theme_name = (
            saved_theme if saved_theme in ("Light", "Dark") else "Dark"
        )

        # Apply visual theme
        self._configure_styles()

        # Build UI
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.root.bind("<Unmap>", self._on_minimize)
        self.refresh_table()
        self._setup_tray_icon()
        self.root.after(100, self._process_tray_actions)

    def _setup_tray_icon(self):
        icon_path = resource_path(os.path.join("assets", "app_icon.ico"))
        with Image.open(icon_path) as image:
            tray_image = image.convert("RGBA").resize(
                (64, 64), Image.Resampling.LANCZOS
            )

        self.tray_icon = pystray.Icon(
            "TimeTracker",
            tray_image,
            "Time Tracker",
            menu=pystray.Menu(
                pystray.MenuItem(
                    "Show Time Tracker",
                    lambda _icon, _item: self._schedule_tk_action(
                        self.show_window
                    ),
                    default=True,
                ),
                pystray.MenuItem(
                    lambda _item: (
                        "Pause tracking" if self.is_tracking
                        else "Start tracking"
                    ),
                    lambda _icon, _item: self._schedule_tk_action(
                        self.toggle_tracking
                    ),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Exit",
                    lambda _icon, _item: self._schedule_tk_action(
                        self.close_app
                    ),
                ),
            ),
        )
        self.tray_icon.run_detached()

    def _schedule_tk_action(self, action):
        if not self._closing:
            self.tray_actions.put(action)

    def _process_tray_actions(self):
        while True:
            try:
                action = self.tray_actions.get_nowait()
            except queue.Empty:
                break
            if not self._closing:
                action()
        if not self._closing:
            self.root.after(100, self._process_tray_actions)

    def _on_minimize(self, _event=None):
        if self.root.state() == "iconic":
            self.root.after_idle(self.root.withdraw)

    def show_window(self):
        if self._closing:
            return
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def toggle_tracking(self):
        if self.is_tracking:
            self.stop_tracking()
        else:
            self.start_tracking()

    def _schedule_track_loop(self):
        if self.track_after_id is None:
            self.track_after_id = self.root.after(1000, self.track_loop)

    def close_app(self):
        if self._closing:
            return
        self._closing = True
        if self.is_tracking:
            self.stop_tracking()
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.root.destroy()

    def _configure_styles(self):
        """Sets up custom fonts, colors, and button styles using ttk."""
        self.style = ttk.Style()
        self.style.theme_use("clam")

        palettes = {
            "Light": {
                "background": "#F1F5F9",
                "surface": "#FDFFFC",
                "surface_alt": "#F6F9FC",
                "border": "#E2E8F0",
                "text": "#0B132B",
                "muted": "#64748B",
                "button_text": "#0B132B",
                "danger_text": "#FDFFFC",
                "blue": "#39A0ED",
                "blue_hover": "#2386CF",
                "blue_light": "#EAF5FC",
                "red": "#CC2936",
                "red_hover": "#B5222D",
            },
            "Dark": {
                "background": "#0B132B",
                "surface": "#111C36",
                "surface_alt": "#172442",
                "border": "#2B3A59",
                "text": "#FDFFFC",
                "muted": "#AAB6CC",
                "button_text": "#0B132B",
                "danger_text": "#FDFFFC",
                "blue": "#39A0ED",
                "blue_hover": "#2386CF",
                "blue_light": "#203A56",
                "red": "#CC2936",
                "red_hover": "#B5222D",
            },
        }
        self.palette = palettes[self.theme_name]
        colors = self.palette

        self.root.configure(bg=colors["background"])
        self.style.configure("TFrame", background=colors["background"])
        self.style.configure(
            "Main.TFrame", background=colors["background"]
        )
        self.style.configure(
            "Card.TFrame", background=colors["surface"], relief="flat"
        )
        self.style.configure(
            "TLabel",
            background=colors["background"],
            foreground=colors["text"],
            font=("Segoe UI", 9),
        )
        self.style.configure(
            "Body.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=("Segoe UI", 9),
        )

        self.style.configure(
            "Header.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure(
            "Title.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=("Segoe UI", 19, "bold"),
        )
        self.style.configure(
            "Subtitle.TLabel",
            background=colors["surface"],
            foreground=colors["muted"],
            font=("Segoe UI", 9),
        )
        self.style.configure(
            "Status.TLabel",
            background=colors["surface"],
            foreground=colors["muted"],
            font=("Segoe UI", 9, "bold"),
        )
        self.style.configure(
            "Status.Active.TLabel",
            background=colors["surface"],
            foreground=colors["blue"],
            font=("Segoe UI", 9, "bold"),
        )
        self.style.configure(
            "Status.Warning.TLabel",
            background=colors["surface"],
            foreground=colors["red"],
            font=("Segoe UI", 9, "bold"),
        )

        self.style.configure(
            "Treeview",
            background=colors["surface"],
            foreground=colors["text"],
            fieldbackground=colors["surface"],
            rowheight=32,
            font=("Segoe UI", 9),
            bordercolor=colors["border"],
            borderwidth=0,
            relief="flat",
        )
        self.style.configure(
            "Treeview.Heading",
            background=colors["surface_alt"],
            foreground=colors["muted"],
            font=("Segoe UI", 9, "bold"),
            padding=(9, 8),
            relief="flat",
        )
        self.style.map(
            "Treeview",
            background=[("selected", colors["blue_light"])],
            foreground=[("selected", colors["blue"])],
        )
        self.style.map(
            "Treeview.Heading",
            background=[("active", colors["blue_light"])],
            foreground=[("active", colors["blue"])],
        )

        self.style.configure(
            "TButton",
            font=("Segoe UI", 9, "bold"),
            background=colors["surface"],
            foreground=colors["text"],
            padding=(12, 8),
            bordercolor=colors["border"],
            lightcolor=colors["surface"],
            darkcolor=colors["surface"],
            focusthickness=1,
            focuscolor=colors["blue"],
        )
        self.style.configure(
            "Primary.TButton",
            background=colors["blue"],
            foreground=colors["button_text"],
            bordercolor=colors["blue"],
        )
        self.style.map(
            "Primary.TButton",
            background=[
                ("disabled", colors["border"]),
                ("active", colors["blue_hover"]),
            ],
            foreground=[("disabled", colors["muted"])],
        )
        self.style.configure(
            "Danger.TButton",
            background=colors["red"],
            foreground=colors["danger_text"],
            bordercolor=colors["red"],
        )
        self.style.map(
            "Danger.TButton",
            background=[
                ("disabled", colors["surface_alt"]),
                ("active", colors["red_hover"]),
            ],
            foreground=[("disabled", colors["muted"])],
        )
        self.style.map(
            "TButton",
            background=[
                ("disabled", colors["surface_alt"]),
                ("active", colors["blue_light"]),
            ],
            foreground=[
                ("disabled", colors["muted"]),
                ("active", colors["blue"]),
            ],
        )
        self.style.configure(
            "TEntry",
            fieldbackground=colors["surface"],
            foreground=colors["text"],
            insertcolor=colors["text"],
            bordercolor=colors["border"],
            padding=(8, 7),
        )
        self.style.map(
            "TEntry",
            bordercolor=[("focus", colors["blue"])],
            fieldbackground=[("disabled", colors["surface_alt"])],
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=colors["surface"],
            background=colors["surface"],
            foreground=colors["text"],
            arrowcolor=colors["muted"],
            bordercolor=colors["border"],
            padding=(7, 6),
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["surface"])],
            selectbackground=[("readonly", colors["surface"])],
            selectforeground=[("readonly", colors["text"])],
        )
        self.style.configure(
            "TCheckbutton",
            background=colors["background"],
            foreground=colors["text"],
            indicatorcolor=colors["surface"],
            focuscolor=colors["blue"],
        )
        self.style.map(
            "TCheckbutton",
            background=[("active", colors["blue_light"])],
            foreground=[("active", colors["blue"])],
            indicatorcolor=[
                ("selected", colors["blue"]),
                ("active", colors["blue_light"]),
            ],
        )
        self.style.configure(
            "TNotebook",
            background=colors["surface"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        self.style.configure(
            "TNotebook.Tab",
            background=colors["surface_alt"],
            foreground=colors["muted"],
            font=("Segoe UI", 9, "bold"),
            padding=(14, 9),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[
                ("selected", colors["surface"]),
                ("active", colors["blue_light"]),
            ],
            foreground=[
                ("selected", colors["blue"]),
                ("active", colors["blue"]),
            ],
        )
        self.style.configure(
            "Horizontal.TProgressbar",
            background=colors["blue"],
            troughcolor=colors["blue_light"],
            bordercolor=colors["blue_light"],
            thickness=8,
        )
        self.style.configure(
            "Vertical.TScrollbar",
            background=colors["surface_alt"],
            troughcolor=colors["surface"],
            bordercolor=colors["surface"],
            arrowcolor=colors["muted"],
        )
        self.style.configure("TSeparator", background=colors["border"])

    def _apply_theme(self, theme_name: str):
        if theme_name not in ("Light", "Dark"):
            raise ValueError(f"Unsupported theme: {theme_name}")

        self.theme_name = theme_name
        self._configure_styles()
        colors = self.palette
        self.period_error_label.configure(
            background=colors["surface"], foreground=colors["red"]
        )
        self.total_time_label.configure(
            background=colors["surface"], foreground=colors["blue"]
        )
        self.top_app_label.configure(
            background=colors["surface"], foreground=colors["text"]
        )
        self.goal_label.configure(
            background=colors["surface"], foreground=colors["muted"]
        )
        self.chart.configure(background=colors["surface"])
        for tree in (self.tree, self.timeline_tree):
            tree.tag_configure("alternate", background=colors["surface_alt"])
        self._draw_chart()

    def _build_ui(self):
        main_container = ttk.Frame(self.root, style="Main.TFrame", padding=15)
        main_container.pack(fill=tk.BOTH, expand=True)

        control_card = ttk.Frame(
            main_container, style="Card.TFrame", padding=(20, 15)
        )
        control_card.pack(fill=tk.X, pady=(0, 15))

        title_frame = ttk.Frame(control_card, style="Card.TFrame")
        title_frame.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(
            title_frame, text="Time Tracker", style="Title.TLabel"
        ).pack(anchor=tk.W)
        ttk.Label(
            title_frame,
            text="A clear view of where your time goes",
            style="Subtitle.TLabel",
        ).pack(anchor=tk.W, pady=(1, 0))

        self.status_label = ttk.Label(
            control_card, text="Status: STOPPED", style="Status.TLabel"
        )
        self.status_label.pack(side=tk.LEFT, padx=(28, 0))

        self.btn_stop = ttk.Button(
            control_card,
            text="Stop",
            style="Danger.TButton",
            command=self.stop_tracking,
            state=tk.DISABLED,
        )
        self.btn_stop.pack(side=tk.RIGHT, padx=(8, 0))

        self.btn_start = ttk.Button(
            control_card,
            text="Start",
            style="Primary.TButton",
            command=self.start_tracking,
        )
        self.btn_start.pack(side=tk.RIGHT, padx=(8, 0))

        period_card = ttk.Frame(
            main_container, style="Card.TFrame", padding=(14, 10)
        )
        period_card.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(
            period_card, text="Showing", style="Body.TLabel"
        ).pack(side=tk.LEFT, padx=(2, 8))
        self.period_var = tk.StringVar(value="Today")
        self.period_combo = ttk.Combobox(
            period_card,
            textvariable=self.period_var,
            values=("Today", "This Week", "Custom"),
            state="readonly",
            width=12,
        )
        self.period_combo.pack(side=tk.LEFT)
        self.period_combo.bind(
            "<<ComboboxSelected>>", self._on_period_changed
        )
        self.start_date_var = tk.StringVar(value=date.today().isoformat())
        self.end_date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Label(
            period_card, text="From", style="Body.TLabel"
        ).pack(side=tk.LEFT, padx=(12, 4))
        self.start_date_entry = ttk.Entry(
            period_card, textvariable=self.start_date_var, width=11
        )
        self.start_date_entry.pack(side=tk.LEFT)
        ttk.Label(
            period_card, text="To", style="Body.TLabel"
        ).pack(side=tk.LEFT, padx=(8, 4))
        self.end_date_entry = ttk.Entry(
            period_card, textvariable=self.end_date_var, width=11
        )
        self.end_date_entry.pack(side=tk.LEFT)
        for entry in (self.start_date_entry, self.end_date_entry):
            entry.bind("<Return>", lambda _event: self.refresh_table())
            entry.bind("<FocusOut>", lambda _event: self.refresh_table())
        self.period_error_label = ttk.Label(
            period_card,
            foreground=self.palette["red"],
            background=self.palette["surface"],
        )
        self.period_error_label.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(period_card, text="Search", style="Body.TLabel").pack(
            side=tk.LEFT, padx=(12, 4)
        )
        self.search_var = tk.StringVar()
        self.search_var.trace_add(
            "write", lambda *_args: self.refresh_table()
        )
        ttk.Entry(
            period_card, textvariable=self.search_var, width=16
        ).pack(side=tk.LEFT)
        ttk.Button(
            period_card,
            text="Settings",
            command=self.show_settings,
        ).pack(side=tk.RIGHT, padx=4)
        ttk.Button(
            period_card,
            text="Export CSV",
            style="Primary.TButton",
            command=self.export_csv,
        ).pack(side=tk.RIGHT, padx=4)

        dashboard_card = ttk.Frame(
            main_container, style="Card.TFrame", padding=15
        )
        dashboard_card.pack(fill=tk.X, pady=(0, 12))

        summary_frame = ttk.Frame(dashboard_card, style="Card.TFrame")
        summary_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
        ttk.Label(
            summary_frame, text="TOTAL TRACKED", style="Header.TLabel"
        ).pack(anchor=tk.W)
        self.total_time_label = ttk.Label(
            summary_frame,
            text="0s",
            background=self.palette["surface"],
            foreground=self.palette["blue"],
            font=("Segoe UI", 25, "bold"),
        )
        self.total_time_label.pack(anchor=tk.W, pady=(4, 12))
        ttk.Label(
            summary_frame, text="MOST USED APP", style="Header.TLabel"
        ).pack(anchor=tk.W)
        self.top_app_label = ttk.Label(
            summary_frame,
            text="No activity yet",
            background=self.palette["surface"],
            foreground=self.palette["text"],
            font=("Segoe UI", 10),
            wraplength=180,
        )
        self.top_app_label.pack(anchor=tk.W, pady=(4, 0))
        ttk.Label(
            summary_frame, text="DAILY GOAL", style="Header.TLabel"
        ).pack(anchor=tk.W, pady=(12, 0))
        self.goal_label = ttk.Label(
            summary_frame,
            text="0 / 0 min",
            background=self.palette["surface"],
            foreground=self.palette["muted"],
            font=("Segoe UI", 9),
        )
        self.goal_label.pack(anchor=tk.W, pady=(4, 2))
        self.goal_progress = ttk.Progressbar(
            summary_frame, maximum=100, length=180, mode="determinate"
        )
        self.goal_progress.pack(anchor=tk.W)

        ttk.Separator(dashboard_card, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(0, 20)
        )
        chart_frame = ttk.Frame(dashboard_card, style="Card.TFrame")
        chart_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.chart_title = ttk.Label(
            chart_frame, text="Top applications", style="Header.TLabel"
        )
        self.chart_title.pack(anchor=tk.W, pady=(0, 4))
        self.chart = tk.Canvas(
            chart_frame,
            height=170,
            background=self.palette["surface"],
            highlightthickness=0,
        )
        self.chart.pack(fill=tk.BOTH, expand=True)
        self.chart.bind("<Configure>", lambda _event: self._draw_chart())

        # Period summary and timeline
        table_card = ttk.Frame(main_container, style="Card.TFrame", padding=15)
        table_card.pack(fill=tk.BOTH, expand=True)

        self.history_tabs = ttk.Notebook(table_card)
        self.history_tabs.pack(fill=tk.BOTH, expand=True)
        summary_tab = ttk.Frame(self.history_tabs, style="Card.TFrame")
        timeline_tab = ttk.Frame(self.history_tabs, style="Card.TFrame")
        self.history_tabs.add(summary_tab, text="App Summary")
        self.history_tabs.add(timeline_tab, text="Timeline")
        summary_controls = ttk.Frame(
            summary_tab, style="Card.TFrame", padding=(8, 10, 8, 8)
        )
        summary_controls.pack(fill=tk.X)
        ttk.Label(
            summary_controls,
            text="Select an app to exclude it from future tracking.",
            style="Subtitle.TLabel",
        ).pack(side=tk.LEFT)
        self.exclude_summary_app_button = ttk.Button(
            summary_controls,
            text="Exclude selected app",
            command=self.exclude_selected_summary_app,
            state=tk.DISABLED,
        )
        self.exclude_summary_app_button.pack(side=tk.RIGHT)
        timeline_controls = ttk.Frame(
            timeline_tab, style="Card.TFrame", padding=(8, 10, 8, 8)
        )
        timeline_controls.pack(fill=tk.X)
        ttk.Label(
            timeline_controls,
            text="Select an activity to exclude its app from future tracking.",
            style="Subtitle.TLabel",
        ).pack(side=tk.LEFT)
        self.exclude_timeline_app_button = ttk.Button(
            timeline_controls,
            text="Exclude selected app",
            command=self.exclude_selected_timeline_app,
            state=tk.DISABLED,
        )
        self.exclude_timeline_app_button.pack(side=tk.RIGHT)

        columns = ("app", "window_title", "time")
        self.tree = ttk.Treeview(
            summary_tab,
            columns=columns,
            show="tree headings",
            selectmode="browse",
            style="Treeview",
        )
        self.tree.tag_configure(
            "alternate", background=self.palette["surface_alt"]
        )
        self.tree.heading("#0", text="Icon")
        self.tree.column(
            "#0", width=60, minwidth=40, anchor=tk.CENTER, stretch=False
        )
        self.tree.heading("app", text="Application Name")
        self.tree.heading("window_title", text="Window Title")
        self.tree.heading("time", text="Total Time Spent")
        self.tree.column("app", width=170, anchor=tk.W)
        self.tree.column("window_title", width=300, anchor=tk.W)
        self.tree.column("time", width=140, anchor=tk.E)

        scrollbar = ttk.Scrollbar(
            summary_tab, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind(
            "<<TreeviewSelect>>", self._on_summary_selection_changed
        )

        timeline_columns = ("timestamp", "app", "window_title", "duration")
        self.timeline_tree = ttk.Treeview(
            timeline_tab,
            columns=timeline_columns,
            show="headings",
            selectmode="browse",
            style="Treeview",
        )
        self.timeline_tree.tag_configure(
            "alternate", background=self.palette["surface_alt"]
        )
        for column, heading in (
            ("timestamp", "Start Time"),
            ("app", "Application"),
            ("window_title", "Window Title"),
            ("duration", "Duration"),
        ):
            self.timeline_tree.heading(column, text=heading)
        self.timeline_tree.column("timestamp", width=155, anchor=tk.W)
        self.timeline_tree.column("app", width=150, anchor=tk.W)
        self.timeline_tree.column("window_title", width=310, anchor=tk.W)
        self.timeline_tree.column("duration", width=100, anchor=tk.E)
        timeline_scrollbar = ttk.Scrollbar(
            timeline_tab,
            orient=tk.VERTICAL,
            command=self.timeline_tree.yview,
        )
        self.timeline_tree.configure(yscrollcommand=timeline_scrollbar.set)
        self.timeline_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        timeline_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.timeline_tree.bind(
            "<<TreeviewSelect>>", self._on_timeline_selection_changed
        )

        self.start_date_entry.configure(state="disabled")
        self.end_date_entry.configure(state="disabled")

    def _on_period_changed(self, _event=None):
        custom_enabled = self.period_var.get() == "Custom"
        state = "normal" if custom_enabled else "disabled"
        self.start_date_entry.configure(state=state)
        self.end_date_entry.configure(state=state)
        self.refresh_table()

    def _on_timeline_selection_changed(self, _event=None):
        state = (
            tk.NORMAL
            if self.timeline_tree.selection()
            else tk.DISABLED
        )
        self.exclude_timeline_app_button.configure(state=state)

    def _on_summary_selection_changed(self, _event=None):
        state = tk.NORMAL if self.tree.selection() else tk.DISABLED
        self.exclude_summary_app_button.configure(state=state)

    @staticmethod
    def _literal_glob_pattern(value: str) -> str:
        replacements = {"[": "[[]", "]": "[]]", "*": "[*]", "?": "[?]"}
        return "".join(
            replacements.get(character, character) for character in value
        )

    def exclude_selected_timeline_app(self):
        selection = self.timeline_tree.selection()
        if not selection:
            self._on_timeline_selection_changed()
            return

        selected_values = self.timeline_tree.item(selection[0], "values")
        self._exclude_app(selected_values[1])

    def exclude_selected_summary_app(self):
        selection = self.tree.selection()
        if not selection:
            self._on_summary_selection_changed()
            return

        selected_values = self.tree.item(selection[0], "values")
        self._exclude_app(selected_values[0])

    def _exclude_app(self, app_name: str):
        patterns = [
            pattern.strip()
            for pattern in self.excluded_apps_var.get().splitlines()
            if pattern.strip()
        ]
        if self._patterns_match(app_name, "\n".join(patterns)):
            messagebox.showinfo(
                "Already excluded",
                f"{app_name} is already excluded from tracking.",
                parent=self.root,
            )
            return

        patterns.append(self._literal_glob_pattern(app_name))
        excluded_apps = "\n".join(patterns)
        set_setting("excluded_apps", excluded_apps)
        self.excluded_apps_var.set(excluded_apps)

        if (
            self.is_tracking
            and self.current_app.casefold() == app_name.casefold()
            and not self.current_activity_excluded
        ):
            now = time.time()
            log_activity(
                self.current_app,
                self.current_title,
                max(0.0, now - self.start_time),
                self.current_executable_path,
            )
            self.current_activity_excluded = True
            self.start_time = now
            self.last_check_time = now

        self.refresh_table()
        messagebox.showinfo(
            "App excluded",
            f"{app_name} will no longer be recorded. Existing history is unchanged.",
            parent=self.root,
        )

    def _period_dates(self) -> tuple[date, date]:
        today = date.today()
        if self.period_var.get() == "This Week":
            start_date = today - timedelta(days=today.weekday())
            return start_date, start_date + timedelta(days=7)
        if self.period_var.get() == "Custom":
            start_date = date.fromisoformat(self.start_date_var.get().strip())
            selected_end = date.fromisoformat(self.end_date_var.get().strip())
            if selected_end < start_date:
                raise ValueError("End date must be on or after the start date.")
            return start_date, selected_end + timedelta(days=1)
        return today, today + timedelta(days=1)

    def format_time(self, seconds: float) -> str:
        """Converts raw seconds into a readable format (e.g. 1h 23m 45s)."""
        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

    def refresh_table(self):
        """Refresh the selected period's app table and dashboard."""
        try:
            start_date, end_date = self._period_dates()
        except ValueError:
            self.period_error_label.config(
                text="Enter a valid date range (YYYY-MM-DD)."
            )
            return
        self.period_error_label.config(text="")
        for row in self.tree.get_children():
            self.tree.delete(row)
        for row in self.timeline_tree.get_children():
            self.timeline_tree.delete(row)
        self._on_summary_selection_changed()
        self._on_timeline_selection_changed()

        start_key, end_key = start_date.isoformat(), end_date.isoformat()
        summary_data = get_summary_for_period(start_key, end_key)
        self.refresh_dashboard(summary_data, start_date, end_date)

        search = self.search_var.get().strip().casefold()
        visible_summary = [
            row
            for row in summary_data
            if not self._patterns_match(row[0], self.excluded_apps_var.get())
            and (
                not search
                or search in row[0].casefold()
                or search in row[1].casefold()
            )
        ]
        for index, (
            app_name,
            window_title,
            total_seconds,
            executable_path,
        ) in enumerate(visible_summary):
            image = self._get_app_image(app_name, executable_path)
            item_options = {
                "values": (
                    app_name,
                    window_title,
                    self.format_time(total_seconds),
                ),
                "tags": ("alternate",) if index % 2 else (),
            }
            if image is not None:
                item_options["image"] = image
            self.tree.insert(
                "",
                tk.END,
                **item_options,
            )
        timeline = get_activities_for_period(start_key, end_key)
        visible_timeline_index = 0
        for timestamp, app_name, window_title, duration in timeline:
            if search and search not in app_name.casefold() and search not in window_title.casefold():
                continue
            time_label = datetime.fromisoformat(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            self.timeline_tree.insert(
                "",
                tk.END,
                values=(
                    time_label,
                    app_name,
                    window_title,
                    self.format_time(duration),
                ),
                tags=(
                    ("alternate",)
                    if visible_timeline_index % 2
                    else ()
                ),
            )
            visible_timeline_index += 1

    def refresh_dashboard(
        self,
        summary_data: list[tuple],
        start_date: date,
        end_date: date,
    ):
        app_totals: dict[str, float] = {}
        for app_name, _window_title, duration, _path in summary_data:
            app_totals[app_name] = app_totals.get(app_name, 0.0) + duration

        total_seconds = sum(app_totals.values())
        self.total_time_label.config(text=self.format_time(total_seconds))
        if app_totals:
            top_app = max(app_totals, key=app_totals.get)
            self.top_app_label.config(
                text=f"{top_app}  ·  {self.format_time(app_totals[top_app])}"
            )
        else:
            self.top_app_label.config(text="No activity yet")

        today = date.today()
        today_totals = get_summary_for_period(
            today.isoformat(), (today + timedelta(days=1)).isoformat()
        )
        today_seconds = sum(row[2] for row in today_totals)
        goal_minutes = self.daily_goal_minutes
        goal_seconds = goal_minutes * 60
        progress = (
            min(100, int(today_seconds * 100 / goal_seconds))
            if goal_seconds > 0
            else 0
        )
        self.goal_progress.configure(value=progress)
        if goal_minutes:
            self.goal_label.config(
                text=(
                    f"{int(today_seconds // 60)} / {goal_minutes} min"
                    f"  ({progress}%)"
                )
            )
        else:
            self.goal_label.config(text="No goal set")

        if self.period_var.get() == "This Week":
            self.chart_title.config(text="Daily usage this week")
            daily_totals = dict(
                get_daily_totals(start_date.isoformat(), end_date.isoformat())
            )
            chart_data = [
                (
                    (start_date + timedelta(days=offset)).strftime("%a"),
                    daily_totals.get(
                        (start_date + timedelta(days=offset)).isoformat(), 0
                    ),
                )
                for offset in range(7)
            ]
        else:
            self.chart_title.config(text="Top applications today")
            chart_data = sorted(
                app_totals.items(), key=lambda item: item[1], reverse=True
            )[:5]
        self._draw_chart(chart_data)

    def _draw_chart(self, chart_data: list[tuple[str, float]] | None = None):
        if chart_data is not None:
            self.chart_data = chart_data
        else:
            chart_data = getattr(self, "chart_data", [])

        self.chart.delete("all")
        if not chart_data:
            self.chart.create_text(
                8,
                20,
                text="No activity for this period yet.",
                anchor=tk.W,
                fill=self.palette["muted"],
                font=("Segoe UI", 9),
            )
            return

        width = max(self.chart.winfo_width(), 300)
        row_height = 25 if len(chart_data) > 5 else 30
        label_width = 105
        value_width = 65
        bar_start = label_width
        bar_end = width - value_width - 8
        bar_width = max(20, bar_end - bar_start)
        max_value = max(value for _label, value in chart_data) or 1

        for index, (label, value) in enumerate(chart_data):
            y = index * row_height + 13
            display_label = label[:16]
            self.chart.create_text(
                2,
                y,
                text=display_label,
                anchor=tk.W,
                fill=self.palette["text"],
                font=("Segoe UI", 9),
            )
            self.chart.create_rectangle(
                bar_start,
                y - 5,
                bar_start + bar_width,
                y + 5,
                fill=self.palette["blue_light"],
                outline="",
            )
            self.chart.create_rectangle(
                bar_start,
                y - 6,
                bar_start + max(2, bar_width * value / max_value),
                y + 6,
                fill=self.palette["blue"],
                outline="",
            )
            self.chart.create_text(
                width - 4,
                y,
                text=self.format_time(value),
                anchor=tk.E,
                fill=self.palette["muted"],
                font=("Segoe UI", 8),
            )

    def export_csv(self):
        start_date, end_date = self._period_dates()
        activities = get_activities_for_period(
            start_date.isoformat(), end_date.isoformat()
        )
        if not activities:
            messagebox.showinfo(
                "Nothing to export",
                "There is no activity recorded for this period yet.",
                parent=self.root,
            )
            return

        default_name = f"time-tracker-{self.period_var.get().lower().replace(' ', '-')}.csv"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export activity to CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(
                    ("Timestamp", "Application", "Window Title", "Duration (seconds)")
                )
                writer.writerows(
                    (
                        timestamp,
                        app_name,
                        window_title,
                        round(duration, 2),
                    )
                    for timestamp, app_name, window_title, duration in activities
                )
        except OSError as error:
            messagebox.showerror(
                "Export failed",
                f"Could not write the CSV file:\n{error}",
                parent=self.root,
            )
            return

        messagebox.showinfo(
            "Export complete",
            f"Exported {len(activities)} activity records.",
            parent=self.root,
        )

    def show_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings & Privacy")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg=self.palette["background"])
        container = ttk.Frame(
            dialog, style="Main.TFrame", padding=20
        )
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="Appearance").grid(
            row=0, column=0, sticky=tk.W, pady=5
        )
        theme_var = tk.StringVar(value=self.theme_name)
        ttk.Combobox(
            container,
            textvariable=theme_var,
            values=("Light", "Dark"),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky=tk.E, pady=5)

        ttk.Label(
            container,
            text="Idle detection (minutes; 0 disables)",
        ).grid(row=1, column=0, sticky=tk.W, pady=5)
        idle_var = tk.StringVar(value=str(self.idle_threshold_minutes))
        ttk.Entry(
            container, textvariable=idle_var, width=12
        ).grid(row=1, column=1, sticky=tk.E, pady=5)

        ttk.Label(
            container,
            text="Daily tracking goal (minutes; 0 disables)",
        ).grid(row=2, column=0, sticky=tk.W, pady=5)
        goal_var = tk.StringVar(value=str(self.daily_goal_minutes))
        ttk.Entry(
            container, textvariable=goal_var, width=12
        ).grid(row=2, column=1, sticky=tk.E, pady=5)

        ttk.Label(
            container,
            text="Excluded application patterns (one per line)",
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(12, 2))
        apps_text = tk.Text(
            container,
            width=52,
            height=4,
            wrap=tk.WORD,
            background=self.palette["surface"],
            foreground=self.palette["text"],
            insertbackground=self.palette["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            highlightcolor=self.palette["blue"],
            padx=8,
            pady=6,
            font=("Segoe UI", 9),
        )
        apps_text.grid(row=4, column=0, columnspan=2, sticky=tk.EW)
        apps_text.insert("1.0", self.excluded_apps_var.get())

        ttk.Label(
            container,
            text="Excluded window-title patterns (one per line)",
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(12, 2))
        titles_text = tk.Text(
            container,
            width=52,
            height=4,
            wrap=tk.WORD,
            background=self.palette["surface"],
            foreground=self.palette["text"],
            insertbackground=self.palette["text"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            highlightcolor=self.palette["blue"],
            padx=8,
            pady=6,
            font=("Segoe UI", 9),
        )
        titles_text.grid(row=6, column=0, columnspan=2, sticky=tk.EW)
        titles_text.insert("1.0", self.excluded_titles_var.get())

        startup_var = tk.BooleanVar(
            value=get_setting("start_with_windows", "0") == "1"
        )
        ttk.Checkbutton(
            container,
            text="Start with Windows (minimized to tray)",
            variable=startup_var,
        ).grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(12, 5))

        button_row = ttk.Frame(container)
        button_row.grid(row=8, column=0, columnspan=2, sticky=tk.EW, pady=(12, 0))
        ttk.Button(
            button_row,
            text="Delete Today's Data",
            style="Danger.TButton",
            command=self._delete_today_data,
        ).pack(side=tk.LEFT)
        ttk.Button(
            button_row,
            text="Delete All Data",
            style="Danger.TButton",
            command=self._delete_all_data,
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            button_row,
            text="Save",
            style="Primary.TButton",
            command=lambda: self._save_settings(
                dialog,
                apps_text,
                titles_text,
                idle_var,
                goal_var,
                startup_var,
                theme_var,
            ),
        ).pack(side=tk.RIGHT)
        ttk.Button(
            button_row, text="Cancel", command=dialog.destroy
        ).pack(side=tk.RIGHT, padx=6)

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.focus_set()

    def _save_settings(
        self,
        dialog: tk.Toplevel,
        apps_text: tk.Text,
        titles_text: tk.Text,
        idle_var: tk.StringVar,
        goal_var: tk.StringVar,
        startup_var: tk.BooleanVar,
        theme_var: tk.StringVar,
    ):
        try:
            idle_minutes = int(idle_var.get())
            goal_minutes = int(goal_var.get())
            if not 0 <= idle_minutes <= 1440 or not 0 <= goal_minutes <= 1440:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid settings",
                "Idle detection and daily goal must be whole numbers from 0 to 1440.",
                parent=dialog,
            )
            return

        excluded_apps = apps_text.get("1.0", "end-1c").strip()
        excluded_titles = titles_text.get("1.0", "end-1c").strip()
        start_with_windows = startup_var.get()
        theme_name = theme_var.get()
        if theme_name not in ("Light", "Dark"):
            messagebox.showerror(
                "Invalid appearance",
                "Choose either the Light or Dark theme.",
                parent=dialog,
            )
            return
        try:
            _set_windows_startup(start_with_windows)
        except OSError as error:
            messagebox.showerror(
                "Startup setting failed",
                f"Could not update the Windows startup setting:\n{error}",
                parent=dialog,
            )
            return

        settings = {
            "idle_threshold_minutes": str(idle_minutes),
            "daily_goal_minutes": str(goal_minutes),
            "excluded_apps": excluded_apps,
            "excluded_titles": excluded_titles,
            "start_with_windows": "1" if start_with_windows else "0",
            "theme": theme_name,
        }
        for key, value in settings.items():
            set_setting(key, value)
        self.idle_threshold_minutes = idle_minutes
        self.daily_goal_minutes = goal_minutes
        self.excluded_apps_var.set(excluded_apps)
        self.excluded_titles_var.set(excluded_titles)
        if theme_name != self.theme_name:
            self._apply_theme(theme_name)

        if self.is_tracking:
            excluded_now = self._is_activity_excluded(
                self.current_app, self.current_title
            )
            if excluded_now != self.current_activity_excluded:
                now = time.time()
                if not self.current_activity_excluded:
                    log_activity(
                        self.current_app,
                        self.current_title,
                        max(0.0, now - self.start_time),
                        self.current_executable_path,
                    )
                self.current_activity_excluded = excluded_now
                self.start_time = now
                self.last_check_time = now

        self.refresh_table()
        dialog.destroy()

    @staticmethod
    def _patterns_match(value: str, patterns: str) -> bool:
        folded_value = value.casefold()
        for pattern in patterns.splitlines():
            pattern = pattern.strip()
            if pattern and fnmatch.fnmatchcase(
                folded_value, pattern.casefold()
            ):
                return True
        return False

    def _is_activity_excluded(self, app_name: str, window_title: str) -> bool:
        return self._patterns_match(
            app_name, self.excluded_apps_var.get()
        ) or self._patterns_match(window_title, self.excluded_titles_var.get())

    def _delete_today_data(self):
        if not messagebox.askyesno(
            "Delete today's data",
            "Delete all activity recorded today? Tracking will resume afterward if active.",
            parent=self.root,
        ):
            return
        was_tracking = self.is_tracking
        if was_tracking:
            self.stop_tracking()
        today = date.today()
        delete_activities_for_period(
            today.isoformat(), (today + timedelta(days=1)).isoformat()
        )
        self.refresh_table()
        if was_tracking:
            self.start_tracking()

    def _delete_all_data(self):
        if not messagebox.askyesno(
            "Delete all data",
            "Permanently delete all recorded activity? This cannot be undone.",
            parent=self.root,
        ):
            return
        was_tracking = self.is_tracking
        if was_tracking:
            self.stop_tracking()
        delete_all_activities()
        self.refresh_table()
        if was_tracking:
            self.start_tracking()

    def _get_app_image(
        self, app_name: str, executable_path: str
    ) -> tk.PhotoImage | None:
        cache_key = executable_path.casefold() or app_name.casefold()
        if cache_key not in self.app_images:
            image = None
            resolved_path = _resolve_executable_path(app_name, executable_path)
            if resolved_path:
                try:
                    image_data = _extract_executable_icon(resolved_path)
                    if image_data:
                        image = tk.PhotoImage(
                            master=self.root,
                            data=base64.b64encode(image_data).decode("ascii"),
                            format="png",
                        )
                except (OSError, tk.TclError):
                    image = None
            self.app_images[cache_key] = image
        return self.app_images[cache_key]

    def start_tracking(self):
        if not self.is_tracking:
            self.resume_after_idle = False
            sleep_time = get_system_sleep_time()
            current_app, current_title, executable_path = get_active_window_info()

            self.is_tracking = True
            self.btn_start.config(state=tk.DISABLED)
            self.btn_stop.config(state=tk.NORMAL)
            self.status_label.config(
                text="Status: TRACKING...",
                style="Status.Active.TLabel",
            )

            self.current_app = current_app
            self.current_title = current_title
            self.current_executable_path = executable_path
            self.current_activity_excluded = self._is_activity_excluded(
                current_app, current_title
            )
            self.start_time = time.time()
            self.last_check_time = self.start_time
            self.sleep_time = sleep_time

            if self.tray_icon is not None:
                self.tray_icon.update_menu()
            self._schedule_track_loop()

    def stop_tracking(self, end_time=None, status_text=None):
        if self.is_tracking:
            self.is_tracking = False
            self.btn_start.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)
            stopped_for_sleep = end_time is not None
            status = status_text or (
                "Status: STOPPED (SLEEP DETECTED)"
                if stopped_for_sleep
                else "Status: STOPPED"
            )
            status_style = (
                "Status.Warning.TLabel"
                if "IDLE" in status or "SLEEP" in status
                else "Status.TLabel"
            )
            self.status_label.config(text=status, style=status_style)
            if self.tray_icon is not None:
                self.tray_icon.update_menu()

            if self.start_time > 0 and not self.current_activity_excluded:
                stop_time = end_time if stopped_for_sleep else time.time()
                duration = max(0.0, stop_time - self.start_time)
                log_activity(
                    self.current_app,
                    self.current_title,
                    duration,
                    self.current_executable_path,
                )

            self.refresh_table()

    def track_loop(self):
        self.track_after_id = None
        if not self.is_tracking:
            if self.resume_after_idle:
                idle_limit = self.idle_threshold_minutes * 60
                if idle_limit == 0 or get_idle_time() < idle_limit:
                    self.resume_after_idle = False
                    self.start_tracking()
                else:
                    self._schedule_track_loop()
            return

        idle_limit = self.idle_threshold_minutes * 60
        if idle_limit > 0 and get_idle_time() >= idle_limit:
            self.resume_after_idle = True
            self.stop_tracking(
                end_time=self.last_check_time,
                status_text="Status: PAUSED (IDLE)",
            )
            self._schedule_track_loop()
            return

        current_sleep_time = get_system_sleep_time()
        if current_sleep_time - self.sleep_time >= 0.5:
            self.stop_tracking(end_time=self.last_check_time)
            return

        self.sleep_time = current_sleep_time
        new_app, new_title, executable_path = get_active_window_info()
        now = time.time()

        if (
            new_title != self.current_title
            or new_app != self.current_app
            or executable_path != self.current_executable_path
        ):
            duration = now - self.start_time

            if not self.current_activity_excluded:
                log_activity(
                    self.current_app,
                    self.current_title,
                    duration,
                    self.current_executable_path,
                )
            self.refresh_table()

            self.current_app = new_app
            self.current_title = new_title
            self.current_executable_path = executable_path
            self.current_activity_excluded = self._is_activity_excluded(
                new_app, new_title
            )
            self.start_time = now

        self.last_check_time = now
        self._schedule_track_loop()


if __name__ == "__main__":
 # Force Windows to assign a unique Taskbar ID to this application
    my_app_id = "timetracker.desktop.app.1.0"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(my_app_id)

    root = tk.Tk()
    app = TimeTrackerGUI(root)
    if "--minimized" in sys.argv[1:]:
        root.after_idle(root.withdraw)
    root.mainloop()
