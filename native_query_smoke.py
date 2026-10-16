"""Validate the game's native row-data-by-ID query against the current PARAM."""
from pathlib import Path
import json
from hotload import DEFAULT_GAME
from sekiro_hotload.winmem import ProcessMemory, find_process
from sekiro_hotload.native import NativeBridge
from sekiro_hotload.engine import Engine
from sekiro_hotload.runtime import inspect_table
from sekiro_hotload.formats import number

root=Path(__file__).resolve().parent
profile=json.loads((root/'profile.json').read_text())
mem=ProcessMemory(find_process(Path(DEFAULT_GAME)/'sekiro.exe'))
bridge=None
try:
    engine=Engine(mem,profile,root/'state',profile['expectedExeSha256'])
    info=inspect_table(engine,'AtkParam_Pc')
    address=int(info['param_address'],16); cap=int(info['resource_cap'],16)
    row_id=number(mem.read(address+0x40,4),0,'<I')
    bridge=NativeBridge(mem,root/'native/SekiroParamAgent12-full3.dll','full3',profile['expectedExeSha256'])
    present=bridge.request(4,cap,row_id)
    absent=bridge.request(4,cap,0x7fff0000)
    print(json.dumps({'pid':mem.pid,'existing_id':row_id,'native_existing_result':hex(present),
                      'native_missing_result':hex(absent),'param_name_ptr':hex(address+number(mem.read(address+0x10,8),0,'<Q')),
                      'first_data_field':hex(address+number(mem.read(address+0x48,8),0,'<Q')),
                      'first_name_field':hex(address+number(mem.read(address+0x50,8),0,'<Q'))},indent=2))
    assert present and not absent
finally:
    if bridge: bridge.close()
    mem.close()
