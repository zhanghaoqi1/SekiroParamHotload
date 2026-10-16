"""Read-only validation of Sekiro's runtime PARAM metadata and ID lookup table.

This module deliberately contains no process writes or resource replacement.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import struct

from .engine import RetryLater, ptr
from .formats import InvalidData, MAX_SIZE, Param, number


@dataclass(frozen=True)
class RuntimeImage:
    """Offline model of an allocation. This is NOT a game-owned allocation."""
    allocation: bytes
    file_size: int
    lookup_offset: int
    row_count: int


def build_runtime_image(param: Param, template_header: bytes, template_metadata: bytes, *, allow_duplicates: bool = False) -> RuntimeImage:
    """Rebuild V5 descriptors, payloads and the ID index for offline validation.

    No allocation is made in the game and no game resource pointer is changed.
    The ownership allocator, safe handoff and stale-reference handling are separate work.
    """
    if len(template_header) != 0x40 or len(template_metadata) != 0x10:
        raise InvalidData("运行时模板大小不正确")
    flags = number(template_header, 0x2c, "<I")
    if (flags >> 8) & 0x7f != 5 or not flags & (1 << 17) or not flags & (1 << 15):
        raise InvalidData("仅重建已识别的 V5 / 64 位运行时格式")
    if template_header[6:10] != param.header[6:10]:
        raise InvalidData("PARAM 数据版本不一致")
    count = len(param.rows)
    if count > 65535:
        raise InvalidData("行数量超过 u16 范围")
    ids = [row.id & 0xffffffff for row in param.rows]
    if not allow_duplicates and len(set(ids)) != count:
        raise InvalidData("实验性结构模型拒绝有歧义的重复 ID")
    if count and (not 0 < param.row_size <= 65536 or any(len(row.data) != param.row_size for row in param.rows)):
        raise InvalidData("行数据长度不一致")
    data_offset = (0x40 + count * 24 + 15) & ~15
    name_offset = data_offset + count * param.row_size
    name = param.type.encode("ascii") + b"\0"
    file_size = name_offset + len(name)
    lookup_offset = (file_size + 15) & ~15
    if 16 + lookup_offset + count * 8 > MAX_SIZE:
        raise InvalidData("重建后的运行时镜像超过大小限制")
    file = bytearray(lookup_offset)
    file[:0x40] = template_header
    struct.pack_into("<I", file, 0, name_offset)
    struct.pack_into("<H", file, 0x0a, count)
    struct.pack_into("<Q", file, 0x10, name_offset)
    struct.pack_into("<Q", file, 0x30, data_offset)
    for index, row in enumerate(param.rows):
        offset = data_offset + index * param.row_size
        struct.pack_into("<IIQQ", file, 0x40 + index * 24, ids[index], 0, offset, name_offset)
        file[offset:offset + param.row_size] = row.data
    file[name_offset:name_offset + len(name)] = name
    for row_id, index in sorted((row_id, index) for index, row_id in enumerate(ids)):
        file.extend(struct.pack("<II", row_id, index))
    metadata = bytearray(template_metadata)
    struct.pack_into("<I", metadata, 0, file_size)
    return RuntimeImage(bytes(metadata + file), file_size, lookup_offset, count)


def inspect_table(engine, name: str) -> dict:
    mem = engine.mem
    repo = ptr(mem, mem.base + engine.repo_rva)
    holder = ptr(mem, repo + engine.offsets[name])
    cap = ptr(mem, holder + 0x70)
    address = ptr(mem, cap + 0x70)
    resource_size = number(mem.read(cap + 0x68, 8), 0, "<Q")
    metadata = mem.read(address - 0x10, 0x10)
    after_name = number(metadata, 0, "<I")
    header = mem.read(address, 0x40)
    count = number(header, 0x0a, "<H")
    flags = number(header, 0x2c, "<I")
    version = (flags >> 8) & 0x7f
    if version != 5 or not flags & (1 << 17) or not flags & (1 << 15):
        raise InvalidData(f"{name}: 运行时格式不是已识别的 64 位 V5")
    descriptors = mem.read(address + 0x40, count * 24) if count else b""
    ids = tuple(struct.unpack_from("<I", descriptors, i * 24)[0] for i in range(count))
    if not 0x40 + count * 24 <= after_name < 256 * 1024 * 1024:
        raise InvalidData(f"{name}: 运行时元数据偏移无效: {after_name:#x}")
    lookup_offset = (after_name + 15) & ~15
    raw = mem.read(address + lookup_offset, count * 8) if count else b""
    entries = tuple(struct.iter_unpack("<II", raw))
    if any(a[0] > b[0] for a, b in zip(entries, entries[1:])):
        raise InvalidData(f"{name}: 运行时 ID 查找表未排序")
    if Counter(index for _, index in entries) != Counter(range(count)):
        raise InvalidData(f"{name}: 运行时查找表索引不是完整排列")
    if any(ids[index] != row_id for row_id, index in entries):
        raise InvalidData(f"{name}: 查找表和描述符的 ID 不一致")
    type_offset = number(header, 0x10, "<Q")
    if not 0x40 <= type_offset < after_name:
        raise InvalidData(f"{name}: 类型名称偏移不在资源范围内")
    type_bytes = mem.read(address + type_offset, min(256, after_name - type_offset))
    if b"\0" not in type_bytes:
        raise InvalidData(f"{name}: 类型名称未终止")
    if (engine.locate(name) != address or mem.read(address, 0x40) != header
            or mem.read(address - 0x10, 0x10) != metadata
            or (descriptors and mem.read(address + 0x40, len(descriptors)) != descriptors)
            or (raw and mem.read(address + lookup_offset, len(raw)) != raw)):
        raise RetryLater(f"{name}: 读取期间参数仓库变化")
    return {"name": name, "resource_cap": hex(cap), "param_address": hex(address),
            "resource_size": resource_size, "metadata_hex": metadata.hex(),
            "after_name_offset": after_name, "lookup_offset": lookup_offset,
            "lookup_end": lookup_offset + count * 8, "row_count": count,
            "type": type_bytes.split(b"\0", 1)[0].decode("ascii"),
            "runtime_version": version, "lookup_validated": True}
