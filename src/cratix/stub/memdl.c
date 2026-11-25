#include "memdl.h"
#include "tlsfix.h"
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <intrin.h>

/* ---------- 真实系统 API（改写 IAT 之前保存） ---------- */
FARPROC g_real_LoadLibraryExW = NULL;
FARPROC g_real_GetProcAddress = NULL;
FARPROC g_real_LoadLibraryW = NULL;

static char g_err[512];

#define DBG(...) do { if (getenv("CRATIX_DEBUG")) fprintf(stderr, "[memdl] " __VA_ARGS__); } while (0)

/* ---------- 宿主 TLS 池 ✅（每模块独占静态槽 ✅）---------- */
static mem_tls_host_info g_tls_host;
static int g_tls_host_ok;
static size_t g_tls_pool_used;

void mem_tls_host_configure(const mem_tls_host_info *info) {
    if (!info) return;
    g_tls_host = *info;
    g_tls_pool_used = 0;
    g_tls_host_ok = (g_tls_host.raw != NULL && g_tls_host.pool_bytes >= 64);
    DBG("host TLS pool: index=%lu raw=%p raw_bytes=%zu pool_off=%zu pool_bytes=%zu ok=%d\n",
        g_tls_host.host_index, g_tls_host.raw, g_tls_host.raw_bytes, g_tls_host.pool_offset,
        g_tls_host.pool_bytes, g_tls_host_ok);
}

/* 当前线程的 TLS 槽指针数组 ✅（x64：TEB+0x58 ✅） */
void **mem_tls_slot_array(void) {
    BYTE *teb = (BYTE *)__readgsqword(0x30);
    if (!teb) return NULL;
    return *(void ***)(teb + 0x58);
}

/* 写宿主镜像里的 TLS 原始数据 ✅
 * 镜像里的模板在**只读页** ✅（.rdata ✅）→ 必须临时放开写权限 ✅。
 * 写完还原 ✅ —— 之后**新线程**由加载器复制这份模板 ✅ → 自动继承 ✅。 */
static int mem_tls_write_raw(size_t off, const BYTE *src, size_t n) {
    SYSTEM_INFO si;
    ULONG_PTR start, end, addr;
    DWORD olds[16];
    int np = 0, i;
    if (!g_tls_host.raw || !src || !n) return 0;
    if (off + n > g_tls_host.raw_bytes) return 0;
    GetSystemInfo(&si);
    start = (ULONG_PTR)g_tls_host.raw + off;
    end = start + n;
    addr = start & ~(ULONG_PTR)(si.dwPageSize - 1);
    while (addr < end && np < 16) {
        if (!VirtualProtect((void *)addr, (SIZE_T)si.dwPageSize, PAGE_READWRITE, &olds[np])) return 0;
        np++;
        addr += (ULONG_PTR)si.dwPageSize;
    }
    memcpy(g_tls_host.raw + off, src, n);
    addr = start & ~(ULONG_PTR)(si.dwPageSize - 1);
    for (i = 0; i < np; i++) {
        DWORD ignored;
        VirtualProtect((void *)addr, (SIZE_T)si.dwPageSize, olds[i], &ignored);
        addr += (ULONG_PTR)si.dwPageSize;
    }
    return 1;
}

const char *mem_last_error(void) { return g_err; }

static void set_err(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_err, sizeof(g_err), fmt, ap);
    va_end(ap);
}

/* 512 ✅（M6c ✅）：真实应用要映射 161 个 Cython .pyd + 标准库 C 扩展 + 第三方
 * 原生扩展 + Qt 全链 ✅ —— 旧值 64 ✅ 装不下 ✅ → 超出的模块**不登记** ✅
 * → find_loaded 永远找不到 ✅ → 每个符号解析都**重新映射整个依赖 DLL** ✅
 * → 无限循环 ✅ 工作集 3.9GB ✅ commit 耗尽 ✅（实测 ✅）。 */
#define MAX_LOADED 512
static MemModule *g_loaded[MAX_LOADED];
static int g_loaded_count;

/* 区域分配器 ✅：被映射的模块放在**离 stub 足够近**的地方 ✅
 * 静态 TLS 修补用的是 rel32 调用 ✅（±2 GB ✅）→ 距离太远就补不上 ✅。
 * 一次预留一大块地址空间 ✅（按模块提交 ✅）→ 所有模块彼此也近 ✅。
 * **可扩展 ✅**（M6c）：全应用 161 个 Cython .pyd + 第三方原生 + Qt 全链
 * 超出单块 512 MB 预留 ✅（实测 Qt6Widgets.dll 映射时耗尽 ✅）→ 用完再预留一块 ✅
 * （候选点沿 ±0x10000000 步进 ✅ —— 保持 rel32 范围 ✅）。 */
#define CRATIX_REGION_BYTES 0x20000000u /* 512 MB ✅ */
#define CRATIX_MAX_REGIONS 8

static struct {
    BYTE *base;
    size_t size;
    size_t used;
} g_regions[CRATIX_MAX_REGIONS];
static int g_region_count;

