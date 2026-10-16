"""Strict, read-only Sekiro DCX/BND4/PARAM support (no paramdef dependency)."""
from __future__ import annotations

import ctypes as C
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

MAX_SIZE = 256 * 1024 * 1024


class InvalidData(ValueError):
    pass


def take(data: bytes, offset: int, size: int) -> bytes:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise InvalidData(f"越界读取: offset={offset:#x}, size={size:#x}, file={len(data):#x}")
    return data[offset:offset + size]


def number(data: bytes, offset: int, fmt: str) -> int:
    return struct.unpack(fmt, take(data, offset, struct.calcsize(fmt)))[0]


def cstring(data: bytes, offset: int, encoding: str = "ascii") -> str:
    width = 2 if encoding.startswith("utf-16") else 1
    end = offset
    while end + width <= len(data) and end - offset < 65536:
        if take(data, end, width) == b"\0" * width:
            return take(data, offset, end - offset).decode(encoding)
        end += width
    raise InvalidData("字符串没有结束符或过长")


class Decoder:
    def __init__(self, game_dir: Path):
        self.game_dir = game_dir
        self.oodle = None

    def decode(self, data: bytes) -> bytes:
        if len(data) > MAX_SIZE:
            raise InvalidData("文件过大")
        if data.startswith(b"DCX\0"):
            # Sekiro KRAK and standard DCX DFLT have the same fixed envelope.
            if take(data, 0x18, 4) != b"DCS\0" or take(data, 0x24, 4) != b"DCP\0":
                raise InvalidData("不支持的 DCX 头")
            size, packed = struct.unpack(">II", take(data, 0x1c, 8))
            if not 0 < size <= MAX_SIZE:
                raise InvalidData("DCX 解压大小无效")
            codec = take(data, 0x28, 4)
            if take(data, 0x44, 4) != b"DCA\0" or number(data, 0x48, ">I") != 8:
                raise InvalidData("不支持的 DCA 头")
            payload = take(data, 0x4c, packed)
            if codec == b"KRAK":
                if self.oodle is None:
                    # Absolute path: never search PATH for a similarly named DLL.
                    self.oodle = C.CDLL(str((self.game_dir / "oo2core_6_win64.dll").resolve()))
                    fn = self.oodle.OodleLZ_Decompress
                    fn.restype = C.c_ssize_t
                    fn.argtypes = [C.c_void_p, C.c_ssize_t, C.c_void_p, C.c_ssize_t,
                                   C.c_int, C.c_int, C.c_int, C.c_void_p, C.c_ssize_t,
                                   C.c_void_p, C.c_void_p, C.c_void_p, C.c_ssize_t, C.c_int]
                src = C.create_string_buffer(payload)
                dest = C.create_string_buffer(size)
                actual = self.oodle.OodleLZ_Decompress(src, len(payload), dest, size,
                                                     1, 0, 0, None, 0, None, None, None, 0, 3)
                if actual != size:
                    raise InvalidData(f"Oodle 解压失败: {actual}/{size}")
                return dest.raw
            if codec == b"DFLT":
                result = self._zlib(payload, size)
                if len(result) != size:
                    raise InvalidData("DFLT 解压大小不符")
                return result
            raise InvalidData(f"不支持的 DCX 压缩: {codec!r}")
        if data[:1] == b"\x78":
            return self._zlib(data, MAX_SIZE)
        return data

    @staticmethod
    def _zlib(data: bytes, limit: int) -> bytes:
        obj = zlib.decompressobj()
        result = obj.decompress(data, limit + 1)
        if len(result) > limit or not obj.eof or obj.unconsumed_tail:
            raise InvalidData("zlib 数据未完成或超过大小限制")
        return result


