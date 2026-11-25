#include "tlsfix.h"
#include <string.h>
#include <stdio.h>

/* 链接器提供的镜像基址符号 ✅（imagebase.h 在部分 CRT 头里缺失 ✅ → 自己声明 ✅） */
extern IMAGE_DOS_HEADER __ImageBase;

/* 已登记的模块 ✅（索引 1 起 ✅ —— 0 留空 ✅） */
typedef struct {
    const unsigned char *tmpl;
    size_t size;
} TlsMod;

static TlsMod g_mods[CRATIX_TLS_MAX_MODULES];
static int g_mod_count;
static DWORD g_tbl_slot = TLS_OUT_OF_INDEXES;
/* 代号 ✅：登记新模块时递增 ✅ —— 每线程的表按代号校验 ✅
 * （表早于模块登记创建 ✅ → 后登记的模块在老表里没有块 ✅ → 必须重建 ✅）。 */
static volatile LONG g_gen = 1;
/* 汇编助手探针 ✅（诊断用 ✅） */
volatile long long g_tlsfix_asm_calls = 0;
volatile long long g_tlsfix_asm_ret = 0;
volatile long long g_tlsfix_retaddr = 0;
/* 补丁点上的标志位 ✅（诊断用 ✅ —— 看 TF 是不是本来就置位 ✅） */
volatile long long g_tlsfix_saved_flags = 0;
/* 调用返回后的标志位 ✅（诊断用 ✅ —— 看栈槽有没有被改坏 ✅） */
volatile long long g_tlsfix_flags_after = 0;

/* 每线程的表 ✅
 * **blocks[] 必须放在偏移 0** ✅ —— 被修补的代码是 `mov rax,[rax+rcx*8]` ✅
 * → 它按「下标 * 8」直接取 ✅ → 有头部就会**整体错位一格** ✅（实测 ✅）。 */
typedef struct {
    void *blocks[CRATIX_TLS_MAX_MODULES];
    LONG gen;
    DWORD count;
} TlsTable;

int cratix_tls_register(const unsigned char *tmpl, size_t size) {
    if (!tmpl || !size) return 0;
    if (g_mod_count + 1 >= CRATIX_TLS_MAX_MODULES) return 0;
    g_mod_count++;
    g_mods[g_mod_count].tmpl = tmpl;
    g_mods[g_mod_count].size = size;
    InterlockedIncrement(&g_gen); /* 老表失效 ✅ → 下次访问时重建 ✅ */
    return g_mod_count;
}

void *cratix_tls_table(void) {
    TlsTable *t;
    if (g_tbl_slot == TLS_OUT_OF_INDEXES) g_tbl_slot = TlsAlloc();
    if (g_tbl_slot == TLS_OUT_OF_INDEXES) return NULL;
    t = (TlsTable *)TlsGetValue(g_tbl_slot);
    if (t && t->gen == g_gen) return t; /* 有效 ✅ */
    if (!t) {
        t = (TlsTable *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(TlsTable));
        if (!t) return NULL;
    }
    {
        int i;
        t->count = (DWORD)g_mod_count;
        for (i = 1; i <= g_mod_count; i++) {
            /* 已有块**不释放** ✅ —— 旧块指针可能已被缓存 ✅（如解释器的 TLS 变量 ✅）
             * → 释放会造成 use-after-free ✅ → 堆损坏 ✅（实测 ✅）。 */
            if (t->blocks[i]) continue;
            {
                /* 多分配一段余量 ✅ —— 解释器的 TLS 变量偏移可能超出模板大小 ✅ → 越界写 → 堆损坏 ✅。 */
                void *blk = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, g_mods[i].size + 4096);
                if (!blk) continue;
                memcpy(blk, g_mods[i].tmpl, g_mods[i].size);
                /* 余量区填哨兵 ✅ —— 崩溃时看哪一格被写过 ✅（定位越界写的偏移 ✅）。 */
                memset((BYTE *)blk + g_mods[i].size, 0xCD, 4096);
                t->blocks[i] = blk;
            }
        }
    }
    t->gen = g_gen;
    TlsSetValue(g_tbl_slot, t);
    return t;
}