static BYTE *mem_alloc_near(size_t size) {
    BYTE *hint = (BYTE *)GetModuleHandleA(NULL);
    size_t align = 0x10000;
    size_t need = (size + align - 1) & ~(align - 1);
    int i, step;
    /* 已有区域里找放得下的 ✅ */
    for (i = 0; i < g_region_count; i++) {
        if (g_regions[i].used + need <= g_regions[i].size) {
            BYTE *p = g_regions[i].base + g_regions[i].used;
            if (!VirtualAlloc(p, size, MEM_COMMIT, PAGE_EXECUTE_READWRITE)) {
                DBG("commit %llu at %p failed err=%lu\n", (unsigned long long)size, (void *)p, GetLastError());
                return NULL;
            }
            g_regions[i].used += need;
            DBG("alloc %llu (%s) -> region#%d off=%zu used=%zu/%d\n", (unsigned long long)need,
                "", i, g_regions[i].used - need, g_regions[i].used, g_region_count);
            return p;
        }
    }
    /* 新开一块 ✅（候选点从 hint 两侧步进 ✅ —— 全部落在 rel32 ±2GB 内 ✅） */
    for (step = 0; g_region_count < CRATIX_MAX_REGIONS && step < 32; step++) {
        BYTE *cand;
        BYTE *base;
        if (step == 0)
            cand = hint;
        else
            cand = (step % 2 == 1) ? hint + 0x10000000ull * (size_t)((step + 1) / 2)
                                   : hint - 0x10000000ull * (size_t)(step / 2);
        if (!cand) continue;
        base = (BYTE *)VirtualAlloc(cand, CRATIX_REGION_BYTES, MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (!base) continue;
        g_regions[g_region_count].base = base;
        g_regions[g_region_count].size = CRATIX_REGION_BYTES;
        g_regions[g_region_count].used = 0;
        g_region_count++;
        DBG("region #%d reserved at %p size=%llu\n", g_region_count, (void *)base,
            (unsigned long long)CRATIX_REGION_BYTES);
        {
            BYTE *p = base;
            if (!VirtualAlloc(p, size, MEM_COMMIT, PAGE_EXECUTE_READWRITE)) return NULL;
            g_regions[g_region_count - 1].used += need;
            return p;
        }
    }
    /* 近处全占满 ✅ → 退到任意地址 ✅（rel32 修补可能够不着 ✅ —— 仅告警口径 ✅） */
    if (g_region_count < CRATIX_MAX_REGIONS) {
        BYTE *base = (BYTE *)VirtualAlloc(NULL, CRATIX_REGION_BYTES, MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (base) {
            g_regions[g_region_count].base = base;
            g_regions[g_region_count].size = CRATIX_REGION_BYTES;
            g_regions[g_region_count].used = 0;
            g_region_count++;
            if (VirtualAlloc(base, size, MEM_COMMIT, PAGE_EXECUTE_READWRITE)) {
                g_regions[g_region_count - 1].used += need;
                return base;
            }
        }
    }
    return NULL;
}

struct MemModule {
    BYTE *base;
    size_t size;
    char name[260];
    const IMAGE_NT_HEADERS64 *nt;
    /* TLS：模板数据与 per-thread 存储 */
    const BYTE *tls_template;
    size_t tls_size;
    DWORD tls_slot;
    /* 静态 TLS 的独占索引 ✅（宿主池里划出来的 ✅）—— 0xFFFFFFFF = 没分配到 ✅ */
    DWORD tls_static_index;
    size_t tls_static_bytes;
};

static int name_eq(const char *a, const char *b) {
    while (*a && *b) {
        char ca = *a, cb = *b;
        if (ca >= 'A' && ca <= 'Z') ca = (char)(ca - 'A' + 'a');
        if (cb >= 'A' && cb <= 'Z') cb = (char)(cb - 'A' + 'a');
        if (ca != cb) return 0;
        a++;
        b++;
    }
    return *a == 0 && *b == 0;
}

MemModule *mem_find_loaded(const char *name) {
    int i;
    if (!name) return NULL;
    for (i = 0; i < g_loaded_count; i++)
        if (name_eq(g_loaded[i]->name, name)) return g_loaded[i];
    return NULL;
}

MemModule *mem_from_hmodule(HMODULE h) {
    int i;
    for (i = 0; i < g_loaded_count; i++)
        if ((HMODULE)g_loaded[i] == h) return g_loaded[i];
    return NULL;
}

HMODULE mem_as_hmodule(MemModule *m) { return (HMODULE)m; }

static BYTE *g_last_image_base = NULL;

BYTE *mem_image_base(MemModule *m) { return m ? m->base : NULL; }

BYTE *mem_last_image_base(void) { return g_last_image_base; }

size_t mem_image_size(MemModule *m) { return m ? m->size : 0; }

/* 诊断：故障地址落在哪个已映射模块 ✅（veh 报告用 ✅） */
const char *mem_module_by_addr(const void *addr) {
    int i;
    if (!addr) return NULL;
    for (i = 0; i < g_loaded_count; i++) {
        const BYTE *b = g_loaded[i]->base;
        if ((const BYTE *)addr >= b && (const BYTE *)addr < b + g_loaded[i]->size) return g_loaded[i]->name;
    }
    return NULL;
}

/* 诊断：取包含该地址的已映射模块基址 ✅（veh 报告用 ✅） */
BYTE *mem_base_by_addr(const void *addr) {
    int i;
    if (!addr) return NULL;
    for (i = 0; i < g_loaded_count; i++) {
        const BYTE *b = g_loaded[i]->base;
        if ((const BYTE *)addr >= b && (const BYTE *)addr < b + g_loaded[i]->size) return g_loaded[i]->base;
    }
    return NULL;
}

/* 系统 DLL：优先已加载，其次 System32（避免 DLL 劫持） */
static HMODULE resolve_system_module(const char *dll) {
    HMODULE h = GetModuleHandleA(dll);
    if (h) return h;
    h = LoadLibraryExA(dll, NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!h) h = LoadLibraryA(dll);
    return h;
}

/* ---------- 导出 ---------- */
static void *export_forwarder(MemModule *m, DWORD rva) {
    const IMAGE_NT_HEADERS64 *nt = m->nt;
    const IMAGE_DATA_DIRECTORY *d = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
    const char *fwd = (const char *)(m->base + rva);
    const char *dot = strchr(fwd, '.');
    char dll[160];
    size_t n;
    HMODULE h;
    if (!dot) return NULL;
    n = (size_t)(dot - fwd);
    if (n >= sizeof(dll)) n = sizeof(dll) - 1;
    memcpy(dll, fwd, n);
    dll[n] = 0;
    h = resolve_system_module(dll);
    if (!h) return NULL;
    return (void *)GetProcAddress(h, dot + 1);
}

void *mem_get_ordinal(MemModule *m, WORD ordinal) {
    const IMAGE_NT_HEADERS64 *nt = m->nt;
    const IMAGE_DATA_DIRECTORY *d = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
    const IMAGE_EXPORT_DIRECTORY *ed;
    DWORD i, f;
    if (!d->VirtualAddress) return NULL;
    ed = (const IMAGE_EXPORT_DIRECTORY *)(m->base + d->VirtualAddress);
    if (ordinal < ed->Base) return NULL;
    i = (DWORD)(ordinal - ed->Base);
    if (i >= ed->NumberOfFunctions) return NULL;
    f = *(const DWORD *)(m->base + ed->AddressOfFunctions + i * sizeof(DWORD));
    if (!f) return NULL;
    if (f >= d->VirtualAddress && f < d->VirtualAddress + d->Size) return export_forwarder(m, f);
    return m->base + f;
}

void *mem_get_export(MemModule *m, const char *name) {
    const IMAGE_NT_HEADERS64 *nt = m->nt;
    const IMAGE_DATA_DIRECTORY *d = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
    const IMAGE_EXPORT_DIRECTORY *ed;
    DWORD i;
    if (!d->VirtualAddress || !name) return NULL;
    ed = (const IMAGE_EXPORT_DIRECTORY *)(m->base + d->VirtualAddress);
    {
        const DWORD *names = (const DWORD *)(m->base + ed->AddressOfNames);
        const WORD *ords = (const WORD *)(m->base + ed->AddressOfNameOrdinals);
        for (i = 0; i < ed->NumberOfNames; i++) {
            const char *cand = (const char *)(m->base + names[i]);
            if (strcmp(cand, name) == 0) {
                DWORD f = *(const DWORD *)(m->base + ed->AddressOfFunctions + (DWORD)ords[i] * sizeof(DWORD));
                if (!f) return NULL;
                if (f >= d->VirtualAddress && f < d->VirtualAddress + d->Size) return export_forwarder(m, f);
                return m->base + f;
            }
        }
    }
    return NULL;
}

/* ---------- 导入 ---------- */
static mem_dep_resolver_fn g_dep_resolver = NULL;

void mem_set_dep_resolver(mem_dep_resolver_fn fn) { g_dep_resolver = fn; }

static void *resolve_symbol(const char *dll, const char *name, WORD ordinal, int by_ordinal) {
    MemModule *known = mem_find_loaded(dll);
    if (!known) {
        /* 稳定 ABI 别名 ✅（§6.2）：python3.dll 是转发器 ✅，转发解析要求 OS 加载器
         * 能按名找到 python3xx.dll ✅ —— 手工映射的模块不在加载器表里 ✅
         * → 直接用已映射的 python314.dll 的导出表解析 ✅（wreq / shiboken6 等大量依赖 ✅） */
        if (_stricmp(dll, "python3.dll") == 0) known = mem_find_loaded("python314.dll");
    }
    if (known) {
        void *p = by_ordinal ? mem_get_ordinal(known, ordinal) : mem_get_export(known, name);
        if (p) return p;
    }
    /* 进程里**已加载**的系统模块优先 ✅（MSVC 运行时 / ucrt / api-ms-* ✅）：
     * 再从归档映射一份 ✅ → 进程里两份 CRT ✅ → C++ 静态状态与异常链分裂 ✅
     * → 崩在别人的代码里 ✅（实测 shiboken6 → python314.dll ✅）。 */
    {
        HMODULE h = GetModuleHandleA(dll);
        if (h) {
            void *p = (void *)GetProcAddress(h, by_ordinal ? MAKEINTRESOURCEA(ordinal) : name);
            if (p) return p;
        }
    }
    if (!known && g_dep_resolver) {
        /* 归档内 DLL 走 memdl ✅：系统里找不到的依赖（pyside6.abi3 / Qt6*.dll 等）由
         * stub 的钩子从载荷里映射 ✅（映射内部递归解析它自己的依赖 ✅） */
        known = g_dep_resolver(dll);
    }
    if (known) {
        void *p = by_ordinal ? mem_get_ordinal(known, ordinal) : mem_get_export(known, name);
        if (p) return p;
    }
    {
        HMODULE h = resolve_system_module(dll);
        if (!h) return NULL;
        return (void *)GetProcAddress(h, by_ordinal ? MAKEINTRESOURCEA(ordinal) : name);
    }
}

static int resolve_imports(MemModule *m) {
    const IMAGE_NT_HEADERS64 *nt = m->nt;
    const IMAGE_DATA_DIRECTORY *d = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    IMAGE_IMPORT_DESCRIPTOR *desc;
    if (!d->VirtualAddress) return 1;
    desc = (IMAGE_IMPORT_DESCRIPTOR *)(m->base + d->VirtualAddress);
    for (; desc->Name; desc++) {
        const char *dll = (const char *)(m->base + desc->Name);
        BYTE *names_thunk = desc->OriginalFirstThunk ? (m->base + desc->OriginalFirstThunk) : NULL;
        BYTE *iat = (BYTE *)(m->base + desc->FirstThunk);
        size_t idx = 0;
        for (;;) {
            ULONGLONG v = names_thunk ? *(const ULONGLONG *)(names_thunk + idx * 8)
                                      : *(const ULONGLONG *)(iat + idx * 8);
            void *addr;
            const char *sym = NULL; /* 诊断用 ✅：失败时报告具体符号 ✅ */
            if (!v) break;
            if (v & IMAGE_ORDINAL_FLAG64) {
                WORD ord = (WORD)(v & 0xFFFF);
                addr = resolve_symbol(dll, NULL, ord, 1);
                if (!addr) {
                    set_err("unresolved import in %s: ordinal #%u", dll, (unsigned)ord);
                    return 0;
                }
            } else {
                DWORD rva = (DWORD)(v & 0x7FFFFFFF);
                const char *fname = (const char *)(m->base + rva + 2); /* 跳过 Hint */
                sym = fname;
                addr = resolve_symbol(dll, fname, 0, 0);
            }
            if (!addr) {
                set_err("unresolved import in %s: %s", dll, sym ? sym : "?");
                return 0;
            }
            *(ULONGLONG *)(iat + idx * 8) = (ULONGLONG)addr;
            idx++;
        }
    }
    return 1;
}

/* ---------- 重定位 ---------- */
static unsigned g_reloc_applied;
static unsigned g_reloc_other;

static int apply_relocs(MemModule *m) {
    const IMAGE_NT_HEADERS64 *nt = m->nt;
    ULONGLONG delta = (ULONGLONG)(uintptr_t)m->base - nt->OptionalHeader.ImageBase;
    const IMAGE_DATA_DIRECTORY *d;
    BYTE *blk;
    DWORD off = 0;
    if (!delta) return 1;
    d = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_BASERELOC];
    if (!d->VirtualAddress || !d->Size) return 1;
    blk = m->base + d->VirtualAddress;
    g_reloc_applied = 0;
    g_reloc_other = 0;
    while (off + sizeof(IMAGE_BASE_RELOCATION) <= d->Size) {
        const IMAGE_BASE_RELOCATION *r = (const IMAGE_BASE_RELOCATION *)(blk + off);
        WORD count, i;
        if (r->SizeOfBlock < sizeof(IMAGE_BASE_RELOCATION)) break;
        count = (WORD)((r->SizeOfBlock - sizeof(IMAGE_BASE_RELOCATION)) / sizeof(WORD));
        {
            const WORD *types = (const WORD *)(r + 1);
            for (i = 0; i < count; i++) {
                WORD t = types[i];
                WORD type = (WORD)(t >> 12);
                WORD o = (WORD)(t & 0x0FFF);
                if (type == IMAGE_REL_BASED_DIR64) {
                    /* **边界校验** ✅：损坏/恶意重定位表能把写指针指到镜像外 ✅
                     * → 直接 OOB 写 ✅。归档条目是我们自己打的 ✅，但仍应失败而非写崩 ✅。 */
                    if ((size_t)r->VirtualAddress + o + sizeof(ULONGLONG) > m->size) {
                        set_err("relocation out of image (%s)", m->name);
                        return 0;
                    }
                    {
                        ULONGLONG *slot = (ULONGLONG *)(m->base + r->VirtualAddress + o);
                        *slot += delta;
                    }
                    g_reloc_applied++;
                } else if (type != IMAGE_REL_BASED_ABSOLUTE) {
                    g_reloc_other++;
                }
            }
        }
        off += r->SizeOfBlock;
    }
    DBG("%s: relocs applied=%u other=%u delta=0x%llx\n", m->name, g_reloc_applied, g_reloc_other,
        (unsigned long long)delta);
    if (g_reloc_other) {
        /* **不支持的重定位类型必须报错** ✅：旧实现只计数然后继续 ✅
         * → 镜像里留着未重定位的指键 ✅ → 运行期随机崩 ✅（难排查 ✅）。
         * 当前载荷全部二进制实测只有 DIR64/ABSOLUTE ✅ → 不会误伤 ✅。 */
        set_err("unsupported relocation types=%u (%s)", g_reloc_other, m->name);
        return 0;
    }
    return 1;
}

/* ---------- TLS ---------- */
static int setup_tls(MemModule *m, DWORD reason) {
    const IMAGE_NT_HEADERS64 *nt = m->nt;
    const IMAGE_DATA_DIRECTORY *d = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_TLS];
    ULONGLONG delta = (ULONGLONG)(uintptr_t)m->base - nt->OptionalHeader.ImageBase;
    IMAGE_TLS_DIRECTORY *tls;
    static DWORD tls_index_storage[64];
    static int tls_index_next;
    if (!d->VirtualAddress) return 1;
    tls = (IMAGE_TLS_DIRECTORY *)(m->base + d->VirtualAddress);
    DBG("%s: TLS dir rva=%llx rawdata=%llx/%llx index=%llx callbacks=%llx delta=%llx image=%p..%p\n",
        m->name, (unsigned long long)d->VirtualAddress,
        (unsigned long long)tls->StartAddressOfRawData, (unsigned long long)tls->EndAddressOfRawData,
        (unsigned long long)tls->AddressOfIndex, (unsigned long long)tls->AddressOfCallBacks,
        (unsigned long long)delta, (void *)m->base, (void *)((BYTE *)m->base + m->size));
    /* 注意：TLS 目录里的 VA 字段（rawdata/index/callbacks）本身属于基址重定位覆盖范围，
     * apply_relocs 已经把它们改写成新基址下的绝对地址；这里再叠一次 delta 就是双重重定位，
     * 写 TLS index 时会越界。 */

    (void)delta;
    /* 记录 TLS 模板（reloc 后已是新基址下的绝对地址），供伪 __tls_get_addr 复制 */
    m->tls_template = (const BYTE *)(uintptr_t)tls->StartAddressOfRawData;
    m->tls_size = (size_t)(tls->EndAddressOfRawData - tls->StartAddressOfRawData) + tls->SizeOfZeroFill;
    m->tls_static_index = 0xFFFFFFFFu;
    m->tls_static_bytes = 0;

    /* 静态 TLS ✅：登记 + 修补 ✅（机制见 tlsfix.h ✅）
     * 加载器只为**自己加载的**模块分配 TLS 索引与块 ✅（实测 ✅）→ 手工映射的模块拿不到 ✅
     * → 给自己一个索引 ✅ + 把「取加载器数组」那条指令换成「取我们自己的每线程表」✅
     * → 语义与加载器一致 ✅（每线程一份 ✅ 新线程自动有 ✅）。 */
    if (m->tls_size && tls->AddressOfIndex) {
        int k = cratix_tls_register(m->tls_template, m->tls_size);
        if (k > 0) {
            int patched = cratix_tls_patch(m->base, m->nt);
            *(DWORD *)(uintptr_t)tls->AddressOfIndex = (DWORD)k;
            m->tls_static_index = (DWORD)k;
            m->tls_static_bytes = m->tls_size;
            DBG("%s: static TLS index=%d bytes=%zu patched=%d helper=%p\n", m->name, k, m->tls_size,
                patched, (void *)cratix_tls_table);
        }
    }
    /* 兜底 ✅：没登记上时的旧口径 ✅（Win32 TLS 槽近似 ✅ —— 只是避免野指针 ✅）。 */
    if (m->tls_static_index == 0xFFFFFFFFu && tls->AddressOfIndex) {
        /* 兜底也要给伪 __tls_get_addr 一个可用的槽 ✅（否则 CRT 的 TLS 回调拿到 NULL ✅ → 崩 ✅） */
        if (m->tls_slot == 0) m->tls_slot = TlsAlloc();
        /* TLS 索引策略：CRATIX_TLS_INDEX=none|zero|alloc（默认 alloc ✅）
         * alloc = TlsAlloc 的 Win32 槽（与 x64 加载器的 TLS 槽空间不同 ✅）
         * zero = 写 0（落到进程 TLS 数组首槽 ✅）
         * none = 不写（保留镜像里的原值 ✅） */
        const char *pol = getenv("CRATIX_TLS_INDEX");
        int mode = pol ? (pol[0] == 'n' ? 0 : (pol[0] == 'z' ? 1 : 2)) : 2;
        if (mode != 0) {
            DWORD slot = (mode == 1) ? 0 : TlsAlloc();
            if (mode == 2 && tls_index_next < (int)(sizeof(tls_index_storage) / sizeof(tls_index_storage[0])))
                tls_index_storage[tls_index_next++] = slot;
            DBG("%s: TLS index slot=%u at %llx\n", m->name, slot,
                (unsigned long long)tls->AddressOfIndex);
            *(DWORD *)(uintptr_t)tls->AddressOfIndex = slot;
        }
    }
    /* TLS 回调 ✅：索引就位后可以正常执行 ✅（MSVC CRT 的 _call_tls_used ✅）。
     * 用 CRATIX_TLS=0 可跳过（对照实验 ✅）。 */
    {
        const char *mode = getenv("CRATIX_TLS");
        int run_cb = !(mode && mode[0] == '0');
        if (run_cb && tls->AddressOfCallBacks) {
            ULONGLONG *cb = (ULONGLONG *)(uintptr_t)tls->AddressOfCallBacks;
            while (*cb) {
                /* 回调签名 ✅：PIMAGE_TLS_CALLBACK = (DllHandle, Reason, Reserved) ✅ */
                void (*fn)(void *, DWORD, void *) = (void (*)(void *, DWORD, void *))(uintptr_t)*cb;
                __try {
                    fn((void *)m->base, reason, NULL);
                } __except (EXCEPTION_EXECUTE_HANDLER) {
                    EXCEPTION_POINTERS *ep = GetExceptionInformation();
                    DBG("%s: TLS callback EXCEPTION code=0x%08lX addr=%p\n", m->name,
                        ep->ExceptionRecord->ExceptionCode, (void *)ep->ExceptionRecord->ExceptionAddress);
                }
                cb++;
            }
        } else if (tls->AddressOfCallBacks) {
            DBG("%s: TLS callbacks skipped\n", m->name);
        }
    }
    return 1;
}

