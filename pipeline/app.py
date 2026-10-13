"""A simple window for the pipeline.

The tabs follow the workflow: ask for data, review and build specs, open the
results, and find indicators. Every button runs the same command as the
command line (python -m pipeline ...) and shows its messages, so the window
never does anything the command line would not.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

import yaml

from .config import load_config
from .paths import PROJECT_ROOT, SPECS_DIR

SOURCES = {
    "World Bank (WDI)": "wdi",
    "Penn World Table 11.0": "pwt",
    "ILOSTAT": "ilostat",
    "IMF": "imf",
    "FAOSTAT": "faostat",
    "Statistics Agency of Uzbekistan": "nso_uzb",
}
RESULT_FILES = [
    ("Load in RStudio", "load_and_check.R"),
    ("Validation report", "validation_report.md"),
    ("Codebook", "codebook.md"),
    ("Growth accounting", "growth_accounting.md"),
    ("Growth accounting in RStudio", "growth_accounting.R"),
    ("Data log", "data_log.md"),
    ("Data table (CSV)", "panel_wide.csv"),
]
EXAMPLE = ("Uzbekistan and Kazakhstan, 2000-2023: real value added and employment by broad sector, "
           "gross fixed capital formation, labor share")


# ------------------------------------------------------------------ helpers


@dataclass
class SpecInfo:
    path: Path
    name: str
    title: str
    status: str  # "Draft", "Confirmed" or "Cannot be read"
    build: str  # "Passed", "Failed" or "Not built"
    out_dir: Path


def read_specs(specs_dir: Path = SPECS_DIR, outputs: Path | None = None) -> list[SpecInfo]:
    """Every spec in the specs folder, with whether it is confirmed and how its last build went."""
    outputs = outputs or load_config().folder("outputs")
    found = []
    for path in sorted(specs_dir.glob("*.yaml")):
        if path.name.endswith(".known_values.yaml"):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
            name = str(data.get("name") or path.stem)
            title = str(data.get("title") or "")
            status = "Confirmed" if data.get("confirmed") is True else "Draft"
        except (yaml.YAMLError, OSError):
            name, title, status = path.stem, "", "Cannot be read"
        out_dir = outputs / name
        report = out_dir / "validation_report.md"
        build = "Not built"
        if report.is_file():
            text = report.read_text(encoding="utf-8", errors="replace")
            build = "Passed" if "**PASSED**" in text else "Failed" if "**FAILED**" in text else "Not built"
        found.append(SpecInfo(path, name, title, status, build, out_dir))
    return found


def report_summary(out_dir: Path) -> str:
    report = out_dir / "validation_report.md"
    if not report.is_file():
        return "Not built yet."
    for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("**PASSED**") or line.startswith("**FAILED**"):
            return line.replace("**", "")
    return "See the validation report."


def child_python() -> str:
    """python.exe next to the running interpreter (the window itself may run as pythonw.exe)."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return str(console if console.exists() else exe)


def open_path(path: Path) -> None:
    """Open a file or folder with its usual program; fall back to Notepad for text."""
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError:
        subprocess.Popen(["notepad.exe", str(path)])


