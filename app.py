from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND_PATH = PROJECT_ROOT / "backend" / "cut.py"
PROGRESS_PREFIX = "CUTPY_PROGRESS "


class CutpyApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Cutpy · 遥感影像切片工具")
        self.geometry("900x680")
        self.minsize(760, 580)
        self.configure(bg="#f4f7fb")

        self.image_path = tk.StringVar()
        self.label_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.status_text = tk.StringVar(value="准备就绪")
        self.progress_text = tk.StringVar(value="0%")
        self.detail_text = tk.StringVar(value="等待开始处理")

        self.process: subprocess.Popen[str] | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.reader_thread: threading.Thread | None = None
        self.completed_normally = False

        self._build_style()
        self._build_ui()
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background="#f4f7fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure(
            "Title.TLabel",
            background="#f4f7fb",
            foreground="#132238",
            font=("Microsoft YaHei UI", 22, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background="#f4f7fb",
            foreground="#617089",
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "CardTitle.TLabel",
            background="#ffffff",
            foreground="#132238",
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        style.configure(
            "Field.TLabel",
            background="#ffffff",
            foreground="#33445d",
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Status.TLabel",
            background="#ffffff",
            foreground="#617089",
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Accent.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(18, 9),
        )
        style.configure("Stop.TButton", padding=(18, 9))
        style.configure("App.Horizontal.TProgressbar", thickness=14)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, style="App.TFrame", padding=28)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="Cutpy", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            root,
            text="ArcGIS 遥感影像数据集切片工具",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 22))

        input_card = ttk.Frame(root, style="Card.TFrame", padding=22)
        input_card.pack(fill="x")
        ttk.Label(input_card, text="输入与输出", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 16)
        )

        self._add_path_row(
            input_card,
            row=1,
            label="basefile / 遥感影像",
            variable=self.image_path,
            button_text="选择影像",
            command=self._choose_image,
            hint="GeoTIFF 等栅格文件，至少包含 3 个 Byte 波段",
        )
        self._add_path_row(
            input_card,
            row=2,
            label="面标注 / Shapefile",
            variable=self.label_path,
            button_text="选择标注",
            command=self._choose_label,
            hint="用于栅格化标签的 Polygon / MultiPolygon Shapefile",
        )
        self._add_path_row(
            input_card,
            row=3,
            label="输出文件夹",
            variable=self.output_path,
            button_text="选择文件夹",
            command=self._choose_output,
            hint="将自动生成 image 和 lable 子文件夹",
        )
        input_card.columnconfigure(1, weight=1)

        action_row = ttk.Frame(root, style="App.TFrame")
        action_row.pack(fill="x", pady=(18, 18))
        self.start_button = ttk.Button(
            action_row,
            text="开始切片",
            style="Accent.TButton",
            command=self._start,
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            action_row,
            text="停止任务",
            style="Stop.TButton",
            command=self._stop,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(10, 0))
        ttk.Label(
            action_row,
            textvariable=self.status_text,
            style="Subtitle.TLabel",
        ).pack(side="right", pady=10)

        progress_card = ttk.Frame(root, style="Card.TFrame", padding=22)
        progress_card.pack(fill="x")
        progress_header = ttk.Frame(progress_card, style="Card.TFrame")
        progress_header.pack(fill="x")
        ttk.Label(progress_header, text="处理进度", style="CardTitle.TLabel").pack(
            side="left"
        )
        ttk.Label(
            progress_header,
            textvariable=self.progress_text,
            style="CardTitle.TLabel",
        ).pack(side="right")
        self.progress_bar = ttk.Progressbar(
            progress_card,
            style="App.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
        )
        self.progress_bar.pack(fill="x", pady=(14, 8))
        ttk.Label(
            progress_card,
            textvariable=self.detail_text,
            style="Status.TLabel",
        ).pack(anchor="w")

        log_card = ttk.Frame(root, style="Card.TFrame", padding=22)
        log_card.pack(fill="both", expand=True, pady=(18, 0))
        ttk.Label(log_card, text="运行日志", style="CardTitle.TLabel").pack(
            anchor="w", pady=(0, 10)
        )
        log_frame = ttk.Frame(log_card, style="Card.TFrame")
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_frame,
            height=8,
            wrap="word",
            state="disabled",
            bg="#f8fafc",
            fg="#41516a",
            relief="flat",
            padx=12,
            pady=10,
            font=("Consolas", 9),
        )
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _add_path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        button_text: str,
        command,
        hint: str,
    ) -> None:
        ttk.Label(parent, text=label, style="Field.TLabel", width=20).grid(
            row=row, column=0, sticky="w", pady=7
        )
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 10), pady=7)
        ttk.Button(parent, text=button_text, command=command).grid(
            row=row, column=2, sticky="e", pady=7
        )
        ttk.Label(parent, text=hint, style="Status.TLabel").grid(
            row=row + 1, column=1, sticky="w", padx=(8, 0), pady=(0, 4)
        )

    def _choose_image(self) -> None:
        path = filedialog.askopenfilename(
            title="选择遥感影像",
            filetypes=[
                ("栅格影像", "*.tif *.tiff *.img *.vrt"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.image_path.set(path)

    def _choose_label(self) -> None:
        path = filedialog.askopenfilename(
            title="选择面标注 Shapefile",
            filetypes=[("Shapefile", "*.shp"), ("所有文件", "*.*")],
        )
        if path:
            self.label_path.set(path)

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出文件夹")
        if path:
            self.output_path.set(path)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def _start(self) -> None:
        if self.process is not None:
            return

        image_text = self.image_path.get().strip()
        label_text = self.label_path.get().strip()
        output_text = self.output_path.get().strip()
        image = Path(image_text)
        label = Path(label_text)
        output = Path(output_text) if output_text else None
        for path, description in (
            (image, "遥感影像"),
            (label, "面标注 Shapefile"),
        ):
            if not path.is_file():
                messagebox.showerror("路径无效", f"请选择有效的{description}。")
                return
        if output is None:
            messagebox.showerror("路径无效", "请选择输出文件夹。")
            return
        if not BACKEND_PATH.is_file():
            messagebox.showerror(
                "找不到后端",
                f"未找到后端文件：\n{BACKEND_PATH}\n\n请确认项目中的 backend\\cut.py 存在。",
            )
            return

        try:
            output.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("输出目录不可用", str(error))
            return

        self._clear_progress()
        self._append_log("开始启动切片后端…")
        command = [
            sys.executable,
            str(BACKEND_PATH),
            "--image",
            str(image),
            "--label",
            str(label),
            "--output-dir",
            str(output),
            "--progress-json",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as error:
            self.process = None
            messagebox.showerror("无法启动后端", str(error))
            return

        self.completed_normally = False
        self._set_running(True)
        self.status_text.set("正在切片…")
        self.reader_thread = threading.Thread(
            target=self._read_process_output,
            args=(self.process,),
            daemon=True,
        )
        self.reader_thread.start()

    def _read_process_output(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if line.startswith(PROGRESS_PREFIX):
                try:
                    event = json.loads(line[len(PROGRESS_PREFIX) :])
                    self.events.put(("progress", event))
                except json.JSONDecodeError:
                    self.events.put(("log", line))
            elif line:
                self.events.put(("log", line))
        return_code = process.wait()
        self.events.put(("finished", return_code))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "progress":
                    self._handle_progress(value)
                elif kind == "finished":
                    self._handle_finished(int(value))
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _handle_progress(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        event_name = event.get("event")
        if event_name == "started":
            total = int(event.get("total", 0))
            self.progress_bar.configure(maximum=max(total, 1), value=0)
            self.progress_text.set("0%")
            self.detail_text.set(f"共 {total:,} 个窗口，正在检查…")
            self._append_log(
                f"影像尺寸：{event.get('width')} × {event.get('height')}；"
                f"面要素：{event.get('feature_count')}。"
            )
        elif event_name == "progress":
            current = int(event.get("current", 0))
            total = max(int(event.get("total", 1)), 1)
            exported = int(event.get("exported", 0))
            skipped = int(event.get("skipped", 0))
            percent = current / total * 100
            self.progress_bar.configure(maximum=total, value=current)
            self.progress_text.set(f"{percent:.1f}%")
            self.detail_text.set(
                f"已检查 {current:,} / {total:,} · 有效样本 {exported:,} · 空标签 {skipped:,}"
            )
        elif event_name == "completed":
            self.completed_normally = True
            self.progress_bar.configure(value=self.progress_bar["maximum"])
            self.progress_text.set("100%")
            self.detail_text.set(
                f"完成：{int(event.get('exported', 0)):,} 对样本已通过校验"
            )
            self._append_log(
                f"输出完成：{event.get('image_output')}；{event.get('label_output')}"
            )
        elif event_name == "error":
            self._append_log(f"错误：{event.get('message', '未知错误')}")

    def _handle_finished(self, return_code: int) -> None:
        self.process = None
        self._set_running(False)
        if return_code == 0 and self.completed_normally:
            self.status_text.set("处理完成")
            messagebox.showinfo("切片完成", "切片任务已完成，输出文件已通过后端校验。")
        elif return_code == 0:
            self.status_text.set("任务结束")
        else:
            self.status_text.set("处理失败")
            self.detail_text.set("任务失败，请查看运行日志")
            self._append_log(f"后端退出码：{return_code}")

    def _clear_progress(self) -> None:
        self.progress_bar.configure(value=0, maximum=100)
        self.progress_text.set("0%")
        self.detail_text.set("等待开始处理")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _stop(self) -> None:
        if self.process is None:
            return
        if not messagebox.askyesno("停止任务", "确定要停止当前切片任务吗？"):
            return
        process = self.process
        self.status_text.set("正在停止…")
        try:
            process.terminate()
        except OSError:
            pass
        self._append_log("已请求停止后端任务。")

    def _on_close(self) -> None:
        if self.process is not None:
            if not messagebox.askyesno("退出", "切片任务仍在运行，确定退出吗？"):
                return
            try:
                self.process.terminate()
            except OSError:
                pass
        self.destroy()


if __name__ == "__main__":
    app = CutpyApp()
    app.mainloop()