/* ---------- PEB 注册 ✅ ----------
 *
 * x64 事实 ✅：异常展开（RtlVirtualUnwind ✅）靠 RtlLookupFunctionEntry ✅
 * → 它在 **PEB 的加载器模块表**里找「包含 PC 的模块」✅ → 再读那个模块的 .pdata ✅。
 * 手工映射的模块不在表里 ✅ → 展开失败 ✅ → 解释器里的异常**接不住** ✅
 * → 崩溃现场被「单步异常」掩盖 ✅（实测 ✅：0x80000004 ✅）。
 * 做法 ✅：造一条 LDR_DATA_TABLE_ENTRY ✅（x64 字段偏移 ✅）→ 挂进 PEB 的三条链表 ✅
 * → 展开器 / GetModuleHandleEx / 栈回溯全部恢复正常 ✅。
 */
typedef struct _cy_list {
    struct _cy_list *flink;
    struct _cy_list *blink;
} cy_list;

/* x64 LDR_DATA_TABLE_ENTRY 字段偏移 ✅（Windows x64 ✅） */
#define LDR_O_LOADLINK   0x00
#define LDR_O_MEMLINK    0x10
#define LDR_O_INITLINK   0x20
#define LDR_O_DLLBASE    0x30
#define LDR_O_ENTRYPOINT 0x38
#define LDR_O_SIZEIMAGE  0x40
#define LDR_O_FULLNAME   0x48 /* UNICODE_STRING ✅ */
#define LDR_O_BASENAME   0x58 /* UNICODE_STRING ✅ */
#define LDR_O_FLAGS      0x68
#define LDR_O_LOADCOUNT  0x6C
#define LDR_ENTRY_BYTES  0x100

