from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Callable
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from urllib import error as urlerror
from urllib import request as urlrequest
import webbrowser

from .core import RunOptions, run_workflow
from . import __version__ as package_version

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_TK_DND = True
except Exception:
    DND_FILES = None
    TkinterDnD = None
    HAS_TK_DND = False

try:
    from importlib import metadata as importlib_metadata
except Exception:
    import importlib_metadata  # type: ignore[no-redef]


GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/davidwambach/swp2tex/releases/latest"
)
UPDATE_MODE_OFF = "off"
UPDATE_MODE_STARTUP = "startup"
UPDATE_MODE_WEEKLY = "weekly"
VALID_UPDATE_MODES = {UPDATE_MODE_OFF, UPDATE_MODE_STARTUP, UPDATE_MODE_WEEKLY}


def parse_dnd_file_list(payload: str) -> list[Path]:
    paths: list[Path] = []
    if not payload:
        return paths
    token_pattern = re.compile(r"\{([^}]*)\}|\"([^\"]*)\"|(\S+)")
    for braced, quoted, plain in token_pattern.findall(payload):
        token = braced or quoted or plain
        token = token.strip()
        if not token:
            continue
        paths.append(Path(token))
    return paths


def select_main_tex_from_drop_payload(payload: str) -> Path | None:
    main, _project, _bib = extract_drop_targets(payload)
    return main


def select_project_dir_from_drop_payload(payload: str) -> Path | None:
    _main, project, _bib = extract_drop_targets(payload)
    return project


def select_bib_from_drop_payload(payload: str) -> Path | None:
    _main, _project, bib = extract_drop_targets(payload)
    return bib


def extract_drop_targets(payload: str) -> tuple[Path | None, Path | None, Path | None]:
    main: Path | None = None
    project: Path | None = None
    bib: Path | None = None
    for candidate in parse_dnd_file_list(payload):
        suffix = candidate.suffix.lower()
        if main is None and suffix in {".tex", ".ltx"}:
            main = candidate
        if bib is None and suffix == ".bib":
            bib = candidate
        if project is None and candidate.exists() and candidate.is_dir():
            project = candidate
        if main is not None and project is not None and bib is not None:
            break
    return main, project, bib


def parse_version_parts(version: str) -> list[int]:
    text = version.strip()
    if text.lower().startswith("v"):
        text = text[1:]
    numbers = [int(part) for part in re.findall(r"\d+", text)]
    return numbers or [0]


def is_newer_version(latest: str, current: str) -> bool:
    a = parse_version_parts(latest)
    b = parse_version_parts(current)
    n = max(len(a), len(b))
    a.extend([0] * (n - len(a)))
    b.extend([0] * (n - len(b)))
    return a > b


def _parse_iso_utc(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def should_run_auto_update_check(
    mode: str, last_checked_iso: str | None, now_utc: datetime | None = None
) -> bool:
    if mode == UPDATE_MODE_OFF:
        return False
    if mode == UPDATE_MODE_STARTUP:
        return True
    if mode != UPDATE_MODE_WEEKLY:
        return False
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    last_dt = _parse_iso_utc(last_checked_iso or "")
    if last_dt is None:
        return True
    return (now_utc - last_dt) >= timedelta(days=7)


def settings_file_path() -> Path:
    appdata = Path(os.environ["APPDATA"]) if "APPDATA" in os.environ else None
    if appdata is not None:
        return appdata / "swp2tex" / "settings.json"
    return Path.home() / ".config" / "swp2tex" / "settings.json"


def load_gui_settings() -> dict[str, str]:
    path = settings_file_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("auto_update_mode", "last_update_check_utc"):
        value = data.get(key)
        if isinstance(value, str):
            out[key] = value
    return out


def save_gui_settings(settings: dict[str, str]) -> None:
    path = settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "auto_update_mode": settings.get("auto_update_mode", UPDATE_MODE_WEEKLY),
        "last_update_check_utc": settings.get("last_update_check_utc", ""),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def current_app_version() -> str:
    if package_version:
        return package_version
    try:
        return importlib_metadata.version("swp2tex-bib")
    except Exception:
        return "0.0.0"


def fetch_latest_release_info(timeout_sec: int = 8) -> tuple[str, str]:
    req = urlrequest.Request(
        GITHUB_LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "swp2tex-updater",
        },
    )
    with urlrequest.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected update response format.")
    tag = str(data.get("tag_name", "")).strip()
    html_url = str(data.get("html_url", "")).strip()
    if not tag or not html_url:
        raise RuntimeError("Update response missing tag_name or html_url.")
    return tag, html_url


