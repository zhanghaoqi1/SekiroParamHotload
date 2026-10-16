from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui


class Commands(unittest.TestCase):
    def test_full_mode_selects_native_backend_without_partial_flag(self):
        command = gui.worker_command("D:/Sekiro", "", "", "2048", True, "watch", full_reload=True)
        self.assertIn("--full-reload", command)
        self.assertNotIn("--existing-only", command)
    def test_read_only_command_and_table_separators(self):
        command = gui.worker_command("D:/Sekiro", "D:/Sekiro/test.dcx", "AtkParam_Pc，SpEffectParam", "2048", False, "check")
        self.assertIn("--dry-run", command)
        self.assertIn("--once", command)
        self.assertIn("--gui-worker", command)
        self.assertNotIn("--existing-only", command)
        i = command.index("--tables")
        self.assertEqual(command[i + 1:i + 3], ["AtkParam_Pc", "SpEffectParam"])

    def test_partial_mode_is_explicit_and_limits_validated(self):
        self.assertIn("--existing-only", gui.worker_command("D:/Sekiro", "", "", "2048", True, "watch"))
        for value in ("0", "50001", "bad"):
            with self.assertRaises(ValueError):
                gui.worker_command("D:/Sekiro", "", "", value, False, "watch")

    def test_worker_stop_command_exits_without_killing_process(self):
        # The real loop is tested with a deterministic, read-only synthetic memory adapter.
        # The stdin protocol must also work before the loop has begun.
        code = """
import sys, threading
from unittest.mock import patch
import hotload
sys.argv=['hotload.py','--gui-worker','--state-dir',sys.argv[1]]
def fake_run(args,stop):
    assert stop.wait(5), 'stop was not delivered'
    print('SAFE_STOP',flush=True)
    return 0
with patch('hotload.run',side_effect=fake_run):
    raise SystemExit(hotload.main())
"""
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, "-c", code, folder], input="STOP\n", capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SAFE_STOP", result.stdout)


@unittest.skipUnless(os.environ.get("SEKIRO_GUI_TEST") == "1", "Set SEKIRO_GUI_TEST=1 to exercise Tk widgets")
class GUIIntegration(unittest.TestCase):
    def test_widgets_and_real_file_inspection_worker(self):
        import tkinter as tk
        with tempfile.TemporaryDirectory() as folder, patch.object(gui, "STATE", Path(folder)), patch.object(gui, "SETTINGS", Path(folder) / "gui.json"):
            root = tk.Tk()
            root.withdraw()
            app = gui.HotloadGUI(root)
            try:
                app.tables.set("AtkParam_Pc SpEffectParam")
                app.start("inspect")
                self.assertIsNotNone(app.process)
                until = time.monotonic() + 10
                while app.process and time.monotonic() < until:
                    root.update()
                    time.sleep(.03)
                self.assertIsNone(app.process)
                self.assertEqual(app.status.get(), "操作完成")
                self.assertIn("2 张表", app.summary.get())
                self.assertIn("文件解析成功", app.log.get("1.0", "end"))
                self.assertGreaterEqual(len(app.icon_images),2,"Application icons must load from packaged assets")
                app.handle_line("2026-10-16 18:00:00,123 +0800 INFO 完整行集合：新增 2 / 删除 1 / 修改 3 / 重排 0 张表")
                self.assertEqual((app.added_count.get(),app.deleted_count.get(),app.modified_count.get()),("2","1","3"))
                app.handle_line("INFO 已有行部分同步完成：2 行 / 8 字节 / 1 张表")
                self.assertEqual(app.status.get(), "部分同步")
                self.assertIn("2 行", app.last_sync.get())
            finally:
                if app.process:
                    app.stop()
                    until = time.monotonic() + 5
                    while app.process and time.monotonic() < until:
                        root.update()
                        time.sleep(.03)
                root.destroy()


if __name__ == "__main__":
    unittest.main()
