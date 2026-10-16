from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sekiro_hotload.formats import Decoder, InvalidData, read_param, take
from sekiro_hotload.engine import Engine, FatalTransaction, RetryLater


def sample(ids=(100, 200), values=(b"abcd", b"efgh")):
    data_start = 0x40 + len(ids) * 24
    strings = data_start + sum(map(len, values))
    buf = bytearray(strings)
    struct.pack_into("<I", buf, 0, strings)
    struct.pack_into("<HH", buf, 8, 1, len(ids))
    buf[0x2d:0x30] = bytes((0x84, 1, 0xff))
    struct.pack_into("<Q", buf, 0x10, strings)
    struct.pack_into("<Q", buf, 0x30, data_start)
    offset = data_start
    for i, (row_id, value) in enumerate(zip(ids, values)):
        struct.pack_into("<iIQQ", buf, 0x40 + i * 24, row_id, 0, offset, 0)
        buf[offset:offset + len(value)] = value
        offset += len(value)
    buf.extend(b"TEST_PARAM_ST\0")
    return bytes(buf)


PROFILE = {"bases": [{"paramBase": "0x100", "paramInnerPath": "0x70/0x70",
                       "paramDataOffset": "0x40", "rowHeaderSize": "0x18",
                       "rowPointerOffset": "0x8", "paramCountOffset": "0xA", "paramOffsets": ["Test:0x78"]}]}


