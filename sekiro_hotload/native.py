"""Sekiro-specific native bridge transport (Windows x64, fingerprint locked)."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import time

from .engine import FatalTransaction, RetryLater, sha256
from .winmem import bind, PTR, SIZE


class Shared(C.Structure):
    _fields_ = [("magic", C.c_uint64), ("abi", C.c_uint32), ("pid", C.c_uint32),
                ("state", C.c_int32), ("status", C.c_int32), ("command", C.c_uint32), ("count", C.c_uint32),
                ("heartbeat", C.c_uint64), ("callback_tid", C.c_uint32), ("bootstrap_tid", C.c_uint32),
                ("sequence", C.c_uint64), ("retained_bytes", C.c_uint64), ("result", C.c_uint64),
                ("arg0", C.c_uint64), ("arg1", C.c_uint64), ("arg2", C.c_uint64), ("arg3", C.c_uint64),
                ("error_index", C.c_uint32), ("ready", C.c_uint32), ("reserved", C.c_uint8 * 16)]


class Operation(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ("cap", "expected_data", "expected_size", "staging", "staging_size", "file_size", "allocator", "new_data")] + [
        (name, C.c_uint32) for name in ("old_count", "new_count", "row_size", "flags")]


class NativeBridge:
    def __init__(self, mem, dll: Path, tag: str, expected_exe_hash: str):
        if mem.exe.name.casefold() != "sekiro.exe" or sha256(mem.exe) != expected_exe_hash:
            raise FatalTransaction("原生后端只允许连接指纹匹配的只狼进程")
        self.mem, self.dll, self.tag = mem, dll.resolve(), tag
        self.view, self.mapping, self.handle = None, None, None
        self.mutex, self.owns_mutex = None, False
        k = C.WinDLL("kernel32", use_last_error=True)
        self.open_map = bind(k, "OpenFileMappingW", W.HANDLE, [W.DWORD, W.BOOL, W.LPCWSTR])
        self.map_view = bind(k, "MapViewOfFile", PTR, [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, SIZE])
        self.unmap = bind(k, "UnmapViewOfFile", W.BOOL, [PTR])
        self.alloc = bind(k, "VirtualAllocEx", PTR, [W.HANDLE, PTR, SIZE, W.DWORD, W.DWORD])
        self.free = bind(k, "VirtualFreeEx", W.BOOL, [W.HANDLE, PTR, SIZE, W.DWORD])
        self.thread = bind(k, "CreateRemoteThread", W.HANDLE, [W.HANDLE, PTR, SIZE, PTR, PTR, W.DWORD, C.POINTER(W.DWORD)])
        self.get_module_ex = bind(k, "GetModuleHandleExW", W.BOOL, [W.DWORD, PTR, C.POINTER(W.HMODULE)])
        self.module_name = bind(k, "GetModuleFileNameW", W.DWORD, [W.HMODULE, W.LPWSTR, W.DWORD])
        self.modules = mem.api.modules
        p = C.WinDLL("psapi", use_last_error=True)
        self.remote_name = bind(p, "GetModuleFileNameExW", W.DWORD, [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD])
        self.flush = bind(k, "FlushProcessWriteBuffers", None, [])
        self.create_mutex = bind(k, "CreateMutexW", W.HANDLE, [PTR, W.BOOL, W.LPCWSTR])
        self.release_mutex = bind(k, "ReleaseMutex", W.BOOL, [W.HANDLE])
        self.handle = mem.api.open(0x100000 | 0x400 | 0x2 | 0x10 | 0x20 | 0x8, False, mem.pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        try:
            self.mutex = self.create_mutex(None, True, f"Local\\SekiroParamAgentClient_{mem.pid}_{tag}")
            if not self.mutex:
                raise C.WinError(C.get_last_error())
            if C.get_last_error() == 183:
                if mem.api.wait(self.mutex, 0) not in (0, 0x80):
                    raise FatalTransaction("另一个控制器正在使用此游戏的原生后端")
            self.owns_mutex = True
            name = f"Local\\SekiroParamAgent12_{mem.pid}_{tag}"
            self.mapping = self.open_map(0xF001F, False, name)
            if not self.mapping:
                self._inject(k)
                until = time.monotonic() + 10
                while time.monotonic() < until and mem.alive():
                    self.mapping = self.open_map(0xF001F, False, name)
                    if self.mapping:
                        break
                    time.sleep(.05)
            if not self.mapping:
                raise FatalTransaction("原生模块未建立通信；取消结构热加载")
            self.view = self.map_view(self.mapping, 0xF001F, 0, 0, 65536)
            if not self.view:
                raise C.WinError(C.get_last_error())
            self.shared = Shared.from_address(self.view)
            until = time.monotonic() + 10
            while not self.shared.ready and not self.shared.status and time.monotonic() < until and mem.alive():
                time.sleep(.02)
            if self.shared.magic != 0x324c4850524b5353 or self.shared.abi != 120 or self.shared.pid != mem.pid:
                raise FatalTransaction("原生协议版本或进程身份不匹配")
            if not self.shared.ready:
                raise FatalTransaction(f"游戏帧任务未就绪，状态 {self.shared.status}")
        except BaseException:
            self.close()
            raise

    def _inject(self, kernel):
        if not self.dll.is_file():
            raise FileNotFoundError(self.dll)
        # Resolve the actual owner of the forwarded LoadLibraryW export in both processes.
        local_fn = C.cast(kernel.LoadLibraryW, PTR).value
        owner = W.HMODULE()
        if not self.get_module_ex(4 | 2, local_fn, C.byref(owner)):
            raise C.WinError(C.get_last_error())
        path = C.create_unicode_buffer(32768)
        self.module_name(owner, path, len(path))
        owner_name = Path(path.value).name.casefold()
        modules, needed = (PTR * 1024)(), W.DWORD()
        if not self.modules(self.handle, modules, C.sizeof(modules), C.byref(needed), 3):
            raise C.WinError(C.get_last_error())
        remote_owner = None
        for module in list(modules)[:min(needed.value // C.sizeof(PTR), 1024)]:
            if self.remote_name(self.handle, module, path, len(path)) and Path(path.value).name.casefold() == owner_name:
                remote_owner = module
                break
        if remote_owner is None:
            raise FatalTransaction("无法定位游戏进程内的系统 DLL 加载入口")
        remote_fn = remote_owner + (local_fn - owner.value)
        # Existing Mod Engine/Steam modules may legitimately hook LoadLibraryW.
        # Verify the owning PE image identity, then call the exported entry normally,
        # preserving those hooks rather than requiring identical prologue bytes.
        import struct
        local_header = C.string_at(owner.value, 4096)
        remote_header = self.mem.read(remote_owner, 4096)
        def pe_identity(header):
            off = struct.unpack_from("<I", header, 0x3c)[0]
            return (header[:2], header[off:off+4], struct.unpack_from("<I", header, off+8)[0],
                    struct.unpack_from("<I", header, off+24+56)[0])
        if pe_identity(local_header) != pe_identity(remote_header):
            raise FatalTransaction("系统加载器的所属模块版本不匹配")
        self.mem.check_range(remote_fn, 16)
        raw = str(self.dll).encode("utf-16-le") + b"\0\0"
        address = self.allocate(raw)
        thread = None
        completed = False
        try:
            thread = self.thread(self.handle, None, 0, remote_fn, address, 0, None)
            if not thread:
                raise C.WinError(C.get_last_error())
            completed = self.mem.api.wait(thread, 10000) == 0
            if not completed:
                raise FatalTransaction("原生模块加载超时；保留加载参数并停止本次操作")
        finally:
            if thread:
                self.mem.api.close(thread)
            if completed or thread is None:
                self.free(self.handle, address, 0, 0x8000)

    def allocate(self, data: bytes) -> int:
        address = self.alloc(self.handle, None, len(data), 0x3000, 4)
        if not address:
            raise C.WinError(C.get_last_error())
        try:
            self.mem.write(address, data)
            return address
        except BaseException:
            self.free(self.handle, address, 0, 0x8000)
            raise

    def request(self, command: int, *args: int, timeout=10) -> int:
        shared = self.shared
        if shared.state not in (0, 3):
            raise FatalTransaction("原生后端有尚未结束的请求")
        for i in range(4):
            setattr(shared, f"arg{i}", args[i] if i < len(args) else 0)
        shared.command, shared.status, shared.sequence = command, 0, shared.sequence + 1
        self.flush()
        # Aligned 32-bit stores are atomic on the required x64 platform.
        shared.state = 1
        until = time.monotonic() + timeout
        while shared.state != 3:
            if time.monotonic() > until or not self.mem.alive():
                raise FatalTransaction("原生事务未确认结束；停止自动更新，避免并发请求")
            time.sleep(.01)
        if shared.status:
            if shared.status == -31:
                raise RetryLater("游戏资源在预检后改变；原生后端整次未写入，等待重新预检")
            reasons = {-32:"输入布局无效",-33:"运行时镜像或行边界无效",-34:"类型/版本不兼容",
                       -330:"候选运行时镜像或行边界无效",-331:"游戏当前运行时镜像或行边界无效",
                       -332:"行数量在预检后不一致",
                       -35:"资源分配器不匹配",-36:"原位模式行集合不同",-37:"镜像长度不匹配",
                       -38:"旧块保留达到 512 MiB 或版本数上限；请重启游戏",
                       -39:"游戏内存分配失败",-40:"候选资源校验失败",-41:"游戏 ID 查询校验失败",
                       -42:"当前行镜像预检失败",-43:"退役块镜像预检失败",-44:"撤销缓冲分配失败",
                       -45:"发布校验失败，已执行回退",-46:"准备阶段超过时间预算，整次未写入",
                       -47:"重复的资源操作"}
            raise FatalTransaction(f"原生请求失败：{reasons.get(shared.status, '未知状态')} ({shared.status})，条目 {shared.error_index}")
        return shared.result

    def apply(self, operations: list[Operation], images: list[bytes]) -> list[int]:
        if len(operations) != len(images) or not 1 <= len(images) <= 136:
            raise ValueError("原生事务条目数无效")
        uploads = []
        try:
            for operation, data in zip(operations, images):
                operation.staging = self.allocate(data)
                operation.staging_size = len(data)
                uploads.append(operation.staging)
            array = (Operation * len(operations))(*operations)
            C.memmove(self.view + 128, C.addressof(array), C.sizeof(array))
            self.shared.count = len(operations)
            self.request(3, timeout=20)
            output = (Operation * len(operations)).from_address(self.view + 128)
            return [op.new_data or op.expected_data for op in output]
        finally:
            # On timeout or controller loss, input blocks must remain available to the task.
            if self.shared.state not in (1, 2):
                for address in uploads:
                    self.free(self.handle, address, 0, 0x8000)

    def close(self):
        if self.view:
            self.unmap(self.view)
            self.view = None
        if self.mapping:
            self.mem.api.close(self.mapping)
            self.mapping = None
        if self.handle:
            self.mem.api.close(self.handle)
            self.handle = None
        if self.mutex:
            if self.owns_mutex:
                self.release_mutex(self.mutex)
            self.mem.api.close(self.mutex)
            self.mutex = None
