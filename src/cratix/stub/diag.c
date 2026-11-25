#include "diag.h"
#include <stdio.h>
#include <string.h>
#include <intrin.h>

void cy_log(const char *fmt, ...);

#define DIAG_SLOTS 1024
typedef struct {
    unsigned long long rva;
    unsigned count;
} diag_slot;

static diag_slot g_slots[DIAG_SLOTS];
static unsigned g_main_tid;

void cratix_diag_set_main_tid(unsigned tid) { g_main_tid = tid; }

/* 直接问 ntdll ✅：展开器能不能找到这个地址所属模块的 .pdata ✅ */
void cratix_diag_lookup(const void *pc) {
    /* 真实签名 ✅（反汇编 ntdll ✅）：(pc, out_table, out_begin, out_end, out_unwind) ✅ */
    typedef void *(WINAPI *fn_lk)(void *, unsigned long long *, unsigned long long *, unsigned long long *,
                                  unsigned long long *);
    static fn_lk lk = NULL;
    unsigned long long ib = 0, b = 0, e = 0, u = 0;
    void *res;
    if (!lk) {
        HMODULE nt = GetModuleHandleW(L"ntdll.dll");
        lk = nt ? (fn_lk)(void *)GetProcAddress(nt, "RtlLookupFunctionEntry") : NULL;
    }
    if (!lk) {
        cy_log("[diag] RtlLookupFunctionEntry unavailable\r\n");
        return;
    }
    res = lk((void *)pc, &ib, &b, &e, &u);
    cy_log("[diag] lookup pc=%p -> res=%p imageBase=%p begin=0x%llx end=0x%llx unwind=0x%llx\r\n",
           pc, res, ib, b, e, u);
}

/* GetModuleHandleEx(FROM_ADDRESS) ✅：看加载器能不能按地址认出我们注册进 PEB 的模块 ✅ */
void cratix_diag_modhandle(const void *pc) {
    HMODULE hm = NULL;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, (LPCWSTR)pc, &hm)) {
        char path[MAX_PATH] = "";
        GetModuleFileNameA(hm, path, MAX_PATH);
        cy_log("[diag] GetModuleHandleEx(%p) -> %p path=%s\r\n", pc, (void *)hm, path);
    } else {
        cy_log("[diag] GetModuleHandleEx(%p) -> FAILED err=%lu\r\n", pc, (unsigned long)GetLastError());
    }
}

void cratix_diag_scan(void *rsp, const unsigned char *ibase, size_t isize) {
    BYTE *teb = (BYTE *)__readgsqword(0x30);
    void *sbase = teb ? *(void **)(teb + 0x08) : NULL;
    void *slim = teb ? *(void **)(teb + 0x10) : NULL;
    unsigned long long *q, *qend;
    long hits = 0;
    int i, j;
    memset(g_slots, 0, sizeof(g_slots));
    cy_log("[diag] tid=%u main_tid=%u rsp=%p sbase=%p slim=%p depth=%lld\r\n",
           GetCurrentThreadId(), g_main_tid, rsp, sbase, slim,
           sbase ? (long long)((char *)sbase - (char *)rsp) : -1LL);
    if (!rsp || !sbase) return;
    q = (unsigned long long *)rsp;
    qend = (unsigned long long *)sbase;
    if ((long long)(qend - q) > 262144) qend = q + 262144;
    for (; q < qend; q++) {
        unsigned long long v = *q;
        if (ibase && v >= (unsigned long long)ibase && v < (unsigned long long)(ibase + isize)) {
            unsigned long long rva = v - (unsigned long long)ibase;
            unsigned h = (unsigned)((rva >> 3) & (DIAG_SLOTS - 1));
            for (j = 0; j < 64; j++) {
                unsigned hh = (h + j) & (DIAG_SLOTS - 1);
                if (g_slots[hh].count == 0) {
                    g_slots[hh].rva = rva;
                    g_slots[hh].count = 1;
                    hits++;
                    break;
                }
                if (g_slots[hh].rva == rva) {
                    g_slots[hh].count++;
                    break;
                }
            }
        }
    }
    cy_log("[diag] interp hits=%ld\r\n", hits);
    for (i = 0; i < 12; i++) {
        int best = -1;
        unsigned bestc = 0;
        for (j = 0; j < DIAG_SLOTS; j++) {
            if (g_slots[j].count > bestc) {
                bestc = g_slots[j].count;
                best = j;
            }
        }
        if (best < 0 || bestc == 0) break;
        cy_log("[diag]   rva=0x%llx count=%u\r\n", g_slots[best].rva, bestc);
        g_slots[best].count = 0;
    }
    {
        unsigned long long *p = (unsigned long long *)rsp;
        for (i = 0; i < 24; i++) {
            unsigned long long v = p[i];
            char extra[80];
            extra[0] = 0;
            if (ibase && v >= (unsigned long long)ibase && v < (unsigned long long)(ibase + isize))
                snprintf(extra, sizeof(extra), " <interp+0x%llx>", v - (unsigned long long)ibase);
            cy_log("[diag]   sp+%03d: %016llx%s\r\n", i * 8, v, extra);
        }
    }
}
