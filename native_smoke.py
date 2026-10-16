"""Live native task heartbeat verification; no PARAM writes."""
from pathlib import Path
import json, time
import argparse
from hotload import DEFAULT_GAME
from sekiro_hotload.winmem import ProcessMemory, find_process
from sekiro_hotload.native import NativeBridge

root = Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--tag',default='ping1')
parser.add_argument('--allocator-probe',action='store_true')
args=parser.parse_args()
profile = json.loads((root/'profile.json').read_text())
pid = find_process(Path(DEFAULT_GAME)/'sekiro.exe')
if not pid:
    raise RuntimeError('Game not running')
mem = ProcessMemory(pid)
bridge = None
try:
    bridge = NativeBridge(mem, root/f'native/SekiroParamAgent12-{args.tag}.dll', args.tag, profile['expectedExeSha256'])
    before = bridge.shared.heartbeat
    value = bridge.request(1)
    if args.allocator_probe:
        from sekiro_hotload.engine import Engine,ptr
        engine=Engine(mem,profile,root/'state',profile['expectedExeSha256'])
        repo=ptr(mem,mem.base+engine.repo_rva)
        holder=ptr(mem,repo+engine.offsets['AtkParam_Pc'])
        cap=ptr(mem,holder+0x70)
        owner=ptr(mem,cap+0x48)
        allocator=ptr(mem,owner+8)
        print('Allocator test allocated and freed:',hex(bridge.request(2,allocator)))
    time.sleep(.2)
    print(json.dumps({'pid':pid,'callback_tid':bridge.shared.callback_tid,
                      'bootstrap_tid':bridge.shared.bootstrap_tid,
                      'heartbeat_before':before,'ping_result':value,'heartbeat_after':bridge.shared.heartbeat},indent=2))
finally:
    if bridge: bridge.close()
    mem.close()