class HoverTip:
    def __init__(self, widget: tk.Widget, text_provider: Callable[[], str]) -> None:
        self.widget = widget
        self.text_provider = text_provider
        self.tip_window: tk.Toplevel | None = None
        self._after_id: str | None = None
        self.widget.bind("<Enter>", self._on_enter, add="+")
        self.widget.bind("<Leave>", self._on_leave, add="+")
        self.widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _event: tk.Event) -> None:
        self._schedule_show()

    def _on_leave(self, _event: tk.Event) -> None:
        self._cancel_show()
        self._hide()

    def _schedule_show(self) -> None:
        self._cancel_show()
        self._after_id = self.widget.after(250, self._show)

    def _cancel_show(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self.tip_window is not None:
            return
        text = self.text_provider().strip()
        if not text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + 18
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tip,
            text=text,
            justify="left",
            bg="#111827",
            fg="#f8fafc",
            relief="solid",
            borderwidth=1,
            wraplength=460,
            padx=8,
            pady=6,
        )
        label.pack()
        self.tip_window = tip

    def _hide(self) -> None:
        if self.tip_window is None:
            return
        self.tip_window.destroy()
        self.tip_window = None


class App:
    def __init__(self, root: tk.Tk, initial_main: str | None = None) -> None:
        self.root = root
        self.root.title("SWP to Overleaf/arXiv Converter")
        self.root.minsize(980, 700)
        self.root.configure(bg="#f2f5fa")

        self.main_var = tk.StringVar()
        self.project_var = tk.StringVar()
        self.bib_var = tk.StringVar()
        self.export_mode_var = tk.StringVar(value="overleaf")
        self.status_var = tk.StringVar(value="")
        self._result_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._update_worker: threading.Thread | None = None
        self._dnd_hover_depth = 0
        self._hover_tips: list[HoverTip] = []
        self._settings = load_gui_settings()
        mode = self._settings.get("auto_update_mode", UPDATE_MODE_WEEKLY)
        if mode not in VALID_UPDATE_MODES:
            mode = UPDATE_MODE_WEEKLY
        self.update_mode_var = tk.StringVar(value=mode)
        self.update_mode_var.trace_add("write", self._on_update_mode_changed)
        self._theme = {
            "app_bg": "#f2f5fa",
            "card_bg": "#ffffff",
            "text": "#0f172a",
            "muted": "#475569",
            "border": "#d7dee8",
            "accent": "#2563eb",
            "accent_hover": "#1d4ed8",
            "accent_disabled": "#93c5fd",
            "ok_bg": "#dcfce7",
            "ok_fg": "#166534",
            "warn_bg": "#fef3c7",
            "warn_fg": "#92400e",
            "err_bg": "#fee2e2",
            "err_fg": "#991b1b",
            "idle_bg": "#e2e8f0",
            "idle_fg": "#334155",
            "log_bg": "#0b1220",
            "log_fg": "#dbeafe",
        }

        self._apply_style()
        self._build_ui()
        self._register_drop_targets()
        self._set_status("Idle")
        self._set_initial_main(initial_main)
        self.root.after(900, self._maybe_auto_check_updates)

    def _apply_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure(
            "App.TFrame",
            background=self._theme["app_bg"],
        )
        style.configure(
            "Card.TFrame",
            background=self._theme["card_bg"],
        )
        style.configure(
            "HeaderTitle.TLabel",
            background=self._theme["app_bg"],
            foreground=self._theme["text"],
            font=("Segoe UI Semibold", 17),
        )
        style.configure(
            "HeaderSubtitle.TLabel",
            background=self._theme["app_bg"],
            foreground=self._theme["muted"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Version.TLabel",
            background=self._theme["app_bg"],
            foreground=self._theme["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Card.TLabel",
            background=self._theme["card_bg"],
            foreground=self._theme["text"],
            padding=1,
        )
        style.configure(
            "Section.TLabelframe",
            background=self._theme["card_bg"],
            bordercolor=self._theme["border"],
            borderwidth=1,
            relief="solid",
            padding=12,
        )
        style.configure(
            "Section.TLabelframe.Label",
            background=self._theme["card_bg"],
            foreground=self._theme["text"],
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "Run.TButton",
            padding=(16, 9),
            font=("Segoe UI Semibold", 10),
            background=self._theme["accent"],
            foreground="#ffffff",
            borderwidth=0,
            focusthickness=1,
            focuscolor=self._theme["accent"],
        )
        style.map(
            "Run.TButton",
            background=[
                ("disabled", self._theme["accent_disabled"]),
                ("active", self._theme["accent_hover"]),
            ],
            foreground=[("disabled", "#f8fafc"), ("active", "#ffffff")],
        )
        style.configure(
            "Small.TButton",
            padding=(10, 5),
            background="#eef2ff",
            foreground="#1e3a8a",
            bordercolor="#c7d2fe",
            borderwidth=1,
        )
        style.map(
            "Small.TButton",
            background=[("active", "#e0e7ff")],
            foreground=[("active", "#1e40af")],
        )
        style.configure(
            "Card.TRadiobutton",
            background=self._theme["card_bg"],
            foreground=self._theme["text"],
        )
        style.configure(
            "Card.TEntry",
            fieldbackground="#ffffff",
            background="#ffffff",
            foreground=self._theme["text"],
            bordercolor=self._theme["border"],
            lightcolor=self._theme["border"],
            darkcolor=self._theme["border"],
            padding=6,
        )
        style.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor="#e2e8f0",
            background=self._theme["accent"],
            bordercolor="#e2e8f0",
            lightcolor=self._theme["accent"],
            darkcolor=self._theme["accent"],
        )
        style.configure(
            "StatusIdle.TLabel",
            background=self._theme["idle_bg"],
            foreground=self._theme["idle_fg"],
            padding=(8, 3),
            font=("Segoe UI Semibold", 9),
        )
        style.configure(
            "StatusRun.TLabel",
            background="#dbeafe",
            foreground="#1e3a8a",
            padding=(8, 3),
            font=("Segoe UI Semibold", 9),
        )
        style.configure(
            "StatusOk.TLabel",
            background=self._theme["ok_bg"],
            foreground=self._theme["ok_fg"],
            padding=(8, 3),
            font=("Segoe UI Semibold", 9),
        )
        style.configure(
            "StatusWarn.TLabel",
            background=self._theme["warn_bg"],
            foreground=self._theme["warn_fg"],
            padding=(8, 3),
            font=("Segoe UI Semibold", 9),
        )
        style.configure(
            "StatusErr.TLabel",
            background=self._theme["err_bg"],
            foreground=self._theme["err_fg"],
            padding=(8, 3),
            font=("Segoe UI Semibold", 9),
        )
        style.configure(
            "App.TNotebook",
            background=self._theme["app_bg"],
            borderwidth=0,
            tabmargins=(2, 2, 2, 0),
        )
        style.configure(
            "App.TNotebook.Tab",
            padding=(12, 7),
            font=("Segoe UI Semibold", 10),
        )
        style.map(
            "App.TNotebook.Tab",
            background=[("selected", "#ffffff"), ("!selected", "#e2e8f0")],
            foreground=[("selected", self._theme["text"]), ("!selected", self._theme["muted"])],
        )

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16, style="App.TFrame")
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header,
            text="SWP to Overleaf/arXiv Converter",
            style="HeaderTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Convert Scientific Workplace files, normalize syntax, build LaTeX, "
                "and export clean packages."
            ),
            style="HeaderSubtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            header,
            text=f"Version {current_app_version()}",
            style="Version.TLabel",
        ).pack(anchor="w", pady=(1, 0))

        notebook = ttk.Notebook(outer, style="App.TNotebook")
        notebook.pack(fill="both", expand=True, pady=(6, 0))
        converter_tab = ttk.Frame(notebook, style="Card.TFrame")
        settings_tab = ttk.Frame(notebook, style="Card.TFrame")
        notebook.add(converter_tab, text="Converter")
        notebook.add(settings_tab, text="Settings")

        input_frame = ttk.LabelFrame(
            converter_tab, text="Input Files", style="Section.TLabelframe"
        )
        input_frame.pack(fill="x", pady=(0, 10))
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(
            input_frame, text="Main .tex file (Scientific Workplace)", style="Card.TLabel"
        ).grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=6
        )
        self.main_entry = ttk.Entry(input_frame, textvariable=self.main_var, style="Card.TEntry")
        self.main_entry.grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(
            input_frame, text="Browse", command=self.pick_main, width=10, style="Small.TButton"
        ).grid(row=0, column=2, padx=8, pady=6)
        self._add_info_icon(input_frame, row=0, column=3, text_provider=self._main_info_text)

        ttk.Label(input_frame, text="Project/resource directory", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(input_frame, textvariable=self.project_var, style="Card.TEntry").grid(
            row=1, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            input_frame,
            text="Browse",
            command=self.pick_project,
            width=10,
            style="Small.TButton",
        ).grid(row=1, column=2, padx=8, pady=6)
        self._add_info_icon(input_frame, row=1, column=3, text_provider=self._project_info_text)

        ttk.Label(input_frame, text="Optional .bib file", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(input_frame, textvariable=self.bib_var, style="Card.TEntry").grid(
            row=2, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            input_frame, text="Browse", command=self.pick_bib, width=10, style="Small.TButton"
        ).grid(row=2, column=2, padx=8, pady=6)
        self._add_info_icon(input_frame, row=2, column=3, text_provider=self._bib_info_text)

        export_frame = ttk.LabelFrame(
            converter_tab, text="Export Target", style="Section.TLabelframe"
        )
        export_frame.pack(fill="x", pady=(0, 10))
        ttk.Radiobutton(
            export_frame,
            text="SWP to Overleaf (normal LaTeX)",
            variable=self.export_mode_var,
            value="overleaf",
            style="Card.TRadiobutton",
        ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Radiobutton(
            export_frame,
            text="SWP to arXiv",
            variable=self.export_mode_var,
            value="arxiv",
            style="Card.TRadiobutton",
        ).grid(row=1, column=0, sticky="w", pady=4)

        status_frame = ttk.LabelFrame(
            converter_tab, text="Run Status", style="Section.TLabelframe"
        )
        status_frame.pack(fill="x", pady=(0, 10))
        status_frame.columnconfigure(2, weight=1)

        self.run_btn = ttk.Button(status_frame, text="Run", command=self.run, style="Run.TButton")
        self.run_btn.grid(row=0, column=0, padx=(0, 12), pady=6)

        self.progress = ttk.Progressbar(
            status_frame,
            mode="indeterminate",
            length=260,
            style="Accent.Horizontal.TProgressbar",
        )
        self.progress.grid(row=0, column=1, padx=(0, 12), pady=6, sticky="w")
        self.progress.grid_remove()

        self.status_chip = ttk.Label(
            status_frame,
            textvariable=self.status_var,
            style="StatusIdle.TLabel",
        )
        self.status_chip.grid(
            row=0, column=2, sticky="w", pady=6
        )

        output_frame = ttk.LabelFrame(
            converter_tab, text="Output Log", style="Section.TLabelframe"
        )
        output_frame.pack(fill="both", expand=True)
        self.output = ScrolledText(output_frame, width=110, height=22)
        self.output.pack(fill="both", expand=True)
        self.output.configure(
            background=self._theme["log_bg"],
            foreground=self._theme["log_fg"],
            insertbackground="#f8fafc",
            selectbackground="#334155",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
            font=("Cascadia Mono", 9),
        )

        settings_intro = ttk.Label(
            settings_tab,
            text="Update behavior and app maintenance options",
            style="Card.TLabel",
        )
        settings_intro.pack(anchor="w", pady=(2, 8), padx=2)

        update_frame = ttk.LabelFrame(
            settings_tab, text="Updates", style="Section.TLabelframe"
        )
        update_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(update_frame, text="Auto-check:", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )
        ttk.Radiobutton(
            update_frame,
            text="Off",
            variable=self.update_mode_var,
            value=UPDATE_MODE_OFF,
            style="Card.TRadiobutton",
        ).grid(row=0, column=1, sticky="w", padx=(0, 12), pady=4)
        ttk.Radiobutton(
            update_frame,
            text="On startup",
            variable=self.update_mode_var,
            value=UPDATE_MODE_STARTUP,
            style="Card.TRadiobutton",
        ).grid(row=0, column=2, sticky="w", padx=(0, 12), pady=4)
        ttk.Radiobutton(
            update_frame,
            text="Weekly",
            variable=self.update_mode_var,
            value=UPDATE_MODE_WEEKLY,
            style="Card.TRadiobutton",
        ).grid(row=0, column=3, sticky="w", padx=(0, 12), pady=4)
        update_frame.columnconfigure(5, weight=1)
        self.update_btn = ttk.Button(
            update_frame,
            text="Check for updates",
            command=self.check_updates_now,
            style="Small.TButton",
        )
        self.update_btn.grid(row=0, column=6, sticky="e", padx=(12, 0), pady=4)

        self.drop_overlay = tk.Frame(
            self.root,
            bg="#dbe5f1",
            highlightthickness=2,
            highlightbackground="#64748b",
        )
        self.drop_overlay_label = tk.Label(
            self.drop_overlay,
            text="Drop .tex/.ltx, .bib, or folder",
            bg="#dbe5f1",
            fg="#1f2937",
            font=("Segoe UI", 14, "bold"),
        )
        self.drop_overlay_label.place(relx=0.5, rely=0.5, anchor="center")

    def _iter_widgets(self, root: tk.Misc) -> list[tk.Misc]:
        widgets = [root]
        for child in root.winfo_children():
            widgets.extend(self._iter_widgets(child))
        return widgets

    def _show_drop_overlay(self, message: str | None = None) -> None:
        if message:
            self.drop_overlay_label.config(text=message)
        self.drop_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.drop_overlay.tkraise()

    def _hide_drop_overlay(self) -> None:
        self.drop_overlay.place_forget()

    def _on_drop_enter(self, event: tk.Event) -> str:
        self._dnd_hover_depth += 1
        payload = getattr(event, "data", "")
        main, project, bib = extract_drop_targets(payload)
        if main is None and project is None and bib is None:
            self._show_drop_overlay("Drop .tex/.ltx, .bib, or folder")
            return "copy"
        lines = ["Release to fill:"]
        if main is not None:
            lines.append(f"Main file: {main.name}")
        if project is not None:
            lines.append(f"Project dir: {project.name}")
        if bib is not None:
            lines.append(f"Bib file: {bib.name}")
        self._show_drop_overlay("\n".join(lines))
        return "copy"

    def _on_drop_leave(self, _event: tk.Event) -> str:
        self._dnd_hover_depth = max(0, self._dnd_hover_depth - 1)
        if self._dnd_hover_depth == 0:
            self._hide_drop_overlay()
        return "copy"

    def _set_initial_main(self, initial_main: str | None) -> None:
        if not initial_main:
            return
        candidate = Path(initial_main).expanduser()
        if candidate.suffix.lower() in {".tex", ".ltx"}:
            self.main_var.set(str(candidate))
            self._set_status("Main file prefilled from startup argument.")

    def _status_style_for(self, text: str) -> str:
        low = text.strip().lower()
        if low.startswith("running") or low.startswith("checking"):
            return "StatusRun.TLabel"
        if "finished with errors" in low:
            return "StatusWarn.TLabel"
        if "failed" in low or "error" in low:
            return "StatusErr.TLabel"
        if "success" in low or "up to date" in low or low == "completed":
            return "StatusOk.TLabel"
        return "StatusIdle.TLabel"

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)
        if hasattr(self, "status_chip"):
            self.status_chip.configure(style=self._status_style_for(text))

    def _on_update_mode_changed(self, *_args: object) -> None:
        mode = self.update_mode_var.get().strip().lower()
        if mode not in VALID_UPDATE_MODES:
            mode = UPDATE_MODE_WEEKLY
            self.update_mode_var.set(mode)
        self._settings["auto_update_mode"] = mode
        save_gui_settings(self._settings)

    def _mark_update_check_now(self) -> None:
        self._settings["last_update_check_utc"] = datetime.now(timezone.utc).isoformat()
        save_gui_settings(self._settings)

    def _set_status_if_not_running(self, text: str) -> None:
        if self.status_var.get().startswith("Running conversion"):
            return
        self._set_status(text)

    def _maybe_auto_check_updates(self) -> None:
        mode = self.update_mode_var.get().strip().lower()
        last_checked = self._settings.get("last_update_check_utc", "")
        if should_run_auto_update_check(mode, last_checked):
            self._start_update_check(user_initiated=False)

    def check_updates_now(self) -> None:
        self._start_update_check(user_initiated=True)

    def _start_update_check(self, user_initiated: bool) -> None:
        if self._update_worker is not None and self._update_worker.is_alive():
            if user_initiated:
                messagebox.showinfo("Updates", "Update check is already running.")
            return
        self._mark_update_check_now()
        self._set_status_if_not_running("Checking for updates...")
        self.update_btn.config(state=tk.DISABLED)
        self._update_worker = threading.Thread(
            target=self._run_update_check_worker,
            args=(user_initiated,),
            daemon=True,
        )
        self._update_worker.start()

    def _run_update_check_worker(self, user_initiated: bool) -> None:
        current = current_app_version()
        try:
            latest_tag, release_url = fetch_latest_release_info()
            has_update = is_newer_version(latest_tag, current)
            payload = {
                "user_initiated": user_initiated,
                "current": current,
                "latest": latest_tag,
                "url": release_url,
                "has_update": has_update,
            }
            self.root.after(0, self._on_update_check_success, payload)
        except (urlerror.URLError, RuntimeError, json.JSONDecodeError, TimeoutError) as exc:
            self.root.after(0, self._on_update_check_failure, user_initiated, str(exc))
        except Exception as exc:
            self.root.after(0, self._on_update_check_failure, user_initiated, str(exc))

    def _on_update_check_success(self, payload: dict[str, object]) -> None:
        self.update_btn.config(state=tk.NORMAL)
        user_initiated = bool(payload.get("user_initiated"))
        current = str(payload.get("current", "0.0.0"))
        latest = str(payload.get("latest", "0.0.0"))
        url = str(payload.get("url", ""))
        has_update = bool(payload.get("has_update"))
        if has_update:
            self._set_status_if_not_running(f"Update available: {latest}")
            self._append(f"Update available: current {current}, latest {latest}.")
            if messagebox.askyesno(
                "Update available",
                (
                    f"A newer version is available.\n\nCurrent: {current}\nLatest: {latest}\n\n"
                    "Open release page now?"
                ),
            ):
                webbrowser.open(url)
            return
        self._set_status_if_not_running("Up to date")
        if user_initiated:
            messagebox.showinfo("Updates", f"You're up to date ({current}).")

    def _on_update_check_failure(self, user_initiated: bool, detail: str) -> None:
        self.update_btn.config(state=tk.NORMAL)
        self._set_status_if_not_running("Update check failed")
        if user_initiated:
            messagebox.showwarning("Updates", f"Could not check for updates.\n\n{detail}")

    def _register_drop_targets(self) -> None:
        if not (HAS_TK_DND and hasattr(self.root, "drop_target_register")):
            return
        for widget in self._iter_widgets(self.root):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<DropEnter>>", self._on_drop_enter)
                widget.dnd_bind("<<DropLeave>>", self._on_drop_leave)
                widget.dnd_bind("<<Drop>>", self._on_file_drop)
            except Exception:
                continue

    def _on_file_drop(self, event: tk.Event) -> str:
        self._dnd_hover_depth = 0
        self._hide_drop_overlay()
        payload = getattr(event, "data", "")
        main, project, bib = extract_drop_targets(payload)
        updates: list[str] = []
        if main is not None:
            self.main_var.set(str(main))
            updates.append("main file")
        if project is not None:
            self.project_var.set(str(project))
            updates.append("project/resource directory")
        if bib is not None:
            self.bib_var.set(str(bib))
            updates.append("optional .bib file")
        if not updates:
            self._set_status("Dropped item ignored (use .tex/.ltx, .bib, or folder).")
            return "copy"
        self._set_status("Set from drag-and-drop: " + ", ".join(updates) + ".")
        return "copy"

    def _add_info_icon(
        self,
        parent: tk.Misc,
        row: int,
        column: int,
        text_provider: Callable[[], str],
    ) -> None:
        bg = self._theme["card_bg"]
        icon = tk.Canvas(
            parent,
            width=18,
            height=18,
            highlightthickness=0,
            bd=0,
            bg=bg,
            cursor="hand2",
        )
        icon.create_oval(2, 2, 16, 16, outline="#94a3b8", width=1)
        icon.create_text(9, 9, text="i", fill="#1e293b", font=("Segoe UI", 9, "bold"))
        icon.grid(row=row, column=column, pady=6, sticky="w")
        self._hover_tips.append(HoverTip(icon, text_provider))

    def _main_info_text(self) -> str:
        return "Choose the Scientific Workplace source .tex/.ltx file you want to convert."

    def _project_info_text(self) -> str:
        suggested = self._suggested_project_dir()
        return (
            "Choose the folder that contains figures and bibliography resources. "
            "Usually this is the same folder as the main file.\n\n"
            f"Suggested location:\n{suggested}"
        )

    def _bib_info_text(self) -> str:
        suggested = self._default_bib_suggestion()
        return (
            "Optional fallback .bib file used only if required bibliography is missing "
            "from the project/resource directory.\n\n"
            f"Suggested common location:\n{suggested}"
        )

    def pick_main(self) -> None:
        initialdir = self._suggested_project_dir()
        if not initialdir.exists():
            initialdir = Path.cwd()
        path = filedialog.askopenfilename(
            filetypes=[("LaTeX files", "*.ltx *.tex")],
            initialdir=str(initialdir),
        )
        if path:
            self.main_var.set(path)

    def pick_project(self) -> None:
        initialdir = self._suggested_project_dir()
        if not initialdir.exists():
            initialdir = Path.cwd()
        path = filedialog.askdirectory(initialdir=str(initialdir))
        if path:
            self.project_var.set(path)

    def pick_bib(self) -> None:
        suggested = self._default_bib_suggestion()
        initialdir = suggested.parent if suggested.parent.exists() else Path.home()
        path = filedialog.askopenfilename(
            filetypes=[("BibTeX files", "*.bib")],
            initialdir=str(initialdir),
            initialfile=suggested.name,
        )
        if path:
            self.bib_var.set(path)

    def _default_bib_suggestion(self) -> Path:
        return Path.home() / "Dropbox" / "bibtex" / "general.bib"

    def _suggested_project_dir(self) -> Path:
        main = self.main_var.get().strip()
        if main:
            candidate = Path(main).expanduser()
            if candidate.parent:
                return candidate.parent
        return Path.cwd()

    def _prompt_yes_no(self, question: str) -> bool:
        return messagebox.askyesno("Confirmation", question)

    def _prompt_yes_no_threadsafe(self, question: str) -> bool:
        if threading.current_thread() is threading.main_thread():
            return self._prompt_yes_no(question)
        event = threading.Event()
        answer = {"value": False}

        def ask() -> None:
            answer["value"] = self._prompt_yes_no(question)
            event.set()

        self.root.after(0, ask)
        event.wait()
        return answer["value"]

    def _append(self, line: str) -> None:
        self.output.insert(tk.END, line + "\n")
        self.output.see(tk.END)

    def run(self) -> None:
        main_txt = self.main_var.get().strip()
        project_txt = self.project_var.get().strip()
        if not main_txt or not project_txt:
            messagebox.showerror("Missing input", "Please select main file and project dir.")
            return
        if self._worker is not None and self._worker.is_alive():
            return
        main = Path(main_txt)
        project = Path(project_txt)
        bib_path = Path(self.bib_var.get().strip()) if self.bib_var.get().strip() else None
        options = RunOptions(
            main_file=main,
            project_dir=project,
            interactive=True,
            bib_file=bib_path,
            export_mode=self.export_mode_var.get(),
        )
        self.output.delete("1.0", tk.END)
        self._set_status("Running conversion...")
        self.progress.grid()
        self.progress.start(10)
        self.run_btn.config(state=tk.DISABLED)
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(options,),
            daemon=True,
        )
        self._worker.start()
        self.root.after(100, self._poll_worker)

    def _run_worker(self, options: RunOptions) -> None:
        try:
            report = run_workflow(options, prompt_yes_no=self._prompt_yes_no_threadsafe)
            self._result_queue.put(("ok", report))
        except Exception as exc:
            self._result_queue.put(("err", exc))

    def _poll_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self.root.after(100, self._poll_worker)
            return
        self.progress.stop()
        self.progress.grid_remove()
        self.run_btn.config(state=tk.NORMAL)
        try:
            state, payload = self._result_queue.get_nowait()
        except queue.Empty:
            self._set_status("Failed")
            self._append("Errors:")
            self._append("  - Conversion terminated unexpectedly.")
            return

        self.output.delete("1.0", tk.END)
        if state == "err":
            self._set_status("Failed")
            self._append("Errors:")
            self._append(f"  - {payload}")
            return

        report = payload
        if report.errors:
            self._set_status("Finished with errors")
        else:
            self._set_status("Finished successfully")

        self._append(f"Build status: {report.build_status}")
        if report.normalized_tex_path:
            self._append(f"Normalized file: {report.normalized_tex_path}")
        if report.export_path:
            self._append(f"Export zip: {report.export_path}")
        if report.syntax_fixes:
            self._append("Syntax fixes:")
            for fix in report.syntax_fixes:
                self._append(f"  - {fix}")
        if report.warnings:
            self._append("Warnings:")
            for warn in report.warnings:
                self._append(f"  - {warn}")
        if report.errors:
            self._append("Errors:")
            for err in report.errors:
                self._append(f"  - {err}")
        else:
            self._append("Completed without errors.")


def launch_gui(initial_main: str | None = None) -> None:
    if HAS_TK_DND and TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    App(root, initial_main=initial_main)
    root.mainloop()
