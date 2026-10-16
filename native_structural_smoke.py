"""Live, reversible structural reload check using an unused temporary row ID."""
from dataclasses import replace
import json
from pathlib import Path
import time

from hotload import DEFAULT_GAME
from sekiro_hotload.engine import sha256
from sekiro_hotload.formats import read_param, Row
from sekiro_hotload.runtime import inspect_table
from sekiro_hotload.structural import StructuralEngine
from sekiro_hotload.winmem import ProcessMemory, find_process

root = Path(__file__).resolve().parent
profile = json.loads((root / 'profile.json').read_text(encoding='utf-8'))
pid = find_process(Path(DEFAULT_GAME) / 'sekiro.exe')
if pid is None:
    raise RuntimeError('Game is not running')
mem = ProcessMemory(pid)
engine = StructuralEngine(mem, profile, root/'state/native-tests', profile['expectedExeSha256'], resource_root=root)
name, test_id = 'AtkParam_Pc', 0x7fff0000
original = None
events = []

def current():
    info = inspect_table(engine, name)
    return info, read_param(name, mem.read(int(info['param_address'],16), info['resource_size']))

def apply(param, label):
    start = time.monotonic()
    tables, patches = engine.stage({name:param})
    journal = engine.commit(tables, patches, 'live-unused-row-test')
    info, actual = current()
    assert engine.semantic(actual) == engine.semantic(param)
    found = engine.native().request(4, int(info['resource_cap'],16), test_id)
    assert bool(found) == any(r.id == test_id for r in param.rows)
    event = dict(step=label, rows=info['row_count'], native_lookup=hex(found),
                 stats=dict(engine.stats), journal=str(journal), elapsed_ms=round((time.monotonic()-start)*1000,1))
    events.append(event)
    print(json.dumps(event), flush=True)
    return journal, found

try:
    info, original = current()
    assert all(r.id != test_id for r in original.rows)
    baseline_address = int(info['param_address'],16)
    baseline_row = baseline_address + original.rows[0].offset
    baseline_bytes = mem.read(baseline_row, original.row_size)
    row = Row(test_id, 0, original.rows[0].data)
    added = replace(original, rows=original.rows+(row,))
    journal, cached_new_row = apply(added, 'add')
    assert mem.read(baseline_row, original.row_size) == baseline_bytes
    # Change only the unused row and verify the value-only path.
    edited_data = bytearray(row.data)
    edited_data[0] ^= 1
    edited = replace(added, rows=added.rows[:-1]+(Row(test_id,0,bytes(edited_data)),))
    apply(edited, 'edit-added-row')
    assert mem.read(cached_new_row, original.row_size) == bytes(edited_data)
    apply(original, 'delete')
    assert mem.read(cached_new_row, original.row_size) == bytes(edited_data)
    second = replace(original, rows=original.rows+(row, Row(test_id+1,0,row.data)))
    apply(second, 'add-two-unused-rows')
    reversed_unused = replace(second,rows=original.rows+(second.rows[-1],second.rows[-2]))
    apply(reversed_unused, 'reorder-unused-rows')
    renamed = replace(original, rows=original.rows+(Row(test_id+1,0,row.data),))
    apply(renamed, 'change-id')
    apply(original, 'remove-renamed-row')
    duplicates = replace(original,rows=original.rows+(row,row))
    apply(duplicates, 'duplicate-unused-id')
    apply(original, 'remove-duplicate-unused-id')
    # Re-add, then restore using the saved format-2 journal.
    journal, _ = apply(added, 're-add')
    restored = engine.restore(journal)
    info, actual = current()
    assert engine.semantic(actual) == engine.semantic(original)
    assert engine.native().request(4,int(info['resource_cap'],16),test_id) == 0
    events.append(dict(step='journal-restore',rows=info['row_count'],journal=str(restored)))
    assert mem.read(baseline_row,original.row_size) == baseline_bytes
    result = dict(pid=pid,table=name,test_id=test_id,events=events,restored=True,
                  callback_tid=engine.bridge.shared.callback_tid,retained_bytes=engine.bridge.shared.retained_bytes)
    (root/'state/native-tests').mkdir(parents=True,exist_ok=True)
    (root/'state/native-tests/live-validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)
finally:
    if original is not None and mem.alive():
        _, actual = current()
        if engine.semantic(actual) != engine.semantic(original):
            apply(original, 'finally-restore')
    engine.close()
    mem.close()
