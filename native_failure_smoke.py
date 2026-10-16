"""Confirm stale-pointer rejection makes no game change and allows reconnect."""
from dataclasses import replace
import json
from pathlib import Path

from hotload import DEFAULT_GAME
from sekiro_hotload.engine import RetryLater
from sekiro_hotload.formats import read_param, Row
from sekiro_hotload.native import Operation
from sekiro_hotload.runtime import inspect_table
from sekiro_hotload.structural import StructuralEngine
from sekiro_hotload.winmem import ProcessMemory, find_process

root=Path(__file__).resolve().parent
profile=json.loads((root/'profile.json').read_text(encoding='utf-8'))
mem=ProcessMemory(find_process(Path(DEFAULT_GAME)/'sekiro.exe'))
engine=StructuralEngine(mem,profile,root/'state/native-tests',profile['expectedExeSha256'],resource_root=root)
try:
    info=inspect_table(engine,'AtkParam_Pc')
    address=int(info['param_address'],16)
    before=mem.read(address-16,16+info['lookup_end'])
    original=read_param('AtkParam_Pc',mem.read(address,info['resource_size']))
    candidate=replace(original,rows=original.rows+(Row(0x7fff0003,0,original.rows[0].data),))
    engine.stage({'AtkParam_Pc':candidate})
    p=engine.plans[0]
    op=Operation(cap=p.cap,expected_data=p.address+16,expected_size=p.size,file_size=p.file_size,
                 allocator=p.allocator,old_count=p.old_count,new_count=p.new_count,row_size=p.width,flags=0)
    try:
        engine.native().apply([op],[p.after])
        raise AssertionError('stale pointer accepted')
    except RetryLater:
        pass
    assert engine.locate(p.name)==address
    assert mem.read(address-16,len(before))==before
    engine.close()
    assert engine.native().request(1)>0
    result=dict(pid=mem.pid,stale_pointer_rejected=True,unchanged=True,reconnect=True)
    (root/'state/native-tests/rejection-validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
finally:
    engine.close()
    mem.close()
