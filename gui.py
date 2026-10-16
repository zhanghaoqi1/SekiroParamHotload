"""Local Tk/ttk interface. All game work runs in the existing console backend."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from hotload import APP, DEFAULT_GAME, auto_source
from sekiro_hotload.engine import atomic_json

VERSION = "1.3"
STATE = APP / "state"
SETTINGS = STATE / "gui-settings.json"


def worker_command(game: str, source: str, tables: str, max_rows: str,
                   existing_only: bool, mode: str, restore: str = "", full_reload: bool = False) -> list[str]:
    if not game.strip():
        raise ValueError("请选择游戏目录")
    limit = int(max_rows)
    if not 1 <= limit <= 50000:
        raise ValueError("单次行数限制必须在 1 到 50,000 之间")
    if mode not in ("watch", "check", "inspect", "restore"):
        raise ValueError("未知操作")
    if getattr(sys, "frozen", False):
        command = [str(APP / "SekiroParamHotload.exe")]
    else:
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        command = [str(interpreter), str(APP / "hotload.py")]
    command += ["--gui-worker", "--game-dir", str(Path(game).resolve()),
                "--state-dir", str(STATE), "--max-rows", str(limit)]
    if source.strip():
        command += ["--source", str(Path(source).resolve())]
    selected = [s for s in re.split(r"[\s,，;；]+", tables.strip()) if s]
    if selected:
        command += ["--tables", *selected]
    if full_reload:
        command.append("--full-reload")
    elif existing_only:
        command.append("--existing-only")
    if mode == "check":
        command += ["--dry-run", "--once"]
    elif mode == "inspect":
        command.append("--inspect")
    elif mode == "restore":
        if not restore:
            raise ValueError("请选择回滚记录")
        command += ["--restore", str(Path(restore).resolve())]
    return command


class HotloadGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.process: subprocess.Popen | None = None
        self.events: queue.Queue = queue.Queue()
        self.closing = False
        self.stopping = False
        self.mode = ""
        self.problem = False
        self.config_widgets: list[ttk.Widget] = []
        self.buttons: list[ttk.Button] = []
        settings = self.load_settings()
        self.game = tk.StringVar(value=settings.get("game", DEFAULT_GAME))
        self.source = tk.StringVar(value=settings.get("source", ""))
        self.tables = tk.StringVar(value=settings.get("tables", ""))
        self.max_rows = tk.StringVar(value=str(settings.get("max_rows", 2048)))
        self.existing_only = tk.BooleanVar(value=bool(settings.get("existing_only", False)))
        self.reload_mode = tk.StringVar(value=settings.get("reload_mode", "full"))
        self.autoscroll = tk.BooleanVar(value=True)
        self.advanced = tk.BooleanVar(value=bool(self.tables.get()) or self.max_rows.get() != "2048")
        self.advanced_widgets = []
        self.status = tk.StringVar(value="已停止")
        self.detail = tk.StringVar(value="进入存档后，先做只读检查。")
        self.summary = tk.StringVar(value="尚未读取参数包")
        self.last_sync = tk.StringVar(value="本次尚未应用修改")
        self.added_count = tk.StringVar(value="—")
        self.modified_count = tk.StringVar(value="—")
        self.deleted_count = tk.StringVar(value="—")
        self._build()
        if not self.source.get():
            self.detect_source(quiet=True)
        self.append_log(f"只狼参数热加载器 {VERSION} 已就绪。保存参数文件后自动同步到游戏。")
        self.append_log("首次使用建议先检查参数文件，进入存档后再运行只读检查。")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll_events)

    @staticmethod
    def load_settings():
        try:
            data = json.loads(SETTINGS.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _build(self):
        from hotload import RESOURCE
        from sekiro_hotload.interface import build_interface
        build_interface(self, RESOURCE / "assets", VERSION, STATE, APP)

    def _config_button(self, parent, label, command, row, column):
        button = ttk.Button(parent, text=label, command=command)
        button.grid(row=row, column=column, padx=(8, 0), pady=4)
        self.config_widgets.append(button)

    def toggle_advanced(self):
        for widget in self.advanced_widgets:
            widget.grid() if self.advanced.get() else widget.grid_remove()

    def choose_game(self):
        selected = filedialog.askdirectory(parent=self.root, title="选择包含 sekiro.exe 的游戏目录", initialdir=self.game.get())
        if selected:
            self.game.set(selected)
            self.detect_source()

    def choose_source(self):
        selected = filedialog.askopenfilename(parent=self.root, title="选择参数包或 .param 文件", initialdir=str(Path(self.source.get()).parent),
                                             filetypes=[("参数文件", "*.dcx *.parambnd *.param"), ("所有文件", "*.*")])
        if selected:
            self.source.set(selected)

    def choose_loose(self):
        selected = filedialog.askdirectory(parent=self.root, title="选择包含 .param 的目录")
        if selected:
            self.source.set(selected)

    def detect_source(self, quiet=False):
        try:
            selected = auto_source(Path(self.game.get()).resolve())
            self.source.set(str(selected))
            if not quiet:
                self.append_log("已按 Mod Engine 配置识别参数来源：" + str(selected))
        except Exception as error:
            if not quiet:
                self.append_log(str(error), "ERROR")

    def save_settings(self):
        STATE.mkdir(parents=True, exist_ok=True)
        atomic_json(SETTINGS, {"game": self.game.get(), "source": self.source.get(), "tables": self.tables.get(),
                               "max_rows": self.max_rows.get(), "existing_only": self.reload_mode.get() == "partial",
                               "reload_mode": self.reload_mode.get()})

    def set_busy(self, busy: bool):
        for widget in self.config_widgets + self.buttons:
            widget.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def start(self, mode="watch"):
        if self.process is not None:
            return
        restore = ""
        if mode == "restore":
            folder = STATE / "journals"
            restore = filedialog.askopenfilename(parent=self.root, title="选择同一游戏会话的回滚记录（不含 .result.json）",
                                                 initialdir=str(folder if folder.exists() else STATE), filetypes=[("回滚记录", "*.json")])
            if not restore:
                return
            if restore.endswith(".result.json"):
                messagebox.showerror("请选择数据记录", "请选择不带 .result 后缀的 JSON 记录。", parent=self.root)
                return
        try:
            if not (Path(self.game.get()) / "sekiro.exe").is_file():
                raise ValueError("游戏目录中没有 sekiro.exe")
            command = worker_command(self.game.get(), self.source.get(), self.tables.get(), self.max_rows.get(),
                                     self.reload_mode.get() == "partial", mode, restore, self.reload_mode.get() == "full")
            self.save_settings()
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            encoding="utf-8", errors="replace", text=True, bufsize=1, creationflags=0x08000000)
        except Exception as error:
            self.append_log(str(error), "ERROR")
            messagebox.showerror("无法启动", str(error), parent=self.root)
            return
        self.mode, self.stopping, self.problem = mode, False, False
        for counter in (self.added_count, self.modified_count, self.deleted_count):
            counter.set("—")
        self.set_busy(True)
        self.set_status("正在启动", "正在验证文件与运行环境……")
        self.append_log("开始：" + {"watch": "自动监视", "check": "只读检查", "inspect": "检查参数文件", "restore": "回滚"}[mode])
        worker = self.process
        def read_output():
            try:
                for line in worker.stdout:
                    self.events.put(("line", line.rstrip("\r\n")))
            except (OSError, ValueError) as error:
                self.events.put(("line", "ERROR 日志读取异常：" + str(error)))
            finally:
                code = worker.wait()
                worker.stdout.close()
                self.events.put(("done", code))
        threading.Thread(target=read_output, daemon=True).start()

    def stop(self):
        if self.process is None or self.stopping:
            return
        self.stopping = True
        self.stop_button.configure(state="disabled")
        self.set_status("正在停止", "等待当前同步结束，随后断开游戏。")
        try:
            self.process.stdin.write("STOP\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            pass

    def set_status(self, status: str, detail: str):
        self.status.set(status)
        self.detail.set(detail[:110])
        warning = status in ("需处理", "部分同步", "操作失败")
        active = status in ("监视中", "已连接", "操作完成")
        self.status_badge.configure(bg="#f5e5cf" if warning else ("#e2eddf" if active else "#e8ebe2"),
                                    fg="#94622e" if warning else ("#3e7356" if active else "#5c685b"))

    def handle_line(self, line: str):
        severity = "ERROR" if " ERROR " in line or line.startswith("ERROR") else ("WARNING" if " WARNING " in line else "INFO")
        self.append_log(line, severity)
        if severity != "INFO":
            self.problem = True
            if not self.stopping:
                self.set_status("需处理", line.split(severity, 1)[-1].strip() or "请查看运行日志中的完整提示。")
        match = re.search(r"文件解析成功：(\d+) 张.*?(\d+) 行", line)
        if match:
            self.summary.set(f"参数包：{int(match[1]):,} 张表  /  {int(match[2]):,} 行")
        counts = re.search(r"完整行集合：新增 (\d+) / 删除 (\d+) / 修改 (\d+)", line)
        if counts:
            self.added_count.set(counts[1])
            self.deleted_count.set(counts[2])
            self.modified_count.set(counts[3])
        if self.stopping:
            return
        if "等待游戏启动" in line:
            self.set_status("等待游戏", "工具已就绪，启动游戏并进入存档后自动连接。")
        elif "已连接 PID=" in line:
            self.set_status("已连接", "已连接只狼，正在核对参数。")
        elif "同步完成：" in line or "差异（只读）：" in line:
            match = re.search(r"：(\d+) 行 / (\d+) 字节 / (\d+) 张表", line)
            if match:
                self.last_sync.set(f"最近差异：{int(match[1]):,} 行 · {int(match[3])} 张表")
            partial = "部分同步完成" in line
            self.set_status("部分同步" if partial else "监视中",
                            "共同 ID 已更新；新行未加载，旧行仍保留。" if partial else "保存参数文件后自动应用；写入后进行回读校验。")

    def poll_events(self):
        for _ in range(300):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                self.handle_line(value)
            elif kind == "done":
                if self.process and self.process.stdin:
                    self.process.stdin.close()
                self.process = None
                self.set_busy(False)
                if self.stopping:
                    self.set_status("已停止", "已安全断开；已应用的内存数据会保留。")
                elif value == 0:
                    self.set_status("操作完成", "操作完成，详情见运行日志。")
                else:
                    self.set_status("操作失败", f"退出码 {value}。请查看日志定位具体原因。")
                if self.closing:
                    self.root.destroy()
                    return
        self.root.after(100, self.poll_events)

    def append_log(self, line: str, severity="INFO"):
        self.log.configure(state="normal")
        formatted = re.match(r"^\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}),\d+ \+\d{4} (INFO|WARNING|ERROR) (.*)$", line)
        if formatted:
            self.log.insert("end", formatted[1] + "   ", "TIME")
            self.log.insert("end", formatted[3] + "\n", severity)
        else:
            self.log.insert("end", line + "\n", severity)
        count = int(self.log.index("end-1c").split(".")[0])
        if count > 2500:
            self.log.delete("1.0", f"{count - 2200}.0")
        if self.autoscroll.get():
            self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def open_local(self, path: Path):
        try:
            if path == STATE:
                STATE.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except OSError as error:
            self.append_log(str(error), "ERROR")

    def on_close(self):
        try:
            self.save_settings()
        except OSError:
            pass
        if self.process is None:
            self.root.destroy()
        else:
            self.closing = True
            self.stop()


def main():
    # DPI awareness must be established before creating Tk widgets.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Sekiro.ParamHotload.GUI.1.3")
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    HotloadGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
