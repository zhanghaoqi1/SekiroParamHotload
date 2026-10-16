// Compile as a local test EXE. Nothing is injected or called in the game.
// Reuse the exact production validator and VirtualQuery-based range checker.
#include "agent.cpp"
#include <stdio.h>

static uint8_t fixture[0x12000];
static void make_fixture() {
    memset(fixture,0,sizeof(fixture));
    auto p=fixture+16;
    *(uint32_t*)fixture=sizeof(fixture)-16;
    *(uint32_t*)(p+0x2c)=0x00078500;
    *(uint16_t*)(p+0xa)=1;
    *(uint32_t*)(p+0x40)=100;
    *(uint64_t*)(p+0x48)=0x60;
    *(uint64_t*)(p+0x10)=0x64;
    memcpy(p+0x64,"TEST_PARAM_ST",14);
    // lookup is outside the file area, so reserve a separate full buffer below.
}

int main(int argc,char** argv) {
    if(argc==3) {
        FILE* file=fopen(argv[1],"rb");if(!file) return 10;
        fseek(file,0,SEEK_END);long size=ftell(file);rewind(file);
        auto block=(uint8_t*)HeapAlloc(GetProcessHeap(),0,size);
        if(!block || fread(block,1,size,file)!=(size_t)size) return 11;
        fclose(file);
        unsigned width=0;if(sscanf(argv[2],"%u",&width)!=1) return 12;
        bool result=valid_file((uintptr_t)(block+16),*(uint32_t*)block,width);
        printf("production-validator fixture: %s\n",result?"PASS":"FAIL");
        HeapFree(GetProcessHeap(),0,block);
        return result?0:13;
    }
    make_fixture();
    const size_t file_size=sizeof(fixture)-16;
    auto block=(uint8_t*)HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,sizeof(fixture)+16);
    memcpy(block,fixture,sizeof(fixture));
    auto p=block+16;
    auto table=(Lookup*)(p+((file_size+15)&~15ULL));
    table[0]={100,0};
    if(!valid_file((uintptr_t)p,file_size,4)) return 1;
    // A genuinely unterminated type string still fails, even in a long section.
    memset(p+0x64,'x',65536);
    if(valid_file((uintptr_t)p,file_size,4)) return 2;
    // Names pointing into a header or outside the file remain invalid.
    *(uint64_t*)(p+0x10)=0;
    if(valid_file((uintptr_t)p,file_size,4)) return 3;
    *(uint64_t*)(p+0x10)=file_size;
    if(valid_file((uintptr_t)p,file_size,4)) return 4;
    printf("long string-area regression and malformed names: PASS\n");
    HeapFree(GetProcessHeap(),0,block);
    return 0;
}