static BYTE *g_peb_entry[MAX_LOADED];
static int g_peb_count;

static void cy_list_push(cy_list *head, cy_list *link) {
    link->flink = head->flink;
    link->blink = head;
    head->flink->blink = link;
    head->flink = link;
}

static void cy_list_remove(cy_list *head, cy_list *link) {
    link->flink->blink = link->blink;
    link->blink->flink = link->flink;
    link->flink = link;
    link->blink = link;
}

static BYTE *cy_peb(void) { return (BYTE *)__readgsqword(0x60); }

int mem_peb_register(MemModule *m) {
    BYTE *peb, *ldr, *entry;
    cy_list *load_head, *mem_head, *init_head;
    WCHAR *wname;
    size_t nlen, i;
    if (!m || g_peb_count >= MAX_LOADED) return 0;
    peb = cy_peb();
    if (!peb) return 0;
    /* LDR 结构在 **PEB.Ldr 字段里** ✅ —— 必须解引用 ✅（之前直接取字段地址 ✅ → 全错 ✅）。 */
    ldr = *(BYTE **)(peb + 0x18);
    if (!ldr) return 0;
    load_head = (cy_list *)(ldr + 0x10);
    mem_head = (cy_list *)(ldr + 0x20);
    init_head = (cy_list *)(ldr + 0x30);
    entry = (BYTE *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, LDR_ENTRY_BYTES);
    if (!entry) return 0;
    nlen = strlen(m->name);
    wname = (WCHAR *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, (nlen + 1) * sizeof(WCHAR));
    if (!wname) {
        HeapFree(GetProcessHeap(), 0, entry);
        return 0;
    }
    for (i = 0; i < nlen; i++) wname[i] = (WCHAR)(unsigned char)m->name[i];
    *(void **)(entry + LDR_O_DLLBASE) = m->base;
    *(void **)(entry + LDR_O_ENTRYPOINT) = m->base + m->nt->OptionalHeader.AddressOfEntryPoint;
    *(ULONG *)(entry + LDR_O_SIZEIMAGE) = (ULONG)m->size;
    {
        USHORT *u = (USHORT *)(entry + LDR_O_FULLNAME);
        u[0] = (USHORT)(nlen * sizeof(WCHAR));
        u[1] = u[0];
        *(WCHAR **)(entry + LDR_O_FULLNAME + 8) = wname;
    }
    {
        USHORT *u = (USHORT *)(entry + LDR_O_BASENAME);
        u[0] = (USHORT)(nlen * sizeof(WCHAR));
        u[1] = u[0];
        *(WCHAR **)(entry + LDR_O_BASENAME + 8) = wname;
    }
    *(ULONG *)(entry + LDR_O_FLAGS) = 0;
    *(USHORT *)(entry + LDR_O_LOADCOUNT) = 1;
    cy_list_push(load_head, (cy_list *)(entry + LDR_O_LOADLINK));
    cy_list_push(mem_head, (cy_list *)(entry + LDR_O_MEMLINK));
    cy_list_push(init_head, (cy_list *)(entry + LDR_O_INITLINK));
    g_peb_entry[g_peb_count++] = entry;
    DBG("%s: PEB registered ✅ (entry=%p base=%p)\n", m->name, (void *)entry, (void *)m->base);
    return 1;
}

