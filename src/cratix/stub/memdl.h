#ifndef CRATIX_MEMDL_H
#define CRATIX_MEMDL_H

/* memdl —— 内存 PE 加载器
 *
 * 把 DLL/EXE 的字节直接映射进内存并完成重定位、导入解析与 DllMain，
 * 全程不落盘。额外提供导出解析与导入表改写（IAT 重定向），
 * 供 cratix 启动器接管 CPython 的扩展模块加载路径。
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct MemModule MemModule;

/* 映射一段 PE 字节。name 用于模块名查找（大小写不敏感）。失败返回 NULL。 */
MemModule *mem_map_bytes(const void *image, size_t size, const char *name);
/* 依赖解析钩子 ✅：resolve_symbol 在「已映射模块 + 系统 DLL」都落空时调用 ✅
 * （stub 注册：按 DLL 名查归档并 memdl 映射 ✅ —— 归档内 DLL 走 memdl ✅ §6.2）。
 * 返回已映射模块或 NULL。 */
typedef MemModule *(*mem_dep_resolver_fn)(const char *dll);
void mem_set_dep_resolver(mem_dep_resolver_fn fn);
/* 按名字找已映射模块 */
MemModule *mem_find_loaded(const char *name);
/* 伪 HMODULE → 模块 */
MemModule *mem_from_hmodule(HMODULE h);
/* 取伪 HMODULE */
HMODULE mem_as_hmodule(MemModule *m);
/* 取镜像基址（诊断用：定位故障落在哪个模块的哪个 RVA） */
BYTE *mem_image_base(MemModule *m);
BYTE *mem_last_image_base(void);
/* 镜像大小 ✅（诊断扫描栈用 ✅） */
size_t mem_image_size(MemModule *m);
/* PEB 注册 ✅：让 ntdll 的异常展开器认得手工映射的模块 ✅
 * （不在加载器表里 ✅ → 展开失败 ✅ → 解释器里的异常接不住 ✅ → 崩溃现场被掩盖 ✅）。 */
int mem_peb_register(MemModule *m);
/* 展开器注册 ✅：把模块的 .pdata 注册给 ntdll ✅（见 memdl.c 的说明 ✅）。 */
int mem_register_unwind(MemModule *m);
void mem_peb_unregister_all(void);
/* 诊断：故障地址落在哪个已映射模块 ✅（返回模块名或 NULL ✅） */
const char *mem_module_by_addr(const void *addr);
BYTE *mem_base_by_addr(const void *addr);
/* 导出解析：按名 / 按序号 */
void *mem_get_export(MemModule *m, const char *name);
void *mem_get_ordinal(MemModule *m, WORD ordinal);
/* 改写导入表槽位：把 import_dll 里 names[i] 的 IAT 槽指向 repls[i] */
/* 按**函数名**重定向 ✅ —— 不管来自哪个 DLL ✅
 * （现代 MSVC 的导入表常写 API-set 名 ✅（api-ms-win-core-libraryloader-l1-2-0.dll ✅），
 *   只按 "KERNEL32.dll" 匹配会漏 ✅ → 按名加载的扩展就找不到 DLL ✅）。 */
int mem_redirect_import_any(MemModule *m, const char *const *names, const void *const *repls, int count);
/* 同上 ✅ —— 但作用在**任意基址**的镜像上 ✅（OS 加载的模块 ✅ 也需要 ✅）。 */
int mem_redirect_import_any_at(void *base, const IMAGE_NT_HEADERS64 *nt, const char *const *names,
                             const void *const *repls, int count);
int mem_redirect_import(MemModule *m, const char *import_dll,
                        const char *const *names, const void *const *repls, int count);
/* 同上，但直接作用于任意基址的 PE（用于 OS 正常加载的模块：改写其 IAT 同样能接管扩展加载路径） */
int mem_redirect_iat_at(void *base, const IMAGE_NT_HEADERS64 *nt, const char *import_dll,
                        const char *const *names, const void *const *repls, int count);
/* 取已加载模块的 PE 头（base = 模块基址） */
const IMAGE_NT_HEADERS64 *mem_nt_of(void *base);
/* 最近一次错误信息 */
const char *mem_last_error(void);

/* x64 TLS 索引（与 ntdll 的 TLS_INDEX 同布局） */
typedef struct {
    ULONG ModuleAddress;
    ULONG Offset;
} mem_tls_index;

/* 宿主 TLS 池 ✅：让每个被映射模块**独占**静态 TLS 槽 ✅
 *
 * 背景（x64 事实 ✅）：
 *   - 静态 TLS 的索引**不是编译期常量** ✅ —— 模块的代码是
 *     `mov ecx, [rip+X]`（X 指向**自己** TLS 目录的 AddressOfIndex ✅）
 *     → `mov rax, gs:[0x58]`（TEB 的 TLS 槽指针数组 ✅）
 *     → `mov rax, [rax+rcx*8]`（取该索引的 per-thread 数据 ✅）
 *   - 所以**索引可在运行时改写** ✅ → 每个模块可以拿到**自己的**索引 ✅
 *   - 槽位数组的长度由**加载器在进程启动时**确定 ✅ → 必须让宿主（stub exe）
 *     预留足够多的槽 ✅（宿主 TLS 数据越大 ✅ → 槽越多 ✅）
 *
 * 机制 ✅：
 *   1) 宿主 exe 自带一块大 TLS 池 ✅（64 KB ✅）→ 加载器为它预留 8192 个槽 ✅；
 *   2) 每个模块从池里划走一段**互不重叠**的槽 ✅ → 索引 = 宿主索引 + 池内偏移/8 ✅；
 *   3) 模块的 TLS 模板字节写两处 ✅：
 *      · 宿主镜像里的 TLS 原始数据 ✅（**只读页 ✅ → VirtualProtect ✅**）
 *        → **新线程创建时加载器会复制这份模板** ✅ → 新线程自动继承 ✅；
 *      · 当前线程的槽 ✅（启动时的快照 ✅ → 当前线程要手动同步 ✅）；
 *   4) 模块自己的 AddressOfIndex 写成该索引 ✅ → 它的访问就落到自己的槽 ✅。
 */
typedef struct {
    DWORD host_index;      /* 宿主（stub exe）的 TLS 索引 ✅（通常是 0 ✅） */
    unsigned char *raw;    /* 宿主 TLS 原始数据在**镜像**里的地址 ✅（可能只读 ✅） */
    size_t raw_bytes;      /* 原始数据 + 零填充 ✅ */
    size_t pool_offset;    /* 池在原始数据里的字节偏移 ✅ */
    size_t pool_bytes;     /* 池大小 ✅ */
} mem_tls_host_info;
void mem_tls_host_configure(const mem_tls_host_info *info);
/* 当前线程的 TLS 槽指针数组 ✅（TEB+0x58 ✅）—— 宿主算池偏移时要用 ✅ */
void **mem_tls_slot_array(void);
/* 伪 __tls_get_addr：给手工映射的模块提供 per-thread TLS */
void *mem_fake_tls_get_addr(mem_tls_index *p);
/* 把模块对 ntdll!__tls_get_addr 的导入重定向到伪实现 */
int mem_redirect_tls_getaddr(MemModule *m);

#ifdef __cplusplus
}
#endif
#endif
