from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from test_hotload import FakeMemory, PROFILE, sample
from test_runtime import template
from sekiro_hotload.formats import InvalidData, read_param
from sekiro_hotload.runtime import build_runtime_image
from sekiro_hotload.structural import StructuralEngine


class StructuralTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old = replace(read_param('Test', sample()), header=template())
        image = build_runtime_image(self.old, template(), b'\0'*16)
        self.mem = FakeMemory(sample())
        self.mem.buf[0x50000-16:0x50000-16+len(image.allocation)] = image.allocation
        struct.pack_into('<Q', self.mem.buf,0x40068,image.file_size)
        struct.pack_into('<Q', self.mem.buf,0x40048,0x41000)
        struct.pack_into('<Q', self.mem.buf,0x41008,0x42000)
        self.engine = StructuralEngine(self.mem, PROFILE, Path(self.temp.name), 'test',resource_root=Path('.'))

    def source(self, ids, values):
        return replace(read_param('Test',sample(ids,values)),header=template())

    def test_add_delete_and_id_change_are_real_structure_plans(self):
        for ids, values, added, deleted in [((100,200,300),(b'abcd',b'efgh',b'ijkl'),1,0),
                                           ((100,),(b'abcd',),0,1),
                                           ((100,300),(b'abcd',b'efgh'),1,1)]:
            self.engine.stage({'Test':self.source(ids,values)})
            self.assertTrue(self.engine.plans[0].structural)
            self.assertEqual(self.engine.stats['added'],added)
            self.assertEqual(self.engine.stats['deleted'],deleted)
        self.assertEqual(self.mem.writes,[])

    def test_order_only_change_produces_a_transaction(self):
        tables, patches = self.engine.stage({'Test':self.source((200,100),(b'efgh',b'abcd'))})
        self.assertTrue(patches)
        self.assertTrue(self.engine.plans[0].structural)
        self.assertEqual(self.engine.stats['reordered'],1)

    def test_value_only_edits_preserve_live_allocation(self):
        self.engine.stage({'Test':self.source((100,200),(b'ABCD',b'efgh'))})
        self.assertFalse(self.engine.plans[0].structural)

    def test_dry_run_never_loads_native_module(self):
        tables, patches = self.engine.stage({'Test':self.source((100,),(b'abcd',))})
        with patch.object(self.engine,'native',side_effect=AssertionError('must not inject')):
            self.assertIsNone(self.engine.commit(tables,patches,'hash',dry_run=True))
        self.assertEqual(self.mem.writes,[])

    def test_incompatible_width_cancels_before_native_load(self):
        with self.assertRaises(InvalidData):
            self.engine.stage({'Test':self.source((100,),(b'12345678',))})
        self.assertEqual(self.mem.writes,[])

    def test_native_duplicate_mapping_keeps_occurrence_order(self):
        value = self.source((100,100,200),(b'abcd',b'ABCD',b'efgh'))
        self.engine.stage({'Test':value})
        self.assertEqual(self.engine.stats['added'],1)
        self.assertTrue(self.engine.plans[0].structural)

    def test_empty_row_set_is_a_structural_delete(self):
        self.engine.stage({'Test':self.source((),())})
        plan = self.engine.plans[0]
        self.assertEqual((plan.new_count,plan.width),(0,4))
        self.assertEqual(self.engine.stats['deleted'],2)

    def test_native_abi_sizes(self):
        import ctypes
        from sekiro_hotload.native import Operation, Shared
        self.assertEqual(ctypes.sizeof(Operation),80)
        self.assertEqual(ctypes.sizeof(Shared),128)
