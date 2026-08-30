"""The desktop window: connect GitHub, pick a repo, see the agent's queue,
click an issue to turn it into a PR. All work runs on worker threads and
reports back through `after()`, so the window never freezes."""

from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable

import config
from gui import engine
from tasks.base import Task

APP_TITLE = "Ticket2PR"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 16, "bold")
FONT_CODE = ("Consolas", 20, "bold")
FONT_MONO = ("Consolas", 10)
OK = "#1a7f37"
BAD = "#b42318"
MUTED = "#6b7280"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x720")
        self.minsize(820, 600)
        self._set_icon()
        self.settings = engine.Settings.load()
        self.tasks: dict[str, Task] = {}
        self.github: engine.GitHubStatus | None = None
        self.running = False
        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self.refresh_status)

    # ----------------------------------------------------------------- UI --

    def _set_icon(self) -> None:
        base = Path(getattr(sys, "_MEIPASS", config.APP_DIR))
        icon = base / "Ticket2PR.ico"
        if icon.is_file():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass

    def _style(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure(".", font=FONT)
        style.configure("Title.TLabel", font=FONT_TITLE)
        style.configure("Bold.TLabel", font=FONT_BOLD)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Accent.TButton", font=FONT_BOLD)
        style.configure("Treeview", rowheight=26, font=FONT)
        style.configure("Treeview.Heading", font=FONT_BOLD)

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 6}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=3)
        self.rowconfigure(5, weight=2)

        # Header: title + connection status
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", **pad)
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="GitHub issues in, pull requests out.", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w"
        )
        status = ttk.Frame(header)
        status.grid(row=0, column=2, rowspan=2, sticky="e")
        self.gh_dot = tk.Label(status, text="●", fg=MUTED, font=("Segoe UI", 12))
        self.gh_dot.grid(row=0, column=0)
        self.gh_label = ttk.Label(status, text="GitHub: checking...")
        self.gh_label.grid(row=0, column=1, sticky="w", padx=(0, 10))
        self.connect_btn = ttk.Button(status, text="Connect...", command=self.open_connect)
        self.connect_btn.grid(row=0, column=2)
        self.claude_dot = tk.Label(status, text="●", fg=MUTED, font=("Segoe UI", 12))
        self.claude_dot.grid(row=1, column=0)
        self.claude_label = ttk.Label(
            status, text="Claude: checking...", wraplength=460, justify="left"
        )
        self.claude_label.grid(row=1, column=1, sticky="w", padx=(0, 10))
        self.claude_btn = ttk.Button(
            status, text="Install...", command=lambda: webbrowser.open(engine.CLAUDE_INSTALL_URL)
        )

        # Repository
        repo = ttk.LabelFrame(self, text="Repository the agent works in")
        repo.grid(row=1, column=0, sticky="ew", **pad)
        repo.columnconfigure(1, weight=1)
        ttk.Label(repo, text="Local clone:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.repo_var = tk.StringVar(
            value=self.settings.target_repo or str(config.DEFAULT_TARGET_REPO)
        )
        self.repo_var.trace_add("write", lambda *_: self._on_repo_changed())
        ttk.Entry(repo, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(repo, text="Browse...", command=self.browse_repo).grid(row=0, column=2, padx=4)
        ttk.Button(repo, text="Clone from GitHub...", command=self.open_clone).grid(
            row=0, column=3, padx=(0, 8)
        )
        self.slug_label = ttk.Label(repo, text="", style="Muted.TLabel")
        self.slug_label.grid(row=1, column=1, columnspan=3, sticky="w", pady=(0, 6))

        # Queue
        queue = ttk.LabelFrame(self, text="Issues assigned to the agent")
        queue.grid(row=2, column=0, sticky="ew", **pad)
        ttk.Label(queue, text="Label:").grid(row=0, column=0, padx=8, pady=6)
        self.label_var = tk.StringVar(value=self.settings.label or config.DEFAULT_LABEL)
        ttk.Entry(queue, textvariable=self.label_var, width=18).grid(row=0, column=1, pady=6)
        ttk.Label(queue, text="Assignee (optional):").grid(row=0, column=2, padx=(16, 4))
        self.assignee_var = tk.StringVar(value=self.settings.assignee)
        ttk.Entry(queue, textvariable=self.assignee_var, width=18).grid(row=0, column=3)
        self.refresh_btn = ttk.Button(queue, text="Refresh", command=self.refresh_tasks)
        self.refresh_btn.grid(row=0, column=4, padx=12)
        self.queue_hint = ttk.Label(queue, text="", style="Muted.TLabel")
        self.queue_hint.grid(row=1, column=0, columnspan=6, sticky="w", padx=8, pady=(0, 6))

        table = ttk.Frame(self)
        table.grid(row=3, column=0, sticky="nsew", padx=12)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        cols = ("number", "title", "labels", "updated")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="extended")
        for col, text, width, anchor in (
            ("number", "#", 60, "e"),
            ("title", "Title", 520, "w"),
            ("labels", "Labels", 180, "w"),
            ("updated", "Updated", 110, "w"),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col == "title"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<Double-1>", lambda _e: self.run_selected())

        # Actions
        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="ew", **pad)
        actions.columnconfigure(3, weight=1)
        self.dry_var = tk.BooleanVar(value=self.settings.dry_run)
        ttk.Checkbutton(
            actions,
            text="Dry run (make the changes, but don't push or open a PR)",
            variable=self.dry_var,
        ).grid(row=0, column=0, sticky="w")
        self.run_btn = ttk.Button(
            actions,
            text="Create PR for selected",
            style="Accent.TButton",
            command=self.run_selected,
        )
        self.run_btn.grid(row=0, column=1, padx=16)
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=160)
        self.progress.grid(row=0, column=2)
        self.progress.grid_remove()  # shown only while a run is in progress
        self.run_status = ttk.Label(actions, text="", style="Muted.TLabel")
        self.run_status.grid(row=0, column=3, sticky="w", padx=8)

        # Log
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 12))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = scrolledtext.ScrolledText(
            log_frame, height=10, font=FONT_MONO, wrap="word", state="disabled"
        )
        self.log.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.log.tag_configure("error", foreground=BAD)
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("muted", foreground=MUTED)
        self.log.tag_configure("link", foreground="#0969da", underline=True)
        self.log.tag_bind("link", "<Button-1>", self._open_link_under_cursor)
        self.log.tag_bind("link", "<Enter>", lambda _e: self.log.configure(cursor="hand2"))
        self.log.tag_bind("link", "<Leave>", lambda _e: self.log.configure(cursor=""))

        self._on_repo_changed()

    # ------------------------------------------------------------ helpers --

    def _bg(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        """Run `work` on a thread, hand its result (or exception) to `done` on the UI thread."""

        def target() -> None:
            try:
                result: object = work()
            except Exception as exc:  # surfaced to the user, never swallowed
                result = exc
            self.after(0, lambda: done(result))

        threading.Thread(target=target, daemon=True).start()

    def append_log(self, text: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        if text.startswith("http://") or text.startswith("https://"):
            self.log.insert("end", text, ("link",))
            self.log.insert("end", "\n")
        else:
            self.log.insert("end", text + "\n", (tag,) if tag else ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def log_link(self, prefix: str, url: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", prefix, ("ok",))
        self.log.insert("end", url, ("link",))
        self.log.insert("end", "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_link_under_cursor(self, event) -> None:
        index = self.log.index(f"@{event.x},{event.y}")
        start, end = self.log.tag_prevrange("link", index + "+1c") or (None, None)
        if start:
            webbrowser.open(self.log.get(start, end))

    def _save_settings(self) -> None:
        self.settings.target_repo = self.repo_var.get().strip()
        self.settings.label = self.label_var.get().strip() or config.DEFAULT_LABEL
        self.settings.assignee = self.assignee_var.get().strip()
        self.settings.dry_run = bool(self.dry_var.get())
        try:
            self.settings.save()
        except OSError:
            pass

    def _on_close(self) -> None:
        self._save_settings()
        self.destroy()

    # ------------------------------------------------------------- status --

    def refresh_status(self) -> None:
        self.gh_label.configure(text="GitHub: checking...")
        self._bg(engine.github_status, self._show_github_status)
        self._bg(engine.claude_status, self._show_claude_status)

    def _show_github_status(self, result: object) -> None:
        if isinstance(result, Exception):
            result = engine.GitHubStatus(False, False, detail=str(result))
        assert isinstance(result, engine.GitHubStatus)
        self.github = result
        if result.ok:
            self.gh_dot.configure(fg=OK)
            self.gh_label.configure(text=f"GitHub: connected as @{result.user}")
            self.connect_btn.configure(text="Switch account...")
            if not self.tasks:
                self.refresh_tasks()
        elif not result.installed:
            self.gh_dot.configure(fg=BAD)
            self.gh_label.configure(text="GitHub: gh CLI not installed")
            self.connect_btn.configure(
                text="Install gh...", command=lambda: webbrowser.open(engine.GH_INSTALL_URL)
            )
        else:
            self.gh_dot.configure(fg=BAD)
            self.gh_label.configure(text="GitHub: not connected")
            self.connect_btn.configure(text="Connect...", command=self.open_connect)
            self.after(300, self.open_connect)

    def _show_claude_status(self, result: object) -> None:
        if isinstance(result, Exception):
            result = engine.ClaudeStatus(False, str(result))
        assert isinstance(result, engine.ClaudeStatus)
        self.claude_dot.configure(fg=OK if result.found else BAD)
        self.claude_label.configure(text=f"Claude: {result.detail}")
        if result.found:
            self.claude_btn.grid_remove()
        else:
            self.claude_btn.grid(row=1, column=2)

    # ------------------------------------------------------------ connect --

    def open_connect(self) -> None:
        if getattr(self, "_connect_dialog", None) and self._connect_dialog.winfo_exists():
            self._connect_dialog.lift()
            return
        self._connect_dialog = ConnectDialog(self, on_connected=self.refresh_status)

    # --------------------------------------------------------------- repo --

    def browse_repo(self) -> None:
        chosen = filedialog.askdirectory(title="Pick the local clone the agent should work in")
        if chosen:
            self.repo_var.set(str(Path(chosen)))

    def open_clone(self) -> None:
        if not (self.github and self.github.ok):
            messagebox.showinfo(APP_TITLE, "Connect to GitHub first.")
            return
        CloneDialog(self, on_cloned=lambda path: self.repo_var.set(str(path)))

    def _on_repo_changed(self) -> None:
        path = self.repo_var.get().strip()
        if not path:
            self.slug_label.configure(text="Pick a local clone, or clone one from GitHub.")
            return
        if not engine.is_git_repo(path):
            self.slug_label.configure(
                text="Not a git repo yet: browse to a clone, or use Clone from GitHub."
            )
            return
        slug = engine.detect_slug(path)
        self.slug_label.configure(
            text=(
                f"GitHub: {slug} (from the origin remote)"
                if slug
                else "origin remote is not a GitHub URL"
            )
        )

    # -------------------------------------------------------------- tasks --

    def _current_slug(self) -> str | None:
        return engine.detect_slug(self.repo_var.get().strip())

    def refresh_tasks(self) -> None:
        slug = self._current_slug()
        if not slug:
            self.queue_hint.configure(text="Pick a GitHub clone first.")
            return
        if not (self.github and self.github.ok):
            self.queue_hint.configure(text="Connect to GitHub first.")
            return
        label = self.label_var.get().strip() or config.DEFAULT_LABEL
        assignee = self.assignee_var.get().strip()
        self.refresh_btn.configure(state="disabled")
        self.queue_hint.configure(text=f"Loading {slug}...")
        self._bg(
            lambda: engine.list_tasks(slug, label, assignee),
            lambda r: self._show_tasks(r, slug, label),
        )

    def _show_tasks(self, result: object, slug: str, label: str) -> None:
        self.refresh_btn.configure(state="normal")
        self.tree.delete(*self.tree.get_children())
        self.tasks.clear()
        if isinstance(result, Exception):
            self.queue_hint.configure(text="Could not load issues.")
            self.append_log(f"Could not load issues from {slug}: {result}", "error")
            return
        assert isinstance(result, list)
        for task in result:
            self.tasks[task.id] = task
            self.tree.insert(
                "",
                "end",
                iid=task.id,
                values=(f"#{task.id}", task.title, ", ".join(task.labels), task.updated_at[:10]),
            )
        if result:
            self.queue_hint.configure(
                text=f"{len(result)} open issue(s) on {slug}. Select one and click Create PR."
            )
        else:
            self.queue_hint.configure(text=f"No open '{label}' issues on {slug}.")
            self.append_log(
                f"No open issues labeled '{label}' on {slug}. "
                "Hand the agent work by adding that label "
                "to an issue (or set the assignee filter).",
                "muted",
            )
        self._save_settings()

    # ---------------------------------------------------------------- run --

    def run_selected(self) -> None:
        if self.running:
            return
        selected = [self.tasks[i] for i in self.tree.selection() if i in self.tasks]
        if not selected:
            messagebox.showinfo(APP_TITLE, "Select one or more issues first.")
            return
        repo_path = self.repo_var.get().strip()
        if not engine.is_git_repo(repo_path):
            messagebox.showerror(APP_TITLE, f"{repo_path} is not a git repo.")
            return
        dry = bool(self.dry_var.get())
        try:
            if engine.repo_is_dirty(repo_path):
                if not messagebox.askyesno(
                    APP_TITLE,
                    "The target repo has uncommitted changes (from a previous dry run?).\n\n"
                    "Every task must start from a clean base branch. Discard them and continue?",
                ):
                    return
                engine.reset_repo(repo_path)
                self.append_log("Discarded uncommitted changes in the target repo.", "muted")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not check the target repo: {exc}")
            return
        self._save_settings()
        self._set_running(True)
        mode = "dry run" if dry else "live run: will push and open PRs"
        self.append_log(f"Starting {len(selected)} task(s) on {repo_path} ({mode})", "muted")
        self._bg(lambda: self._run_all(repo_path, selected, dry), self._run_finished)

    def _run_all(self, repo_path: str, tasks: list[Task], dry: bool) -> list[tuple[Task, object]]:
        results: list[tuple[Task, object]] = []
        for task in tasks:
            self.after(
                0, lambda t=task: self.run_status.configure(text=f"Working on #{t.id}: {t.title}")
            )
            self.after(0, lambda t=task: self.append_log(f"\n=== #{t.id}: {t.title} ===", "ok"))
            try:
                result = engine.run_issue(
                    repo_path,
                    task,
                    dry,
                    on_log=lambda line: self.after(0, lambda text=line: self.append_log(text)),
                    model=config.AGENT_MODEL,
                )
            except Exception as exc:
                result = exc
            results.append((task, result))
        return results

    def _run_finished(self, result: object) -> None:
        self._set_running(False)
        if isinstance(result, Exception):
            self.append_log(f"Run failed: {result}", "error")
            return
        assert isinstance(result, list)
        opened = 0
        for task, outcome in result:
            if isinstance(outcome, Exception):
                self.append_log(f"#{task.id} failed: {outcome}", "error")
            elif outcome.pr_url:
                opened += 1
                self.log_link(f"#{task.id} PR ready for review: ", outcome.pr_url)
            elif not outcome.changed:
                self.append_log(
                    f"#{task.id}: the agent made no changes; make the issue more specific.", "muted"
                )
            else:
                self.append_log(
                    f"#{task.id}: dry run finished; "
                    "the changes are uncommitted in the target repo. "
                    "Untick Dry run and run again to open the PR.",
                    "muted",
                )
        self.run_status.configure(text=f"Done: {opened} PR(s) opened." if opened else "Done.")
        self.refresh_tasks()

    def _set_running(self, running: bool) -> None:
        self.running = running
        state = "disabled" if running else "normal"
        for widget in (self.run_btn, self.refresh_btn):
            widget.configure(state=state)
        if running:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()


class ConnectDialog(tk.Toplevel):
    """Sign in with the browser (gh's device flow) or paste a token. Either way
    the login lives in gh's credential store, not in this app."""

    def __init__(self, parent: App, on_connected: Callable[[], None]) -> None:
        super().__init__(parent)
        self.title("Connect to GitHub")
        self.resizable(False, False)
        self.transient(parent)
        self.on_connected = on_connected
        self.login: engine.WebLogin | None = None
        pad = {"padx": 16, "pady": 6}

        ttk.Label(self, text="Connect to GitHub", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", **pad
        )
        ttk.Label(
            self,
            text="Ticket2PR reads issues and opens pull requests through the GitHub CLI,\n"
            "using its login. No token is stored by this app.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", **pad)

        web = ttk.LabelFrame(self, text="Sign in with your browser")
        web.grid(row=2, column=0, sticky="ew", **pad)
        self.web_btn = ttk.Button(
            web, text="Get a one-time code", style="Accent.TButton", command=self.start_web
        )
        self.web_btn.grid(row=0, column=0, padx=12, pady=10, sticky="w")
        self.code_label = ttk.Label(web, text="", font=FONT_CODE)
        self.code_label.grid(row=0, column=1, padx=12)
        self.web_hint = ttk.Label(
            web, text="", style="Muted.TLabel", wraplength=420, justify="left"
        )
        self.web_hint.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))
        self.open_btn = ttk.Button(
            web,
            text="Open github.com/login/device",
            command=lambda: webbrowser.open(engine.DEVICE_URL),
        )

        token = ttk.LabelFrame(self, text="Or paste a personal access token")
        token.grid(row=3, column=0, sticky="ew", **pad)
        self.token_var = tk.StringVar()
        ttk.Entry(token, textvariable=self.token_var, show="*", width=48).grid(
            row=0, column=0, padx=12, pady=10
        )
        ttk.Button(token, text="Use token", command=self.use_token).grid(
            row=0, column=1, padx=(0, 12)
        )
        ttk.Label(
            token,
            text="Needs the repo scope (classic) or Issues + Pull requests + Contents "
            "(fine-grained).",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))

        self.status = ttk.Label(self, text="", style="Muted.TLabel", wraplength=460, justify="left")
        self.status.grid(row=4, column=0, sticky="w", **pad)
        ttk.Button(self, text="Close", command=self.close).grid(row=5, column=0, sticky="e", **pad)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.grab_set()

    def start_web(self) -> None:
        self.web_btn.configure(state="disabled")
        self.web_hint.configure(text="Asking GitHub for a code...")
        self.login = engine.WebLogin()
        self.login.start(
            on_code=lambda code: self.after(0, lambda: self._show_code(code)),
            on_done=lambda ok, out: self.after(0, lambda: self._web_done(ok, out)),
        )

    def _show_code(self, code: str) -> None:
        self.code_label.configure(text=code)
        self.clipboard_clear()
        self.clipboard_append(code)
        self.web_hint.configure(
            text="Code copied to your clipboard. Paste it on the GitHub page that opens, "
            "approve the login, then come back here."
        )
        self.open_btn.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))
        webbrowser.open(engine.DEVICE_URL)

    def _web_done(self, ok: bool, output: str) -> None:
        if ok:
            self.status.configure(text="Connected.", foreground=OK)
            self.on_connected()
            self.after(600, self.close)
        else:
            self.web_btn.configure(state="normal")
            self.status.configure(text=f"Login did not complete: {output[-400:]}", foreground=BAD)

    def use_token(self) -> None:
        self.status.configure(text="Checking token...", foreground=MUTED)

        def done(result: object) -> None:
            if isinstance(result, Exception):
                self.status.configure(text=str(result), foreground=BAD)
                return
            ok, detail = result  # type: ignore[misc]
            if ok:
                self.status.configure(text="Connected.", foreground=OK)
                self.on_connected()
                self.after(600, self.close)
            else:
                self.status.configure(text=detail, foreground=BAD)

        token = self.token_var.get()
        self.master._bg(lambda: engine.login_with_token(token), done)  # type: ignore[attr-defined]

    def close(self) -> None:
        if self.login:
            self.login.cancel()
        self.grab_release()
        self.destroy()