void mem_peb_unregister_all(void) {
    BYTE *peb = cy_peb();
    BYTE *ldr;
    cy_list *load_head, *mem_head, *init_head;
    int i;
    if (!peb || !g_peb_count) return;
    ldr = *(BYTE **)(peb + 0x18);
    if (!ldr) return;
    load_head = (cy_list *)(ldr + 0x10);
    mem_head = (cy_list *)(ldr + 0x20);
    init_head = (cy_list *)(ldr + 0x30);
    for (i = 0; i < g_peb_count; i++) {
        BYTE *e = g_peb_entry[i];
        if (!e) continue;
        cy_list_remove(load_head, (cy_list *)(e + LDR_O_LOADLINK));
        cy_list_remove(mem_head, (cy_list *)(e + LDR_O_MEMLINK));
        cy_list_remove(init_head, (cy_list *)(e + LDR_O_INITLINK));
        HeapFree(GetProcessHeap(), 0, e);
        g_peb_entry[i] = NULL;
    }
    g_peb_count = 0;
    DBG("PEB registrations removed ✅\n");
}

/* ---------- 展开器注册（RtlAddFunctionTable ✅）----------
 *
 * 事实 ✅（反汇编 ntdll ✅）：x64 的展开器（RtlLookupFunctionEntry ✅）查的是
 * **加载器在进程启动时建好的「排序数组」** ✅（{起始, 结束, 展开信息} ✅ 三元组 ✅）
 * —— **不是** PEB 的模块表 ✅ → 手工映射的模块永远不在里面 ✅ → 展开失败 ✅。
 * RtlAddFunctionTable ✅ 的第 3 个参数就是**基址** ✅（反汇编 ✅：add rax,rsi ✅
 * —— rsi = 第 3 参数 ✅ → 表里的 32 位值是**相对基址的偏移** ✅）。
 * ⇒ 直接把镜像里的 .pdata 原样交给它 ✅ + 基址 = 镜像基址 ✅ → 偏移天然正确 ✅。
 */
