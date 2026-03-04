from __future__ import annotations

from pathlib import Path
from typing import Callable
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .core import RunOptions, run_workflow

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_TK_DND = True
except Exception:
    DND_FILES = None
    TkinterDnD = None
    HAS_TK_DND = False


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
            bg="#fffdf0",
            fg="#1f2937",
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

        self.main_var = tk.StringVar()
        self.project_var = tk.StringVar()
        self.bib_var = tk.StringVar()
        self.export_mode_var = tk.StringVar(value="overleaf")
        self.status_var = tk.StringVar(value="Idle")
        self._result_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._dnd_hover_depth = 0
        self._hover_tips: list[HoverTip] = []

        self._apply_style()
        self._build_ui()
        self._register_drop_targets()
        self._set_initial_main(initial_main)

    def _apply_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", padding=0)
        style.configure("TLabel", padding=1)
        style.configure("Section.TLabelframe", padding=10)
        style.configure("Section.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Run.TButton", padding=(16, 8), font=("Segoe UI", 10, "bold"))
        style.configure("Small.TButton", padding=(10, 4))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        input_frame = ttk.LabelFrame(outer, text="Input Files", style="Section.TLabelframe")
        input_frame.pack(fill="x", pady=(0, 10))
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="Main .tex file (Scientific Workplace)").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=6
        )
        self.main_entry = ttk.Entry(input_frame, textvariable=self.main_var)
        self.main_entry.grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(
            input_frame, text="Browse", command=self.pick_main, width=10, style="Small.TButton"
        ).grid(row=0, column=2, padx=8, pady=6)
        self._add_info_icon(input_frame, row=0, column=3, text_provider=self._main_info_text)

        ttk.Label(input_frame, text="Project/resource directory").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(input_frame, textvariable=self.project_var).grid(
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

        ttk.Label(input_frame, text="Optional .bib file").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=6
        )
        ttk.Entry(input_frame, textvariable=self.bib_var).grid(
            row=2, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            input_frame, text="Browse", command=self.pick_bib, width=10, style="Small.TButton"
        ).grid(row=2, column=2, padx=8, pady=6)
        self._add_info_icon(input_frame, row=2, column=3, text_provider=self._bib_info_text)

        export_frame = ttk.LabelFrame(
            outer, text="Export Target", style="Section.TLabelframe"
        )
        export_frame.pack(fill="x", pady=(0, 10))
        ttk.Radiobutton(
            export_frame,
            text="SWP to Overleaf (normal LaTeX)",
            variable=self.export_mode_var,
            value="overleaf",
        ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Radiobutton(
            export_frame,
            text="SWP to arXiv",
            variable=self.export_mode_var,
            value="arxiv",
        ).grid(row=1, column=0, sticky="w", pady=4)

        status_frame = ttk.LabelFrame(outer, text="Run Status", style="Section.TLabelframe")
        status_frame.pack(fill="x", pady=(0, 10))
        status_frame.columnconfigure(2, weight=1)

        self.run_btn = ttk.Button(status_frame, text="Run", command=self.run, style="Run.TButton")
        self.run_btn.grid(row=0, column=0, padx=(0, 12), pady=6)

        self.progress = ttk.Progressbar(status_frame, mode="indeterminate", length=260)
        self.progress.grid(row=0, column=1, padx=(0, 12), pady=6, sticky="w")
        self.progress.grid_remove()

        ttk.Label(status_frame, textvariable=self.status_var).grid(
            row=0, column=2, sticky="w", pady=6
        )

        output_frame = ttk.LabelFrame(outer, text="Output Log", style="Section.TLabelframe")
        output_frame.pack(fill="both", expand=True)
        self.output = ScrolledText(output_frame, width=110, height=22)
        self.output.pack(fill="both", expand=True)

        self.drop_overlay = tk.Frame(self.root, bg="#cfcfcf")
        self.drop_overlay_label = tk.Label(
            self.drop_overlay,
            text="Drop .tex/.ltx, .bib, or folder",
            bg="#cfcfcf",
            fg="#222222",
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
            self.status_var.set("Main file prefilled from startup argument.")

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
            self.status_var.set("Dropped item ignored (use .tex/.ltx, .bib, or folder).")
            return "copy"
        self.status_var.set("Set from drag-and-drop: " + ", ".join(updates) + ".")
        return "copy"

    def _add_info_icon(
        self,
        parent: tk.Misc,
        row: int,
        column: int,
        text_provider: Callable[[], str],
    ) -> None:
        icon = tk.Canvas(
            parent,
            width=18,
            height=18,
            highlightthickness=0,
            bd=0,
            bg=self.root.cget("bg"),
            cursor="hand2",
        )
        icon.create_oval(2, 2, 16, 16, outline="#6b7280", width=1)
        icon.create_text(9, 9, text="i", fill="#374151", font=("Segoe UI", 9, "bold"))
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
        self.status_var.set("Running conversion...")
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
            self.status_var.set("Failed")
            self._append("Errors:")
            self._append("  - Conversion terminated unexpectedly.")
            return

        self.output.delete("1.0", tk.END)
        if state == "err":
            self.status_var.set("Failed")
            self._append("Errors:")
            self._append(f"  - {payload}")
            return

        report = payload
        if report.errors:
            self.status_var.set("Finished with errors")
        else:
            self.status_var.set("Finished successfully")

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