/* 诊断打印 ✅：**单独一个函数** ✅ —— 放主路径里会把栈帧撑大 ✅（实测 ✅：栈失衡 ✅）。 */
__declspec(noinline) static void cratix_tls_debug_dump(const void *t) {
    const TlsTable *tt = (const TlsTable *)t;
    int k;
    fprintf(stderr, "[tlsfix] table=%p mods=%d asm=%p c=%p\n", t, g_mod_count,
            (void *)cratix_tls_asm_0, (void *)cratix_tls_table);
    if (!tt) return;
    for (k = 1; k <= g_mod_count; k++)
        fprintf(stderr, "[tlsfix]   block[%d]=%p size=%zu tmpl=%p\n", k, tt->blocks[k],
                g_mods[k].size, (const void *)g_mods[k].tmpl);
}

void *cratix_tls_table_c(void) {
    void *t;
    g_tlsfix_asm_calls++;
    t = cratix_tls_table();
    if (t) {
        static int logged;
        if (!logged) {
            logged = 1;
            if (getenv("CRATIX_DEBUG")) cratix_tls_debug_dump(t);
        }
    }
    return t;
}

void *cratix_tls_block(unsigned int index) {
    TlsTable *t = (TlsTable *)cratix_tls_table();
    if (!t || index == 0 || index >= CRATIX_TLS_MAX_MODULES) return NULL;
    return t->blocks[index];
}

/* 指令修补 ✅
 *
 * 目标序列 ✅（MSVC x64 ✅）：
 *     65 48 8B <modrm> 25 58 00 00 00    mov <reg>, gs:[0x58]   （9 字节 ✅）
 * 替换为 ✅：
 *     E8 <rel32>                          call cratix_tls_table  （5 字节 ✅）
 *     48 89 <modrm2>                      mov <reg>, rax          （3 字节 ✅ —— 目标不是 rax 时 ✅）
 *     90                                   nop                    （1 字节 ✅）
 * 汇编助手**保留除 rax 外的所有寄存器** ✅ → 原代码的活跃值不受影响 ✅。
 */
/* 每个目标寄存器一个助手 ✅（MASM ✅）—— **只能破坏目标寄存器** ✅
 * （原指令只改目标寄存器 ✅ → 助手也必须只改它 ✅ → rax 等必须全部还原 ✅）。 */
static BYTE *tls_stub_for(BYTE reg) {
    static BYTE *tbl[16];
    if (!tbl[0]) {
        tbl[0] = (BYTE *)cratix_tls_asm_0;
        tbl[1] = (BYTE *)cratix_tls_asm_1;
        tbl[2] = (BYTE *)cratix_tls_asm_2;
        tbl[3] = (BYTE *)cratix_tls_asm_3;
        tbl[4] = (BYTE *)cratix_tls_asm_4;
        tbl[5] = (BYTE *)cratix_tls_asm_5;
        tbl[6] = (BYTE *)cratix_tls_asm_6;
        tbl[7] = (BYTE *)cratix_tls_asm_7;
        tbl[8] = (BYTE *)cratix_tls_asm_8;
        tbl[9] = (BYTE *)cratix_tls_asm_9;
        tbl[10] = (BYTE *)cratix_tls_asm_10;
        tbl[11] = (BYTE *)cratix_tls_asm_11;
        tbl[12] = (BYTE *)cratix_tls_asm_12;
        tbl[13] = (BYTE *)cratix_tls_asm_13;
        tbl[14] = (BYTE *)cratix_tls_asm_14;
        tbl[15] = (BYTE *)cratix_tls_asm_15;
    }
    return (reg < 16) ? tbl[reg] : NULL;
}