typedef LONG(WINAPI *fn_rtl_add_function_table)(PVOID table, ULONG len, ULONG_PTR base, ULONG *id);
static fn_rtl_add_function_table g_add_function_table = NULL;
static ULONG g_unwind_ids[MAX_LOADED];
static int g_unwind_count;

int mem_register_unwind(MemModule *m) {
    const IMAGE_NT_HEADERS64 *nt;
    const IMAGE_DATA_DIRECTORY *d;
    HMODULE ntdll;
    ULONG id = 0;
    LONG rc;
    if (!m) return 0;
    nt = m->nt;
    if (!nt) return 0;
    d = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXCEPTION];
    if (!d->VirtualAddress || !d->Size) return 0;
    if (!g_add_function_table) {
        ntdll = GetModuleHandleW(L"ntdll.dll");
        if (!ntdll) return 0;
        g_add_function_table = (fn_rtl_add_function_table)(void *)GetProcAddress(ntdll, "RtlAddFunctionTable");
        if (!g_add_function_table) {
            DBG("%s: RtlAddFunctionTable unavailable\n", m->name);
            return 0;
        }
    }
    /* 长度参数是**条目数**✅（不是字节数✅ —— 传字节数会越界遍历 ✅ → 崩 ✅）。 */
    rc = g_add_function_table((PVOID)(m->base + d->VirtualAddress), d->Size / 12u, (ULONG_PTR)(uintptr_t)m->base, &id);
    if (rc == 0 && g_unwind_count < MAX_LOADED) g_unwind_ids[g_unwind_count++] = id;
    DBG("%s: unwind registered rc=%ld id=%lu pdata=%llx/%llu\n", m->name, (long)rc, (unsigned long)id,
        (unsigned long long)d->VirtualAddress, (unsigned long long)d->Size);
    return rc == 0;
}

/* 按**函数名**重定向 ✅ —— 遍历**所有**导入描述符 ✅ —— 不管它来自哪个 DLL ✅
 * （现代 MSVC 的导入表里常写成 API-set 名 ✅（api-ms-win-core-libraryloader-l1-2-0.dll ✅），
 *   只按 KERNEL32.dll 匹配会漏 ✅ → 扩展模块的 LoadLibrary 落到真实加载器 ✅ → 找不到 DLL ✅）。 */


int mem_redirect_import_any_at(void *base, const IMAGE_NT_HEADERS64 *nt, const char *const *names,
                             const void *const *repls, int count) {
    const IMAGE_DATA_DIRECTORY *d;
    IMAGE_IMPORT_DESCRIPTOR *desc;
    int hits = 0;
    if (!base || !nt || !names || !repls) return 0;
    d = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!d->VirtualAddress) return 0;
    desc = (IMAGE_IMPORT_DESCRIPTOR *)((BYTE *)base + d->VirtualAddress);
    for (; desc->Name; desc++) {
        BYTE *names_thunk = desc->OriginalFirstThunk ? (BYTE *)base + desc->OriginalFirstThunk : NULL;
        BYTE *iat = (BYTE *)base + desc->FirstThunk;
        size_t idx = 0;
        for (;;) {
            ULONGLONG v = names_thunk ? *(const ULONGLONG *)(names_thunk + idx * 8)
                                      : *(const ULONGLONG *)(iat + idx * 8);
            int k;
            if (!v) break;
            if (!(v & IMAGE_ORDINAL_FLAG64)) {
                const char *fname = (const char *)((BYTE *)base + (DWORD)(v & 0x7FFFFFFF) + 2);
                for (k = 0; k < count; k++) {
                    if (strcmp(fname, names[k]) != 0) continue;
                    {
                        DWORD oldprot = 0;
                        if (VirtualProtect((void *)(iat + idx * 8), 8, PAGE_READWRITE, &oldprot)) {
                            *(ULONGLONG *)(iat + idx * 8) = (ULONGLONG)repls[k];
                            VirtualProtect((void *)(iat + idx * 8), 8, oldprot, &oldprot);
                        } else {
                            *(ULONGLONG *)(iat + idx * 8) = (ULONGLONG)repls[k];
                        }
                    }
                    hits++;
                    break;
                }
            }
            idx++;
        }
    }
    return hits;
}

int mem_redirect_import_any(MemModule *m, const char *const *names, const void *const *repls, int count) {
    if (!m) return 0;
    return mem_redirect_import_any_at(m->base, m->nt, names, repls, count);
}

/* ---------- 映射主流程 ---------- */
static const IMAGE_NT_HEADERS64 *nt_of_bytes(const BYTE *img, size_t size) {
    const IMAGE_DOS_HEADER *dos;
    LONG pe_off;
    const IMAGE_NT_HEADERS64 *nt;
    if (size < sizeof(IMAGE_DOS_HEADER)) return NULL;
    dos = (const IMAGE_DOS_HEADER *)img;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return NULL;
    pe_off = dos->e_lfanew;
    if (pe_off <= 0 || (size_t)pe_off + sizeof(IMAGE_NT_HEADERS64) > size) return NULL;
    nt = (const IMAGE_NT_HEADERS64 *)(img + pe_off);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return NULL;
    if (nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) return NULL;
    return nt;
}