def reverse_bits(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


def read_bnd4(data: bytes, decoder: Decoder) -> dict[str, bytes]:
    if take(data, 0, 4) != b"BND4" or data[9] != 0:
        raise InvalidData("仅支持 PC 小端 BND4")
    bits_big = not data[10]
    raw = data[0x31]
    flags = raw if bits_big or (raw & 1 and not raw & 0x80) else reverse_bits(raw)
    count = number(data, 0x0c, "<I")
    stride = number(data, 0x20, "<Q")
    if number(data, 0x10, "<Q") != 0x40 or not 0 < count < 4096:
        raise InvalidData("BND4 头大小或文件数量无效")
    has_compression, long_offset, ids, names = bool(flags & 32), bool(flags & 16), bool(flags & 2), bool(flags & 12)
    expected = 16 + (8 if has_compression else 0) + (8 if long_offset else 4) + (4 if ids else 0) + (4 if names else 0) + (8 if flags == 4 else 0)
    if stride != expected or not names:
        raise InvalidData("不支持的 BND4 文件表布局")
    take(data, 0x40, count * stride)
    result = {}
    for index in range(count):
        pos = 0x40 + index * stride
        file_flags = data[pos] if bits_big else reverse_bits(data[pos])
        if take(data, pos + 1, 3) != b"\0" * 3 or number(data, pos + 4, "<i") != -1:
            raise InvalidData("无效的 BND4 文件项")
        packed = number(data, pos + 8, "<q")
        cursor = pos + 16
        unpacked = number(data, cursor, "<q") if has_compression else -1
        cursor += 8 if has_compression else 0
        offset = number(data, cursor, "<Q" if long_offset else "<I")
        cursor += 8 if long_offset else 4
        cursor += 4 if ids else 0
        name_offset = number(data, cursor, "<I")
        name = cstring(data, name_offset, "utf-16-le" if data[0x30] else "shift_jis")
        if not name.lower().endswith(".param"):
            continue
        content = take(data, offset, packed)
        if file_flags & 1:
            content = decoder.decode(content)
            if unpacked >= 0 and len(content) != unpacked:
                raise InvalidData(f"{name}: 解压大小不符")
        key = PureWindowsPath(name).stem
        if key in result:
            raise InvalidData(f"重复参数文件: {key}")
        result[key] = content
    if not result:
        raise InvalidData("包中没有 .param 文件")
    return result


@dataclass(frozen=True)
class Row:
    id: int
    offset: int
    data: bytes


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    header: bytes
    row_headers: bytes
    row_size: int
    rows: tuple[Row, ...]
    # First byte of string/type area; used to verify the last row's boundary.
    data_end: int

    @property
    def schema(self):
        return (self.type, self.header[6:10], self.header[0x2c:0x30],
                self.row_size, tuple(row.id for row in self.rows))


def read_param(name: str, data: bytes) -> Param:
    header = take(data, 0, 0x40)
    if header[0x2c] != 0 or not header[0x2d] & 4:
        raise InvalidData(f"{name}: 仅支持只狼小端、64 位行偏移 PARAM")
    count = number(header, 0x0a, "<H")
    row_headers = take(data, 0x40, count * 0x18)
    type_ptr = number(header, 0x10, "<Q") if header[0x2d] & 0x80 else None
    param_type = cstring(data, type_ptr) if type_ptr is not None else cstring(header, 0x0c)
    if not param_type or not all(ch.isalnum() or ch == "_" for ch in param_type):
        raise InvalidData(f"{name}: 参数类型无效")
    metadata = [struct.unpack("<iIQQ", row_headers[i * 24:(i + 1) * 24]) for i in range(count)]
    if not count:
        return Param(name, param_type, header, row_headers, 0, (), 0)
    offsets = [entry[2] for entry in metadata]
    start = number(header, 0x30, "<Q")
    if start != offsets[0] or start < 0x40 + count * 24:
        raise InvalidData(f"{name}: 行数据起点无效")
    strings = [entry[3] for entry in metadata if entry[3] not in (0, len(data))]
    if type_ptr is not None:
        strings.append(type_ptr)
    # SoulsFormats notes that header stringsOffset is unreliable; prefer actual strings.
    data_end = min(strings) if strings else number(header, 0, "<I")
    if not offsets[-1] < data_end <= len(data):
        raise InvalidData(f"{name}: 无法可靠确定末行边界")
    lengths = [b - a for a, b in zip(offsets, offsets[1:] + [data_end])]
    if not 0 < lengths[0] <= 65536 or len(set(lengths)) != 1:
        raise InvalidData(f"{name}: 行大小不一致，拒绝猜测布局: {sorted(set(lengths))}")
    size = lengths[0]
    rows = tuple(Row(entry[0], entry[2], take(data, entry[2], size)) for entry in metadata)
    return Param(name, param_type, header, row_headers, size, rows, data_end)


def load_params(path: Path, decoder: Decoder, names: set[str] | None = None, raw: bytes | None = None) -> dict[str, Param]:
    content = decoder.decode(path.read_bytes() if raw is None else raw)
    if path.name.lower().endswith(".param"):
        if names is not None and path.stem not in names:
            return {}
        return {path.stem: read_param(path.stem, content)}
    return {name: read_param(name, blob) for name, blob in read_bnd4(content, decoder).items()
            if names is None or name in names}
