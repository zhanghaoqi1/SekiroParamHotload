from collections import Counter
from dataclasses import replace
import struct
import unittest

from test_hotload import FakeMemory, PROFILE, sample
from sekiro_hotload.engine import Engine
from sekiro_hotload.formats import InvalidData, read_param
from sekiro_hotload.runtime import build_runtime_image, inspect_table


def template():
    header = bytearray(sample()[:64])
    header[0x2d:0x30] = b"\x85\x07\0"
    return bytes(header)


def query(image, row_id):
    """Independent reader following the published V5 binary-search contract."""
    allocation = image.allocation
    after_name = struct.unpack_from("<I", allocation, 0)[0]
    table = 16 + ((after_name + 15) & ~15)
    count = struct.unpack_from("<H", allocation, 16 + 10)[0]
    low, high = 0, count
    while low < high:
        middle = (low + high) // 2
        found, index = struct.unpack_from("<II", allocation, table + middle * 8)
        if found < row_id:
            low = middle + 1
        elif found > row_id:
            high = middle
        else:
            actual, _, offset, _ = struct.unpack_from("<IIQQ", allocation, 16 + 64 + index * 24)
            if actual != row_id:
                raise AssertionError("lookup points to wrong descriptor")
            return allocation[16 + offset:16 + offset + 4]
    return None


class RuntimeTests(unittest.TestCase):
    def build(self, ids=(100, 200), values=(b"abcd", b"efgh")):
        return build_runtime_image(read_param("Test", sample(ids, values)), template(), b"\0" * 16)

    def test_added_row_is_in_rebuilt_lookup(self):
        image = self.build((100, 200, 300), (b"abcd", b"efgh", b"ijkl"))
        self.assertEqual(query(image, 300), b"ijkl")
        self.assertEqual(image.row_count, 3)

    def test_removed_row_is_absent_from_rebuilt_lookup(self):
        image = self.build((100,), (b"abcd",))
        self.assertEqual(query(image, 100), b"abcd")
        self.assertIsNone(query(image, 200))

    def test_lookup_sort_is_independent_of_descriptor_order(self):
        image = self.build((200, 100), (b"efgh", b"abcd"))
        self.assertEqual(query(image, 100), b"abcd")
        self.assertEqual(query(image, 200), b"efgh")

    def test_empty_and_unsigned_ids(self):
        self.assertIsNone(query(self.build((), ()), 0))
        image = self.build((-1, 100), (b"zzzz", b"abcd"))
        self.assertEqual(query(image, 0xffffffff), b"zzzz")
        self.assertEqual(query(image, 100), b"abcd")

    def test_duplicate_ids_and_wrong_runtime_version_rejected(self):
        with self.assertRaises(InvalidData):
            self.build((100, 100))
        with self.assertRaises(InvalidData):
            build_runtime_image(read_param("Test", sample()), sample()[:64], b"\0" * 16)

    def test_metadata_preserved_except_recomputed_size(self):
        metadata = b"\0" * 4 + bytes(range(12))
        image = build_runtime_image(read_param("Test", sample()), template(), metadata)
        self.assertEqual(image.allocation[4:16], metadata[4:])
        self.assertEqual(image.lookup_offset % 16, 0)

    def test_inspector_accepts_consistent_lookup_and_rejects_corruption(self):
        image = self.build()
        mem = FakeMemory(sample())
        mem.buf[0x50000 - 16:0x50000 - 16 + len(image.allocation)] = image.allocation
        struct.pack_into("<Q", mem.buf, 0x40068, image.file_size)
        engine = Engine(mem, PROFILE, None, "test")
        report = inspect_table(engine, "Test")
        self.assertEqual(report["row_count"], 2)
        self.assertTrue(report["lookup_validated"])
        struct.pack_into("<I", mem.buf, 0x50000 + image.lookup_offset + 4, 99)
        with self.assertRaises(InvalidData):
            inspect_table(engine, "Test")
        self.assertEqual(mem.writes, [])


if __name__ == "__main__":
    unittest.main()
