"""Developer-only, read-only PE and singleton address investigation."""
import sys
sys.path.insert(0, '.native-deps')
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from pathlib import Path
import re, struct, json

path = Path(r'D:\SteamLibrary\steamapps\common\Sekiro\sekiro.exe')
raw = path.read_bytes()
pe = struct.unpack_from('<I', raw, 0x3c)[0]
count = struct.unpack_from('<H', raw, pe + 6)[0]
opt_size = struct.unpack_from('<H', raw, pe + 20)[0]
image = bytearray(struct.unpack_from('<I', raw, pe + 24 + 56)[0])
sections = {}
for i in range(count):
    pos = pe + 24 + opt_size + i * 40
    name = raw[pos:pos+8].rstrip(b'\0').decode()
    vsize, rva, size, offset = struct.unpack_from('<IIII', raw, pos+8)
    image[rva:rva+size] = raw[offset:offset+size]
    sections[name] = (rva, vsize)
md = Cs(CS_ARCH_X86, CS_MODE_64)
from sekiro_hotload.winmem import ProcessMemory, find_process
pid = find_process(path)
if pid:
    memory = ProcessMemory(pid, False)
    for name in ('.text', '.data', '.rdata'):
        rva, size = sections[name]
        image[rva:rva+size] = memory.read(memory.base+rva,size)
    from sekiro_hotload.engine import ptr
    for slot in (0x3ea1508,0x3f42d28,0x3d8e7c8):
        try:
            obj=ptr(memory,memory.base+slot); vt=ptr(memory,obj)
            col=ptr(memory,vt-8)
            td=struct.unpack('<I',memory.read(col+12,4))[0]
            name=memory.read(memory.base+td+16,100).split(b'\0')[0]
            print('SINGLETON',hex(slot),hex(obj),hex(vt),name)
        except Exception as e: print('slot',hex(slot),e)
    memory.close()
    print('Using live sections from PID',pid)
names = {m.start() for m in re.finditer(b'SprjTask\0', image)}
print('Task name RVAs:', [hex(x) for x in names])
print('Task RTTI names:',[(hex(m.start()),m.group().decode()) for m in re.finditer(rb'\.\?AVSprjTask[^\0]{0,60}\0',image)])
vts={}
for hit in re.finditer(rb'\.\?AVSprjTaskImp@NS_SPRJ@@\0',image):
    td=hit.start()-16
    for ref in re.finditer(re.escape(struct.pack('<I',td)),image):
        col=ref.start()-12
        if col>=0 and struct.unpack_from('<I',image,col)[0]==1 and struct.unpack_from('<I',image,col+20)[0]==col:
            for vref in re.finditer(re.escape(struct.pack('<Q',0x140000000+col)),image):
                vts[0x140000000+vref.start()+8]=hit.group().decode()
print('Task VTABLES:',{hex(k):v for k,v in vts.items()})
if pid and vts:
    memory=ProcessMemory(pid,False)
    drva,dsize=sections['.data']; checked={}
    for pos in range(drva,drva+dsize-8,8):
        obj=struct.unpack_from('<Q',image,pos)[0]
        if not 0x10000000<=obj<0x800000000000 or memory.base<=obj<memory.base+len(image): continue
        if obj not in checked:
            try: checked[obj]=struct.unpack('<Q',memory.read(obj,8))[0]
            except Exception: checked[obj]=0
        if checked[obj] in vts: print('TASK INSTANCE',hex(pos),hex(obj),hex(checked[obj]))
    memory.close()
for name in names:
    for token in (struct.pack('<Q',0x140000000+name),struct.pack('<I',name)):
        print('Name pointer references:',[hex(m.start()) for m in re.finditer(re.escape(token),image)][:20])
names |= {0x3ea1498,0x3de0a50,0x3ea14b0}
trva, tsize = sections['.text']
prva, psize = sections['.pdata']
functions = list(struct.iter_unpack('<III', bytes(image[prva:prva+psize-(psize%12)])))
for hit in re.finditer(b'[\x48\x4c]\x8d[\x0d\x15\x05]....', bytes(image[trva:trva+tsize]), re.DOTALL):
    ip = trva + hit.start()
    target = ip + 7 + struct.unpack_from('<i', image, ip+3)[0]
    if target in names:
        print('TASK XREF',hex(ip))
        start = next((begin for begin,end,_ in functions if begin<=ip<end),ip-70)
        for ins in md.disasm(bytes(image[start:ip+60]),0x140000000+start):
            print(hex(ins.address),ins.mnemonic,ins.op_str)
print('Register function:')
for ins in md.disasm(bytes(image[0x11df640:0x11df700]),0x1411df640):
    print(hex(ins.address),ins.mnemonic,ins.op_str)
if '--lookup' in sys.argv:
    import bisect
    starts=[x[0] for x in functions]
    candidates=set()
    for hit in re.finditer(rb'\x8b[\x40-\x7f]\xf0',bytes(image[trva:trva+tsize])):
        at=trva+hit.start(); idx=bisect.bisect_right(starts,at)-1
        if idx<0: continue
        begin,end,_=functions[idx]
        code=bytes(image[begin:end])
        if end-begin<2048 and re.search(rb'\x0f\xb7[\x40-\x7f]\x0a',code): candidates.add((begin,end))
    print('LOOKUP CANDIDATES',len(candidates))
    for begin,end in sorted(candidates):
        print('LOOKUP FUNCTION',hex(begin),hex(end))
        for ins in md.disasm(bytes(image[begin:end]),0x140000000+begin):
            print(hex(ins.address),ins.mnemonic,ins.op_str)
if '--details' in sys.argv:
    for target in (0x5483f0,0x61e220):
        begin,end,_=next((f for f in functions if f[0]<=target<f[1]),(target,target+192,0))
        print('DETAIL',hex(begin),hex(end))
        for ins in md.disasm(bytes(image[begin:end]),0x140000000+begin):
            print(hex(ins.address),ins.mnemonic,ins.op_str)
            if ins.mnemonic=='ret' and target==0x5483f0: break
