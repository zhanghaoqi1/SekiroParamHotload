"""Exercise the packaged file watcher with a temporary .param; restore baseline."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import time

from hotload import DEFAULT_GAME
from sekiro_hotload.engine import Engine
from sekiro_hotload.formats import read_param, Row
from sekiro_hotload.runtime import inspect_table, build_runtime_image
from sekiro_hotload.winmem import ProcessMemory, find_process

root = Path(__file__).resolve().parent
profile = json.loads((root/'profile.json').read_text(encoding='utf-8'))
mem = ProcessMemory(find_process(Path(DEFAULT_GAME)/'sekiro.exe'),writable=False)
engine = Engine(mem,profile,root/'state',profile['expectedExeSha256'])
info = inspect_table(engine,'AtkParam_Pc')
baseline = mem.read(int(info['param_address'],16),info['resource_size'])
param = read_param('AtkParam_Pc',baseline)
test_id = 0x7fff0002
assert all(r.id!=test_id for r in param.rows)
changed = replace(param,rows=param.rows+(Row(test_id,0,param.rows[0].data),))
image = build_runtime_image(changed,param.header,bytes.fromhex(info['metadata_hex']))
state = root/'state/packaged-watch-test'
state.mkdir(parents=True,exist_ok=True)
process = None

def wait_rows(expected):
    until = time.monotonic()+20
    while time.monotonic()<until:
        report=inspect_table(engine,'AtkParam_Pc')
        actual=read_param('AtkParam_Pc',mem.read(int(report['param_address'],16),report['resource_size']))
        if len(actual.rows)==len(expected.rows) and all((a.id,a.data)==(b.id,b.data) for a,b in zip(actual.rows,expected.rows)):
            return report
        if process.poll() is not None:
            raise RuntimeError('Watcher exited: '+str(process.returncode))
        time.sleep(.1)
    raise TimeoutError('Watcher did not publish expected rows')

with tempfile.TemporaryDirectory(prefix='sekiro-watch-') as folder, (state/'stdout.log').open('w',encoding='utf-8') as log:
    source = Path(folder)/'AtkParam_Pc.param'
    source.write_bytes(baseline)
    process = subprocess.Popen([str(root/'dist/SekiroParamHotload-1.2.1/SekiroParamHotload.exe'),
         '--gui-worker','--full-reload','--source',str(source),'--state-dir',str(state),'--tables','AtkParam_Pc'],
         stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,text=True,creationflags=0x08000000)
    try:
        # Wait for the initial sync, then change the actual watched file.
        until=time.monotonic()+15
        while '同步完成' not in (state/'stdout.log').read_text(encoding='utf-8'):
            if process.poll() is not None or time.monotonic()>until:
                raise RuntimeError('Initial watcher sync failed')
            time.sleep(.1)
        source.write_bytes(image.allocation[16:16+image.file_size])
        add=wait_rows(changed)
        # Wait through multiple generation checks to catch self-invalidating snapshots.
        time.sleep(3)
        wait_rows(changed)
        source.write_bytes(baseline)
        delete=wait_rows(param)
        process.stdin.write('STOP\n'); process.stdin.flush()
        code=process.wait(timeout=15)
        assert code==0
        result=dict(pid=mem.pid,added_count=add['row_count'],restored_count=delete['row_count'],exit_code=code)
        (state/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result))
    finally:
        source.write_bytes(baseline)
        if process.poll() is None:
            wait_rows(param)
            process.stdin.write('STOP\n');process.stdin.flush()
            process.wait(timeout=15)
        process.stdin.close()
        mem.close()
