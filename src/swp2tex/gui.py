from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import RunOptions, run_workflow


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SWP to Overleaf/arXiv Converter")
        self.main_var = tk.StringVar()
        self.project_var = tk.StringVar()
        self.bib_var = tk.StringVar()
        self.export_mode_var = tk.StringVar(value="overleaf")
        self.status_var = tk.StringVar(value="Idle")
        self._result_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        tk.Label(self.root, text="Main .tex file (Scientific Workplace)").grid(
            row=0, column=0, sticky="w", padx=8, pady=6
        )
        tk.Entry(self.root, textvariable=self.main_var, width=60).grid(
            row=0, column=1, padx=8, pady=6
        )
        tk.Button(self.root, text="Browse", command=self.pick_main).grid(
            row=0, column=2, padx=8, pady=6
        )
        tk.Button(self.root, text="Info", command=self.show_main_info).grid(
            row=0, column=3, padx=8, pady=6
        )

        tk.Label(self.root, text="Project/resource directory").grid(
            row=1, column=0, sticky="w", padx=8, pady=6
        )
        tk.Entry(self.root, textvariable=self.project_var, width=60).grid(
            row=1, column=1, padx=8, pady=6
        )
        tk.Button(self.root, text="Browse", command=self.pick_project).grid(
            row=1, column=2, padx=8, pady=6
        )
        tk.Button(self.root, text="Info", command=self.show_project_info).grid(
            row=1, column=3, padx=8, pady=6
        )

        tk.Label(self.root, text="Optional .bib file").grid(
            row=2, column=0, sticky="w", padx=8, pady=6
        )
        tk.Entry(self.root, textvariable=self.bib_var, width=60).grid(
            row=2, column=1, padx=8, pady=6
        )
        tk.Button(self.root, text="Browse", command=self.pick_bib).grid(
            row=2, column=2, padx=8, pady=6
        )
        tk.Button(self.root, text="Info", command=self.show_bib_info).grid(
            row=2, column=3, padx=8, pady=6
        )

        tk.Label(self.root, text="Export target").grid(
            row=3, column=0, sticky="w", padx=8, pady=6
        )
        radio_frame = tk.Frame(self.root)
        radio_frame.grid(row=3, column=1, sticky="w", padx=8, pady=6)
        tk.Radiobutton(
            radio_frame,
            text="SWP to Overleaf (normal LaTeX)",
            variable=self.export_mode_var,
            value="overleaf",
        ).pack(anchor="w")
        tk.Radiobutton(
            radio_frame,
            text="SWP to arXiv",
            variable=self.export_mode_var,
            value="arxiv",
        ).pack(anchor="w")

        self.run_btn = tk.Button(self.root, text="Run", command=self.run)
        self.run_btn.grid(
            row=4, column=1, sticky="e", padx=8, pady=8
        )

        self.progress = ttk.Progressbar(self.root, mode="indeterminate", length=300)
        self.progress.grid(row=4, column=0, sticky="w", padx=8, pady=8)
        self.progress.grid_remove()
        tk.Label(self.root, textvariable=self.status_var).grid(
            row=4, column=2, columnspan=2, sticky="w", padx=8, pady=8
        )

        self.output = tk.Text(self.root, width=90, height=20)
        self.output.grid(row=5, column=0, columnspan=4, padx=8, pady=8)

    def pick_main(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("LaTeX files", "*.ltx *.tex")])
        if path:
            self.main_var.set(path)

    def pick_project(self) -> None:
        initialdir = self._suggested_project_dir()
        path = filedialog.askdirectory(initialdir=str(initialdir))
        if path:
            self.project_var.set(path)

    def pick_bib(self) -> None:
        suggested = self._default_bib_suggestion()
        path = filedialog.askopenfilename(
            filetypes=[("BibTeX files", "*.bib")],
            initialdir=str(suggested.parent),
            initialfile=suggested.name,
        )
        if path:
            self.bib_var.set(path)

    def show_main_info(self) -> None:
        messagebox.showinfo(
            "Main .tex file",
            "Choose the Scientific Workplace source .tex/.ltx file you want to convert.",
        )

    def show_project_info(self) -> None:
        suggested = self._suggested_project_dir()
        messagebox.showinfo(
            "Project/resource directory",
            "Choose the folder that contains figures and bibliography resources. "
            "Usually this is the same folder as the main file.\n\n"
            f"Suggested location:\n{suggested}",
        )

    def show_bib_info(self) -> None:
        suggested = self._default_bib_suggestion()
        messagebox.showinfo(
            "Optional .bib file",
            "Optional fallback .bib file used only if required bibliography is missing "
            "from the project/resource directory.\n\n"
            f"Suggested common location:\n{suggested}",
        )

    def _default_bib_suggestion(self) -> Path:
        return Path.home() / "Dropbox" / "bibtex" / "general.bib"

    def _suggested_project_dir(self) -> Path:
        main = self.main_var.get().strip()
        if main:
            return Path(main).resolve().parent
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
        main = Path(self.main_var.get().strip())
        project = Path(self.project_var.get().strip())
        if not main or not project:
            messagebox.showerror("Missing input", "Please select main file and project dir.")
            return
        if self._worker is not None and self._worker.is_alive():
            return
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
        self.status_var.set("Finished")
        try:
            state, payload = self._result_queue.get_nowait()
        except queue.Empty:
            self._append("Errors:")
            self._append("  - Conversion terminated unexpectedly.")
            return
        if state == "err":
            self._append("Errors:")
            self._append(f"  - {payload}")
            return
        report = payload
        self.output.delete("1.0", tk.END)
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


def launch_gui() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
