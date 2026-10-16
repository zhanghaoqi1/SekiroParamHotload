"""Validate/stage outside the pause; commit, read back, and roll back inside it."""
from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import struct
import time
import uuid

from .formats import InvalidData, Param, Row, number


class RetryLater(RuntimeError):
    """Game is not ready, or a load transition invalidated the preflight."""


class FatalTransaction(RuntimeError):
    """Automatic retries must stop; human inspection is required."""


@dataclass(frozen=True)
class Table:
    name: str
    base: int
    header: bytes
    index: bytes
    size: int
    offsets: tuple[int, ...]

    @property
    def ids(self) -> tuple[int, ...]:
        return tuple(struct.unpack_from("<i", self.index, i * 24)[0] for i in range(len(self.offsets)))


@dataclass(frozen=True)
class Patch:
    table: str
    index: int
    address: int
    before: bytes
    after: bytes


def ptr(mem, address: int) -> int:
    result = number(mem.read(address, 8), 0, "<Q")
    if not 0x10000 <= result < 0x0000800000000000:
        raise RetryLater(f"参数仓库尚未加载或指针无效: [{address:#x}]={result:#x}")
    return result


class Engine:
    def __init__(self, mem, profile: dict, state: Path, exe_hash: str, max_rows: int = 2048,
                 existing_only: bool = False):
        self.mem, self.profile, self.state = mem, profile, state
        self.exe_hash, self.max_rows = exe_hash, max_rows
        self.existing_only = existing_only
        self.notices: list[str] = []
        base = profile["bases"][0]
        self.repo_rva = int(base["paramBase"], 16)
        self.offsets = {key: int(value, 16) for key, value in (x.split(":") for x in base["paramOffsets"])}
        if (base["paramInnerPath"], base["paramDataOffset"], base["rowHeaderSize"], base["rowPointerOffset"], base["paramCountOffset"]) != ("0x70/0x70", "0x40", "0x18", "0x8", "0xA"):
            raise InvalidData("配置与本工具实现不兼容")

    def locate(self, name: str) -> int:
        repo = ptr(self.mem, self.mem.base + self.repo_rva)
        holder = ptr(self.mem, repo + self.offsets[name])
        inner = ptr(self.mem, holder + 0x70)
        return ptr(self.mem, inner + 0x70)

    def table(self, param: Param) -> Table:
        address = self.locate(param.name)
        header = self.mem.read(address, 0x40)
        count = number(header, 0x0a, "<H")
        if header[0x2c:0x30] != param.header[0x2c:0x30]:
            raise InvalidData(f"{param.name}: 格式标志不同，内存={header[0x2c:0x30].hex()}，文件={param.header[0x2c:0x30].hex()}；拒绝按不同布局写入")
        if header[6:10] != param.header[6:10]:
            raise InvalidData(f"{param.name}: 数据版本或头元数据不同，内存={header[6:10].hex()}，文件={param.header[6:10].hex()}；需使用兼容本游戏版本的参数")
        if header[0x2d] & 0x80:
            type_offset = number(header, 0x10, "<Q")
            if not 0x40 <= type_offset < 256 * 1024 * 1024:
                raise InvalidData(f"{param.name}: 内存参数类型偏移无效")
            actual_type = self.mem.read(address + type_offset, len(param.type) + 1)
        else:
            actual_type = header[0x0c:0x2c].split(b"\0", 1)[0] + b"\0"
        if actual_type != param.type.encode("ascii") + b"\0":
            raise InvalidData(f"{param.name}: 内存参数类型不匹配")
        index = self.mem.read(address + 0x40, count * 24) if count else b""
        entries = [struct.unpack_from("<iIQQ", index, i * 24) for i in range(count)]
        offsets = tuple(e[2] for e in entries)
        size = 0
        if count:
            first = number(header, 0x30, "<Q")
            strings = [e[3] for e in entries if e[3]]
            if header[0x2d] & 0x80:
                strings.append(number(header, 0x10, "<Q"))
            end = min(strings) if strings else number(header, 0, "<I")
            if first != offsets[0] or first < 0x40 + count * 24 or end > 256 * 1024 * 1024:
                raise InvalidData(f"{param.name}: 内存布局无效")
            lengths = [b - a for a, b in zip(offsets, offsets[1:] + (end,))]
            size = lengths[0]
            if not 0 < size <= 65536 or len(set(lengths)) != 1:
                raise InvalidData(f"{param.name}: 内存行边界无效")
            if param.rows and size != param.row_size:
                raise InvalidData(f"{param.name}: 每行大小不同，内存={size} 字节，文件={param.row_size} 字节；不能通过原位写入改变字段布局")
        return Table(param.name, address, header, index, size, offsets)

    def match_rows(self, param: Param, table: Table) -> list[tuple[int, Row]]:
        source_ids = tuple(row.id for row in param.rows)
        live_ids = table.ids
        if source_ids == live_ids:
            return list(enumerate(param.rows))
        source_counts, live_counts = Counter(source_ids), Counter(live_ids)
        if any(n > 1 for n in source_counts.values()) or any(n > 1 for n in live_counts.values()):
            raise InvalidData(f"{param.name}: 行集合或顺序变化且存在重复 ID，无法无歧义地按 ID 匹配；拒绝写入")
        added, missing = source_counts - live_counts, live_counts - source_counts
        def describe(counter):
            values = sorted(counter)
            return str(values[:12]) + (f" 等 {len(values)} 个" if len(values) > 12 else "")
        if added or missing:
            message = (f"{param.name}: 内存 {len(live_ids)} 行，文件 {len(source_ids)} 行；"
                       f"文件新增 ID {describe(added)}；文件缺少的内存 ID {describe(missing)}")
            if not self.existing_only:
                raise InvalidData(message + "；整次未写入。--existing-only 可只更新共同 ID（不会新增或删除内存行）")
            self.notices.append(message + "；跳过新增行，保留文件缺少的内存行，属于部分同步")
        # Source order is irrelevant when IDs are unique. Preserve the live index and pointers.
        source = {row.id: row for row in param.rows}
        return [(i, source[row_id]) for i, row_id in enumerate(live_ids) if row_id in source]

    def stage(self, params: dict[str, Param]) -> tuple[list[Table], list[Patch]]:
        tables, patches, errors = [], [], []
        self.notices = []
        for name, param in params.items():
            if name not in self.offsets:
                raise InvalidData(f"没有经过验证的参数地址: {name}")
            try:
                table = self.table(param)
                matches = self.match_rows(param, table)
            except InvalidData as error:
                errors.append(str(error))
                continue
            tables.append(table)
            if not matches:
                continue
            blob = self.mem.read(table.base + table.offsets[0], len(table.offsets) * table.size)
            for i, row in matches:
                before = blob[i * table.size:(i + 1) * table.size]
                if before != row.data:
                    patches.append(Patch(name, i, table.base + table.offsets[i], before, row.data))
        if errors:
            raise InvalidData("参数结构预检失败（整次未写入）：\n  " + "\n  ".join(errors))
        if len(patches) > self.max_rows:
            raise InvalidData(f"本次修改 {len(patches)} 行，超过限制 {self.max_rows}；检查来源或使用 --max-rows 调整")
        return tables, patches

    def validate_table(self, table: Table):
        if self.locate(table.name) != table.base:
            raise RetryLater(f"{table.name}: 参数地址已变化，等待加载结束")
        if self.mem.read(table.base, 0x40) != table.header:
            raise RetryLater(f"{table.name}: 参数头已变化")
        if table.index and self.mem.read(table.base + 0x40, len(table.index)) != table.index:
            raise RetryLater(f"{table.name}: 行索引已变化")

    def save_journal(self, tables: list[Table], patches: list[Patch], source_hash: str) -> Path:
        folder = self.state / "journals"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() + 8 * 3600)) + "-" + uuid.uuid4().hex[:8] + ".json")
        encode = lambda data: base64.b64encode(data).decode("ascii")
        doc = {"format": 1, "pid": self.mem.pid, "identity": self.mem.identity,
               "module_base": self.mem.base, "exe_sha256": self.exe_hash,
               "source_sha256": source_hash,
               "tables": [{"name": t.name, "base": t.base, "header": encode(t.header),
                           "index": encode(t.index), "size": t.size, "offsets": t.offsets} for t in tables],
               "patches": [{"table": p.table, "index": p.index, "address": p.address,
                            "before": encode(p.before), "after": encode(p.after)} for p in patches]}
        atomic_json(path, doc)
        return path

    def commit(self, tables: list[Table], patches: list[Patch], source_hash: str, dry_run: bool = False) -> Path | None:
        if not patches or dry_run:
            return None
        changed = {p.table for p in patches}
        tables = [t for t in tables if t.name in changed]
        journal = self.save_journal(tables, patches, source_hash)
        outcome = "not_started"
        attempted = []
        try:
            with self.mem.frozen():
                start = time.monotonic()
                def deadline():
                    if time.monotonic() - start > 2:
                        raise RetryLater("暂停超过 2 秒预算，取消本次更新")
                try:
                    for table in tables:
                        deadline()
                        self.validate_table(table)
                    for patch in patches:
                        deadline()
                        if self.mem.read(patch.address, len(patch.before)) != patch.before:
                            raise RetryLater(f"{patch.table}: 预检之后数据变化，稍后重试")
                        self.mem.check_range(patch.address, len(patch.after), write=True)
                    for patch in patches:
                        deadline()
                        # Record before attempting: WriteProcessMemory can fail after a partial write.
                        attempted.append(patch)
                        self.mem.write(patch.address, patch.after)
                    for patch in patches:
                        deadline()
                        if self.mem.read(patch.address, len(patch.after)) != patch.after:
                            raise RuntimeError("写入后回读不一致")
                    outcome = "committed"
                except BaseException:
                    failures = []
                    for patch in reversed(attempted):
                        try:
                            self.mem.write(patch.address, patch.before)
                            if self.mem.read(patch.address, len(patch.before)) != patch.before:
                                raise RuntimeError("回滚校验失败")
                        except BaseException as error:
                            failures.append(str(error))
                    outcome = "rolled_back" if not failures else "rollback_failed"
                    if failures:
                        raise FatalTransaction(f"回滚失败，必须停止并重启游戏；记录 {journal}: {failures}")
                    raise
        except BaseException as error:
            if outcome == "committed" or getattr(error, "fatal", False):
                outcome = "recovery_failed"
                raise FatalTransaction(f"进程恢复保护失败；停止自动更新。记录: {journal}")
            raise
        finally:
            try:
                atomic_json(journal.with_suffix(".result.json"), {"outcome": outcome, "attempted_rows": len(attempted)})
            except OSError as error:
                raise FatalTransaction(f"事务结果无法保存；停止自动更新。备份: {journal}") from error
        return journal

    def restore(self, path: Path) -> Path | None:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if (doc.get("format"), doc.get("pid"), doc.get("identity"), doc.get("module_base"), doc.get("exe_sha256")) != (
                1, self.mem.pid, self.mem.identity, self.mem.base, self.exe_hash):
            raise InvalidData("回滚记录属于另一游戏会话或可执行文件；拒绝复用旧地址")
        decode = lambda value: base64.b64decode(value, validate=True)
        tables = [Table(t["name"], t["base"], decode(t["header"]), decode(t["index"]),
                        t["size"], tuple(t["offsets"])) for t in doc["tables"]]
        lookup = {t.name: t for t in tables}
        patches = []
        for p in doc["patches"]:
            t = lookup[p["table"]]
            address = t.base + t.offsets[p["index"]]
            before, after = decode(p["after"]), decode(p["before"])
            if address != p["address"] or len(before) != t.size or len(after) != t.size:
                raise InvalidData("回滚记录的行地址或大小无效")
            patches.append(Patch(t.name, p["index"], address, before, after))
        if len(patches) > self.max_rows:
            raise InvalidData("回滚行数超过 --max-rows")
        for table in tables:
            self.validate_table(table)
        # commit requires current bytes to match the journal's AFTER image.
        return self.commit(tables, patches, doc["source_sha256"])


def atomic_json(path: Path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()