MemModule *mem_map_bytes(const void *image, size_t size, const char *name) {
    const BYTE *img = (const BYTE *)image;
    const IMAGE_NT_HEADERS64 *nt_in;
    MemModule *m;
    BYTE *mem;
    SIZE_T size_of_image, hdr;
    WORD i;

    nt_in = nt_of_bytes(img, size);
    if (!nt_in) {
        set_err("not a PE64 image (%s)", name ? name : "?");
        return NULL;
    }
    DBG("%s: size=%llu sections=%u imagebase=%llx sizeimage=%llx sizeheaders=%llx\n",
        name ? name : "?", (unsigned long long)size, nt_in->FileHeader.NumberOfSections,
        (unsigned long long)nt_in->OptionalHeader.ImageBase,
        (unsigned long long)nt_in->OptionalHeader.SizeOfImage,
        (unsigned long long)nt_in->OptionalHeader.SizeOfHeaders);
    size_of_image = nt_in->OptionalHeader.SizeOfImage;
    if (size_of_image == 0 || size_of_image > 512u * 1024u * 1024u) {
        set_err("bad SizeOfImage (%s)", name ? name : "?");
        return NULL;
    }
    mem = mem_alloc_near(size_of_image);
    if (!mem) {
        set_err("VirtualAlloc failed (%s)", name ? name : "?");
        return NULL;
    }
    DBG("%s: allocated %p\n", name ? name : "?", (void *)mem);
    g_last_image_base = mem;
    hdr = nt_in->OptionalHeader.SizeOfHeaders;
    if (hdr > size) hdr = (SIZE_T)size;
    memcpy(mem, img, hdr);
    DBG("%s: headers copied (%llu)\n", name ? name : "?", (unsigned long long)hdr);
    for (i = 0; i < nt_in->FileHeader.NumberOfSections; i++) {
        const IMAGE_SECTION_HEADER *sh = IMAGE_FIRST_SECTION(nt_in) + i;
        DBG("%s: section %u va=%llx raw=%llx rawsize=%llx\n", name ? name : "?", i,
            (unsigned long long)sh->VirtualAddress, (unsigned long long)sh->PointerToRawData,
            (unsigned long long)sh->SizeOfRawData);
        if (!sh->SizeOfRawData) continue;
        if ((size_t)sh->PointerToRawData + sh->SizeOfRawData > size) {
            set_err("section out of range (%s)", name ? name : "?");
            VirtualFree(mem, 0, MEM_RELEASE);
            return NULL;
        }
        if ((size_t)sh->VirtualAddress + sh->SizeOfRawData > size_of_image) {
            set_err("section exceeds image (%s)", name ? name : "?");
            VirtualFree(mem, 0, MEM_RELEASE);
            return NULL;
        }
        memcpy(mem + sh->VirtualAddress, img + sh->PointerToRawData, sh->SizeOfRawData);
    }
    DBG("%s: sections copied\n", name ? name : "?");
    m = (MemModule *)calloc(1, sizeof(MemModule));
    if (!m) {
        VirtualFree(mem, 0, MEM_RELEASE);
        set_err("out of memory");
        return NULL;
    }
    m->base = mem;
    m->size = size_of_image;
    m->nt = (const IMAGE_NT_HEADERS64 *)(mem + ((const IMAGE_DOS_HEADER *)mem)->e_lfanew);
    if (name) {
        strncpy(m->name, name, sizeof(m->name) - 1);
        m->name[sizeof(m->name) - 1] = 0;
    }
    if (getenv("CRATIX_DEBUG")) fprintf(stderr, "[memdl] %s: reloc...\n", m->name);
    if (!apply_relocs(m)) {
        set_err("relocation failed (%s)", m->name);
        goto fail;
    }
    /* 头里的 ImageBase 改成**实际映射基址** ✅
     * （手工映射时头里还是原始值 ✅ → 展开器按头里的 ImageBase 找 .pdata ✅
     *   → 找不到 ✅ → 解释器里的异常接不住 ✅ → 崩溃现场被掩盖 ✅）。
     * 必须在重定位**之后**改 ✅（重定位的 delta 用的是原始 ImageBase ✅）。 */
    {
        IMAGE_NT_HEADERS64 *h = (IMAGE_NT_HEADERS64 *)(void *)m->nt;
        h->OptionalHeader.ImageBase = (ULONGLONG)(uintptr_t)m->base;
    }
    /* 先登记再解析导入 ✅：依赖环（A→B→A）在解析时 find_loaded(A) 要能找到自己 ✅，
     * 否则会无限递归映射 ✅。失败路径再摘除 ✅。 */
    if (g_loaded_count < MAX_LOADED) g_loaded[g_loaded_count++] = m;
    if (getenv("CRATIX_DEBUG")) fprintf(stderr, "[memdl] %s: imports...\n", m->name);
    if (!resolve_imports(m)) {
        set_err("%s", g_err);
        goto fail;
    }
    /* 展开器注册 ✅：ntdll 的展开器只认「加载器建的排序数组」✅ → 必须显式注册 ✅。
     *
     * **不要**再往 PEB 的加载器链表里塞伪造条目 ✅ —— 实测 ✅：**B 形态会崩** ✅
     * （B 形态里 .pyd 也是内存映射的 ✅ → 伪造条目进了加载器链表 ✅
     *   → 加载器建线程 / 走链表时按「加载顺序 + TLS 索引」处理 ✅ → 槽位错乱 ✅ → 崩 ✅）。
     * 而且它对展开器**没用** ✅（展开器查的是 ntdll 的排序数组 ✅ —— 见 §12.5 ✅）。 */
    mem_register_unwind(m);
    if (getenv("CRATIX_DEBUG")) fprintf(stderr, "[memdl] %s: tls...\n", m->name);
    /* 必须在 DllMain 之前接管 __tls_get_addr：CRT 初始化与 TLS 回调都会用到它 */
    mem_redirect_tls_getaddr(m);
    setup_tls(m, DLL_PROCESS_ATTACH);
    if (nt_in->OptionalHeader.AddressOfEntryPoint) {
        BOOL(WINAPI *dllmain)(HMODULE, DWORD, LPVOID) =
            (BOOL(WINAPI *)(HMODULE, DWORD, LPVOID))(void *)(mem + nt_in->OptionalHeader.AddressOfEntryPoint);
        /* CRATIX_DLLMAIN: 0=跳过，1=用伪 HMODULE（默认），2=用 NULL */
        const char *dm = getenv("CRATIX_DLLMAIN");
        int mode = dm ? dm[0] - '0' : 1;
        if (mode != 0) {
            DBG("%s: DllMain (mode=%d)...\n", m->name, mode);
            __try {
                dllmain(mode == 2 ? NULL : (HMODULE)m, DLL_PROCESS_ATTACH, NULL);
                DBG("%s: DllMain done\n", m->name);
            } __except (EXCEPTION_EXECUTE_HANDLER) {
                EXCEPTION_POINTERS *ep = GetExceptionInformation();
                void *addr = ep->ExceptionRecord->ExceptionAddress;
                unsigned long rva = (unsigned long)((BYTE *)addr - m->base);
                DBG("%s: DllMain EXCEPTION code=0x%08lX addr=%p rva=0x%lx in_module=%d\n", m->name,
                    ep->ExceptionRecord->ExceptionCode, addr, rva,
                    (addr >= (void *)m->base && addr < (void *)(m->base + m->size)) ? 1 : 0);
            }
        } else {
            DBG("%s: DllMain skipped\n", m->name);
        }
    }
    g_last_image_base = mem;
    return m;

fail:
    /* 摘除先登记的记录 ✅（见上方「先登记再解析导入」✅） */
    for (int li = 0; li < g_loaded_count; li++) {
        if (g_loaded[li] == m) {
            g_loaded[li] = g_loaded[--g_loaded_count];
            break;
        }
    }
    VirtualFree(mem, 0, MEM_RELEASE);
    free(m);
    return NULL;
}