class FakeMemory:
    base, pid, identity = 0x10000, 123, 456

    def __init__(self, blob):
        self.buf = bytearray(0x60000)
        self.writes = []
        self.paused = False
        self.fail_at = 0
        self.fail_rollback = False
        for address, value in [(self.base + 0x100, 0x20000), (0x20078, 0x30000),
                               (0x30070, 0x40000), (0x40070, 0x50000)]:
            struct.pack_into("<Q", self.buf, address, value)
        self.buf[0x50000:0x50000 + len(blob)] = blob

    def check_range(self, address, size, write=False):
        if address < 0 or size < 1 or address + size > len(self.buf):
            raise RuntimeError("out of range")

    def read(self, address, size):
        self.check_range(address, size)
        return bytes(self.buf[address:address + size])

    def write(self, address, data):
        assert self.paused, "Writes must happen during the freeze"
        self.writes.append((address, data))
        if len(self.writes) == self.fail_at:
            self.buf[address:address + 1] = data[:1]
            raise RuntimeError("injected partial write")
        if self.fail_rollback and len(self.writes) > self.fail_at:
            raise RuntimeError("injected rollback failure")
        self.buf[address:address + len(data)] = data

    @contextmanager
    def frozen(self):
        self.paused = True
        try:
            yield
        finally:
            self.paused = False


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.old = sample()
        self.new = read_param("Test", sample(values=(b"ABCD", b"EFGH")))
        self.mem = FakeMemory(self.old)
        self.engine = Engine(self.mem, PROFILE, self.state, "exe-hash")

    def stage(self):
        return self.engine.stage({"Test": self.new})

    def test_parse_multi_single_empty_and_duplicate_ids(self):
        self.assertEqual(self.new.row_size, 4)
        self.assertEqual(read_param("Test", sample((100,), (b"abcd",))).row_size, 4)
        self.assertEqual(len(read_param("Test", sample((), ())).rows), 0)
        p = read_param("Test", sample((100, 100)))
        self.assertEqual([r.id for r in p.rows], [100, 100])

    def test_reject_truncation_and_irregular_rows(self):
        for blob in (b"", self.old[:0x50], sample(values=(b"abc", b"efgh"))):
            with self.assertRaises(InvalidData):
                read_param("Test", blob)

    def test_commit_and_restore(self):
        tables, patches = self.stage()
        journal = self.engine.commit(tables, patches, "source-hash")
        self.assertEqual(self.mem.read(patches[0].address, 4), b"ABCD")
        self.assertEqual(json.loads(journal.with_suffix(".result.json").read_text())["outcome"], "committed")
        self.engine.restore(journal)
        self.assertEqual(self.mem.read(patches[0].address, 8), b"abcdefgh")
        self.assertFalse(self.mem.paused)

    def test_partial_write_rolls_back_attempted_row(self):
        self.mem.fail_at = 2
        tables, patches = self.stage()
        with self.assertRaisesRegex(RuntimeError, "partial write"):
            self.engine.commit(tables, patches, "hash")
        self.assertEqual(self.mem.read(patches[0].address, 8), b"abcdefgh")
        self.assertFalse(self.mem.paused)

    def test_rollback_failure_is_fatal(self):
        self.mem.fail_at, self.mem.fail_rollback = 2, True
        with self.assertRaises(FatalTransaction):
            self.engine.commit(*self.stage(), "hash")
        self.assertFalse(self.mem.paused)

    def test_pointer_relocation_cancels_without_writes(self):
        staged = self.stage()
        struct.pack_into("<Q", self.mem.buf, 0x40070, 0x51000)
        with self.assertRaises(RetryLater):
            self.engine.commit(*staged, "hash")
        self.assertEqual(self.mem.writes, [])

    def test_preflight_race_cancels_without_writes(self):
        tables, patches = self.stage()
        self.mem.buf[patches[-1].address] = 255
        with self.assertRaises(RetryLater):
            self.engine.commit(tables, patches, "hash")
        self.assertEqual(self.mem.writes, [])

    def test_structure_changes_rejected(self):
        for blob in (sample((1, 2)), sample((100,), (b"abcd",)), sample(values=(b"12345", b"67890"))):
            with self.assertRaises(InvalidData):
                self.engine.stage({"Test": read_param("Test", blob)})
        self.assertEqual(self.mem.writes, [])

    def test_reordered_unique_ids_commit_and_restore_by_live_index(self):
        param = read_param("Test", sample((200, 100), (b"EFGH", b"ABCD")))
        tables, patches = self.engine.stage({"Test": param})
        original_index = tables[0].index
        self.assertEqual([p.index for p in patches], [0, 1])
        journal = self.engine.commit(tables, patches, "reordered")
        self.assertEqual(self.mem.read(tables[0].base + tables[0].offsets[0], 8), b"ABCDEFGH")
        self.assertEqual(self.mem.read(tables[0].base + 0x40, len(original_index)), original_index)
        self.engine.restore(journal)
        self.assertEqual(self.mem.read(patches[0].address, 8), b"abcdefgh")

    def test_existing_only_skips_added_rows(self):
        self.engine.existing_only = True
        param = read_param("Test", sample((100, 200, 300), (b"ABCD", b"EFGH", b"IJKL")))
        tables, patches = self.engine.stage({"Test": param})
        self.engine.commit(tables, patches, "added")
        self.assertEqual(len(patches), 2)
        self.assertIn("300", self.engine.notices[0])
        self.assertEqual(self.mem.read(tables[0].base, 0x40), tables[0].header)
        self.assertEqual(self.mem.read(patches[0].address, 8), b"ABCDEFGH")

    def test_existing_only_retains_rows_missing_from_file(self):
        self.engine.existing_only = True
        param = read_param("Test", sample((100,), (b"ABCD",)))
        tables, patches = self.engine.stage({"Test": param})
        self.engine.commit(tables, patches, "removed")
        self.assertEqual(self.mem.read(patches[0].address, 8), b"ABCDefgh")
        self.assertIn("200", self.engine.notices[0])

    def test_existing_only_does_not_change_schema_or_match_ambiguous_duplicates(self):
        self.engine.existing_only = True
        for blob in (sample((100, 100)), sample(values=(b"12345", b"67890"))):
            with self.assertRaises(InvalidData):
                self.engine.stage({"Test": read_param("Test", blob)})
        self.assertEqual(self.mem.writes, [])

    def test_diagnostics_list_specific_added_and_missing_ids(self):
        with self.assertRaises(InvalidData) as error:
            self.engine.stage({"Test": read_param("Test", sample((100, 300)))})
        self.assertIn("Test", str(error.exception))
        self.assertIn("300", str(error.exception))
        self.assertIn("200", str(error.exception))
        self.assertEqual(self.mem.writes, [])

    def test_dry_run_does_not_pause_or_write(self):
        self.assertIsNone(self.engine.commit(*self.stage(), "hash", dry_run=True))
        self.assertFalse(self.mem.paused)
        self.assertEqual(self.mem.writes, [])

    def test_stale_session_journal_refused(self):
        journal = self.engine.commit(*self.stage(), "hash")
        self.mem.identity += 1
        with self.assertRaises(InvalidData):
            self.engine.restore(journal)

    def test_restore_refuses_subsequent_change(self):
        journal = self.engine.commit(*self.stage(), "hash")
        address = self.stage()[0][0].base + self.new.rows[0].offset
        self.mem.buf[address] = 254
        count = len(self.mem.writes)
        with self.assertRaises(RetryLater):
            self.engine.restore(journal)
        self.assertEqual(len(self.mem.writes), count)

    def test_bounded_zlib(self):
        payload = zlib.compress(b"x" * 2000)
        self.assertEqual(Decoder._zlib(payload, 2000), b"x" * 2000)
        with self.assertRaises(InvalidData):
            Decoder._zlib(payload, 1000)

    def test_deadline_cancels(self):
        staged = self.stage()
        with patch("sekiro_hotload.engine.time.monotonic", side_effect=[0, 3]):
            with self.assertRaises(RetryLater):
                self.engine.commit(*staged, "hash")
        self.assertEqual(self.mem.writes, [])


