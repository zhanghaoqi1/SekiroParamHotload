"""Full row-set reload using the game's frame task and game allocator."""
from __future__ import annotations

import base64
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import time
import uuid

from .engine import Engine, FatalTransaction, Patch, atomic_json, ptr, sha256
from .formats import InvalidData, Param, Row, number, read_param
from .native import NativeBridge, Operation
from .runtime import build_runtime_image, inspect_table


@dataclass
class Plan:
    name: str
    cap: int
    address: int
    size: int
    allocator: int
    width: int
    before: bytes
    after: bytes
    file_size: int
    old_count: int
    new_count: int
    structural: bool


class StructuralEngine(Engine):
    def __init__(self, *args, resource_root: Path, **kwargs):
        super().__init__(*args, **kwargs)
        self.resource_root = resource_root
        self.bridge = None
        self.plans: list[Plan] = []
        self.stats = {"added": 0, "deleted": 0, "modified": 0, "reordered": 0}
        self.widths = {}

    def native(self):
        if self.bridge is None:
            config = self.profile["native"]
            dll = self.resource_root / config["dll"]
            if sha256(dll) != config["sha256"]:
                raise FatalTransaction("原生模块指纹不匹配")
            self.bridge = NativeBridge(self.mem, dll, config["tag"], self.exe_hash)
        return self.bridge

    def close(self):
        if self.bridge:
            self.bridge.close()
            self.bridge = None

    def stage(self, params: dict[str, Param]):
        tables, patches, plans, errors = [], [], [], []
        self.notices = []
        self.stats = {"added": 0, "deleted": 0, "modified": 0, "reordered": 0}
        for name, param in params.items():
            try:
                table = self.table(param)
                info = inspect_table(self, name)
                if int(info["param_address"], 16) != table.base:
                    raise InvalidData(f"{name}: 采样期间参数地址改变")
                cap = int(info["resource_cap"], 16)
                width = table.size or self.widths.get(name) or param.row_size
                if table.size:
                    self.widths[name] = table.size
                if not width:
                    tables.append(table)
                    continue
                before = self.mem.read(table.base - 16, 16 + info["lookup_end"])
                if before[:16].hex() != info["metadata_hex"] or before[16:80] != table.header:
                    raise InvalidData(f"{name}: 采样期间参数头改变")
                old = defaultdict(list)
                new = defaultdict(list)
                for i, row_id in enumerate(table.ids):
                    off = table.offsets[i]
                    old[row_id].append((table.base + off, before[16 + off:16 + off + width]))
                for row in param.rows:
                    new[row.id].append(row.data)
                changes = []
                for row_id in old.keys() | new.keys():
                    old_rows, new_rows = old[row_id], new[row_id]
                    common = min(len(old_rows), len(new_rows))
                    for i in range(common):
                        address, data = old_rows[i]
                        if data != new_rows[i]:
                            changes.append(Patch(name, i, address, data, new_rows[i]))
                            self.stats["modified"] += 1
                    for i in range(common, len(new_rows)):
                        changes.append(Patch(name, i, 0, b"", new_rows[i]))
                        self.stats["added"] += 1
                    for i in range(common, len(old_rows)):
                        address, data = old_rows[i]
                        changes.append(Patch(name, i, address, data, b""))
                        self.stats["deleted"] += 1
                tables.append(table)
                source_ids = tuple(row.id for row in param.rows)
                reordered = table.ids != source_ids and Counter(table.ids) == Counter(source_ids)
                if reordered:
                    self.stats["reordered"] += 1
                    changes.append(Patch(name, 0, 0, b"", b""))
                if not changes:
                    continue
                image = build_runtime_image(param, table.header, before[:16], allow_duplicates=True)
                owner = ptr(self.mem, cap + 0x48)
                allocator = ptr(self.mem, owner + 8)
                structural = table.ids != source_ids
                plans.append(Plan(name, cap, table.base, info["resource_size"], allocator, width,
                                  before, image.allocation, image.file_size, len(table.ids), len(param.rows), structural))
                patches.extend(changes)
            except (InvalidData, KeyError) as error:
                errors.append(f"{name}: {error}")
        if errors:
            raise InvalidData("完整热加载预检失败（整次未写入）：\n  " + "\n  ".join(errors))
        if len(patches) > self.max_rows:
            raise InvalidData(f"本次新增/删除/修改共 {len(patches)} 行，超过限制 {self.max_rows}；可在高级设置调整")
        self.plans = plans
        return tables, patches

    def commit(self, tables, patches, source_hash, dry_run=False):
        if not patches or dry_run:
            return None
        return self._apply(self.plans, source_hash)

    def _apply(self, plans: list[Plan], source_hash: str):
        bridge = self.native()
        for plan in plans:
            if not plan.old_count and plan.new_count:
                known = self.widths.get(plan.name) or bridge.request(5, plan.cap)
                if known != plan.width:
                    raise InvalidData(f"{plan.name}: 空表的原始行宽未验证；需先从非空的兼容表建立运行时记录")
        folder = self.state / "journals"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() + 28800)) + "-native-" + uuid.uuid4().hex[:8] + ".json")
        encode = lambda data: base64.b64encode(data).decode("ascii")
        document = {"format": 2, "pid": self.mem.pid, "identity": self.mem.identity, "module_base": self.mem.base,
                    "exe_sha256": self.exe_hash, "source_sha256": source_hash,
                    "tables": [{"name": p.name, "cap": p.cap, "before_data": p.address, "width": p.width,
                                "before": encode(p.before), "after": encode(p.after), "structural": p.structural}
                               for p in plans]}
        atomic_json(path, document)
        operations = [Operation(cap=p.cap, expected_data=p.address, expected_size=p.size, file_size=p.file_size,
                                allocator=p.allocator, old_count=p.old_count, new_count=p.new_count,
                                row_size=p.width, flags=0 if p.structural else 1) for p in plans]
        outcome = "not_started"
        try:
            output = bridge.apply(operations, [p.after for p in plans])
            outcome = "committed"
            # Verify the actual published rows and the game's native ID lookup.
            for plan, data in zip(plans, output):
                if self.locate(plan.name) != data:
                    raise FatalTransaction(f"{plan.name}: 游戏资源切换回读不一致；备份 {path}")
                source = read_param(plan.name, plan.after[16:16 + plan.file_size])
                live_info = inspect_table(self, plan.name)
                actual = read_param(plan.name, self.mem.read(data, live_info["resource_size"]))
                if self.semantic(actual) != self.semantic(source):
                    raise FatalTransaction(f"{plan.name}: 参数行回读不一致；备份 {path}")
            atomic_json(path.with_suffix(".result.json"), {"outcome": outcome, "new_data": output,
                        "retained_bytes": self.bridge.shared.retained_bytes,
                        "callback_thread": self.bridge.shared.callback_tid})
            return path
        except BaseException as error:
            atomic_json(path.with_suffix(".result.json"), {"outcome": outcome if outcome == "committed" else "not_committed_or_unknown"})
            if isinstance(error, FatalTransaction) and outcome != "committed":
                index = bridge.shared.error_index
                name = plans[index].name if 0 <= index < len(plans) else "未知参数表"
                raise FatalTransaction(f"{name}: {error}；回滚镜像 {path}") from error
            raise

    @staticmethod
    def semantic(param: Param):
        return param.type, param.header[6:10], Counter((row.id, row.data) for row in param.rows)

    def restore(self, path: Path):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("format") == 1:
            return Engine(self.mem, self.profile, self.state, self.exe_hash, self.max_rows).restore(path)
        if (doc.get("format"), doc.get("pid"), doc.get("identity"), doc.get("module_base"), doc.get("exe_sha256")) != (
                2, self.mem.pid, self.mem.identity, self.mem.base, self.exe_hash):
            raise InvalidData("结构回滚记录不属于当前游戏会话")
        plans = []
        for item in doc["tables"]:
            name = item["name"]
            info = inspect_table(self, name)
            cap, address = int(info["resource_cap"], 16), int(info["param_address"], 16)
            if cap != item["cap"]:
                raise InvalidData(f"{name}: 资源对象已重建，不能复用旧会话记录")
            old_image = base64.b64decode(item["before"], validate=True)
            after = base64.b64decode(item["after"], validate=True)
            after_size = number(after, 0, "<I")
            current = read_param(name, self.mem.read(address, info["resource_size"]))
            expected = read_param(name, after[16:16 + after_size])
            if self.semantic(current) != self.semantic(expected):
                raise InvalidData(f"{name}: 当前数据已与记录的新值不同，拒绝覆盖后续修改")
            before = self.mem.read(address - 16, 16 + info["lookup_end"])
            owner = ptr(self.mem, cap + 0x48)
            size = number(old_image, 0, "<I")
            count = number(old_image, 16 + 0xa, "<H")
            plans.append(Plan(name, cap, address, info["resource_size"], ptr(self.mem, owner + 8), item["width"],
                              before, old_image, size, info["row_count"], count, True))
        if not plans:
            raise InvalidData("结构回滚记录没有参数表")
        return self._apply(plans, doc["source_sha256"])
