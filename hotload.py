"""Command line entry point. Requires Python 3.10+ x64 on Windows."""
from __future__ import annotations

import argparse
import configparser
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

from sekiro_hotload.engine import Engine, FatalTransaction, RetryLater, sha256
from sekiro_hotload.structural import StructuralEngine
from sekiro_hotload.formats import Decoder, InvalidData, load_params
from sekiro_hotload.winmem import ProcessMemory, file_version, find_process, watchdog

APP = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE = Path(getattr(sys, "_MEIPASS", APP))
DEFAULT_GAME = r"D:\SteamLibrary\steamapps\common\Sekiro"
LOG = logging.getLogger("hotload")


def auto_source(game: Path) -> Path:
    ini = game / "modengine.ini"
    if ini.exists():
        config = configparser.ConfigParser(inline_comment_prefixes=(";",))
        config.read(ini, encoding="utf-8-sig")
        if config.getboolean("files", "useModOverrideDirectory", fallback=False):
            subdir = config.get("files", "modOverrideDirectory").strip().strip('"').strip("\\/")
            source = (game / subdir / "param/gameparam/gameparam.parambnd.dcx").resolve()
            if not source.is_relative_to(game.resolve()):
                raise InvalidData("Mod Engine 路径超出游戏目录；请用 --source 明确选择")
            if not source.is_file():
                raise InvalidData(f"Mod Engine 已启用覆盖目录，但参数包不存在: {source}")
            return source
    return game / "param/gameparam/gameparam.parambnd.dcx"


def sources(path: Path) -> list[Path]:
    if path.is_dir():
        files = sorted(path.rglob("*.param"))
        if not files:
            raise InvalidData("目录内没有 .param 文件；压缩包请使用 --source 指定完整文件路径")
        return files
    if not path.is_file():
        raise FileNotFoundError(path)
    if not path.name.lower().endswith((".param", ".parambnd", ".parambnd.dcx")):
        raise InvalidData("仅支持 .param、.parambnd、.parambnd.dcx；不支持 .gparam")
    return [path]


def signature(files: list[Path]):
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files)


def read_snapshot(source: Path, decoder: Decoder, supported: set[str]):
    files = sources(source)
    before = signature(files)
    params, digest = {}, hashlib.sha256()
    for path in files:
        # Hash the exact bytes being parsed by checking content twice as well as metadata.
        if path.stat().st_size > 256 * 1024 * 1024:
            raise InvalidData("源文件超过大小上限")
        raw = path.read_bytes()
        first_hash = hashlib.sha256(raw).hexdigest()
        loaded = load_params(path, decoder, supported, raw=raw)
        if sha256(path) != first_hash:
            raise RetryLater("文件仍在保存，等待稳定")
        digest.update(str(path).encode("utf-8"))
        digest.update(first_hash.encode("ascii"))
        for name, param in loaded.items():
            if name not in supported:
                raise InvalidData(f"未验证的参数表: {name}")
            if name in params:
                raise InvalidData(f"重复参数文件: {name}")
            params[name] = param
    if before != signature(sources(source)):
        raise RetryLater("文件集合或时间戳仍在变化")
    if not params:
        raise InvalidData("没有可热加载的参数表")
    return params, digest.hexdigest(), before


def content_signature(source: Path):
    # Also detects editors that preserve the original timestamp and file length.
    return tuple((str(path), sha256(path)) for path in sources(source))


def arguments():
    parser = argparse.ArgumentParser(description="只狼 1.06 PARAM 自动热加载（Windows x64）")
    parser.add_argument("--game-dir", type=Path, default=Path(DEFAULT_GAME))
    parser.add_argument("--source", type=Path, help="参数包或含松散 .param 的目录；默认读取 Mod Engine 配置")
    parser.add_argument("--inspect", action="store_true", help="仅验证文件解析，不连接游戏")
    parser.add_argument("--dry-run", action="store_true", help="只读连接，验证内存并报告差异")
    parser.add_argument("--once", action="store_true", help="处理一次后退出；游戏或参数未就绪时返回非零")
    parser.add_argument("--restore", type=Path, help="还原同一游戏会话的指定 journals/*.json 记录")
    parser.add_argument("--poll", type=float, default=0.3)
    parser.add_argument("--debounce", type=float, default=0.9)
    parser.add_argument("--max-rows", type=int, default=2048)
    parser.add_argument("--tables", nargs="+", help="只同步指定表，例如 --tables AtkParam_Pc SpEffectParam")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--existing-only", action="store_true", help="部分同步：只更新共同的已有 ID，跳过新行并保留旧行；不执行结构重载")
    modes.add_argument("--full-reload", action="store_true", help="原生帧回调完整行集合热加载：支持新增、删除、ID 和顺序变化")
    parser.add_argument("--state-dir", type=Path, default=APP / "state")
    args = parser.parse_args()
    if args.poll < 0.1 or args.debounce < 0.3 or not 1 <= args.max_rows <= 50000:
        parser.error("--poll >= 0.1、--debounce >= 0.3、1 <= --max-rows <= 50000")
    if args.restore and (args.dry_run or args.inspect):
        parser.error("--restore 不可与 --inspect 或 --dry-run 同用")
    return args


