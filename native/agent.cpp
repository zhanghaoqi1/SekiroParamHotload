// Original Sekiro 1.06 runtime bridge. Native task interface layout is documented
// by the public FromSoftware-rs SDK; no SDK code is linked into this module.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stddef.h>
#include "build_tag.h"

#ifndef AGENT_TAG
#define AGENT_TAG L"ping1"
#endif

struct Shared {
    uint64_t magic;
    uint32_t abi, pid;
    volatile LONG state, status;
    uint32_t command, count;
    volatile LONG64 heartbeat;
    uint32_t callback_tid, bootstrap_tid;
    uint64_t sequence, retained_bytes, result;
    uint64_t arg0, arg1, arg2, arg3;
    uint32_t error_index, ready;
    uint8_t reserved[16];
};
static_assert(sizeof(Shared) == 128, "protocol header mismatch");
static Shared* shared = nullptr;
static uintptr_t game = 0;
static HMODULE self_module;
static bool readable(const void*, size_t, bool);
static int allocator_probe();
#include "param_engine.h"
struct Task { void** vtable; uintptr_t unknown; uint8_t storage[48]; };
static uintptr_t runtime_class(Task*) { return 0; }
static void destroy_task(Task*) {} // static, pinned for this game process lifetime
static void execute_task(Task*, void*) {
    if (!shared) return;
    shared->callback_tid = GetCurrentThreadId();
    InterlockedIncrement64(&shared->heartbeat);
    shared->ready = 1;
    if (InterlockedCompareExchange(&shared->state, 2, 1) != 1) return;
    shared->status = 0;
    shared->result = shared->heartbeat;
    if (shared->command == 2) shared->status = allocator_probe();
    else if(shared->command==3) shared->status=apply_operations();
    else if(shared->command==4) shared->status=query_command();
    else if(shared->command==5) {
        shared->result=0;
        for(uint32_t i=0;i<retired_count;++i) if(retired[i].cap==shared->arg0) shared->result=retired[i].row_size;
    }
    else if (shared->command != 1) shared->status = -1;
    MemoryBarrier();
    InterlockedExchange(&shared->state, 3);
}
static void* task_vtable[] = {(void*)runtime_class, (void*)destroy_task, (void*)execute_task};
static Task task = {task_vtable, 0, {}};

static bool readable(const void* address, size_t size, bool write = false) {
    uintptr_t at = (uintptr_t)address, end = at + size;
    if (at < 0x10000 || end < at || end > 0x800000000000ULL) return false;
    while (at < end) {
        MEMORY_BASIC_INFORMATION info{};
        if (!VirtualQuery((void*)at, &info, sizeof(info)) || info.State != MEM_COMMIT ||
            (info.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return false;
        if (write && !(info.Protect & (PAGE_READWRITE | PAGE_WRITECOPY))) return false;
        at = (uintptr_t)info.BaseAddress + info.RegionSize;
    }
    return true;
}

static int allocator_probe() {
    auto allocator = (uintptr_t*)shared->arg0;
    if (!readable(allocator,8,false)) return -20;
    auto vt=(uintptr_t*)*allocator;
    if (!readable(vt,14*8,false) || (uintptr_t)vt!=game+0x2bb9b80) return -21;
    using Allocate = void* (*)(void*,size_t,size_t);
    using Free = void (*)(void*,void*);
    void* block=((Allocate)vt[10])(allocator,4096,16);
    if (!block) return -22;
    int status=0;
    if ((uintptr_t)block%16 || !readable(block,4096,true)) status=-23;
    else {
        volatile uint64_t* data=(volatile uint64_t*)block;
        data[0]=0x1234aabbccdd9876ULL;
        data[511]=0xa5a5121234345a5aULL;
        if(data[0]!=0x1234aabbccdd9876ULL || data[511]!=0xa5a5121234345a5aULL) status=-24;
    }
    shared->result=(uintptr_t)block;
    ((Free)vt[13])(allocator,block);
    return status;
}

static DWORD WINAPI bootstrap(void*) {
    game = (uintptr_t)GetModuleHandleW(nullptr);
    wchar_t name[128];
    wsprintfW(name, L"Local\\SekiroParamAgent12_%lu_" AGENT_TAG, GetCurrentProcessId());
    HANDLE map = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, 65536, name);
    if (!map || GetLastError() == ERROR_ALREADY_EXISTS) { if (map) CloseHandle(map); return 0; }
    shared = (Shared*)MapViewOfFile(map, FILE_MAP_ALL_ACCESS, 0, 0, 65536);
    if (!shared) { CloseHandle(map); return 1; }
    shared->magic = 0x324c4850524b5353ULL;
    shared->abi = 120;
    shared->pid = GetCurrentProcessId();
    shared->bootstrap_tid = GetCurrentThreadId();
    auto slot = (uintptr_t*)(game + 0x3ea1488);
    const uint8_t signature[] = {0x41,0x56,0x48,0x83,0xec,0x50};
    if (!readable(slot, 8) || !*slot || !readable((void*)*slot, 16) ||
        *(uintptr_t*)*slot != game + 0x2bd8710) { shared->status=-10; return 2; }
    auto reg = (uint8_t*)(game + 0x11df640);
    if (!readable(reg, sizeof(signature))) { shared->status=-11; return 2; }
    for (size_t i=0;i<sizeof(signature);++i) if (reg[i]!=signature[i]) { shared->status=-11; return 2; }
    // The callback object and its code must outlive the task registration.
    HMODULE pin;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                            (LPCWSTR)&execute_task, &pin)) { shared->status=-12; return 2; }
    using Register = void (*)(void*, uint32_t, uint64_t, Task*);
    ((Register)reg)((void*)*slot, 0 /* FrameBegin */, 0, &task);
    return 0;
}

extern "C" BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        self_module = module;
        DisableThreadLibraryCalls(module);
        HANDLE thread = CreateThread(nullptr, 0, bootstrap, nullptr, 0, nullptr);
        if (thread) CloseHandle(thread);
    }
    return TRUE;
}
