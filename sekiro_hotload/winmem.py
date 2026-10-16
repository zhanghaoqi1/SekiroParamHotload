"""Windows x64 process I/O; never allocate executable memory or start remote threads."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


class MemoryError(RuntimeError):
    pass


class RecoveryError(MemoryError):
    fatal = True


SIZE = C.c_size_t
PTR = C.c_void_p
INVALID_HANDLE = C.c_void_p(-1).value


class PROCESSENTRY32W(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
                ("th32DefaultHeapID", SIZE), ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD),
                ("th32ParentProcessID", W.DWORD), ("pcPriClassBase", W.LONG),
                ("dwFlags", W.DWORD), ("szExeFile", W.WCHAR * 260)]


class MEMORY_BASIC_INFORMATION(C.Structure):
    _fields_ = [("BaseAddress", PTR), ("AllocationBase", PTR), ("AllocationProtect", W.DWORD),
                ("PartitionId", W.WORD), ("RegionSize", SIZE), ("State", W.DWORD),
                ("Protect", W.DWORD), ("Type", W.DWORD)]


def bind(dll, name, result, args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn


class API:
    def __init__(self):
        if os.name != "nt" or C.sizeof(PTR) != 8:
            raise MemoryError("需要 Windows 和 64 位 Python / 64 位程序")
        k = C.WinDLL("kernel32", use_last_error=True)
        n = C.WinDLL("ntdll")
        p = C.WinDLL("psapi")
        self.open = bind(k, "OpenProcess", W.HANDLE, [W.DWORD, W.BOOL, W.DWORD])
        self.close = bind(k, "CloseHandle", W.BOOL, [W.HANDLE])
        self.snapshot = bind(k, "CreateToolhelp32Snapshot", W.HANDLE, [W.DWORD, W.DWORD])
        self.first = bind(k, "Process32FirstW", W.BOOL, [W.HANDLE, C.POINTER(PROCESSENTRY32W)])
        self.next = bind(k, "Process32NextW", W.BOOL, [W.HANDLE, C.POINTER(PROCESSENTRY32W)])
        self.path = bind(k, "QueryFullProcessImageNameW", W.BOOL, [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)])
        self.modules = bind(p, "EnumProcessModulesEx", W.BOOL, [W.HANDLE, C.POINTER(PTR), W.DWORD, C.POINTER(W.DWORD), W.DWORD])
        self.read = bind(k, "ReadProcessMemory", W.BOOL, [W.HANDLE, PTR, PTR, SIZE, C.POINTER(SIZE)])
        self.write = bind(k, "WriteProcessMemory", W.BOOL, [W.HANDLE, PTR, PTR, SIZE, C.POINTER(SIZE)])
        self.query = bind(k, "VirtualQueryEx", SIZE, [W.HANDLE, PTR, C.POINTER(MEMORY_BASIC_INFORMATION), SIZE])
        self.wait = bind(k, "WaitForSingleObject", W.DWORD, [W.HANDLE, W.DWORD])
        self.suspend = bind(n, "NtSuspendProcess", W.LONG, [W.HANDLE])
        self.resume = bind(n, "NtResumeProcess", W.LONG, [W.HANDLE])
        self.times = bind(k, "GetProcessTimes", W.BOOL, [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4)


def file_version(path: Path) -> str:
    v = C.WinDLL("version", use_last_error=True)
    size_fn = bind(v, "GetFileVersionInfoSizeW", W.DWORD, [W.LPCWSTR, C.POINTER(W.DWORD)])
    get_fn = bind(v, "GetFileVersionInfoW", W.BOOL, [W.LPCWSTR, W.DWORD, W.DWORD, PTR])
    query_fn = bind(v, "VerQueryValueW", W.BOOL, [PTR, W.LPCWSTR, C.POINTER(PTR), C.POINTER(W.UINT)])
    unused = W.DWORD()
    size = size_fn(str(path), C.byref(unused))
    if not size:
        raise MemoryError("无法读取游戏版本")
    buf = C.create_string_buffer(size)
    if not get_fn(str(path), 0, size, buf):
        raise C.WinError(C.get_last_error())
    value, length = PTR(), W.UINT()
    if not query_fn(buf, "\\", C.byref(value), C.byref(length)) or length.value < 52:
        raise MemoryError("游戏没有有效的版本信息")
    words = C.cast(value, C.POINTER(W.DWORD))
    ms, ls = words[2], words[3]
    return f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"


def find_process(exe: Path) -> int | None:
    api = API()
    snapshot = api.snapshot(2, 0)
    if snapshot == INVALID_HANDLE:
        raise C.WinError(C.get_last_error())
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = C.sizeof(entry)
        ok = api.first(snapshot, C.byref(entry))
        while ok:
            if entry.szExeFile.casefold() == exe.name.casefold():
                handle = api.open(0x1000, False, entry.th32ProcessID)
                if not handle:
                    raise MemoryError("发现游戏但没有读取权限；请以与游戏相同的权限运行工具")
                try:
                    buf, size = C.create_unicode_buffer(32768), W.DWORD(32768)
                    if not api.path(handle, 0, buf, C.byref(size)):
                        raise C.WinError(C.get_last_error())
                    if Path(buf.value).resolve() == exe.resolve():
                        found.append(entry.th32ProcessID)
                finally:
                    api.close(handle)
            ok = api.next(snapshot, C.byref(entry))
    finally:
        api.close(snapshot)
    if len(found) > 1:
        raise MemoryError("目标目录有多个游戏进程，拒绝自动选择")
    return found[0] if found else None


class ProcessMemory:
    def __init__(self, pid: int, writable: bool = True):
        self.api = API()
        self.pid = pid
        rights = 0x100000 | 0x400 | 0x10 | (0x800 | 0x20 | 0x8 if writable else 0)
        self.handle = self.api.open(rights, False, pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        try:
            mods, needed = (PTR * 1024)(), W.DWORD()
            if not self.api.modules(self.handle, mods, C.sizeof(mods), C.byref(needed), 3):
                raise C.WinError(C.get_last_error())
            self.base = mods[0]
            times = [W.FILETIME() for _ in range(4)]
            if not self.api.times(self.handle, *(C.byref(t) for t in times)):
                raise C.WinError(C.get_last_error())
            self.identity = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            buf, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            if not self.api.path(self.handle, 0, buf, C.byref(size)):
                raise C.WinError(C.get_last_error())
            self.exe = Path(buf.value).resolve()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.api.close(self.handle)
            self.handle = None

    def alive(self):
        return self.api.wait(self.handle, 0) == 0x102

    def check_range(self, address: int, size: int, write: bool = False):
        if not 0x10000 <= address < 0x0000800000000000 or not 0 < size <= 256 * 1024 * 1024:
            raise MemoryError(f"无效地址或长度: {address:#x}, {size}")
        end = address + size
        while address < end:
            info = MEMORY_BASIC_INFORMATION()
            if not self.api.query(self.handle, address, C.byref(info), C.sizeof(info)):
                raise C.WinError(C.get_last_error())
            allowed = (4, 8) if write else (2, 4, 8, 0x20, 0x40, 0x80)
            if info.State != 0x1000 or info.Protect & 0x100 or info.Protect & 0xff not in allowed:
                raise MemoryError(f"目标页未提交或没有所需权限: {address:#x}, protect={info.Protect:#x}")
            address = (info.BaseAddress or 0) + info.RegionSize

    def read(self, address: int, size: int) -> bytes:
        self.check_range(address, size)
        buf, actual = C.create_string_buffer(size), SIZE()
        if not self.api.read(self.handle, address, buf, size, C.byref(actual)) or actual.value != size:
            raise MemoryError(f"读取内存失败: {address:#x} ({actual.value}/{size})")
        return buf.raw

    def write(self, address: int, data: bytes):
        self.check_range(address, len(data), write=True)
        actual = SIZE()
        src = C.create_string_buffer(data)
        if not self.api.write(self.handle, address, src, len(data), C.byref(actual)) or actual.value != len(data):
            raise MemoryError(f"写入内存失败: {address:#x} ({actual.value}/{len(data)})")

    @contextmanager
    def frozen(self):
        # An independent helper owns a process handle and resumes after parent death/timeout.
        cmd = [sys.executable]
        if not getattr(sys, "frozen", False):
            cmd.append(str(Path(__file__).resolve().parents[1] / "hotload.py"))
        cmd += ["--watchdog", str(self.pid), str(self.identity)]
        guard = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, creationflags=0x08000000)
        suspended = False
        started = time.monotonic()
        try:
            # Bounded startup: watchdog does not depend on the game being responsive.
            ready = []
            thread = threading.Thread(target=lambda: ready.append(guard.stdout.readline()), daemon=True)
            thread.start()
            thread.join(5)
            if not ready or ready[0].strip() != b"READY" or time.monotonic() - started > 5:
                raise MemoryError("恢复看门狗未就绪，取消写入")
            # Mark before entering native code so Ctrl+C cannot lose the resume obligation.
            suspended = True
            status = self.api.suspend(self.handle)
            if status < 0:
                raise MemoryError(f"NtSuspendProcess 失败: {status:#x}")
            yield
        finally:
            resumed = not suspended or self.api.resume(self.handle) >= 0
            if resumed:
                try:
                    guard.stdin.write(b"DONE\n")
                    guard.stdin.flush()
                except (OSError, ValueError):
                    pass
            # EOF also tells the helper to resume if the controller died or resume failed.
            guard.stdin.close()
            try:
                guard.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Do not terminate an armed helper; it must be able to resume the game.
                pass
            guard.stdout.close()
            if not resumed:
                raise RecoveryError("恢复进程失败；看门狗已接手，停止热加载")


def watchdog(pid: int, identity: int) -> int:
    # Opening its own handle also protects against PID reuse after the game exits.
    mem = ProcessMemory(pid)
    try:
        if mem.identity != identity:
            return 2
        done = threading.Event()
        message = []
        def reader():
            message.append(sys.stdin.buffer.readline().strip())
            done.set()
        threading.Thread(target=reader, daemon=True).start()
        print("READY", flush=True)
        done.wait(10)
        if not message or message[0] != b"DONE":
            mem.api.resume(mem.handle)
        return 0
    finally:
        mem.close()