def run(args, stop: threading.Event | None = None) -> int:
    game = args.game_dir.resolve()
    exe = game / "sekiro.exe"
    profile = json.loads((RESOURCE / "profile.json").read_text(encoding="utf-8"))
    version = file_version(exe)
    if version != profile["exeVersion"]:
        raise InvalidData(f"仅支持 {profile['exeVersion']}，实际为 {version}；拒绝猜测地址")
    exe_hash = sha256(exe)
    if exe_hash != profile["expectedExeSha256"]:
        raise InvalidData("sekiro.exe 指纹与本机验证配置不同；需重新核实内存布局后更新 profile.json")
    supported = {x.split(":")[0] for x in profile["bases"][0]["paramOffsets"]}
    if args.tables:
        unknown = set(args.tables) - supported
        if unknown:
            raise InvalidData(f"没有公开地址映射的表: {sorted(unknown)}")
        supported &= set(args.tables)
    source = args.source.resolve() if args.source else auto_source(game)
    decoder = Decoder(game)
    LOG.info("游戏 %s | 版本 %s | SHA256 %s", exe, version, exe_hash)
    LOG.info("监视 %s", source)
    params, digest, sig = read_snapshot(source, decoder, supported)
    LOG.info("文件解析成功：%d 张受支持的表，%d 行（未映射的辅助表不写入游戏）", len(params), sum(len(p.rows) for p in params.values()))
    if args.inspect:
        for name, param in params.items():
            LOG.info("  %-36s %6d 行 x %4d 字节 [%s]", name, len(param.rows), param.row_size, param.type)
        return 0
    LOG.info("模式：%s；Ctrl+C 退出。", "只读检查" if args.dry_run else "自动写入 + 回读校验 + 失败回滚")
    mem = None
    engine = None
    # Do not baseline the source before the first successful memory synchronization.
    applied_digest = None
    observed_sig, changed_at = sig, time.monotonic()
    pending = True
    next_retry = 0.0
    last_notice = None
    memory_tables = []
    next_generation_check = 0
    hash_sig = content_signature(source)
    next_hash_check = 0
    try:
        while True:
            if stop is not None and stop.is_set():
                LOG.info("已安全停止：当前事务已结束")
                return 0
            if mem is not None and not mem.alive():
                LOG.info("游戏进程已退出，等待下一次启动")
                if isinstance(engine, StructuralEngine):
                    engine.close()
                engine = None
                mem.close()
                mem = None
                applied_digest = None
                pending = True
            if mem is None:
                pid = find_process(exe)
                if pid is not None:
                    mem = ProcessMemory(pid, writable=not args.dry_run)
                    if mem.exe != exe:
                        mem.close()
                        mem = None
                        raise InvalidData("目标进程路径在连接时改变")
                    native_mode = getattr(args, "full_reload", False)
                    if args.restore:
                        native_mode = json.loads(args.restore.read_text(encoding="utf-8")).get("format") == 2
                    engine = (StructuralEngine(mem, profile, args.state_dir, exe_hash, args.max_rows, resource_root=RESOURCE)
                              if native_mode else Engine(mem, profile, args.state_dir, exe_hash, args.max_rows, args.existing_only))
                    LOG.info("已连接 PID=%d，模块基址=%#x", pid, mem.base)
                    if args.restore:
                        journal = engine.restore(args.restore.resolve())
                        LOG.info("回滚完成；本次回滚也已备份到 %s", journal)
                        return 0
                    next_retry = 0
                elif args.once or args.restore:
                    LOG.error("游戏未运行。请启动游戏并进入存档后重试。")
                    return 2
                elif last_notice != "waiting":
                    LOG.info("等待游戏启动并加载存档……")
                    last_notice = "waiting"
            try:
                current = signature(sources(source))
                now = time.monotonic()
                if current != observed_sig:
                    observed_sig, changed_at, pending = current, now, True
                    next_retry = 0
                if now >= next_hash_check:
                    new_hash_sig = content_signature(source)
                    if new_hash_sig != hash_sig:
                        hash_sig, changed_at, pending = new_hash_sig, now, True
                        next_retry = 0
                    next_hash_check = now + 1
                if mem and not pending and now >= next_generation_check:
                    try:
                        for table in memory_tables:
                            engine.validate_table(table)
                    except (RuntimeError, OSError):
                        applied_digest, pending = None, True
                    next_generation_check = now + 2
                ready = args.once or now - changed_at >= args.debounce
                if mem and pending and ready and now >= next_retry:
                    params, digest, sig = read_snapshot(source, decoder, supported)
                    if applied_digest != digest:
                        tables, patches = engine.stage(params)
                        for notice in engine.notices:
                            LOG.warning(notice)
                        if signature(sources(source)) != sig:
                            raise RetryLater("预检期间源文件再次改变")
                        journal = engine.commit(tables, patches, digest, args.dry_run)
                        label = "差异（只读）" if args.dry_run else ("已有行部分同步完成" if engine.notices else "同步完成")
                        LOG.info("%s：%d 行 / %d 字节 / %d 张表%s", label,
                                 len(patches), sum(len(p.after) for p in patches), len({p.table for p in patches}),
                                 f"；回滚记录 {journal}" if journal else "")
                        if isinstance(engine, StructuralEngine):
                            LOG.info("完整行集合：新增 %d / 删除 %d / 修改 %d / 重排 %d 张表%s",
                                     engine.stats["added"], engine.stats["deleted"], engine.stats["modified"], engine.stats["reordered"],
                                     f"；保留旧块 {engine.bridge.shared.retained_bytes / 1048576:.2f} MiB" if engine.bridge else "")
                        applied_digest = digest
                        memory_tables = ([engine.table(p) for p in params.values()] if isinstance(engine, StructuralEngine)
                                         and not args.dry_run else tables)
                    pending, last_notice = False, None
                    if args.once:
                        return 0
                elif args.once:
                    return 2
            except FatalTransaction:
                raise
            except (RetryLater, InvalidData, OSError, RuntimeError) as error:
                # A malformed save is retried even if the editor preserves mtime/size.
                text = str(error)
                if text != last_notice:
                    LOG.warning("未应用修改：%s", text)
                    last_notice = text
                if args.once:
                    return 3
                pending, next_retry = True, time.monotonic() + 2
            if stop is None:
                time.sleep(args.poll)
            else:
                stop.wait(args.poll)
    finally:
        if isinstance(engine, StructuralEngine):
            engine.close()
        if mem:
            mem.close()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    if len(sys.argv) == 4 and sys.argv[1] == "--watchdog":
        return watchdog(int(sys.argv[2]), int(sys.argv[3]))
    stop = None
    if "--gui-worker" in sys.argv:
        sys.argv.remove("--gui-worker")
        stop = threading.Event()
        def receive_stop():
            # Stop only between transactions; never terminate a worker during memory writes.
            if sys.stdin is not None:
                for line in sys.stdin:
                    if line.strip() == "STOP":
                        break
            stop.set()
        threading.Thread(target=receive_stop, daemon=True).start()
    args = arguments()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    logging.Formatter.converter = staticmethod(lambda stamp: time.gmtime(stamp + 8 * 3600))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s +0800 %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(args.state_dir / "hotload.log", encoding="utf-8")])
    # Windows exclusive byte lock prevents simultaneous writers (including restore).
    import msvcrt
    mutex = None
    if not args.inspect:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel.CreateMutexW
        create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        create_mutex.restype = ctypes.c_void_p
        key = hashlib.sha256(str(args.game_dir.resolve()).casefold().encode("utf-8")).hexdigest()
        mutex = create_mutex(None, False, "Local\\SekiroParamHotload-" + key)
        if not mutex or ctypes.get_last_error() == 183:
            if mutex:
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.CloseHandle(mutex)
            LOG.error("此游戏目录已有一个热加载实例运行，请先关闭它（包含只读检查窗口）")
            return 4
    lock = (args.state_dir / "instance.lock").open("a+b")
    lock.seek(0)
    if lock.read(1) == b"":
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        LOG.error("另一个工具实例正在使用此 state 目录，请先关闭它")
        lock.close()
        if mutex:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)
        return 4
    try:
        return run(args, stop)
    except KeyboardInterrupt:
        LOG.info("已停止")
        return 0
    except Exception:
        LOG.exception("工具停止")
        return 1
    finally:
        lock.close()
        if mutex:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
