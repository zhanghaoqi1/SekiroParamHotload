// Included after Shared/game/readable declarations. No external C++ runtime.
#include <string.h>

struct Operation {
    uint64_t cap, expected_data, expected_size, staging, staging_size, file_size, allocator, new_data;
    uint32_t old_count, new_count, row_size, flags;
};
static_assert(sizeof(Operation)==80,"operation ABI mismatch");
struct Retired { uintptr_t cap, data; uint64_t size, bytes; uint32_t row_size; };
static Retired retired[8192];
static uint32_t retired_count=0;
static const uint64_t RETAIN_LIMIT=512ULL*1024*1024;
struct Lookup { uint32_t id,index; };
static uint16_t row_count(uintptr_t p) { return *(uint16_t*)(p+0xa); }
static uint32_t row_id(uintptr_t p,uint32_t i) { return *(uint32_t*)(p+0x40+i*24); }
static uintptr_t row_ptr(uintptr_t p,uint32_t i) { return p+*(uint64_t*)(p+0x48+i*24); }
static Lookup* lookup(uintptr_t p) { return (Lookup*)(p+((*(uint32_t*)(p-16)+15ULL)&~15ULL)); }
static bool valid_file(uintptr_t p,uint64_t size,uint32_t width) {
    if(size<65 || size>256ULL*1024*1024 || !readable((void*)(p-16),size+16,false)) return false;
    uint32_t flags=*(uint32_t*)(p+0x2c), count=row_count(p);
    if(((flags>>8)&127)!=5 || !(flags&(1<<17)) || !(flags&(1<<15)) || 0x40+count*24ULL>size) return false;
    if(*(uint32_t*)(p-16)!=size) return false;
    auto table=lookup(p);
    if(!readable(table,count*8ULL,false)) return false;
    auto seen=(uint8_t*)HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,count?count:1);
    if(!seen) return false;
    bool good=true;
    for(uint32_t i=0;i<count;++i) {
        auto index=table[i].index;
        if(index>=count || seen[index] || table[i].id!=row_id(p,index) || (i && table[i-1].id>table[i].id)) { good=false; break; }
        seen[index]=1;
        uint64_t off=*(uint64_t*)(p+0x48+i*24);
        if(off<0x40+count*24ULL || off>size || width>size-off) { good=false; break; }
    }
    uint64_t name=*(uint64_t*)(p+0x10);
    // The type name may precede a large row-name area. Bound the string scan,
    // not the entire remaining string section (BehaviorParam/Bullet exceed 64 KiB).
    size_t name_limit=name<size?(size_t)((size-name)<65536?(size-name):65536):0;
    if(name<0x40 || !name_limit || !memchr((void*)(p+name),0,name_limit)) good=false;
    HeapFree(GetProcessHeap(),0,seen);
    return good;
}
static uintptr_t native_query(uintptr_t file,uint32_t id) {
    struct Wrapper { uintptr_t unused,data; } wrapper{0,file};
    using Find=uintptr_t(*)(Wrapper*,uint32_t);
    return ((Find)(game+0x61e220))(&wrapper,id);
}
static int query_command() {
    uintptr_t cap=shared->arg0;
    if(!readable((void*)cap,0x78,false)) return -60;
    uintptr_t data=*(uintptr_t*)(cap+0x70); uint64_t size=*(uint64_t*)(cap+0x68);
    if(!valid_file(data,size,1)) return -61;
    shared->result=native_query(data,(uint32_t)shared->arg1);
    return 0;
}
// Map duplicate rows by their occurrence in descriptor order. This matches the
// existing row-edit behavior without conflating row index and parameter ID.
static uintptr_t corresponding(uintptr_t source,uintptr_t old,uint32_t index) {
    uint32_t id=row_id(old,index), occurrence=0;
    auto old_table=lookup(old); uint32_t count=row_count(old), lo=0,hi=count;
    while(lo<hi) { uint32_t mid=(lo+hi)/2; if(old_table[mid].id<id) lo=mid+1; else hi=mid; }
    for(uint32_t i=lo;i<count && old_table[i].id==id;++i) if(old_table[i].index<index) ++occurrence;
    auto table=lookup(source); count=row_count(source); lo=0;hi=count;
    while(lo<hi) { uint32_t mid=(lo+hi)/2; if(table[mid].id<id) lo=mid+1; else hi=mid; }
    // Candidate images sort ties by descriptor index, so occurrence maps directly.
    uint32_t found=lo+occurrence;
    if(found>=count || table[found].id!=id) return 0;
    return row_ptr(source,table[found].index);
}
struct Undo { uintptr_t destination, source; uint32_t size; uint64_t offset; };
static Undo* undo_items=nullptr;
static uint32_t undo_count=0, undo_capacity=0;
static uint64_t undo_bytes=0;
static bool add_mirrors(uintptr_t old,uint64_t size,uintptr_t source,uint32_t width) {
    if(!valid_file(old,size,width)) return false;
    uint32_t count=row_count(old);
    for(uint32_t i=0;i<count;++i) {
        uintptr_t src=corresponding(source,old,i), dest=row_ptr(old,i);
        if(!src || !memcmp((void*)dest,(void*)src,width)) continue;
        if(!readable((void*)dest,width,true) || undo_bytes+width>256ULL*1024*1024 || undo_count>=1000000) return false;
        if(undo_count==undo_capacity) {
            uint32_t cap=undo_capacity?undo_capacity*2:1024;
            auto next=(Undo*)(undo_items?HeapReAlloc(GetProcessHeap(),0,undo_items,cap*sizeof(Undo)):
                                               HeapAlloc(GetProcessHeap(),0,cap*sizeof(Undo)));
            if(!next) return false;
            undo_items=next;undo_capacity=cap;
        }
        undo_items[undo_count++]={dest,src,width,undo_bytes}; undo_bytes+=width;
    }
    return true;
}
static void free_game_block(uintptr_t allocator,void* block) {
    using Free=void(*)(void*,void*);
    auto vt=*(uintptr_t**)allocator;
    ((Free)vt[13])((void*)allocator,block);
}
static int apply_operations() {
    const ULONGLONG started=GetTickCount64();
    auto ops=(Operation*)((uint8_t*)shared+128);
    uint32_t n=shared->count;
    if(n==0 || n>136) return -30;
    void* allocated[136]{};
    uint64_t extra_retained=0;
    uint32_t structural=0;
    undo_count=0;undo_bytes=0;
    int status=0;
    // Validate every table and source before making changes to game data.
    for(uint32_t i=0;i<n;++i) {
        shared->error_index=i;
        auto& op=ops[i]; op.new_data=0;
        for(uint32_t k=0;k<i;++k) if(ops[k].cap==op.cap) return -47;
        if(op.flags>1) {status=-32;break;}
        if(!readable((void*)op.cap,0x78,true) || *(uintptr_t*)op.cap!=game+0x3280eb0 ||
           *(uintptr_t*)(op.cap+0x70)!=op.expected_data || *(uint64_t*)(op.cap+0x68)!=op.expected_size) {status=-31;break;}
        if(!op.row_size || op.row_size>65536 || op.staging_size<80 || op.staging_size>256ULL*1024*1024 ||
           !readable((void*)op.staging,(size_t)op.staging_size,false)) {status=-32;break;}
        uintptr_t source=op.staging+16;
        if(!valid_file(source,op.file_size,op.row_size)) {status=-330;break;}
        if(!valid_file(op.expected_data,op.expected_size,op.row_size)) {status=-331;break;}
        if(row_count(source)!=op.new_count || row_count(op.expected_data)!=op.old_count) {status=-332;break;}
        if(memcmp((void*)(source+6),(void*)(op.expected_data+6),4) ||
           memcmp((void*)(source+0x2c),(void*)(op.expected_data+0x2c),4)) {status=-34;break;}
        uint64_t source_name=*(uint64_t*)(source+0x10),old_name=*(uint64_t*)(op.expected_data+0x10);
        if(strcmp((char*)(source+source_name),(char*)(op.expected_data+old_name))) {status=-34;break;}
        uintptr_t owner=*(uintptr_t*)(op.cap+0x48);
        if(!readable((void*)owner,16,false) || *(uintptr_t*)(owner+8)!=op.allocator ||
           !readable((void*)op.allocator,8,false) || *(uintptr_t*)op.allocator!=game+0x2bb9b80) {status=-35;break;}
        if(!(op.flags&1)) {
            ++structural;
            uint64_t old_bytes=16+((op.expected_size+15)&~15ULL)+op.old_count*8ULL;
            extra_retained+=old_bytes>op.staging_size?old_bytes:op.staging_size;
        }
        else {
            if(op.new_count!=op.old_count) {status=-36;break;}
            for(uint32_t k=0;k<op.old_count;++k) if(!corresponding(source,op.expected_data,k)) {status=-36;break;}
            if(status) break;
        }
        uint64_t required=16+((op.file_size+15)&~15ULL)+op.new_count*8ULL;
        if(required!=op.staging_size) {status=-37;break;}
    }
    if(!status && (retired_count+structural>8192 || shared->retained_bytes+extra_retained>RETAIN_LIMIT)) status=-38;
    // Build every replacement with the actual game allocator, on the game frame thread.
    if(!status) for(uint32_t i=0;i<n;++i) {
        shared->error_index=i;auto& op=ops[i];
        if(!(op.flags&1)) {
            using Allocate=void*(*)(void*,size_t,size_t);
            auto vt=*(uintptr_t**)op.allocator;
            allocated[i]=((Allocate)vt[10])((void*)op.allocator,(size_t)op.staging_size,16);
            if(!allocated[i] || !readable(allocated[i],(size_t)op.staging_size,true)) {status=-39;break;}
            memcpy(allocated[i],(void*)op.staging,(size_t)op.staging_size);
            op.new_data=(uintptr_t)allocated[i]+16;
            if(!valid_file(op.new_data,op.file_size,op.row_size)) {status=-40;break;}
            // The game's own ID query routine must agree with every lookup entry.
            for(uint32_t k=0;k<op.new_count;++k) {
                uint32_t id=row_id(op.new_data,k);uintptr_t got=native_query(op.new_data,id);
                auto table=lookup(op.new_data); bool matched=false;
                uint32_t lo=0,hi=op.new_count;
                while(lo<hi) {uint32_t mid=(lo+hi)/2;if(table[mid].id<id) lo=mid+1;else hi=mid;}
                for(uint32_t q=lo;q<op.new_count && table[q].id==id;++q)
                    if(got==row_ptr(op.new_data,table[q].index)) {matched=true;break;}
                if(!matched) {status=-41;break;}
            }
            if(status) break;
        }
        uintptr_t source=op.staging+16;
        if(!add_mirrors(op.expected_data,op.expected_size,source,op.row_size)) {status=-42;break;}
        for(uint32_t k=0;k<retired_count;++k) if(retired[k].cap==op.cap && retired[k].row_size==op.row_size) {
            if(!add_mirrors(retired[k].data,retired[k].size,source,op.row_size)) {status=-43;break;}
        }
        if(status) break;
        if(GetTickCount64()-started>1500) {status=-46;break;}
    }
    uint8_t* before=nullptr;
    if(!status && undo_bytes) {
        before=(uint8_t*)HeapAlloc(GetProcessHeap(),0,(size_t)undo_bytes);
        if(!before) status=-44;
    }
    if(!status && GetTickCount64()-started>1500) status=-46;
    if(!status) {
        for(uint32_t i=0;i<undo_count;++i) {auto& u=undo_items[i];memcpy(before+u.offset,(void*)u.destination,u.size);}
        for(uint32_t i=0;i<undo_count;++i) {auto& u=undo_items[i];memcpy((void*)u.destination,(void*)u.source,u.size);}
        MemoryBarrier();
        for(uint32_t i=0;i<n;++i) if(!(ops[i].flags&1)) {
            *(uint64_t*)(ops[i].cap+0x68)=ops[i].file_size;
            InterlockedExchangePointer((PVOID volatile*)(ops[i].cap+0x70),(void*)ops[i].new_data);
        }
        MemoryBarrier();
        bool committed=true;
        for(uint32_t i=0;i<n;++i) if(!(ops[i].flags&1) && *(uintptr_t*)(ops[i].cap+0x70)!=ops[i].new_data) committed=false;
        for(uint32_t i=0;i<undo_count && committed;++i) {
            auto& u=undo_items[i];if(memcmp((void*)u.destination,(void*)u.source,u.size)) committed=false;
        }
        if(!committed) {
            for(uint32_t i=0;i<n;++i) if(!(ops[i].flags&1)) {
                *(uint64_t*)(ops[i].cap+0x68)=ops[i].expected_size;
                InterlockedExchangePointer((PVOID volatile*)(ops[i].cap+0x70),(void*)ops[i].expected_data);
                // A published allocation may already have readers: retain it on rollback too.
                retired[retired_count++]={ops[i].cap,ops[i].new_data,ops[i].file_size,ops[i].staging_size,ops[i].row_size};
                shared->retained_bytes+=ops[i].staging_size;allocated[i]=nullptr;
            }
            for(uint32_t i=0;i<undo_count;++i) {auto& u=undo_items[i];memcpy((void*)u.destination,before+u.offset,u.size);}
            status=-45;
        } else {
            for(uint32_t i=0;i<n;++i) if(!(ops[i].flags&1)) {
                uint64_t bytes=16+((ops[i].expected_size+15)&~15ULL)+ops[i].old_count*8ULL;
                retired[retired_count++]={ops[i].cap,ops[i].expected_data,ops[i].expected_size,bytes,ops[i].row_size};
                shared->retained_bytes+=bytes;allocated[i]=nullptr;
            }
            shared->result=undo_count;
        }
    }
    for(uint32_t i=0;i<n;++i) if(allocated[i]) free_game_block(ops[i].allocator,allocated[i]);
    if(before) HeapFree(GetProcessHeap(),0,before);
    return status;
}
