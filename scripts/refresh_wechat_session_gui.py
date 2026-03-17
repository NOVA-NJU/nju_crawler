from __future__ import annotations

import queue
import threading
import subprocess
import traceback
from datetime import datetime
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

import tkinter as tk
from tkinter import messagebox, ttk

import refresh_wechat_session as worker


@dataclass
class TaskConfig:
    urls: List[str]
    mode: str
    timeout: int
    file_field: str


class QueueWriter:
    def __init__(self, output_queue: "queue.Queue[str]") -> None:
        self.output_queue = output_queue

    def write(self, message: str) -> None:
        if message:
            self.output_queue.put(message)

    def flush(self) -> None:
        return


class SessionRefreshApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("微信 Session 刷新工具")
        self.root.geometry("760x520")

        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.task_thread: threading.Thread | None = None

        default_urls = ",".join(worker.parse_urls(None))
        self.urls_var = tk.StringVar(value=default_urls)
        self.mode_var = tk.StringVar(value="json")
        self.timeout_var = tk.StringVar(value="60")
        self.file_field_var = tk.StringVar(value=worker.DEFAULT_FILE_FIELD)
        self.status_var = tk.StringVar(value="等待开始")

        self._build_ui()
        self.root.after(120, self._drain_log_queue)

    def _error_log_path(self) -> Path:
        return Path(worker.PROJECT_ROOT) / "refresh_wechat_session_error.log"

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="上传接口(可填多个，逗号分隔):").pack(anchor=tk.W)
        ttk.Entry(frame, textvariable=self.urls_var).pack(fill=tk.X, pady=(2, 8))

        options = ttk.Frame(frame)
        options.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(options, text="上传模式:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        ttk.Radiobutton(options, text="json", value="json", variable=self.mode_var).grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(options, text="file", value="file", variable=self.mode_var).grid(row=0, column=2, sticky=tk.W, padx=(10, 0))

        ttk.Label(options, text="超时(秒):").grid(row=0, column=3, sticky=tk.W, padx=(20, 6))
        ttk.Entry(options, textvariable=self.timeout_var, width=8).grid(row=0, column=4, sticky=tk.W)

        ttk.Label(options, text="file字段名:").grid(row=0, column=5, sticky=tk.W, padx=(20, 6))
        ttk.Entry(options, textvariable=self.file_field_var, width=14).grid(row=0, column=6, sticky=tk.W)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(0, 8))
        self.start_button = ttk.Button(buttons, text="开始扫码并更新 Session", command=self.start_task)
        self.start_button.pack(side=tk.LEFT)
        ttk.Button(buttons, text="打开cfg目录", command=self.open_cfg_dir).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(frame, textvariable=self.status_var).pack(anchor=tk.W, pady=(0, 6))

        self.log_text = tk.Text(frame, height=20, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _drain_log_queue(self) -> None:
        while True:
            try:
                chunk = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(chunk)
        self.root.after(120, self._drain_log_queue)

    def _parse_config(self) -> TaskConfig:
        urls = [item.strip() for item in self.urls_var.get().split(",") if item.strip()]
        if not urls:
            raise ValueError("请至少填写一个上传接口地址")
        try:
            timeout = int(self.timeout_var.get().strip())
        except ValueError as exc:
            raise ValueError("超时必须是整数秒") from exc
        if timeout <= 0:
            raise ValueError("超时必须大于 0")
        mode = self.mode_var.get().strip() or "json"
        if mode not in {"json", "file"}:
            raise ValueError("上传模式必须是 json 或 file")
        file_field = self.file_field_var.get().strip() or worker.DEFAULT_FILE_FIELD
        return TaskConfig(urls=urls, mode=mode, timeout=timeout, file_field=file_field)

    def start_task(self) -> None:
        if self.task_thread and self.task_thread.is_alive():
            messagebox.showinfo("任务进行中", "请等待当前任务完成")
            return

        try:
            config = self._parse_config()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("参数错误", str(exc))
            return

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.start_button.configure(state=tk.DISABLED)
        self.status_var.set("正在运行，请在弹出的浏览器中扫码登录微信公众平台...")

        self.task_thread = threading.Thread(target=self._run_task, args=(config,), daemon=True)
        self.task_thread.start()

    def _run_task(self, config: TaskConfig) -> None:
        writer = QueueWriter(self.log_queue)
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                self._log_browser_process_hint()
                session_path = worker.get_session_path()
                session_data = worker.refresh_session(session_path)
                worker.upload_session(
                    urls=config.urls,
                    session_data=session_data,
                    session_path=session_path,
                    mode=config.mode,
                    file_field=config.file_field,
                    timeout=config.timeout,
                )
            self.root.after(0, self._on_task_done, True, "Session 已刷新并同步成功")
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            log_path = self._error_log_path()
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(f"\n[{stamp}] {exc}\n{tb}\n")
            except Exception:
                pass
            self.root.after(0, self._on_task_done, False, f"{exc}\n详细日志: {log_path}")

    def _log_browser_process_hint(self) -> None:
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-Process | Where-Object { $_.ProcessName -match 'msedgedriver|geckodriver' } | Measure-Object | Select-Object -ExpandProperty Count",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            count = int((completed.stdout or "0").strip() or "0")
            if count > 0:
                print(f"[提示] 检测到 {count} 个浏览器驱动残留进程，若本次无弹窗可先重启工具后重试。")
        except Exception:
            pass

    def _on_task_done(self, success: bool, message: str) -> None:
        self.start_button.configure(state=tk.NORMAL)
        if success:
            self.status_var.set("执行成功")
            self._append_log(f"\n[DONE] {message}\n")
            messagebox.showinfo("执行成功", message)
        else:
            self.status_var.set("执行失败")
            self._append_log(f"\n[ERROR] {message}\n")
            messagebox.showerror("执行失败", message)

    def open_cfg_dir(self) -> None:
        cfg_dir = worker.get_session_path().parent
        cfg_dir.mkdir(parents=True, exist_ok=True)
        try:
            import os

            os.startfile(str(cfg_dir))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", f"无法打开目录: {cfg_dir}\n{exc}")


def main() -> int:
    root = tk.Tk()
    SessionRefreshApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