const IMAGE_NT_HEADERS64 *mem_nt_of(void *base) {
    const IMAGE_DOS_HEADER *dos;
    LONG pe_off;
    const IMAGE_NT_HEADERS64 *nt;
    if (!base) return NULL;
    dos = (const IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return NULL;
    pe_off = dos->e_lfanew;
    if (pe_off <= 0) return NULL;
    nt = (const IMAGE_NT_HEADERS64 *)((BYTE *)base + pe_off);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return NULL;
    return nt;
}

int mem_redirect_iat_at(void *base, const IMAGE_NT_HEADERS64 *nt, const char *import_dll,
                        const char *const *names, const void *const *repls, int count) {
    const IMAGE_DATA_DIRECTORY *d = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    IMAGE_IMPORT_DESCRIPTOR *desc;
    if (!d->VirtualAddress) return 0;
    desc = (IMAGE_IMPORT_DESCRIPTOR *)((BYTE *)base + d->VirtualAddress);
    for (; desc->Name; desc++) {
        const char *dll = (const char *)((BYTE *)base + desc->Name);
        if (!name_eq(dll, import_dll)) continue;
        {
            BYTE *names_thunk = desc->OriginalFirstThunk ? (BYTE *)base + desc->OriginalFirstThunk : NULL;
            BYTE *iat = (BYTE *)base + desc->FirstThunk;
            size_t idx = 0;
            for (;;) {
                ULONGLONG v = names_thunk ? *(const ULONGLONG *)(names_thunk + idx * 8)
                                          : *(const ULONGLONG *)(iat + idx * 8);
                int k;
                if (!v) break;
                if (!(v & IMAGE_ORDINAL_FLAG64)) {
                    const char *fname = (const char *)((BYTE *)base + (DWORD)(v & 0x7FFFFFFF) + 2);
                    for (k = 0; k < count; k++) {
                        if (strcmp(fname, names[k]) == 0) {
                            DWORD oldprot = 0;
                            /* OS 加载的模块：IAT 所在页可能是只读，先放开再写 */
                            if (VirtualProtect((void *)(iat + idx * 8), 8, PAGE_READWRITE, &oldprot)) {
                                *(ULONGLONG *)(iat + idx * 8) = (ULONGLONG)repls[k];
                                VirtualProtect((void *)(iat + idx * 8), 8, oldprot, &oldprot);
                            } else {
                                *(ULONGLONG *)(iat + idx * 8) = (ULONGLONG)repls[k];
                            }
                            break;
                        }
                    }
                }
                idx++;
            }
        }
        return 1;
    }
    return 0;
}

/* ------------------------------------------------------------------
 * 伪 __tls_get_addr
 *
 * 手工映射的模块拿不到 OS 加载器分配的 TLS 槽，而 CPython 3.13+ 把线程状态
 * 放在 __declspec(thread) 变量里 —— 没有它，Py_Initialize 会死在
 * 「_PyThreadState_Attach: non-NULL old thread state」。
 *
 * 做法：把模块对 ntdll!__tls_get_addr 的导入重定向到本函数，按「模块 + 偏移」
 * 维护 per-thread 存储（Win32 TLS 槽 → 该线程的模板副本），语义对齐加载器：
 * 返回「该线程内该变量的地址」。
 * ------------------------------------------------------------------ */
typedef void *(WINAPI *fn_real_tls_get_addr)(mem_tls_index *);
static fn_real_tls_get_addr g_real_tls_getaddr = NULL;

static MemModule *mem_from_base(BYTE *base) {
    int i;
    for (i = 0; i < g_loaded_count; i++)
        if (g_loaded[i]->base == base) return g_loaded[i];
    return NULL;
}

void *mem_fake_tls_get_addr(mem_tls_index *p) {
    MemModule *m;
    void *block;
    if (!p) return NULL;
    if (getenv("CRATIX_DEBUG"))
        fprintf(stderr, "[memdl] fake __tls_get_addr p=%p mod=%p off=%u\n", (void *)p,
                (void *)(uintptr_t)p->ModuleAddress, (unsigned)p->Offset);
    /* 模块归属 ✅：动态 TLS（ModuleAddress=模块基址 ✅）✅；
     * 静态 TLS（ModuleAddress=0 ✅ → 描述符在**调用方模块**自己的 .data 里 ✅）。 */
    m = p->ModuleAddress ? mem_from_base((BYTE *)(uintptr_t)p->ModuleAddress) : mem_module_by_addr(p);
    if (!m) {
        /* 不是我们映射的模块 → 交回真正的 ntdll 实现 */
        if (g_real_tls_getaddr) return g_real_tls_getaddr(p);
        return NULL;
    }
    /* 我们的每线程块 ✅（语义对齐加载器 ✅：模块块 + 变量偏移 ✅） */
    if (m->tls_static_index != 0xFFFFFFFFu) {
        BYTE *base = (BYTE *)cratix_tls_block(m->tls_static_index);
        if (base) return base + p->Offset;
    }
    if (!m->tls_slot) return NULL;
    block = TlsGetValue(m->tls_slot);
    if (!block) {
        size_t sz = m->tls_size ? m->tls_size : 64;
        block = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sz);
        if (!block) return NULL;
        if (m->tls_template && m->tls_size) memcpy(block, m->tls_template, m->tls_size);
        TlsSetValue(m->tls_slot, block);
    }
    return (BYTE *)block + p->Offset;
}

int mem_redirect_tls_getaddr(MemModule *m) {
    /* MSVC x64 的导入名是 **_tls_get_addr** ✅（单下划线 ✅ —— 写成 __tls_get_addr ✅
     * 会一个都匹配不上 ✅ → 重定向静默失败 ✅ → 解释器调用真实 ntdll 实现 ✅ → 抛异常 ✅）。 */
    static const char *names[2] = {"_tls_get_addr", "__tls_get_addr"};
    static const void *repls[2];
    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    if (!g_real_tls_getaddr && ntdll) {
        g_real_tls_getaddr = (fn_real_tls_get_addr)GetProcAddress(ntdll, "_tls_get_addr");
        if (!g_real_tls_getaddr)
            g_real_tls_getaddr = (fn_real_tls_get_addr)GetProcAddress(ntdll, "__tls_get_addr");
    }
    repls[0] = (const void *)mem_fake_tls_get_addr;
    repls[1] = (const void *)mem_fake_tls_get_addr;
    {
        int rc = mem_redirect_iat_at(m->base, m->nt, "ntdll.dll", names, repls, 2);
        if (getenv("CRATIX_DEBUG"))
            fprintf(stderr, "[memdl] %s: redirect __tls_get_addr rc=%d real=%p\n", m->name, rc,
                    (void *)g_real_tls_getaddr);
        return rc;
    }
}

int mem_redirect_import(MemModule *m, const char *import_dll,
                        const char *const *names, const void *const *repls, int count) {
    if (!m) return 0;
    return mem_redirect_iat_at(m->base, m->nt, import_dll, names, repls, count);
}