@unittest.skipUnless(os.name == "nt", "Windows only")
class WindowsIntegration(unittest.TestCase):
    def test_real_read_write_suspend_resume_and_watchdog(self):
        from sekiro_hotload.winmem import ProcessMemory
        code = """
import ctypes as C, json, os, time
b=C.create_string_buffer(16)
b.value=b'abcdefgh'
counter=C.c_uint64.from_address(C.addressof(b)+8)
print(json.dumps({'pid':os.getpid(),'address':C.addressof(b)}),flush=True)
while True:
    counter.value+=1
    time.sleep(.03)
"""
        child = subprocess.Popen([sys.executable, "-u", "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            info = json.loads(child.stdout.readline())
            mem = ProcessMemory(info["pid"])
            try:
                self.assertEqual(mem.read(info["address"], 8), b"abcdefgh")
                with mem.frozen():
                    tick = mem.read(info["address"] + 8, 8)
                    mem.write(info["address"], b"ABCDEFGH")
                    self.assertEqual(mem.read(info["address"], 8), b"ABCDEFGH")
                    time.sleep(.1)
                    self.assertEqual(mem.read(info["address"] + 8, 8), tick)
                self.assertTrue(mem.alive())
                # Controller exit without DONE (simulated by closing the guard's pipe).
                helper = ([os.environ["SEKIRO_TEST_WATCHDOG"]] if os.environ.get("SEKIRO_TEST_WATCHDOG") else
                          [sys.executable, str(Path(__file__).resolve().parents[1] / "hotload.py")])
                guard = subprocess.Popen(helper + ["--watchdog", str(mem.pid), str(mem.identity)],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                self.assertEqual(guard.stdout.readline().strip(), b"READY")
                self.assertGreaterEqual(mem.api.suspend(mem.handle), 0)
                guard.stdin.close()
                self.assertEqual(guard.wait(timeout=5), 0)
                guard.stdout.close()
                # Verify real thread execution after recovery, not merely process existence.
                count = int.from_bytes(mem.read(info["address"] + 8, 8), 'little')
                time.sleep(.2)
                self.assertGreater(int.from_bytes(mem.read(info["address"] + 8, 8), 'little'), count)
                self.assertTrue(mem.alive())
            finally:
                # Test cleanup must always resume, even if a test assertion failed.
                mem.api.resume(mem.handle)
                mem.close()
        finally:
            child.terminate()
            child.wait(timeout=5)
            child.stdout.close()


if __name__ == "__main__":
    unittest.main()