class CloneDialog(tk.Toplevel):
    def __init__(self, parent: App, on_cloned: Callable[[Path], None]) -> None:
        super().__init__(parent)
        self.title("Clone a repository from GitHub")
        self.resizable(False, False)
        self.transient(parent)
        self.on_cloned = on_cloned
        pad = {"padx": 16, "pady": 6}
        ttk.Label(self, text="Repository:").grid(row=0, column=0, sticky="w", **pad)
        self.repo_var = tk.StringVar()
        self.combo = ttk.Combobox(
            self, textvariable=self.repo_var, width=48, values=["Loading your repositories..."]
        )
        self.combo.grid(row=0, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Label(self, text="Clone into folder:").grid(row=1, column=0, sticky="w", **pad)
        self.dest_var = tk.StringVar(value=str(Path(config.DEFAULT_TARGET_REPO).parent))
        ttk.Entry(self, textvariable=self.dest_var, width=48).grid(
            row=1, column=1, sticky="ew", **pad
        )
        ttk.Button(self, text="Browse...", command=self.browse).grid(row=1, column=2, **pad)
        self.status = ttk.Label(self, text="", style="Muted.TLabel")
        self.status.grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        self.clone_btn = ttk.Button(self, text="Clone", style="Accent.TButton", command=self.clone)
        self.clone_btn.grid(row=3, column=2, sticky="e", **pad)
        parent._bg(engine.list_repos, self._show_repos)
        self.grab_set()

    def _show_repos(self, result: object) -> None:
        if isinstance(result, Exception):
            self.status.configure(text=f"Could not list repositories: {result}")
            self.combo.configure(values=[])
            return
        assert isinstance(result, list)
        self.combo.configure(values=result)
        if result:
            self.combo.current(0)
        self.status.configure(
            text=f"{len(result)} repositories you can access. You can also type owner/repo."
        )

    def browse(self) -> None:
        chosen = filedialog.askdirectory(title="Folder to clone into")
        if chosen:
            self.dest_var.set(str(Path(chosen)))

    def clone(self) -> None:
        slug = self.repo_var.get().strip()
        dest = Path(self.dest_var.get().strip())
        if "/" not in slug:
            self.status.configure(text="Pick a repository (owner/repo).")
            return
        if not dest.is_dir():
            self.status.configure(text="Pick an existing folder to clone into.")
            return
        target = dest / slug.split("/")[-1]
        if target.exists():
            self.status.configure(text=f"{target} already exists; using it.")
            self.on_cloned(target)
            self.after(800, self.destroy)
            return
        self.clone_btn.configure(state="disabled")
        self.status.configure(text=f"Cloning {slug} into {dest}...")

        def done(result: object) -> None:
            if isinstance(result, Exception):
                self.clone_btn.configure(state="normal")
                self.status.configure(text=f"Clone failed: {result}")
                return
            assert isinstance(result, Path)
            self.on_cloned(result)
            self.destroy()

        self.master._bg(lambda: engine.clone_repo(slug, dest), done)  # type: ignore[attr-defined]


def run() -> None:
    App().mainloop()