class Runner:
    """Runs one pipeline command at a time in the background, passing its output on line by line."""

    def __init__(self, on_line: Callable[[str], None], on_done: Callable[[int], None]):
        self.on_line = on_line
        self.on_done = on_done
        self.process: subprocess.Popen | None = None

    @property
    def busy(self) -> bool:
        return self.process is not None

    def start(self, args: list[str]) -> None:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.process = subprocess.Popen(
            [child_python(), "-m", "pipeline", *args], cwd=PROJECT_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()

    def _read(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.on_line(line.rstrip("\n"))
        code = process.wait()
        self.process = None
        self.on_done(code)

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()


# --------------------------------------------------------------------- window


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.messages: queue.Queue = queue.Queue()
        self.runner = Runner(lambda line: self.messages.put(("line", line)),
                             lambda code: self.messages.put(("done", code)))
        self.when_done: Callable[[int], None] | None = None
        self.action_buttons: list[ttk.Button] = []
        self.specs: dict[str, SpecInfo] = {}

        root.title("Econ data pipeline")
        root.geometry("1000x740")
        root.minsize(960, 640)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.refresh_specs()
        root.after(100, self._poll)
        self.run(["check-setup"], "Checking the setup")

    # ------------------------------------------------------------- layout

    def _build(self) -> None:
        header = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        header.pack(fill="x")
        ttk.Label(header, text="Econ data pipeline", font=("Segoe UI", 15, "bold")).pack(side="left")
        self.status = ttk.Label(header, text="Ready", foreground="#555555")
        self.status.pack(side="right")

        panes = ttk.PanedWindow(self.root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tabs = ttk.Notebook(panes)
        panes.add(self.tabs, weight=3)
        self._ask_tab()
        self._specs_tab()
        self._results_tab()
        self._find_tab()

        bottom = ttk.Frame(panes)
        panes.add(bottom, weight=2)
        bar = ttk.Frame(bottom)
        bar.pack(fill="x", pady=(8, 2))
        ttk.Label(bar, text="Messages", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.stop_button = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="right")
        ttk.Button(bar, text="Clear", command=self.clear_log).pack(side="right", padx=6)
        frame = ttk.Frame(bottom)
        frame.pack(fill="both", expand=True)
        self.log = tk.Text(frame, height=10, wrap="word", font=("Consolas", 10), state="disabled",
                           background="#fbfbfb", relief="solid", borderwidth=1)
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    def _tab(self, title: str, intro: str) -> ttk.Frame:
        tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(tab, text=title)
        ttk.Label(tab, text=intro, wraplength=900, justify="left").pack(anchor="w", pady=(0, 10))
        return tab

    def _button(self, parent: tk.Widget, text: str, command: Callable[[], None], action: bool = True) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        if action:
            self.action_buttons.append(button)
        return button

    def _ask_tab(self) -> None:
        tab = self._tab("1. Ask for data",
                        "Describe the data you need in plain English: countries, years and variables. Claude "
                        "searches the official sources and drafts a spec (an order form) for you to review in "
                        "the next tab. It takes a minute or two and costs a few cents.")
        self.request = tk.Text(tab, height=6, wrap="word", font=("Segoe UI", 11), relief="solid", borderwidth=1)
        self.request.pack(fill="x")
        ttk.Label(tab, text=f"Example: {EXAMPLE}", foreground="#666666", wraplength=900,
                  justify="left").pack(anchor="w", pady=(6, 10))
        row = ttk.Frame(tab)
        row.pack(anchor="w")
        self._button(row, "Draft a spec", self.draft).pack(side="left")
        self._button(row, "Use the example", lambda: self._set_request(EXAMPLE), action=False).pack(side="left", padx=8)

    def _specs_tab(self) -> None:
        tab = self._tab("2. Review and build",
                        "Select a spec. Open it in Notepad to review or edit it (save with Ctrl+S), then Check, "
                        "Confirm and Build. Building reuses the files already downloaded, so results stay the "
                        "same, unless you tick 'Download fresh data'.")
        columns = ("file", "title", "status", "build")
        self.tree = ttk.Treeview(tab, columns=columns, show="headings", height=8, selectmode="browse")
        for col, text, width in (("file", "File", 240), ("title", "Title", 430), ("status", "Status", 110),
                                 ("build", "Last build", 110)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _: self.edit_spec())
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(10, 0))
        self._button(row, "Open in Notepad", self.edit_spec, action=False).pack(side="left")
        self._button(row, "Review notes", self.review_notes, action=False).pack(side="left", padx=6)
        self._button(row, "Check", self.check).pack(side="left", padx=(18, 0))
        self._button(row, "Confirm", self.confirm).pack(side="left", padx=6)
        self._button(row, "Build", self.build).pack(side="left")
        self.refresh = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Download fresh data", variable=self.refresh).pack(side="left", padx=10)
        self._button(row, "Refresh list", self.refresh_specs, action=False).pack(side="right")

    def _results_tab(self) -> None:
        tab = self._tab("3. Results",
                        "Pick a built spec. 'Load in RStudio' opens load_and_check.R: click Source in RStudio and "
                        "you get panel (one row per country-year), panel_long and codebook. Read the validation "
                        "report before analysing: it lists breaks, gaps and values the sources filled in.")
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Label(row, text="Spec:").pack(side="left")
        self.result_choice = ttk.Combobox(row, state="readonly", width=50)
        self.result_choice.pack(side="left", padx=8)
        self.result_choice.bind("<<ComboboxSelected>>", lambda _: self.show_result())
        self.result_status = ttk.Label(tab, text="", wraplength=900, justify="left")
        self.result_status.pack(anchor="w", pady=10)
        grid = ttk.Frame(tab)
        grid.pack(anchor="w")
        self.result_buttons: dict[str, ttk.Button] = {}
        for i, (text, name) in enumerate(RESULT_FILES + [("Open results folder", "")]):
            button = ttk.Button(grid, text=text, width=28, command=lambda n=name: self.open_result(n))
            button.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="w")
            self.result_buttons[name] = button

    def _find_tab(self) -> None:
        tab = self._tab("Find indicators",
                        "Search a source for indicator codes, or list the choices (filters) a code offers for a "
                        "country. Results appear under Messages.")
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Label(row, text="Source:").pack(side="left")
        self.find_source = ttk.Combobox(row, state="readonly", values=list(SOURCES), width=30)
        self.find_source.current(0)
        self.find_source.pack(side="left", padx=8)
        ttk.Label(row, text="Words:").pack(side="left")
        self.find_words = ttk.Entry(row, width=40)
        self.find_words.pack(side="left", padx=8)
        self.find_words.bind("<Return>", lambda _: self.search())
        self._button(row, "Search", self.search).pack(side="left")
        row2 = ttk.Frame(tab)
        row2.pack(fill="x", pady=(14, 0))
        ttk.Label(row2, text="Code:").pack(side="left")
        self.find_code = ttk.Entry(row2, width=26)
        self.find_code.pack(side="left", padx=8)
        ttk.Label(row2, text="Dataset (IMF, FAOSTAT):").pack(side="left")
        self.find_dataset = ttk.Entry(row2, width=16)
        self.find_dataset.pack(side="left", padx=8)
        ttk.Label(row2, text="Country:").pack(side="left")
        self.find_country = ttk.Entry(row2, width=6)
        self.find_country.insert(0, "UZB")
        self.find_country.pack(side="left", padx=8)
        self._button(row2, "Show choices", self.describe).pack(side="left")

    # ------------------------------------------------------------ running

    def run(self, args: list[str], what: str, when_done: Callable[[int], None] | None = None) -> None:
        if self.runner.busy:
            messagebox.showinfo("Please wait", "Another step is still running. Wait for it, or press Stop.")
            return
        self.write(f"\n> {what}")
        self.status.configure(text=f"Working: {what}...")
        for button in self.action_buttons:
            button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.when_done = when_done
        try:
            self.runner.start(args)
        except OSError as exc:
            self.messages.put(("line", f"Could not start: {exc}"))
            self.messages.put(("done", 1))

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "line":
                    self.write(value)
                else:
                    self._finished(value)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finished(self, code: int) -> None:
        self.status.configure(text="Ready" if code == 0 else "Ready (the last step did not succeed; see Messages)")
        for button in self.action_buttons:
            button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.refresh_specs()
        callback, self.when_done = self.when_done, None
        if callback is not None:
            callback(code)

    def stop(self) -> None:
        self.runner.stop()
        self.write("Stopping...")

    def close(self) -> None:
        if self.runner.busy and not messagebox.askyesno("Still working", "A step is still running. Stop it and close?"):
            return
        self.runner.stop()
        self.root.destroy()

    def write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -------------------------------------------------------------- specs

    def refresh_specs(self) -> None:
        chosen = self.selected_spec()
        infos = read_specs()
        self.specs = {str(i.path): i for i in infos}
        self.tree.delete(*self.tree.get_children())
        for info in infos:
            self.tree.insert("", "end", iid=str(info.path),
                             values=(info.path.name, info.title, info.status, info.build))
        if chosen and str(chosen.path) in self.specs:
            self.tree.selection_set(str(chosen.path))
        built = [i.path.name for i in infos if i.build != "Not built"]
        current = self.result_choice.get()
        self.result_choice.configure(values=built)
        if built:
            self.result_choice.set(current if current in built else built[0])
        self.show_result()

    def selected_spec(self) -> SpecInfo | None:
        selection = self.tree.selection() if hasattr(self, "tree") else ()
        return self.specs.get(selection[0]) if selection else None

    def _need_spec(self) -> SpecInfo | None:
        spec = self.selected_spec()
        if spec is None:
            messagebox.showinfo("Choose a spec", "Select a spec in the list first.")
        return spec

    def edit_spec(self) -> None:
        if spec := self._need_spec():
            subprocess.Popen(["notepad.exe", str(spec.path)])

    def review_notes(self) -> None:
        if spec := self._need_spec():
            notes = spec.path.with_name(f"{spec.path.stem}.review.md")
            if notes.is_file():
                subprocess.Popen(["notepad.exe", str(notes)])
            else:
                messagebox.showinfo("No review notes", "This spec has no review notes (they come from 'Draft a spec').")

    def check(self) -> None:
        if spec := self._need_spec():
            self.run(["check-spec", str(spec.path)], f"Checking {spec.path.name}")

    def confirm(self) -> None:
        if spec := self._need_spec():
            if spec.status == "Confirmed":
                messagebox.showinfo("Already confirmed", f"{spec.path.name} is already confirmed.")
                return
            if messagebox.askyesno("Confirm the spec",
                                   f"Have you reviewed {spec.path.name}? Confirming means the build will "
                                   "download and use exactly what it lists."):
                self.run(["confirm", str(spec.path)], f"Confirming {spec.path.name}")

    def build(self) -> None:
        if spec := self._need_spec():
            if spec.status != "Confirmed":
                messagebox.showinfo("Not confirmed yet", "Review the spec and click Confirm before building.")
                return
            args = ["build", str(spec.path)] + (["--refresh"] if self.refresh.get() else [])
            self.run(args, f"Building {spec.path.name}", lambda code, s=spec: self._built(s, code))

    def _built(self, spec: SpecInfo, code: int) -> None:
        self.result_choice.set(spec.path.name)
        self.show_result()
        if code == 0:
            self.tabs.select(2)

    # ---------------------------------------------------------- requests

    def _set_request(self, text: str) -> None:
        self.request.delete("1.0", "end")
        self.request.insert("1.0", text)

    def draft(self) -> None:
        text = self.request.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("Describe the data", "Type what data you need first.")
            return
        before = {p.name for p in SPECS_DIR.glob("*.yaml")}
        self.run(["request", text], "Drafting a spec with Claude", lambda code: self._drafted(before, code))

    def _drafted(self, before: set[str], code: int) -> None:
        new = [p for p in SPECS_DIR.glob("*.yaml") if p.name not in before and not p.name.endswith(".known_values.yaml")]
        if code == 0 and new:
            path = max(new, key=lambda p: p.stat().st_mtime)
            self.tree.selection_set(str(path))
            self.tree.see(str(path))
            self.tabs.select(1)
            self.write(f"New draft: {path.name}. Open it in Notepad to review it, then Confirm and Build.")

    # ----------------------------------------------------------- results

    def _result_dir(self) -> Path | None:
        name = self.result_choice.get()
        info = next((i for i in self.specs.values() if i.path.name == name), None)
        return info.out_dir if info else None

    def show_result(self) -> None:
        out = self._result_dir()
        if out is None:
            self.result_status.configure(text="Nothing built yet: build a spec in the previous tab.")
        else:
            self.result_status.configure(text=report_summary(out) + f"\nFolder: {out}")
        for name, button in self.result_buttons.items():
            available = out is not None and (out / name).exists() if name else out is not None and out.is_dir()
            button.configure(state="normal" if available else "disabled")

    def open_result(self, name: str) -> None:
        out = self._result_dir()
        if out is not None:
            open_path(out / name if name else out)

    # -------------------------------------------------------------- find

    def search(self) -> None:
        words = self.find_words.get().strip()
        if not words:
            messagebox.showinfo("Search", "Type some words to search for.")
            return
        source = SOURCES[self.find_source.get()]
        self.run(["search", source, words, "--limit", "8"], f"Searching {self.find_source.get()} for '{words}'")

    def describe(self) -> None:
        code = self.find_code.get().strip()
        if not code:
            messagebox.showinfo("Show choices", "Type an indicator code (from a search) first.")
            return
        args = ["describe", SOURCES[self.find_source.get()], code, "--country", self.find_country.get().strip() or "UZB"]
        if self.find_dataset.get().strip():
            args += ["--dataset", self.find_dataset.get().strip()]
        self.run(args, f"Choices for {code}")


def main() -> None:
    try:  # sharp text on high-resolution screens
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