int cratix_tls_patch(BYTE *base, const IMAGE_NT_HEADERS64 *nt) {
    const IMAGE_SECTION_HEADER *sh;
    int patched = 0;
    if (!base || !nt) return 0;
    sh = IMAGE_FIRST_SECTION(nt);
    for (; sh < IMAGE_FIRST_SECTION(nt) + nt->FileHeader.NumberOfSections; sh++) {
        BYTE *sec;
        size_t i, n;
        if (sh->SizeOfRawData == 0) continue;
        if (!(sh->Characteristics & IMAGE_SCN_MEM_EXECUTE)) continue;
        sec = base + sh->VirtualAddress;
        n = sh->SizeOfRawData;
        for (i = 0; i + 9 <= n; i++) {
            BYTE *p = sec + i;
            BYTE reg, modrm, rex;
            BYTE *stub;
            long rel;
            if (p[0] != 0x65 || p[2] != 0x8B) continue;
            rex = p[1];
            /* REX 前缀任意 ✅ —— 目标寄存器可能是 r8-r15 ✅（REX=0x4C ✅）
             * （只认 0x48 会漏掉 20 处 ✅ → 那些点仍读加载器 TSP ✅ → 崩 ✅）。 */
            if (rex < 0x40 || rex > 0x4F) continue;
            modrm = p[3];
            if ((modrm & 0xC7) != 0x04) continue;
            if (p[4] != 0x25 || p[5] != 0x58 || p[6] != 0x00 || p[7] != 0x00 || p[8] != 0x00) continue;
            reg = (BYTE)((((rex & 0x04) >> 2) << 3) | ((modrm >> 3) & 7));
            /* 选**目标寄存器对应**的助手 ✅（助手只破坏目标寄存器 ✅）。 */
            stub = tls_stub_for(reg);
            if (!stub) continue;
            rel = (long)(stub - (BYTE *)(p + 5));
            if (rel > 0x7FFFFFFFL || rel < -0x80000000L) continue; /* 超出 rel32 范围 ✅ → 跳过 ✅ */
            p[0] = 0xE8;
            p[1] = (BYTE)(rel & 0xFF);
            p[2] = (BYTE)((rel >> 8) & 0xFF);
            p[3] = (BYTE)((rel >> 16) & 0xFF);
            p[4] = (BYTE)((rel >> 24) & 0xFF);
            /* 后 4 字节补 nop ✅（保持 9 字节长度 ✅）—— 助手已把表放进目标寄存器 ✅。 */
            p[5] = 0x90;
            p[6] = 0x90;
            p[7] = 0x90;
            p[8] = 0x90;
            patched++;
            if (getenv("CRATIX_DEBUG")) {
                unsigned long long rva = (unsigned long long)(p - base);
                if (patched <= 3 || (rva >= 0x18C200 && rva <= 0x18C400))
                    fprintf(stderr, "[tlsfix] patch #%d rva=%llx bytes=%02X %02X %02X %02X %02X %02X %02X %02X %02X"
                                    " (helper=%p rel=%ld)\n",
                            patched, rva, p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], (void *)stub, rel);
            }
            i += 8;
        }
    }
    /* 自检 ✅：还剩多少没改掉的 TLS 访问点 ✅（应为 0 ✅） */
    {
        int leftover = 0;
        for (sh = IMAGE_FIRST_SECTION(nt); sh < IMAGE_FIRST_SECTION(nt) + nt->FileHeader.NumberOfSections; sh++) {
            BYTE *sec;
            size_t i, n;
            if (sh->SizeOfRawData == 0) continue;
            if (!(sh->Characteristics & IMAGE_SCN_MEM_EXECUTE)) continue;
            sec = base + sh->VirtualAddress;
            n = sh->SizeOfRawData;
            for (i = 0; i + 9 <= n; i++) {
                BYTE *p = sec + i;
                if (p[0] != 0x65 || p[2] != 0x8B) continue;
                if (p[1] < 0x40 || p[1] > 0x4F) continue;
                if ((p[3] & 0xC7) != 0x04) continue;
                if (p[4] != 0x25 || p[5] != 0x58 || p[6] != 0x00 || p[7] != 0x00 || p[8] != 0x00) continue;
                leftover++;
                if (leftover <= 3 && getenv("CRATIX_DEBUG"))
                    fprintf(stderr, "[tlsfix] leftover rva=%llx modrm=%02X\n",
                            (unsigned long long)(p - base), p[3]);
            }
        }
        if (getenv("CRATIX_DEBUG")) fprintf(stderr, "[tlsfix] patched=%d leftover=%d\n", patched, leftover);
    }
    return patched;
}
