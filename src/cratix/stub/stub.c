/* cratix 启动器 —— M0 spike
 *
 * 产物结构：[stub.exe][zip 载荷][cookie(32B)]
 * 目标：单文件 exe、运行期零落盘。
 *
 * 本 spike 验证四件事：
 *   1) 手工映射 python314.dll（memdl）后能正常 Py_Initialize；
 *   2) exe 自身是合法 zip → zipimport 能从中读取 .pyc 并执行；
 *   3) 改写 python314.dll 的 IAT 后，CPython 自己的扩展加载路径会经过我们的内存加载器；
 *   4) 全程不向磁盘写任何文件。
 *
 * 注意：stub 不链接 python314.lib —— 否则 OS 会在进程启动时从磁盘加载 python314.dll，
 * 与「手工映射」的目标冲突。所有 Python API 都在运行时从已映射模块的导出表解析。
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <io.h>       /* _fileno / _get_osfhandle ✅ —— 把 Win32 标准句柄接到 CRT 的流上 ✅ */
#include <fcntl.h>    /* _O_RDONLY / _O_BINARY ✅（os.dup 的 VFS 包装 ✅） */
#include <stdint.h>   /* intptr_t ✅ */
#include "memdl.h"
#include "zipread.h"
#include "vfs.h"
#include "tlsfix.h"
#include "diag.h"
#include "xz/LzmaDec.h" /* LZMA SDK（公有领域 ✅）—— ZIP_LZMA 条目解码 ✅ M7 ✅ */
#include "zstd/zstd.h"  /* libzstd（BSD ✅）—— ZIP_ZSTANDARD 条目解码 ✅ M7 ✅ */

#ifndef PYTHON_HOME
#define PYTHON_HOME "C:\\Program Files\\Python314"
#endif

#define COOKIE_SIZE 32

typedef struct {
    char magic[8];
    unsigned long long off;
    unsigned long long size;
    unsigned int crc;
    unsigned int reserved;
} cookie_t;

/* ---- 真实系统 API 指针（改写 IAT 前保存） ---- */
typedef HMODULE(WINAPI *fn_LoadLibraryExW)(LPCWSTR, HANDLE, DWORD);
typedef HMODULE(WINAPI *fn_LoadLibraryW)(LPCWSTR);
typedef FARPROC(WINAPI *fn_GetProcAddress)(HMODULE, LPCSTR);
static fn_LoadLibraryExW g_real_LoadLibraryExW = NULL;
static fn_LoadLibraryW g_real_LoadLibraryW = NULL;
static fn_GetProcAddress g_real_GetProcAddress = NULL;
typedef BOOL(WINAPI *fn_FreeLibrary)(HMODULE);
static fn_FreeLibrary g_real_FreeLibrary = NULL;

/* ---- Python API（动态解析） ---- */
typedef void PyObject;
typedef int(__cdecl *fn_Py_InitializeEx)(int);
typedef int(__cdecl *fn_Py_IsInitialized)(void);
typedef int(__cdecl *fn_Py_FinalizeEx)(void);
typedef int(__cdecl *fn_PyRun_SimpleString)(const char *);
typedef void(__cdecl *fn_PyErr_Print)(void);
typedef PyObject *(__cdecl *fn_PyImport_ImportModule)(const char *);
typedef PyObject *(__cdecl *fn_PyObject_GetAttrString)(PyObject *, const char *);
typedef PyObject *(__cdecl *fn_PyObject_CallObject)(PyObject *, PyObject *);
typedef PyObject *(__cdecl *fn_PyTuple_New)(long long);
typedef PyObject *(__cdecl *fn_PyTuple_SetItem)(PyObject *, long long, PyObject *);
typedef PyObject *(__cdecl *fn_PyLong_FromLongLong)(long long);
typedef void *(__cdecl *fn_PyCapsule_GetPointer)(PyObject *, const char *);
typedef PyObject *(__cdecl *fn_PyModule_GetDef)(PyObject *);
typedef PyObject *(__cdecl *fn_PyModule_ExecDef)(PyObject *, PyObject *);
typedef int(__cdecl *fn_PyArg_ParseTuple)(PyObject *, const char *, ...);
typedef PyObject *(__cdecl *fn_PyErr_Format)(PyObject *, const char *, ...);
typedef PyObject *(__cdecl *fn_PyModule_Create2)(void *, void *);
/* PyModule_FromDefAndSpec 在 CPython 头文件里是**宏** ✅（转发到 PyModule_FromDefAndSpec2 ✅）
 * → DLL 里只有 **PyModule_FromDefAndSpec2**（三参，第三参 PYTHON_API_VERSION=1013 ✅）✅。
 * 旧代码按两参名解析 ✅ → NULL ✅ → is_def 分支静默跳过 ✅ → 返回裸 moduledef ✅
 * → exec_module 给 moduledef 设 __file__ ✅ → AttributeError ✅ 冒烟当场断 ✅。 */
typedef PyObject *(__cdecl *fn_PyModule_FromDefAndSpec)(void *, PyObject *, int);
typedef int(__cdecl *fn_PyImport_AppendInittab)(const char *, void *);
typedef PyObject *(__cdecl *fn_PyModule_New)(const char *);
/* PyCFunction_New(ml, self) 是**两个参数** ✅（三参数的是 PyCFunction_NewEx ✅）。
 * 之前按 (self, func, name) 三参调用 ✅ → 函数指针被当成 self 做 Py_XNewRef ✅
 * → 往只读代码段写引用计数 → 0xC0000005（veh 抓到的就是这个 ✅）。 */
typedef PyObject *(__cdecl *fn_PyCFunction_New)(void *, void *);
/* PyImport_AddModule(const char *) 一个参数 ✅：在 sys.modules 里建/取模块并返回**借用引用** ✅。
 * 之前按 (name, module) 两参调 PyImport_AddModuleObject ✅ → 第二参被忽略 ✅
 * → sys.modules 里留下的是「空模块」✅（load 属性根本没挂上去 ✅）。 */
typedef PyObject *(__cdecl *fn_PyImport_AddModule)(const char *);
typedef PyObject *(__cdecl *fn_Py_BuildValue)(const char *, ...);
typedef int(__cdecl *fn_PyObject_SetAttrString)(PyObject *, const char *, PyObject *);
typedef void(__cdecl *fn_Py_DecRef)(PyObject *);
typedef PyObject *(__cdecl *fn_PyCFunction_Call)(PyObject *, PyObject *);
typedef int(__cdecl *fn_PyObject_IsInstance)(PyObject *, PyObject *);

static fn_Py_InitializeEx p_Py_InitializeEx = NULL;
static fn_Py_IsInitialized p_Py_IsInitialized = NULL;
static fn_Py_FinalizeEx p_Py_FinalizeEx = NULL;
static fn_PyRun_SimpleString p_PyRun_SimpleString = NULL;
static fn_PyErr_Print p_PyErr_Print = NULL;
static fn_PyImport_ImportModule p_PyImport_ImportModule = NULL;
static fn_PyObject_GetAttrString p_PyObject_GetAttrString = NULL;
static fn_PyObject_CallObject p_PyObject_CallObject = NULL;
static fn_PyTuple_New p_PyTuple_New = NULL;
static fn_PyTuple_SetItem p_PyTuple_SetItem = NULL;
static fn_PyLong_FromLongLong p_PyLong_FromLongLong = NULL;
static fn_PyModule_GetDef p_PyModule_GetDef = NULL;
static fn_PyModule_ExecDef p_PyModule_ExecDef = NULL;
static fn_PyObject_IsInstance p_PyObject_IsInstance = NULL;
static void *p_PyModuleDef_Type = NULL; /* 数据导出：指向 PyModuleDef_Type 变量 */
static fn_PyArg_ParseTuple p_PyArg_ParseTuple = NULL;
static fn_PyErr_Format p_PyErr_Format = NULL;
static fn_PyModule_Create2 p_PyModule_Create2 = NULL;
static fn_PyModule_FromDefAndSpec p_PyModule_FromDefAndSpec = NULL;
static fn_PyImport_AppendInittab p_PyImport_AppendInittab = NULL;
static fn_PyModule_New p_PyModule_New = NULL;
static fn_PyCFunction_New p_PyCFunction_New = NULL;
static fn_PyImport_AddModule p_PyImport_AddModule = NULL;
static fn_Py_BuildValue p_Py_BuildValue = NULL;
static fn_PyObject_SetAttrString p_PyObject_SetAttrString = NULL;
static fn_Py_DecRef p_Py_DecRef = NULL;
static fn_PyCapsule_GetPointer p_PyCapsule_GetPointer = NULL;
static PyObject *p_PyExc_ImportError = NULL;   /* 数据导出：指向 PyExc_ImportError 变量 */
static void *p_PyExc_ImportError_addr = NULL;

/* ---- 归档上下文 ---- */
static zipidx_t g_zip;

/* 宿主的 TLS 池 ✅：让加载器给本 exe 预留**足够的 TLS 索引空间** ✅
 * （x64 的 TLS 槽数组长度在**进程启动时**就定死了 ✅ → 宿主必须一次预留够 ✅）。
 * 池里的空间逐段划给被映射模块 ✅ → 每个模块**独占**静态 TLS 槽 ✅
 * （机制见 memdl.h 的 mem_tls_host_info ✅）。
 * 池字节同时是「新线程的模板」载体 ✅ —— 加载器建新线程时复制宿主 TLS 原始数据 ✅
 * → 我们写进镜像的那份模板会被新线程继承 ✅。 */
__declspec(thread) volatile char cratix_tls_pool[64 * 1024];

/* 日志函数在下方定义 ✅ —— 本 helper 要用它 ✅ */
void cy_log(const char *fmt, ...);

/* 宿主 TLS 池 ✅：把宿主自己的 TLS 索引 / 原始数据 / 池位置交给 memdl ✅
 * → 被映射模块各拿**独占**静态 TLS 槽 ✅（x64 的静态 TLS 索引来自模块**自己**的
 *   TLS 目录 ✅ → 运行时可改写 ✅ —— 机制见 memdl.h ✅）。
 * 必须在**映射任何模块之前**调用 ✅（否则先映射的模块拿不到独占槽 ✅）。 */
static void cratix_configure_host_tls(void) {
    DWORD host_index = 0xFFFFFFFFu;
    HMODULE selfh = GetModuleHandleW(NULL);
    const IMAGE_NT_HEADERS64 *selfnt = mem_nt_of((void *)selfh);
    if (!selfnt) return;
    {
        const IMAGE_DATA_DIRECTORY *td =
            &selfnt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_TLS];
        if (!td->VirtualAddress) return;
        const IMAGE_TLS_DIRECTORY *t = (const IMAGE_TLS_DIRECTORY *)((BYTE *)selfh + td->VirtualAddress);
        mem_tls_host_info info;
        void **slots;
        size_t raw_bytes;
        host_index = *(const DWORD *)(uintptr_t)t->AddressOfIndex;
        raw_bytes = (size_t)(t->EndAddressOfRawData - t->StartAddressOfRawData) + t->SizeOfZeroFill;
        memset(&info, 0, sizeof(info));
        info.host_index = host_index;
        info.raw = (unsigned char *)(uintptr_t)t->StartAddressOfRawData;
        info.raw_bytes = raw_bytes;
        slots = mem_tls_slot_array();
        if (slots && host_index != 0xFFFFFFFFu) {
            char *pool_addr = (char *)(void *)cratix_tls_pool;
            char *slot_base = (char *)slots[host_index];
            if (pool_addr && slot_base && pool_addr >= slot_base &&
                (size_t)(pool_addr - slot_base) < raw_bytes) {
                info.pool_offset = (size_t)(pool_addr - slot_base);
                info.pool_bytes = sizeof(cratix_tls_pool);
                if (info.pool_offset + info.pool_bytes > raw_bytes)
                    info.pool_bytes = raw_bytes - info.pool_offset;
            }
        }
        mem_tls_host_configure(&info);
        cy_log("[stub] host TLS index = %lu (pool=%p size=%u)\n", (unsigned long)host_index,
               (void *)cratix_tls_pool, (unsigned)sizeof(cratix_tls_pool));
    }
}

static HMODULE hpy_disk = NULL;
/* 内存映射的解释器 ✅（A 形态 ✅）：加载请求回指它 ✅（防双解释器 ✅） */
static MemModule *g_interp_mem = NULL;
/* A 形态（**默认** ✅ M6c ✅ 用户口径：不能解压释放文件 ✅）标志 ✅
 * —— A 形态里解释器与全部原生模块都走**内存** ✅ → 每个被映射模块的加载面
 * 必须由我们接管 ✅；CRATIX_INTERP=disk ✅ 回退 B 形态（OS 从缓存目录加载 ✅）✅。 */
static int g_a_form = 0;
static BYTE *g_module_base = NULL;

/* 日志函数在下方定义 ✅ —— VEH 处理器要用 ✅ → 前置声明 ✅ */
void cy_log(const char *fmt, ...);

static LONG WINAPI veh_handler(EXCEPTION_POINTERS *ep) {
    char buf[320];
    int n;
    unsigned long rva = 0;
    int in_mod = 0;
    if (!g_module_base) g_module_base = mem_last_image_base();
    if (ep && ep->ExceptionRecord && g_module_base) {
        BYTE *a = (BYTE *)ep->ExceptionRecord->ExceptionAddress;
        if (a >= g_module_base && a < g_module_base + 0x1000000) {
            rva = (unsigned long)(a - g_module_base);
            in_mod = 1;
        }
    }
    {
        HMODULE hm = NULL;
        char modname[MAX_PATH] = "?";
        const char *mm = mem_module_by_addr(ep ? (void *)ep->ExceptionRecord->ExceptionAddress : NULL);
        if (mm) {
            strncpy(modname, mm, MAX_PATH - 1);
            modname[MAX_PATH - 1] = 0;
        } else if (ep && GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                                            (LPCWSTR)ep->ExceptionRecord->ExceptionAddress, &hm)) {
            GetModuleFileNameA(hm, modname, MAX_PATH);
        }
        n = sprintf(buf,
                      "[veh] code=0x%08lX addr=%p rva=0x%lx in_module=%d mod=%s\r\n",
                      ep ? ep->ExceptionRecord->ExceptionCode : 0,
                      ep ? (void *)ep->ExceptionRecord->ExceptionAddress : NULL, rva, in_mod, modname);
    }
    /* 寄存器 + 栈顶返回地址 ✅：定位「谁传了坏指针」✅ */
    if (ep && ep->ContextRecord) {
        CONTEXT *ctx = ep->ContextRecord;
        cy_log("[veh] flags_after=0x%llx tf=%d asm_calls=%lld\r\n", (unsigned long long)g_tlsfix_flags_after,
               (g_tlsfix_flags_after & 0x100LL) ? 1 : 0, g_tlsfix_asm_calls);
        cy_log("[veh] saved_flags=0x%llx tf=%d\r\n", (unsigned long long)g_tlsfix_saved_flags,
               (g_tlsfix_saved_flags & 0x100LL) ? 1 : 0);
        if (g_interp_mem)
            cratix_diag_lookup((const void *)(ep ? ep->ExceptionRecord->ExceptionAddress
                                                 : (const void *)(mem_image_base(g_interp_mem) + 0x1000)));
        if (g_interp_mem) cratix_diag_modhandle((const void *)(mem_image_base(g_interp_mem) + 0x1000));
        cy_log("[veh] eflags=%llx tf=%d\r\n", (unsigned long long)ctx->EFlags,
               (ctx->EFlags & 0x100ULL) ? 1 : 0);
        if (!cratix_legacy())
            cratix_diag_scan((void *)ctx->Rsp, g_interp_mem ? mem_image_base(g_interp_mem) : NULL,
                             g_interp_mem ? mem_image_size(g_interp_mem) : 0);
        void *ret = *(void **)ctx->Rsp;
        const char *rm = mem_module_by_addr(ret);
        cy_log("[veh] rip=%p rsp=%p rbp=%p rax=%p rbx=%p rcx=%p rdx=%p r8=%p r9=%p r12=%p r13=%p r10=%p r11=%p r14=%p r15=%p\r\n",
               (void *)ctx->Rip, (void *)ctx->Rsp, (void *)ctx->Rbp, (void *)ctx->Rax,
               (void *)ctx->Rbx, (void *)ctx->Rcx, (void *)ctx->Rdx, (void *)ctx->R8, (void *)ctx->R9,
               (void *)ctx->R12, (void *)ctx->R13, (void *)ctx->R10, (void *)ctx->R11,
               (void *)ctx->R14, (void *)ctx->R15);
        {
            HMODULE hm2 = NULL;
            if (ep && GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                                         (LPCWSTR)ep->ExceptionRecord->ExceptionAddress, &hm2)) {
                cy_log("[veh] faultmod=%p rva=0x%lx\r\n", (void *)hm2,
                       (unsigned long)((BYTE *)ep->ExceptionRecord->ExceptionAddress - (BYTE *)hm2));
            }
        }

        cy_log("[veh] ret=%p owner=%s fault=%p\r\n", ret, rm ? rm : "(unknown)",
               (ep->ExceptionRecord->NumberParameters >= 2 ? (void *)ep->ExceptionRecord->ExceptionInformation[1] : NULL));
        {
            void *tt = cratix_tls_table();
            cy_log("[veh] tls table=%p block2=%p\r\n", tt, cratix_tls_block(2));
{
                BYTE *mb = mem_base_by_addr(ep ? (void *)ep->ExceptionRecord->ExceptionAddress : NULL);
                void **tbl = (void **)tt;

                cy_log("[veh] tbl=%p t0=%p t1=%p t2=%p t3=%p\r\n", (void *)tbl, tbl ? tbl[0] : NULL,
                       tbl ? tbl[1] : NULL, tbl ? tbl[2] : NULL, tbl ? tbl[3] : NULL);


                cy_log("[veh] asm_calls=%lld asm_ret=%p retaddr=%p\r\n", g_tlsfix_asm_calls,
                       (void *)g_tlsfix_asm_ret, (void *)g_tlsfix_retaddr);
                {
                    BYTE *blk = (BYTE *)cratix_tls_block(2);
                    char hx[3 * 64 + 1];
                    int bi, hk = 0;
                    cy_log("[veh] tls margin:", (void *)0);
                    if (blk) {
                        for (bi = 0; bi < 64; bi++) {
                            BYTE v = blk[40 + bi];
                            hx[hk++] = (v == 0xCD) ? '.' : (char)((v < 10 ? '0' + v : 'A' + v - 10));
                        }
                        hx[hk] = 0;
                        cy_log(" %s", hx);
                    }
                    cy_log("\r\n");
                }

}
            if (g_interp_mem) {
                BYTE *mb = mem_image_base(g_interp_mem);
                const IMAGE_NT_HEADERS64 *pnt = mem_nt_of((void *)mb);
                const IMAGE_DATA_DIRECTORY *td =
                    &pnt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_TLS];
                const IMAGE_TLS_DIRECTORY *t = (const IMAGE_TLS_DIRECTORY *)(mb + td->VirtualAddress);
                DWORD idx = *(const DWORD *)(uintptr_t)t->AddressOfIndex;
                BYTE *code = mb + 0x18C2D1;
                cy_log("[veh] interp TLS index=%lu code@18c2d1=%02X %02X %02X %02X %02X %02X %02X %02X %02X\r\n",
                       (unsigned long)idx, code[0], code[1], code[2], code[3], code[4], code[5], code[6], code[7], code[8]);
            }
        }
        /* 挂着的 Python 异常 ✅：shiboken6 很可能 import 失败后没查返回值 ✅ → 把异常打出来 ✅ */
        if (p_PyErr_Print && p_Py_IsInitialized && p_Py_IsInitialized()) {
            __try {
                p_PyErr_Print();
            } __except (EXCEPTION_EXECUTE_HANDLER) {
                cy_log("[veh] PyErr_Print boom\r\n");
            }
        }
        {
            typedef BOOL(WINAPI *fn_qts)(ULONG_PTR *, ULONG_PTR *);
            static fn_qts qts = NULL;
            ULONG_PTR lo = 0, hi = 0;
            if (!qts) {
                HMODULE k = GetModuleHandleW(L"kernel32.dll");
                qts = k ? (fn_qts)(void *)GetProcAddress(k, "QueryThreadStackLimits") : NULL;
            }
            if (qts && qts(&lo, &hi))
                cy_log("[veh] tid=%u stack low=%p high=%p rsp=%p margin=%lld\r\n", GetCurrentThreadId(),
                       (void *)lo, (void *)hi, (void *)ctx->Rsp,
                       (long long)((char *)(uintptr_t)ctx->Rsp - (char *)(uintptr_t)lo));
            else
                cy_log("[veh] tid=%u (stack limits unavailable)\r\n", GetCurrentThreadId());
        }
        void *frames[24];
        USHORT got = 0;
        typedef USHORT(WINAPI *fn_cap)(ULONG, ULONG, PVOID *, PULONG);
        static fn_cap cap = NULL;
        if (!cap) {
            HMODULE nt = GetModuleHandleW(L"ntdll.dll");
            cap = nt ? (fn_cap)(void *)GetProcAddress(nt, "RtlCaptureStackBackTrace") : NULL;
        }
        if (cap) got = cap(0, 24, frames, NULL);
        USHORT k;
        cy_log("[veh] stack (%u frames):\r\n", (unsigned)got);
        for (k = 0; k < got && k < 24; k++) {
            const char *own = mem_module_by_addr(frames[k]);
            char path[MAX_PATH] = "";
            if (!own) {
                HMODULE hm = NULL;
                if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, (LPCWSTR)frames[k], &hm))
                    GetModuleFileNameA(hm, path, MAX_PATH);
            }
            cy_log("[veh]   #%u %p %s%s%s\r\n", (unsigned)k, frames[k], own ? own : path,
                   own ? "+" : "", own ? "" : "");
        }
    }
    {
        HANDLE h = GetStdHandle(STD_ERROR_HANDLE);
        DWORD w = 0;
        WriteFile(h, buf, (DWORD)n, &w, NULL);
    }
    /* 单步异常 ✅：CRT 展开器用 TF 做单步 ✅ —— 这里放它过去 ✅（诊断用 ✅）
     * （否则展开器里的单步会逃逸成未处理异常 ✅ → 真因被掩盖 ✅）。 */
    /* 单步异常 ✅：TF 是「被继续执行的异常」带进来的 ✅ → 会掩盖真因 ✅
     * （清掉 TF 并放行 ✅ —— 只用于诊断 ✅ —— CRATIX_TLSW=1 才开 ✅）。 */
    if (ep && ep->ExceptionRecord && ep->ContextRecord &&
        ep->ExceptionRecord->ExceptionCode == 0x80000004u) {
        const char *sw = getenv("CRATIX_TLSW");
        if (sw && sw[0] == '1') {
            ep->ContextRecord->EFlags &= ~0x100ULL;
            cy_log("[veh] swallow single-step ✅ (TF cleared)\r\n");
            return EXCEPTION_CONTINUE_EXECUTION;
        }
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static char g_exe_dir[MAX_PATH];
static char g_exe_dir_slash[MAX_PATH]; /* 归一化分隔符后的 exe 目录（比较用） */
static char g_exe_path[MAX_PATH];

/* 载荷来源二选一 ✅（D1 ✅）：
 *   内嵌 ✅（onefile ✅）：载荷追加在 exe 末尾 ✅（cookie 定位 ✅）；
 *   旁置 ✅（onedir ✅）：<exe_dir>/payload.zip ✅（独立文件 ✅）。 */
static const unsigned char *g_payload_base = NULL;
static size_t g_payload_size = 0;
static WCHAR g_payload_zip_w[MAX_PATH]; /* 旁置时 zip 的绝对路径 ✅（PYTHONPATH 用 ✅） */
static int g_payload_embedded = 1;

/* 新增特性的总闸 ✅（诊断用 ✅ —— 用来定位「哪一项改动引起回归」✅）。 */
static int cratix_legacy(void) {
    const char *v = getenv("CRATIX_LEGACY");
    return v && v[0] == '1';
}

static HMODULE WINAPI cratix_LoadLibraryExW(LPCWSTR name, HANDLE file, DWORD flags);
static HMODULE WINAPI cratix_LoadLibraryW(LPCWSTR name);
static FARPROC WINAPI cratix_GetProcAddress(HMODULE h, LPCSTR name);
static BOOL WINAPI cratix_FreeLibrary(HMODULE h);

/* CRT 的符号解析 ✅ —— **必须也接管** ✅
 *
 * 实测 ✅：CPython 的动态扩展加载（dynload_shlib ✅）走的是 **CRT 的 dlsym** ✅ ——
 * 而解释器**并不直接 import GetProcAddress** ✅（python314.dll 的导入表里没有 ✅）
 * → 只改解释器的 IAT 拦不到 ✅ → 只能改 **ucrtbase 的 IAT** ✅。
 * 非我们模块的句柄 ✅ → 回落真实实现 ✅ → 对系统其余部分透明 ✅。 */
static void cratix_hook_crt_symbols(void) {
    HMODULE ucrt = GetModuleHandleW(L"ucrtbase.dll");
    const IMAGE_NT_HEADERS64 *unt;
    const char *names[4] = {"GetProcAddress", "LoadLibraryExW", "LoadLibraryA", "FreeLibrary"};
    const void *repls[4] = {(const void *)cratix_GetProcAddress, (const void *)cratix_LoadLibraryExW,
                            (const void *)cratix_LoadLibraryExW, (const void *)cratix_FreeLibrary};
    if (!ucrt) return;
    unt = mem_nt_of((void *)ucrt);
    if (!unt) return;
    if (mem_redirect_import_any_at((void *)ucrt, unt, names, repls, 4) > 0)
        cy_log("[stub] CRT symbols hooked ✅\n");
}
/* 退出前摘掉 PEB 注册 ✅ —— 否则进程退出时加载器会「卸载」我们伪造的条目 ✅
 * → 调 DllMain(DETACH) ✅（解释器半初始化 ✅）→ 崩 ✅。 */
static VOID(WINAPI *g_real_ExitProcess)(UINT) = NULL;

static VOID WINAPI cratix_ExitProcess(UINT code) {
    mem_peb_unregister_all();
    if (g_real_ExitProcess) {
        g_real_ExitProcess(code);
        return;
    }
    TerminateProcess(GetCurrentProcess(), code);
}

/* **CRT 文件 API 重定向** ✅（A 形态 ✅）：Python 的 open() 走 ucrtbase 的 _wopen ✅ →
 * CreateFileW / ReadFile / SetFilePointerEx / CloseHandle 都是 **ucrtbase 的 IAT** ✅，
 * 只改 python314.dll 的 IAT 拦不到 ✅（实测：open(<exe>\entry.txt) FileNotFoundError ✅）。
 * → 把 ucrtbase 的 KERNEL32 导入按名接到 VFS ✅：真实路径照常透传 ✅ →
 * 载荷里的**包数据**（pypinyin json / certifi pem ✅）也能被 open() 读到 ✅。
 * B 形态不开 ✅（与 ExitProcess 钩子同口径 ✅ —— 改系统 DLL 的 IAT 在 B 形态已实测过风险 ✅）。 */
static void cratix_hook_crt_files(void) {
    HMODULE ucrt;
    const IMAGE_NT_HEADERS64 *unt;
    /* **两种形态都开** ✅（M16 实测：B 形态读包数据也需要 ✅）。
     * ExitProcess 钩子保留 A 形态限定 ✅（它和 PEB 伪造条目强相关 ✅）；
     * 文件 API 钩子只做“虚拟路径 → VFS / 真实路径透传”✅ → 与形态无关 ✅。
     * 逃生开关：CRATIX_VFS_CRT=0 ✅。 */
    {
        const char *novfs = getenv("CRATIX_NOVFS");
        if (novfs && novfs[0] == '1') {
            /* **NOVFS 逃生开关必须一致** ✅：解释器 IAT 不挂了 ✅，
             * 但 CRT 钩子还挂的话 ✅ → CreateFileW 返回伪句柄 ✅、ReadFile 走真实 API ✅
             * → WinError 6 ✅（实测：与 A58 同症状 ✅）。→ NOVFS 时两边都不挂 ✅。 */
            cy_log("[stub] CRT file IO hook: skipped (CRATIX_NOVFS=1)\n");
            return;
        }
        const char *off = getenv("CRATIX_VFS_CRT");
        if (off && off[0] == '0') {
            cy_log("[stub] CRT file IO hook: disabled by env\n");
            return;
        }
    }
    ucrt = GetModuleHandleW(L"ucrtbase.dll");
    if (!ucrt) return;
    unt = mem_nt_of((void *)ucrt);
    if (!unt) return;
    if (mem_redirect_import_any_at((void *)ucrt, unt, vfs_kernel_redirect_names(), vfs_kernel_redirect_funcs(),
                                   vfs_kernel_redirect_count()) > 0)
        cy_log("[stub] CRT file IO hooked ✅\n");
    else
        cy_log("[stub] CRT file IO hook: not found ⚠️\n");
}

/* 把 ucrtbase 的 ExitProcess 换成我们的 ✅（CRT 的 exit() 走它 ✅）。 */
static void cratix_hook_exit(void) {
    HMODULE ucrt = GetModuleHandleW(L"ucrtbase.dll");
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    /* **只对 A 形态开** ✅ —— B 形态 ✅：没有 PEB 伪造条目 ✅ → 不需要拦退出 ✅
     * （改系统 DLL 的 IAT 有风险 ✅ —— 上一轮实测 B 形态在**工作线程**上崩 ✅ → 先排除它 ✅）。
     * 口径用 g_a_form ✅（A 形态是**默认** ✅ M6c ✅ —— 之前按 mode[0]=='m' 判断 ✅
     * env 不设时钩子被错误跳过 ✅ 与 A 默认的语义不一致 ✅）。 */
    if (!g_a_form) {
        cy_log("[stub] ExitProcess hook: skipped (not A-form)\n");
        return;
    }
    const char *names[1] = {"ExitProcess"};
    const void *repls[1] = {(const void *)cratix_ExitProcess};
    const IMAGE_NT_HEADERS64 *unt;
    int ok = 0;
    if (k32) g_real_ExitProcess = (VOID(WINAPI *)(UINT))(void *)GetProcAddress(k32, "ExitProcess");
    if (!ucrt) return;
    unt = mem_nt_of((void *)ucrt);
    if (unt) ok = mem_redirect_iat_at((void *)ucrt, unt, "KERNEL32.dll", names, repls, 1);
    cy_log("[stub] ExitProcess hook: %s\n", ok ? "ok ✅" : "not found ⚠️");
}

/* ---------- 日志输出通道 ✅ ---------- */
/* 默认（GUI 子系统 ✅）**没有控制台** ✅ → 日志丢给 NUL ✅（不打扰用户 ✅）。
 * 三个出口 ✅：
 *   ① `CRATIX_CONSOLE=1` ✅ → 挂到**父进程控制台** ✅（AttachConsole ✅）→ UTF-8 ✅；
 *   ② 构建时 `--console` ✅ → 自带控制台 ✅ → UTF-8 ✅；
 *   ③ `CRATIX_LOG=<路径>` ✅ → 写文件 ✅（二进制模式 ✅ —— 不做 \n → \r\n 转换 ✅）。
 * 编码 ✅：控制台输出码页设成 **UTF-8** ✅ —— 否则中文被按 GBK 解释 ✅ → 乱码 ✅。 */
static FILE *g_log_file = NULL;   /* 日志文件 ✅（NULL = 走 stdout ✅） */
static int g_log_ready = 0;       /* 有没有出口 ✅ */

/* 把 Win32 的三个标准句柄接到 CRT 的流上 ✅ —— Explorer 双击 GUI 程序时进程**没有标准句柄** ✅
 * → CPython 把 sys.stdout / sys.stderr 设成 **None** ✅
 * → 业务代码 logger.add(sys.stderr) 直接 TypeError ✅（实测 ✅ 入口导入失败 ✅）。
 * 补上之后解释器拿到的是普通文件流 ✅（语义与 pythonw 挂 NUL 一致 ✅）。 */
extern void cy_log(const char *fmt, ...);

typedef intptr_t (*fn_ucrt_get_osfhandle)(int);
typedef int (*fn_ucrt_open_osfhandle)(intptr_t, int);
typedef int (*fn_ucrt_dup)(int);

/* os.dup 的 VFS 包装：python314.dll 的 _dup 导入被重定向到这里。
 * 伪句柄先复制 VFS 句柄，再用 ucrtbase 的 _open_osfhandle 分配新 fd；
 * 真实句柄回落原 _dup（不改变普通文件语义）。 */
static int cratix_dup_crt(int fd) {
    static fn_ucrt_get_osfhandle pget;
    static fn_ucrt_open_osfhandle popen;
    static fn_ucrt_dup pdup;
    static int tried;
    if (!tried) {
        HMODULE u = GetModuleHandleW(L"ucrtbase.dll");
        tried = 1;
        if (u) {
            pget = (fn_ucrt_get_osfhandle)(void *)GetProcAddress(u, "_get_osfhandle");
            popen = (fn_ucrt_open_osfhandle)(void *)GetProcAddress(u, "_open_osfhandle");
            pdup = (fn_ucrt_dup)(void *)GetProcAddress(u, "_dup");
        }
    }
    if (!pget || !pdup) return -1;
    {
        intptr_t osf = pget(fd);
        if (osf != -1 && vfs_is_pseudo_handle((HANDLE)osf)) {
            HANDLE nh = vfs_dup_handle((HANDLE)osf);
            if (nh && popen) {
                int nfd = popen((intptr_t)nh, _O_RDONLY | _O_BINARY);
                if (nfd >= 0) return nfd;
            }
            return -1;
        }
    }
    return pdup(fd);
}

/* DuplicateHandle 是 **7 个参数**（源进程、源句柄、目标进程、输出、访问、继承、选项）。
 * 旧实现漏了源进程/目标进程两个参数 → 实参整体错位 → 真实句柄也失败（mmap 998）。 */
typedef BOOL (WINAPI *fn_dup_handle)(HANDLE, HANDLE, HANDLE, LPHANDLE, DWORD, BOOL, DWORD);
static fn_dup_handle g_real_dup_handle;

static BOOL WINAPI cratix_DuplicateHandle(HANDLE src_proc, HANDLE src, HANDLE tgt_proc, LPHANDLE out,
                                          DWORD access, BOOL inherit, DWORD options) {
    if (vfs_is_pseudo_handle(src)) {
        HANDLE nh = vfs_dup_handle(src);
        if (nh) {
            if (out) *out = nh;
            return TRUE;
        }
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    if (!g_real_dup_handle) {
        HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
        if (k32) g_real_dup_handle = (fn_dup_handle)(void *)GetProcAddress(k32, "DuplicateHandle");
    }
    if (!g_real_dup_handle) return FALSE;
    return g_real_dup_handle(src_proc, src, tgt_proc, out, access, inherit, options);
}

typedef BOOL (WINAPI *fn_get_handle_information)(HANDLE, LPDWORD);
typedef BOOL (WINAPI *fn_set_handle_information)(HANDLE, DWORD, DWORD);
static fn_get_handle_information g_real_get_handle_information;
static fn_set_handle_information g_real_set_handle_information;

static BOOL WINAPI cratix_GetHandleInformation(HANDLE h, LPDWORD flags) {
    if (vfs_is_pseudo_handle(h)) {
        if (flags) *flags = 0;
        return TRUE;
    }
    if (!g_real_get_handle_information) {
        HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
        if (k32) g_real_get_handle_information =
            (fn_get_handle_information)(void *)GetProcAddress(k32, "GetHandleInformation");
    }
    return g_real_get_handle_information ? g_real_get_handle_information(h, flags) : FALSE;
}

static BOOL WINAPI cratix_SetHandleInformation(HANDLE h, DWORD mask, DWORD flags) {
    if (vfs_is_pseudo_handle(h)) return TRUE;
    if (!g_real_set_handle_information) {
        HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
        if (k32) g_real_set_handle_information =
            (fn_set_handle_information)(void *)GetProcAddress(k32, "SetHandleInformation");
    }
    return g_real_set_handle_information ? g_real_set_handle_information(h, mask, flags) : FALSE;
}

static void cratix_fix_std_handles(void) {
    intptr_t hin = _get_osfhandle(_fileno(stdin));
    intptr_t hout = _get_osfhandle(_fileno(stdout));
    intptr_t herr = _get_osfhandle(_fileno(stderr));
    if (hin != -1 && hin != 0) SetStdHandle(STD_INPUT_HANDLE, (HANDLE)hin);
    if (hout != -1 && hout != 0) SetStdHandle(STD_OUTPUT_HANDLE, (HANDLE)hout);
    if (herr != -1 && herr != 0) SetStdHandle(STD_ERROR_HANDLE, (HANDLE)herr);
}

/* 读环境变量并转成 UTF-8 ✅：CRT 的 getenv 返回 **ANSI**（本机 GBK）✅ ——
 * 直接喂给 PyRun_SimpleString / PyImport_ImportModule（都按 UTF-8 解释）✅
 * 会因非 ASCII 字节变成 SyntaxError ✅（实测：CRATIX_CODE 里放中文 → 退出码 5 ✅）。
 * → 统一走宽字符 API + WideCharToMultiByte(CP_UTF8) ✅。 */
static char *env_utf8(const wchar_t *name) {
    DWORD n = GetEnvironmentVariableW(name, NULL, 0);
    wchar_t *w;
    char *s;
    int len;
    if (!n) return NULL;
    w = (wchar_t *)malloc((size_t)n * sizeof(wchar_t));
    if (!w) return NULL;
    if (GetEnvironmentVariableW(name, w, n) == 0) {
        free(w);
        return NULL;
    }
    len = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    if (len <= 0) {
        free(w);
        return NULL;
    }
    s = (char *)malloc((size_t)len);
    if (s) WideCharToMultiByte(CP_UTF8, 0, w, -1, s, len, NULL, NULL);
    free(w);
    return s;
}

/* CRC32 ✅（IEEE 802.3 多项式 ✅，与 Python zlib.crc32 同口径 ✅）—— 用于校验
 * cookie 里的 ck.crc ✅（assemble 写入 ✅，A40 补验证 ✅）。
 * **slicing-by-16** ✅（A408）：旧字节查表 ~370 MB/s ✅ → 40 MB 载荷实测 94-109 ms ✅
 * （启动单点最大 ✅）；16 路切片 4.6 倍 ✅ 64 MB 实测 165 → 36 ms ✅（40 MB ≈ 23 ms ✅）。
 * 等价性 ✅：test/cratix_crc16_test.c 长度×对齐矩阵 + 标准值 0xCBF43926 全过 ✅，
 * 且与 zlib.crc32 端到端对账（stub 日志的 crc ok 值 == python zlib.crc32(载荷) ✅）。 */
static unsigned int cratix_crc32(const unsigned char *p, size_t n) {
    static unsigned int t[16][256];
    static int ready = 0;
    unsigned int c;
    const unsigned char *end;
    if (!ready) {
        unsigned int x;
        int i, k, j;
        for (i = 0; i < 256; i++) {
            x = (unsigned int)i;
            for (k = 0; k < 8; k++) x = (x & 1u) ? (0xEDB88320u ^ (x >> 1)) : (x >> 1);
            t[0][i] = x;
        }
        for (j = 1; j < 16; j++)
            for (i = 0; i < 256; i++)
                t[j][i] = (t[j - 1][i] >> 8) ^ t[0][t[j - 1][i] & 0xFFu];
        ready = 1;
    }
    c = 0xFFFFFFFFu;
    end = p + n;
    while (n >= 16) {
        unsigned int one, two, three, four;
        memcpy(&one, p, 4);
        memcpy(&two, p + 4, 4);
        memcpy(&three, p + 8, 4);
        memcpy(&four, p + 12, 4);
        one ^= c;
        c = t[15][one & 0xFFu] ^ t[14][(one >> 8) & 0xFFu] ^ t[13][(one >> 16) & 0xFFu] ^ t[12][(one >> 24) & 0xFFu] ^
            t[11][two & 0xFFu] ^ t[10][(two >> 8) & 0xFFu] ^ t[9][(two >> 16) & 0xFFu] ^ t[8][(two >> 24) & 0xFFu] ^
            t[7][three & 0xFFu] ^ t[6][(three >> 8) & 0xFFu] ^ t[5][(three >> 16) & 0xFFu] ^ t[4][(three >> 24) & 0xFFu] ^
            t[3][four & 0xFFu] ^ t[2][(four >> 8) & 0xFFu] ^ t[1][(four >> 16) & 0xFFu] ^ t[0][(four >> 24) & 0xFFu];
        p += 16;
        n -= 16;
    }
    while (p < end) c = t[0][(c ^ *p++) & 0xFFu] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

static void cy_log_init(void) {
    const char *env;
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    int have_std = 0;   /* 进程有没有可用的标准句柄 ✅（Explorer 双击 ✅ → 没有 ✅） */
    int have_out = 0;   /* 有没有日志出口 ✅（控制台 ✅ / 日志文件 ✅） */
    int streams_ok = 0; /* CRT 的 stdout/stderr 是不是已经是有效流 ✅ */
    /* 先看自己有没有**可用的标准输出** ✅ —— 从 shell 启动时是继承来的管道/控制台 ✅
     * （GUI 子系统 ✅ 但句柄是继承的 ✅ → 输出照常回写到调用方 ✅）。 */
    have_std = (GetFileType(GetStdHandle(STD_OUTPUT_HANDLE)) != FILE_TYPE_UNKNOWN);
    have_out = have_std;
    streams_ok = have_std;
    /* 没有 ✅（双击启动 ✅ —— Explorer 不给标准句柄 ✅）→ 按需挂父进程的控制台 ✅ */
    env = getenv("CRATIX_CONSOLE");
    if (!have_std && env && env[0] && env[0] != '0' && k32) {
        typedef BOOL(WINAPI *fn_attach)(DWORD);
        fn_attach attach = (fn_attach)GetProcAddress(k32, "AttachConsole");
        if (attach && attach(ATTACH_PARENT_PROCESS)) {
            /* GUI 进程启动时没有标准句柄 ✅ → 重新绑定到控制台 ✅ */
            freopen("CONOUT$", "w", stdout);
            freopen("CONOUT$", "w", stderr);
            have_std = 1;
            have_out = 1;
            streams_ok = 1;
        }
    }
    /* ③ 日志文件 ✅ */
    env = getenv("CRATIX_LOG");
    if (env && env[0]) {
        g_log_file = fopen(env, "wb");
        if (g_log_file) have_out = 1;
        /* stdout/stderr 都指向日志文件 ✅ —— Python 的 print ✅ 与 logger 的 stderr sink ✅
         * （GUI 程序没有控制台 ✅ → 否则丢 ✅）。 */
        if (g_log_file && !cratix_legacy()) {
            /* **不能让 Python 的 stdout 从头写** ✅：旧实现 freopen(env,"ab",stdout) ✅
             * 实测并不按追加写 ✅ → Python 的输出从**偏移 0** 开始写 ✅
             * → 把 stub 最早的那几十行诊断（exe 路径 / 载荷 offset / CRC / TLS ✅）
             * 直接覆盖掉 ✅（实测：同一份日志里 [+0ms] 行消失 ✅）。
             * → 改成「同一个句柄」✅：把 OS 标准句柄指向日志文件 ✅
             * → Python 与 g_log_file 共享同一个文件位置 ✅ → 真正追加 ✅。 */
            HANDLE hlog = (HANDLE)_get_osfhandle(_fileno(g_log_file));
            if (hlog != INVALID_HANDLE_VALUE) {
                SetStdHandle(STD_OUTPUT_HANDLE, hlog);
                SetStdHandle(STD_ERROR_HANDLE, hlog);
                _dup2(_fileno(g_log_file), _fileno(stdout));
                _dup2(_fileno(g_log_file), _fileno(stderr));
            } else {
                freopen(env, "ab", stdout);
                freopen(env, "ab", stderr);
            }
            streams_ok = 1;
        }
        /* 不缓冲 ✅ —— 崩溃时缓冲会丢 ✅（诊断输出必须落盘 ✅）。 */
        setvbuf(stdout, NULL, _IONBF, 0);
        setvbuf(stderr, NULL, _IONBF, 0);
    }
    /* ② UTF-8 ✅ —— 控制台默认按 ANSI 代码页解释字节 ✅ → 中文乱码 ✅ */
    if (have_std) {
        SetConsoleOutputCP(CP_UTF8);
        SetConsoleCP(CP_UTF8);
    }
    if (have_out) {
        /* Python 侧的输出也统一 UTF-8 ✅（与应用输出混排时不错乱 ✅ —— 日志文件也是 UTF-8 字节流 ✅） */
        SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");
        SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    }
    g_log_ready = have_out;
    if (!streams_ok) {
        /* 没有出口 ✅ → 丢给 NUL ✅（printf 不会失败 ✅）。 */
        freopen("NUL", "w", stdout);
        freopen("NUL", "w", stderr);
    }
    if (!have_std) {
        /* **标准句柄必须补齐** ✅ —— Explorer 双击 GUI 程序时进程**没有标准句柄** ✅
         * → CPython 把 sys.stdout / sys.stderr 设成 **None** ✅
         * → 业务代码 logger.add(sys.stderr) 直接 TypeError ✅
         *   （实测 ✅ 入口模块导入失败 ✅「导入入口模块失败：client.app」✅）。
         * 补上之后解释器拿到的是普通文件流 ✅（语义与 pythonw 挂 NUL 一致 ✅）。 */
        freopen("NUL", "r", stdin);
        cratix_fix_std_handles();
    }
}

void cy_log(const char *fmt, ...) { /* 非 static ✅（vfs.c 的调试日志要引用 ✅） */
    va_list ap;
    FILE *out = g_log_file ? g_log_file : stdout;
    /* 每行前缀「自进程起 +N ms」✅ —— 启动耗时定位用 ✅（R8 ✅） */
    static ULONGLONG t0 = 0;
    ULONGLONG now = GetTickCount64();
    if (!t0) t0 = now;
    if (!out) return;
    fprintf(out, "[+%llums] ", (unsigned long long)(now - t0));
    va_start(ap, fmt);
    vfprintf(out, fmt, ap);
    va_end(ap);
    fflush(out);
}

/* ---------- 启动失败可见性（M7 ✅）---------- */
/* GUI 子系统下双击启动没有控制台 ✅ —— 打包破损 / 解释器初始化失败 / 入口抛异常 /
 * 原生崩溃全都表现为「点了没反应」✅ → 失败路径统一：落盘错误日志 + 弹窗 ✅。
 * CRATIX_NO_ERRORBOX=1 ✅ 关掉弹窗（自动化验收里 MessageBox 会挂住测试 ✅）。 */

static int cratix_errorbox_enabled(void) {
    const char *nb = getenv("CRATIX_NO_ERRORBOX");
    return !(nb && nb[0] && nb[0] != '0');
}

/* MessageBoxW 动态解析 ✅：stub 只链 kernel32/CRT ✅（不引 user32.lib ✅
 * —— GUI 子系统进程里 user32 不一定已加载 ✅ → LoadLibrary 按需拉 ✅）。 */
static void cratix_show_msgbox(const WCHAR *title, const WCHAR *body) {
    typedef int(WINAPI *fn_msgbox)(void *, LPCWSTR, LPCWSTR, UINT);
    HMODULE u32 = LoadLibraryW(L"user32.dll");
    fn_msgbox mb;
    if (!u32) return;
    mb = (fn_msgbox)(void *)GetProcAddress(u32, "MessageBoxW");
    if (mb) mb(NULL, body, title, MB_OK | MB_ICONERROR | MB_TOPMOST | MB_SETFOREGROUND);
}

/* 错误日志 ✅：<exe 目录>\stub_error.log ✅（追加写 ✅）。
 * 尽力而为 ✅：exe 目录可能只读 ✅ → 写失败不影响后续弹窗 ✅。
 * 生产运行（无 CRATIX_LOG ✅）下这是**唯一**落盘诊断 ✅ —— cy_log 走 NUL ✅。 */
static void cratix_error_log_write(const char *utf8_text) {
    char path[MAX_PATH];
    FILE *f;
    _snprintf(path, sizeof(path), "%s\\stub_error.log", g_exe_dir);
    path[MAX_PATH - 1] = 0;
    f = fopen(path, "ab");
    if (!f) return;
    fprintf(f, "[+%llums] %s\n", (unsigned long long)GetTickCount64(), utf8_text ? utf8_text : "(null)");
    fclose(f);
}

static void cratix_fail_dialog(const char *utf8_msg) {
    WCHAR wtitle[64], wmsg[1800], wbody[2600];
    if (utf8_msg && utf8_msg[0]) cy_log("[stub] FAIL: %s\n", utf8_msg);
    cratix_error_log_write(utf8_msg);
    if (!cratix_errorbox_enabled()) return;
    MultiByteToWideChar(CP_UTF8, 0, "应用程序启动失败", -1, wtitle, 64);
    if (MultiByteToWideChar(CP_UTF8, 0, utf8_msg ? utf8_msg : "(unknown)", -1, wmsg, 1800) <= 0) return;
    /* %hs ✅：宽格式串里的 char* 参数 ✅（%s 在宽格式串里是 wchar* ✅） */
    _snwprintf(wbody, sizeof(wbody) / sizeof(WCHAR) - 1,
               L"%s\n\n程序：%hs\n详细日志已写入同目录 stub_error.log", wmsg, g_exe_path);
    wbody[sizeof(wbody) / sizeof(WCHAR) - 1] = 0;
    cratix_show_msgbox(wtitle, wbody);
}

/* stub_error.log 尾部 ✅（弹窗正文用 ✅）—— 读最后 ~2800 字节 ✅。 */
static void cratix_read_log_tail(char *out, size_t cap) {
    char path[MAX_PATH];
    FILE *f;
    long sz;
    out[0] = 0;
    _snprintf(path, sizeof(path), "%s\\stub_error.log", g_exe_dir);
    path[MAX_PATH - 1] = 0;
    f = fopen(path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    sz = ftell(f);
    if (sz > (long)(cap - 1)) fseek(f, -(long)(cap - 1), SEEK_END);
    {
        size_t n = fread(out, 1, cap - 1, f);
        out[n] = 0;
    }
    fclose(f);
}

/* Python 异常版 ✅：PyErr_Print 先清异常 ✅（3.12+ 会把异常存进 sys.last_exc ✅）
 * → 再用 PyRun_SimpleString 把 sys.last_exc 格式化追加进 stub_error.log ✅
 * （纯 Python 侧取文本 ✅ —— 不需要额外的解释器导出 ✅）→ 弹窗显示日志尾部 ✅。
 * 解释器状态可能已经烂了 ✅ → 每步都判空/容错 ✅ 最坏情况只弹摘要 ✅。 */
static void cratix_py_fail_dialog(const char *context) {
    char snip[1500];
    char text[4600];
    cy_log("[stub] FAIL(py): %s\n", context);
    cratix_error_log_write(context);
    if (p_PyErr_Print) p_PyErr_Print(); /* 清异常 + 打到 stdout（CRATIX_LOG 文件/控制台 ✅） */
    if (p_PyRun_SimpleString) {
        char logpath[MAX_PATH];
        /* 路径反斜杠不转义 ✅：Python **raw 字符串** r'...' 里反斜杠是字面量 ✅
         * （g_exe_dir 不会以反斜杠结尾 ✅ —— strrchr 砍掉的 ✅）。 */
        _snprintf(logpath, sizeof(logpath) - 1, "%s\\stub_error.log", g_exe_dir);
        logpath[MAX_PATH - 1] = 0;
        /* C 格式串里不能有裸 % ✅（%r 是未定义转换 ✅ → 输出可能从这里断掉 ✅
         * —— Python 侧的字符串拼接改用 repr() ✅）。 */
        _snprintf(snip, sizeof(snip) - 1,
                  "try:\n"
                  "    import sys as _sys, traceback as _tb\n"
                  "    _e = getattr(_sys, 'last_exc', None)\n"
                  "    _s = ''.join(_tb.format_exception(_e)) if _e is not None else '(no sys.last_exc)'\n"
                  "except Exception as _x:\n"
                  "    _s = '(traceback failed: ' + repr(_x) + ')'\n"
                  "try:\n"
                  "    _f = open(r'%s', 'ab')\n"
                  "    _f.write(_s.encode('utf-8', 'replace') if isinstance(_s, str) else _s)\n"
                  "    _f.write(b'\\n\\n')\n"
                  "    _f.close()\n"
                  "except Exception:\n"
                  "    pass\n",
                  logpath);
        snip[sizeof(snip) - 1] = 0;
        p_PyRun_SimpleString(snip); /* 失败也无妨 ✅ —— 日志里至少有上下文行 ✅ */
    }
    /* 弹窗正文 ✅：上下文 + 日志尾部（含 traceback ✅） */
    _snprintf(text, sizeof(text) - 1, "%s", context);
    if (strlen(text) < sizeof(text) - 200) {
        strcat(text, "\n\n-------- traceback --------\n");
        cratix_read_log_tail(text + strlen(text), sizeof(text) - strlen(text) - 1);
    }
    if (!cratix_errorbox_enabled()) return;
    {
        WCHAR wtitle[64], wbody[4096];
        MultiByteToWideChar(CP_UTF8, 0, "应用程序启动失败", -1, wtitle, 64);
        if (MultiByteToWideChar(CP_UTF8, 0, text, -1, wbody, 4096) <= 0) return;
        wbody[4095] = 0;
        cratix_show_msgbox(wtitle, wbody);
    }
}

/* 原生崩溃兜底 ✅：memdl 映射的模块里的未处理异常 ✅ 默认直接死进程 ✅（用户视角 = 没反应 ✅）
 * → UEF 里弹窗带异常码与归属模块 ✅（VEH 日志里有完整堆栈 ✅）→ 至少知道去哪找日志 ✅。
 * EXCEPTION_EXECUTE_HANDLER ✅ = 弹完窗按异常路径终止进程 ✅（不吞异常 ✅）。 */
static LONG WINAPI cratix_unhandled_exception(EXCEPTION_POINTERS *ep) {
    char buf[512];
    const char *owner;
    if (!cratix_errorbox_enabled()) return EXCEPTION_EXECUTE_HANDLER;
    owner = ep ? mem_module_by_addr((void *)ep->ExceptionRecord->ExceptionAddress) : NULL;
    _snprintf(buf, sizeof(buf) - 1,
              "程序崩溃：未处理异常 0x%08lX @ %p%s%s\n\n详细日志已写入同目录 stub_error.log",
              ep ? (unsigned long)ep->ExceptionRecord->ExceptionCode : 0,
              ep ? ep->ExceptionRecord->ExceptionAddress : NULL,
              owner ? "\n归属模块：" : "", owner ? owner : "");
    buf[sizeof(buf) - 1] = 0;
    cratix_fail_dialog(buf);
    return EXCEPTION_EXECUTE_HANDLER;
}

/* exe 目录前缀 → 归档内相对路径。
 * 分隔符必须归一化：同一条路径可能以 '\\' 或 '/' 出现（我们生成的虚拟路径用 '/'，
 * CPython 生成的用 os.sep），比较前统一成 '/'。 */
static int path_to_rel(const WCHAR *path, char *out, size_t cap) {
    size_t dlen = strlen(g_exe_dir_slash);
    size_t i = 0;
    char norm[MAX_PATH * 4];
    int n;
    if (!path || !*path) return 0;
    /* **必须 UTF-8 编码**：旧实现把 WCHAR 直接截成单字节（(char)c）✅ →
     * 中文目录的 UTF-16 低字节 ≠ g_exe_dir_slash 的 UTF-8 ✅ → 前缀永不匹配 ✅
     * → LoadLibrary 落到真实加载器 ✅ → cratix_bootstrap 导入失败 ✅
     * （实测：中文目录下 A 形态 exit 2 / DLL load failed）。 */
    n = WideCharToMultiByte(CP_UTF8, 0, path, -1, norm, (int)sizeof(norm), NULL, NULL);
    if (n <= 0) return 0;
    for (i = 0; norm[i]; i++) {
        if (norm[i] == '\\') norm[i] = '/';
    }
    if (strncmp(norm, "//?/", 4) == 0) {
        memmove(norm, norm + 4, strlen(norm + 4) + 1);
    }
    for (i = 0; i < dlen; i++) {
        char a = norm[i], b = g_exe_dir_slash[i];
        if (!a) return 0;
        if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
        if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
        if (a != b) return 0;
    }
    i = dlen;
    while (norm[i] == '/' || norm[i] == '\\') i++;
    {
        size_t outlen = 0;
        while (norm[i] && outlen + 1 < cap) {
            char c = norm[i++];
            if (c == '\\') c = '/';
            out[outlen++] = c;
        }
        out[outlen] = 0;
        return outlen > 0;
    }
}

/* Qt6Core 挂 VFS ✅（M4 / D1 ✅）：文件面 + NT 面 + 插件加载三组重定向 ✅ */
static HMODULE WINAPI cratix_LoadLibraryExW(LPCWSTR name, HANDLE file, DWORD flags);
static HMODULE WINAPI cratix_LoadLibraryW(LPCWSTR name);
static FARPROC WINAPI cratix_GetProcAddress(HMODULE h, LPCSTR name);
static BOOL WINAPI cratix_FreeLibrary(HMODULE h);

static void hook_qt6core_vfs(MemModule *m) {
    /* KERNEL32：VFS 文件面 ✅ */
    if (!mem_redirect_import_any(m, vfs_kernel_redirect_names(), vfs_kernel_redirect_funcs(),
                             vfs_kernel_redirect_count())) {
        cy_log("[stub] vfs: KERNEL32 redirect failed\n");
        return;
    }
    /* NTDLL：NtCreateFile / NtQueryVolumeInformationFile ✅ */
    if (!mem_redirect_import_any(m, vfs_nt_redirect_names(), vfs_nt_redirect_funcs(),
                             vfs_nt_redirect_count())) {
        cy_log("[stub] vfs: ntdll redirect skipped (not imported)\n");
    }
    /* 加载面 ✅：插件加载走我们的 LoadLibraryExW / GetProcAddress ✅ */
    {
        const char *names[3] = {"LoadLibraryExW", "LoadLibraryW", "GetProcAddress"};
        const void *repls[3] = {(const void *)cratix_LoadLibraryExW, (const void *)cratix_LoadLibraryW,
                                 (const void *)cratix_GetProcAddress};
        if (!mem_redirect_import_any(m, names, repls, 3))
            cy_log("[stub] vfs: loader redirect failed\n");
    }
    cy_log("[stub] vfs: Qt6Core hooked ✅\n");
}

/* **每个**被映射的模块都要接管它的加载面 ✅ —— 不只是解释器 ✅
 * （.pyd 里的 ctypes ✅ / OpenSSL 的 provider ✅ / 任何按名加载 DLL 的扩展 ✅ 都走这条路 ✅；
 *   之前只挂了 python314.dll ✅ → 这些模块的 LoadLibrary 落到真实加载器 ✅ → 按名找不到 DLL ✅）。
 * **GetModuleHandleExW 也必须接管 ✅**（M6c ✅）：手工映射的模块不在 OS 加载器表里 ✅
 *   → 模块内地址查不到自己 ✅ → OpenSSL 的 `ossl_init_load_crypto_nodelete` 走
 *   `GetModuleHandleExW(FROM_ADDRESS|PIN, &base_inited)` **失败** ✅ → `OPENSSL_init_crypto`
 *   每次**提前返回 0** ✅ → 后续懒初始化链断掉 ✅ → provider 算法表全空 ✅
 *   （EVP_MD_fetch("MD5") 报 0308010C ✅ —— 实测 harness 对照 ✅ 见 findings §13 ✅）。 */
static BOOL WINAPI cratix_GetModuleHandleExW(DWORD flags, LPCWSTR name, HMODULE *out) {
    const DWORD FROM_ADDRESS = 0x00000004u;
    typedef BOOL(WINAPI *fn_gmhex)(DWORD, LPCWSTR, HMODULE *);
    static fn_gmhex real_gmhex = NULL;
    if (!real_gmhex) {
        HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
        real_gmhex = k32 ? (fn_gmhex)(void *)GetProcAddress(k32, "GetModuleHandleExW") : NULL;
    }
    /* FROM_ADDRESS ✅：地址落在**我们映射的模块**里 ✅ → 返回伪句柄 ✅
     * （伪句柄在其它接管面上都能解释 ✅；PIN 只是「不卸载」语义 ✅ 内存映射天然满足 ✅）。 */
    if (out && (flags & FROM_ADDRESS)) {
        BYTE *base = mem_base_by_addr((const void *)(uintptr_t)name);
        if (base) {
            *out = (HMODULE)base;
            return TRUE;
        }
    }
    return real_gmhex ? real_gmhex(flags, name, out) : FALSE;
}

static void cratix_hook_loader(MemModule *m) {
    const char *names[6] = {"LoadLibraryExW", "LoadLibraryW", "LoadLibraryA", "GetProcAddress", "FreeLibrary",
                            "GetModuleHandleExW"};
    const void *repls[6] = {(const void *)cratix_LoadLibraryExW, (const void *)cratix_LoadLibraryW,
                             (const void *)cratix_LoadLibraryExW, (const void *)cratix_GetProcAddress,
                             (const void *)cratix_FreeLibrary, (const void *)cratix_GetModuleHandleExW};
    /* MemModule 在 stub.c 里是**不透明类型** ✅ —— 取不到 name ✅ → 只报成功 ✅。 */
    /* 按**函数名**接管 ✅ —— 导入表里可能是 API-set 名 ✅（不是 KERNEL32.dll ✅）。 */
    if (m && g_a_form && mem_redirect_import_any(m, names, repls, 6) > 0)
        cy_log("[stub] loader hook: ok ✅\n");
}

/* IAT 重定向目标：命中归档 → 内存映射；否则回落真实加载 */
static HMODULE WINAPI cratix_LoadLibraryExW(LPCWSTR name, HANDLE file, DWORD flags) {
    char rel[MAX_PATH];
    /* 解释器本体唯一 ✅：python3 / python314 的加载请求一律回指**内存映射的那份** ✅
     * （否则 OS 会按名再加载一份真实解释器 ✅ → 双解释器 ✅ 状态分裂 ✅ 崩溃 ✅）。 */
    if (name && g_interp_mem) {
        if (_stricmp(name, "python3") == 0 || _stricmp(name, "python3.dll") == 0 ||
            _stricmp(name, "python314") == 0 || _stricmp(name, "python314.dll") == 0) {
            cy_log("[stub] LoadLibraryExW(%s) -> 内存解释器 ✅\n", name);
            return mem_as_hmodule(g_interp_mem);
        }
    }
    if (getenv("CRATIX_DEBUG")) {
        char wbuf[512];
        WideCharToMultiByte(CP_UTF8, 0, name, -1, wbuf, sizeof(wbuf), NULL, NULL);
        cy_log("[stub] LoadLibraryExW: %s\n", wbuf);
    }
    if (g_zip.count) {
        const zentry_t *e = NULL;
        const char *label = rel;
        if (path_to_rel(name, rel, sizeof(rel))) e = zipidx_find(&g_zip, rel);
        /* 原生扩展在 ext3rd/<pkg>/<x>.pyd；根可见后按包路径加载时要走别名。 */
        if (!e && rel[0]) {
            char alias[MAX_PATH];
            _snprintf(alias, sizeof(alias) - 1, "ext3rd/%s", rel);
            alias[sizeof(alias) - 1] = 0;
            e = zipidx_find(&g_zip, alias);
            if (e) label = e->name;
        }
        if (!e) {
            /* **按 basename 兜底** ✅ —— 归档里的原生库都在 native/** ✅。
             * 「按名加载」的场景 ✅（ctypes ✅ / OpenSSL 的 provider ✅ / Qt 插件 ✅）都走这条路 ✅。 */
            const WCHAR *bn = name;   /* 宽字符串 ✅ —— 下面要交给 WideCharToMultiByte ✅ */
            const WCHAR *p;
            char base[260];
            for (p = name; *p; p++)
                if (*p == L'\\' || *p == L'/') bn = p + 1;
            if (g_a_form && WideCharToMultiByte(CP_UTF8, 0, bn, -1, base, sizeof(base), NULL, NULL) > 0) {
                e = zipidx_find_basename(&g_zip, base);
                /* 只接受**原生库** ✅（native/** ✅ 或 .dll ✅）—— 别把 .pyc 当 DLL 映射 ✅。 */
                if (e && !strstr(e->name, "native/") && !strstr(e->name, ".dll")) e = NULL;
                if (e) label = e->name;
            }
        }
        if (e) {
            unsigned char *bytes = NULL;
            size_t len = 0;
            if (zipidx_read(&g_zip, e, &bytes, &len)) {
                MemModule *m = mem_map_bytes(bytes, len, label);
                free(bytes);
                if (m) {
                    cy_log("[stub] memdl mapped %s -> %p\n", label, (void *)m);
                    cratix_hook_loader(m); /* 该模块自己的 LoadLibrary 也要接管 ✅ */
                    if (_stricmp(rel, "native/Qt6Core.dll") == 0 || _stricmp(rel, "Qt6Core.dll") == 0) {
                        hook_qt6core_vfs(m);
                    }
                    return mem_as_hmodule(m);
                }
                cy_log("[stub] memdl failed for %s: %s\n", rel, mem_last_error());
            }
        }
    }
    return g_real_LoadLibraryExW(name, file, flags);
}

static HMODULE WINAPI cratix_LoadLibraryW(LPCWSTR name) {
    return cratix_LoadLibraryExW(name, NULL, 0);
}

/* 内存里的模块 ✅ —— FreeLibrary 直接忽略 ✅（OS 不认识这个句柄 ✅ → 会误判 ✅）。 */
static BOOL WINAPI cratix_FreeLibrary(HMODULE h) {
    MemModule *m = mem_from_hmodule(h);
    if (m) return TRUE;
    return g_real_FreeLibrary ? g_real_FreeLibrary(h) : TRUE;
}

extern DWORD WINAPI vfs_dynamic_GetFileAttributesW(LPCWSTR name);
extern BOOL WINAPI vfs_dynamic_GetFileAttributesExW(LPCWSTR name, GET_FILEEX_INFO_LEVELS level, LPVOID buf);
extern HANDLE WINAPI vfs_dynamic_FindFirstFileW(LPCWSTR pattern, LPWIN32_FIND_DATAW data);
extern BOOL WINAPI vfs_dynamic_FindNextFileW(HANDLE h, LPWIN32_FIND_DATAW data);
extern BOOL WINAPI vfs_dynamic_FindClose(HANDLE h);
extern HANDLE WINAPI vfs_dynamic_CreateFileMappingW(HANDLE, LPSECURITY_ATTRIBUTES, DWORD, DWORD, DWORD, LPCWSTR);
extern LPVOID WINAPI vfs_dynamic_MapViewOfFile(HANDLE, DWORD, DWORD, DWORD, SIZE_T);
extern BOOL WINAPI vfs_dynamic_UnmapViewOfFile(LPCVOID);

static FARPROC WINAPI cratix_GetProcAddress(HMODULE h, LPCSTR name) {
    /* GetFileInformationByName 拦截 ✅（Python 3.14 的 os.stat 快路径 ✅ —— 动态解析 ✅
     *   绕过全部文件面挂钩 ✅ → 必须在这一步给 VFS 实现 ✅ 见 vfs.c ✅）。 */
    if (name && strcmp(name, "GetFileInformationByName") == 0) {
        void *p = vfs_file_information_by_name();
        if (p) return (FARPROC)p;
    }
    {
        MemModule *m = mem_from_hmodule(h);
        if (m) {
            FARPROC p = (FARPROC)mem_get_export(m, name);
            if (getenv("CRATIX_DEBUG"))
                cy_log("[stub] GetProcAddress(%p, %s) -> %p (mem module)\n", (void *)h, name, (void *)p);
            return p;
        }
    }
    if (name && strcmp(name, "GetFileAttributesW") == 0) {
        return (FARPROC)vfs_dynamic_GetFileAttributesW;
    }
    if (name && strcmp(name, "GetFileAttributesExW") == 0) {
        return (FARPROC)vfs_dynamic_GetFileAttributesExW;
    }
    if (name && strcmp(name, "FindFirstFileW") == 0) {
        return (FARPROC)vfs_dynamic_FindFirstFileW;
    }
    if (name && strcmp(name, "FindNextFileW") == 0) {
        return (FARPROC)vfs_dynamic_FindNextFileW;
    }
    if (name && strcmp(name, "FindClose") == 0) {
        return (FARPROC)vfs_dynamic_FindClose;
    }
    if (name && strcmp(name, "CreateFileMappingW") == 0) {
        return (FARPROC)vfs_dynamic_CreateFileMappingW;
    }
    if (name && strcmp(name, "MapViewOfFile") == 0) {
        return (FARPROC)vfs_dynamic_MapViewOfFile;
    }
    if (name && strcmp(name, "UnmapViewOfFile") == 0) {
        return (FARPROC)vfs_dynamic_UnmapViewOfFile;
    }

    return g_real_GetProcAddress(h, name);
}

/* 用宽字符 API 读文件：产品名含中文（胜点_*.exe），
 * fopen 走 ANSI 代码页会把 UTF-8 路径解成别的字符 → 打不开自己。 */
static char *read_whole_file_w(const WCHAR *path, size_t *out_len) {
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    LARGE_INTEGER size;
    char *buf;
    DWORD got = 0;
    if (h == INVALID_HANDLE_VALUE) return NULL;
    if (!GetFileSizeEx(h, &size) || size.QuadPart <= 0) {
        CloseHandle(h);
        return NULL;
    }
    buf = (char *)malloc((size_t)size.QuadPart);
    if (!buf) {
        CloseHandle(h);
        return NULL;
    }
    if (!ReadFile(h, buf, (DWORD)size.QuadPart, &got, NULL) || got == 0) {
        free(buf);
        CloseHandle(h);
        return NULL;
    }
    CloseHandle(h);
    *out_len = got;
    return buf;
}

/* ────────────────────────────────────────────────────────────────
 * cratix_loader：给 Python 侧用的**内存加载桥**（内置模块，进 sys.modules）
 *
 * 提供 load(entry, init_symbol, spec) -> module：
 *   1. 按载荷条目名把 .pyd 映射进内存（memdl）；
 *   2. 用**我们指定的** init 符号调用 → 符号可以唯一化 ✅
 *      （CPython 按模块名末段查 PyInit_xxx → 同名末段模块会互相冲突 ✅）；
 *   3. 多阶段初始化（PEP 489）：init() 返回的是「模块定义对象」→
 *      PyModule_FromDefAndSpec(def, spec) 建模块 ✅ + PyModule_ExecDef 执行模块体 ✅
 *      （与 CPython 官方加载器同一套机制 ✅）。
 * ──────────────────────────────────────────────────────────────── */
typedef struct {
    long long ob_refcnt;
    void *ob_type;
} CpyObjectHead;

/* 与 CPython 的 PyMethodDef 布局一致（ml_name/ml_meth/ml_flags/ml_doc）✅ */
typedef struct {
    const char *ml_name;
    void *ml_meth;
    int ml_flags;
    const char *ml_doc;
} CpyMethodDef;

#define METH_VARARGS 0x0001

/* ---------- 整块组（solid group）✅ ----------
 * 多个条目合成**一个** zstd 流 ✅ —— 逐条压缩会把**跨条目冗余**全丢掉 ✅：
 * Cython 产物每份都带 ~148 KiB 的样板代码 ✅（162 份 ✅ ≈ 23 MiB ✅）→
 * 逐条压 18.1 MiB ✅ / 整块压 6.0 MiB ✅（实测 ✅）。
 * 条目名形如 "solid/ext.zst#<offset>:<size>:<count>" ✅ —— 加载桥认这个形态 ✅。 */
#define SOLID_MAX 4
typedef struct {
    char name[128];
    unsigned char *buf;
    size_t len;
    int served;
    long long total;
} solid_group_t;
static solid_group_t g_solid[SOLID_MAX];
static int g_solid_n;

/* 取组内一片 ✅（首次读整块 ✅ → 缓存 ✅ → 全部条目服务完 ✅ → 释放 ✅ 省常驻 ✅）。 */
static int cratix_solid_slice(const char *group, unsigned long long off, unsigned long long size,
                               long long total, unsigned char **out, size_t *out_len) {
    int i;
    solid_group_t *g = NULL;
    for (i = 0; i < g_solid_n; i++) {
        if (strcmp(g_solid[i].name, group) == 0) {
            g = &g_solid[i];
            break;
        }
    }
    if (!g) {
        const zentry_t *e = zipidx_find(&g_zip, group);
        unsigned char *buf = NULL;
        size_t len = 0;
        if (!e || !zipidx_read(&g_zip, e, &buf, &len)) return 0;
        if (g_solid_n >= SOLID_MAX) {
            free(buf);
            return 0;
        }
        g = &g_solid[g_solid_n++];
        strncpy(g->name, group, sizeof(g->name) - 1);
        g->name[sizeof(g->name) - 1] = 0;
        g->buf = buf;
        g->len = len;
        g->served = 0;
        g->total = total;
    }
    if (!g->buf) {
        /* **按需重载** ✅：整块缓冲在 served>=total 后释放 ✅ —— 之后再有模块加载
         * （sys.modules.pop 后重导 ✅ / 二次 import ✅）会走到这里 ✅。
         * 旧实现直接 return 0 ✅ → 「solid group slice failed」✅（合成载荷实测 ✅）。
         * → 从 zip 重新解一份 ✅（罕见路径 ✅，代价仅一次解压 ✅）。 */
        const zentry_t *e2 = zipidx_find(&g_zip, group);
        unsigned char *buf2 = NULL;
        size_t len2 = 0;
        if (!e2 || !zipidx_read(&g_zip, e2, &buf2, &len2)) return 0;
        g->buf = buf2;
        g->len = len2;
        g->served = 0;
    }
    if (off + size > (unsigned long long)g->len) return 0;
    if (getenv("CRATIX_DEBUG"))
        cy_log("[solid] slice %s off=%llu size=%llu total=%lld\n", group, off, size, total);
    *out = g->buf + off;
    *out_len = (size_t)size;
    /* **不在这里释放** ✅：旧实现 served>=total 就 free ✅ —— 但调用方马上要用
     * 这个指针 mem_map_bytes 拷贝镜像 ✅ → free-before-use ✅（实测：count=11 时
     * 第 11 个 pyd 映射当场崩 ✅ → 进而演成 TypeError 异常 ✅）。
     * → 由调用方在 map 完成后调 cratix_solid_release ✅。 */
    return 1;
}

/* 写回「这个切片服务完了」✅：计数到达 total → 释放整块缓冲 ✅。 */
static void cratix_solid_release(const char *group) {
    int i;
    for (i = 0; i < g_solid_n; i++) {
        if (strcmp(g_solid[i].name, group) == 0) {
            solid_group_t *g = &g_solid[i];
            g->served++;
            if (g->total > 0 && g->served >= g->total) {
                if (getenv("CRATIX_DEBUG"))
                    cy_log("[solid] free %s served=%d total=%lld\n", g->name, g->served, g->total);
                free(g->buf);
                g->buf = NULL;
                g->len = 0;
                g->served = 0;
            }
            return;
        }
    }
}

/* ---------- 重入守卫（正在 init 的入口）✅ ----------
 * 同一「条目 + init 符号」在 init **尚未返回**时又被 import = 循环导入 ✅。
 * 键必须带 init 符号 ✅：分组 pyd 的一个条目服务多个模块 ✅（只按条目名会误判组内互导 ✅）。
 * ⚠ 旧实现只加不减 ✅：init 返回后键仍留在表里 ✅ →
 *   sys.modules.pop 后重导 / 首次导入失败后重试 ✅ 都被误判「循环导入」✅（实测 ✅）。 */
#define CRATIX_INFLIGHT_MAX 16
static char g_inflight[CRATIX_INFLIGHT_MAX][620];
static int g_inflight_n;

static int cratix_inflight_enter(const char *key) {
    int i;
    for (i = 0; i < g_inflight_n; i++) {
        if (strcmp(g_inflight[i], key) == 0) return 0; /* 正在 init ✅ */
    }
    if (g_inflight_n >= CRATIX_INFLIGHT_MAX) return 1; /* 表满 → 不守卫 ✅ */
    strncpy(g_inflight[g_inflight_n], key, sizeof(g_inflight[0]) - 1);
    g_inflight[g_inflight_n][sizeof(g_inflight[0]) - 1] = 0;
    g_inflight_n++;
    return 1;
}

static void cratix_inflight_leave(const char *key) {
    int i;
    for (i = 0; i < g_inflight_n; i++) {
        if (strcmp(g_inflight[i], key) == 0) {
            g_inflight_n--;
            if (i != g_inflight_n) {
                memcpy(g_inflight[i], g_inflight[g_inflight_n], sizeof(g_inflight[i]));
            }
            return;
        }
    }
}

static PyObject *cratix_loader_load(PyObject *self, PyObject *args) {
    const char *entry = NULL;
    const char *init_name = NULL;
    PyObject *spec = NULL;
    const zentry_t *e;
    unsigned char *bytes = NULL;
    size_t len = 0;
    MemModule *m;
    void *sym;
    PyObject *mod;
    int owned = 0;
    char modname[300];
    char solid_group[128] = {0}; /* 整块组名 ✅（map 完成后才计数/释放 ✅） */
    char guard_key[620] = {0};  /* 重入守卫键 ✅（init 返回后必须移除 ✅） */
    typedef PyObject *(*fn_init)(void);

    if (!p_PyArg_ParseTuple(args, "ssO", &entry, &init_name, &spec)) return NULL;
    strncpy(modname, entry, sizeof(modname) - 1);
    modname[sizeof(modname) - 1] = 0;
    /* **已映射模块缓存** ✅：整块组里同一个 pyd 服务多个模块 ✅ ——
     * 旧实现每个模块都 mem_map_bytes 一次 ✅ → 同一个 pyd 被重复映射 N 次 ✅
     * （实测：冒烟只导入 8 个 Cython 模块就映射 8 次；全量启动 × 组内模块数 ✅）
     * → 内存成倍放大 ✅，也与 bootstrap 注释「只映射一次」不符 ✅。
     * 键 = **完整条目名**（含 #off:size:count ✅ —— 同一 pyd 的所有模块键相同 ✅）。 */
    static struct {
        char key[300];
        MemModule *m;
    } g_loader_cache[512];
    static int g_loader_cache_n;
    MemModule *cached = NULL;
    int ci;
    for (ci = 0; ci < g_loader_cache_n; ci++) {
        if (strcmp(g_loader_cache[ci].key, entry) == 0) {
            cached = g_loader_cache[ci].m;
            break;
        }
    }
    if (cached) {
        m = cached;
    } else {
    {
        /* **整块组** ✅：条目名形如 "solid/ext.zst#<offset>:<size>:<count>" ✅ */
        const char *hash = strchr(entry, '#');
        if (hash) {
            unsigned long long off = 0, size = 0, count = 0;
            char group[128];
            size_t glen = (size_t)(hash - entry);
            if (glen >= sizeof(group)) glen = sizeof(group) - 1;
            memcpy(group, entry, glen);
            group[glen] = 0;
            strncpy(solid_group, group, sizeof(solid_group) - 1);
            if (sscanf(hash + 1, "%llu:%llu:%llu", &off, &size, &count) < 2) {
                p_PyErr_Format(p_PyExc_ImportError, "cratix: bad solid entry %s", entry);
                return NULL;
            }
            if (!cratix_solid_slice(group, off, size, (long long)count, &bytes, &len)) {
                p_PyErr_Format(p_PyExc_ImportError, "cratix: solid group %s slice failed", group);
                return NULL;
            }
            owned = 0; /* 切片 ✅ → 不能 free ✅（整块缓冲归组管 ✅） */
            modname[hash - entry] = 0;
        } else {
            e = zipidx_find(&g_zip, entry);
            if (!e || !zipidx_read(&g_zip, e, &bytes, &len)) {
                p_PyErr_Format(p_PyExc_ImportError, "cratix: payload has no entry %s", entry);
                return NULL;
            }
            owned = 1;
        }
    }
    m = mem_map_bytes(bytes, len, modname);
    if (owned) free(bytes);
    /* **map 已完成** ✅ → 现在才能计数并可能释放整块缓冲 ✅（见 slice 处注释 ✅）。 */
    if (solid_group[0]) cratix_solid_release(solid_group);
    if (m) cratix_hook_loader(m); /* 桥里加载的 .pyd ✅ —— 加载面也要接管 ✅ */
    /* **每个装载的 pyd 也挂 VFS** ✅：它自己的文件 IO（mmap 的 GetFileSizeEx ✅ /
     * 包数据读取 ✅）走它自己的 IAT ✅ —— 只挂解释器/ucrtbase/Qt 拦不到 ✅
     * （实测：_mmap.pyd 对伪句柄 GetFileSizeEx → WinError 6 ✅）。真实路径照旧透传 ✅。 */
    if (m && mem_redirect_import_any(m, vfs_kernel_redirect_names(), vfs_kernel_redirect_funcs(),
                                    vfs_kernel_redirect_count())) {
        mem_redirect_import_any(m, vfs_nt_redirect_names(), vfs_nt_redirect_funcs(), vfs_nt_redirect_count());
        mem_redirect_import_any(m, vfs_crt_redirect_names(), vfs_crt_redirect_funcs(), vfs_crt_redirect_count());
    }
    if (m && g_loader_cache_n < 512) {
        strncpy(g_loader_cache[g_loader_cache_n].key, entry, sizeof(g_loader_cache[0].key) - 1);
        g_loader_cache[g_loader_cache_n].key[sizeof(g_loader_cache[0].key) - 1] = 0;
        g_loader_cache[g_loader_cache_n].m = m;
        g_loader_cache_n++;
    }
    if (!m) {
        p_PyErr_Format(p_PyExc_ImportError, "cratix: map %s failed: %s", entry, mem_last_error());
        return NULL;
    }
    }
    sym = mem_get_export(m, init_name);
    if (!sym) {
        p_PyErr_Format(p_PyExc_ImportError, "cratix: export %s not found in %s", init_name, entry);
        return NULL;
    }
    if (getenv("CRATIX_DEBUG")) cy_log("[stub] load %s: init=%p\n", entry, sym);
    /* 重入守卫 ✅：同一入口 + init **正在执行**时又被 import ✅ → 循环导入 ✅
     * （CPython 抛 ImportError ✅ → 上层不查返回值 ✅ → 崩在 PyObject_GetAttr(NULL) ✅）。
     * 进入/离开成对 ✅：init 一返回就移出表 ✅（旧实现只进不出 ✅ —— 见上方说明 ✅）。 */
    _snprintf(guard_key, sizeof(guard_key) - 1, "%s|%s", entry, init_name);
    guard_key[sizeof(guard_key) - 1] = 0;
    if (!cratix_inflight_enter(guard_key)) {
        cy_log("[stub] RECURSIVE load of %s ⚠️ 循环导入 ✅\n", entry);
        p_PyErr_Format(p_PyExc_ImportError, "cratix: recursive import of %s", entry);
        return NULL;
    }
    mod = NULL;
    __try {
        mod = ((fn_init)sym)();
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        EXCEPTION_POINTERS *ep = GetExceptionInformation();
        cy_log("[stub] init EXCEPTION code=0x%08lX addr=%p\n", ep->ExceptionRecord->ExceptionCode,
               (void *)ep->ExceptionRecord->ExceptionAddress);
        /* 把**挂着的 Python 异常**打出来 ✅：崩溃多半是某个 import 失败后没检查返回值 ✅ */
        if (p_PyErr_Print) p_PyErr_Print();
        cratix_inflight_leave(guard_key);
        return NULL;
    }
    /* **init 已返回 → 立刻移出重入表** ✅：否则同一模块的第二次创建
     * （sys.modules.pop 后重导 / 导入失败后重试 ✅）会被误判循环导入 ✅（实测 ✅）。 */
    cratix_inflight_leave(guard_key);
    if (getenv("CRATIX_DEBUG")) cy_log("[stub] load %s: init -> %p\n", entry, (void *)mod);
    if (!mod) return NULL; /* init 自己设了异常 ✅ */
    /* 多阶段初始化的第一段（PEP 489）✅：mod 是「模块定义对象」→ 用 spec 建模块 ✅。
     * **模块体不在这里执行** ✅ —— CPython 官方时序是「create_module → **sys.modules 登记**
     * → exec_module（执行模块体）」✅。模块体若在 create_module 里跑 ✅，
     * 包 __init__ 引子模块（client/agent/__init__ 的 from ._agent import ✅）会
     * 重入自己 ✅（sys.modules 里还没有自己 ✅）→ 无限递归 ✅。
     * 模块体由 exec_def（第二个方法 ✅）在 exec_module 里执行 ✅。 */
    if (p_PyModule_FromDefAndSpec && p_PyModuleDef_Type) {
        /* 直接比对 ob_type ✅（不跑 PyObject_IsInstance 的完整协议 ✅）。
         * **PyModuleDef_Type 是数据导出 ✅ = 类型对象本身** ✅（不是「指向类型对象的指针」✅）
         * → 不能再解一层 ✅ —— 旧代码 `*(PyObject **)p_PyModuleDef_Type` 读到的是
         * 类型对象的**头 8 个字节** ✅（refcnt/etype ✅ 垃圾 ✅）→ 当类型用 ✅
         * → IsInstance 崩在 python314.dll 内部 ✅ rva 0x8dc0c ✅。
         * Cython 的 init 返回 PyModuleDef_Type 的**精确实例** ✅ ✅ */
        int is_def = ((CpyObjectHead *)mod)->ob_type == (void *)p_PyModuleDef_Type;
        if (getenv("CRATIX_DEBUG"))
            cy_log("[stub] load %s: mod=%p ob_type=%p is_def=%d\n", entry, (void *)mod,
                   (void *)((CpyObjectHead *)mod)->ob_type, is_def);
        if (is_def > 0) {
            PyObject *created = p_PyModule_FromDefAndSpec((void *)mod, spec, 1013); /* PYTHON_API_VERSION ✅ */
            if (getenv("CRATIX_DEBUG")) cy_log("[stub] load %s: FromDefAndSpec2 -> %p\n", entry, (void *)created);
            if (!created) return NULL;
            /* def 对象不 DecRef ✅：FromDefAndSpec 只存**指针** ✅（Cython 的 moduledef
             * 是静态 C 对象 ✅）—— 提前释放会让模块内部悬空 ✅（风险远大于一个
             * 进程生命周期的小对象 ✅）。 */
            return created;
        }
    }
    return mod;
}

/* 模块体执行（exec_module 调用 ✅）：PEP 489 的第二段 ✅。
 * 单阶段模块没有 Py_mod_exec 槽 ✅ → ExecDef 是 no-op ✅（重复调用也安全 ✅）。 */
static PyObject *cratix_loader_exec_def(PyObject *self, PyObject *args) {
    PyObject *module = NULL;
    PyObject *def;
    if (!p_PyArg_ParseTuple(args, "O", &module)) return NULL;
    if (!p_PyModule_GetDef || !p_PyModule_ExecDef) {
        p_PyErr_Format(p_PyExc_ImportError, "cratix: exec_def unavailable (GetDef/ExecDef unresolved)");
        return NULL;
    }
    if (getenv("CRATIX_DEBUG")) cy_log("[stub] exec_def %p...\n", (void *)module);
    def = p_PyModule_GetDef(module);
    if (getenv("CRATIX_DEBUG")) cy_log("[stub] exec_def def=%p\n", (void *)def);
    if (!def) {
        p_PyErr_Format(p_PyExc_ImportError, "cratix: module has no moduledef");
        return NULL;
    }
    if (getenv("CRATIX_DEBUG")) cy_log("[stub] exec_def: ExecDef...\n");
    if (p_PyModule_ExecDef(module, def) != 0) return NULL; /* 异常已由 ExecDef 设置 ✅ */
    if (getenv("CRATIX_DEBUG")) cy_log("[stub] exec_def: done ✅\n");
    return p_Py_BuildValue(""); /* None ✅ */
}

/* 桥的方法描述符 ✅：PyCFunction_New(ml, self) 要一个真正的 PyMethodDef ✅。
 * 之前传 ml=NULL、把函数指针塞进 self 参数 ✅ → Py_XNewRef(代码指针) ✅ → 0xC0000005 ✅。 */
/* mypyc 组产物的 capsule 指针解析 ✅（bootstrap 用 ✅）：
 * 打包形态下 ctypes.pythonapi 指向 **stub exe** ✅（GUI 子系统无 python314.dll 导出 ✅）
 * → PyCapsule_GetPointer 拿不到 ✅ → 由这里从**内存解释器**的导出表解析 ✅
 * 返回函数指针（int ✅）→ bootstrap 用 ctypes.PYFUNCTYPE 现场造 callable ✅。 */
static PyObject *cratix_loader_capsule_ptr(PyObject *self, PyObject *args) {
    PyObject *capsule = NULL;
    const char *name = NULL;
    typedef void *(*fn_capsule_ptr)(PyObject *, const char *);
    static fn_capsule_ptr p_capsule = NULL;
    void *addr;

    if (!p_PyArg_ParseTuple(args, "Os", &capsule, &name)) return NULL;
    if (!p_capsule) {
        /* 与其它 p_* API 同款双分支解析 ✅：A 形态从内存解释器 ✅ B 形态从 OS 加载的
         * python314.dll ✅（g_interp_mem 在 B 形态是 NULL ✅ 走它会必炸 ✅ 审计发现 ✅）。
         * init 阶段已解析过 ✅ —— 这里只是兜底（loader install 早于 api 解析的调用序防御 ✅）。 */
        if (p_PyCapsule_GetPointer) {
            p_capsule = (fn_capsule_ptr)p_PyCapsule_GetPointer;
        } else if (g_interp_mem) {
            p_capsule = (fn_capsule_ptr)mem_get_export(g_interp_mem, "PyCapsule_GetPointer");
        }
        if (!p_capsule) {
            p_PyErr_Format(p_PyExc_ImportError, "cratix: PyCapsule_GetPointer not resolved");
            return NULL;
        }
    }
    addr = p_capsule(capsule, name);
    if (!addr) {
        /* PyCapsule_GetPointer 失败时挂着它自己的异常 ✅ —— PyErr_Format 覆盖成
         * 我们的 ImportError ✅（与 load 的错误路径同口径 ✅ 不引额外 API 依赖 ✅）。 */
        p_PyErr_Format(p_PyExc_ImportError, "cratix: capsule %s pointer not available", name);
        return NULL;
    }
    return p_PyLong_FromLongLong((long long)(intptr_t)addr);
}

static CpyMethodDef cratix_loader_load_def = {
    "load", (void *)cratix_loader_load, METH_VARARGS,
    "load(entry, init_symbol, spec) -> module"};
static CpyMethodDef cratix_loader_exec_def_def = {
    "exec_def", (void *)cratix_loader_exec_def, METH_VARARGS,
    "exec_def(module) -> None（PEP 489 第二段：执行模块体 ✅）"};
static CpyMethodDef cratix_loader_capsule_ptr_def = {
    "capsule_ptr", (void *)cratix_loader_capsule_ptr, METH_VARARGS,
    "capsule_ptr(capsule, name) -> int（PyCapsule_GetPointer from mapped interpreter ✅）"};

static int cratix_loader_install(void) {
    PyObject *mod, *fn;
    cy_log("[stub] loader apis: CFunc=%p AddModule=%p SetAttr=%p BuildValue=%p\n",
           (void *)p_PyCFunction_New, (void *)p_PyImport_AddModule,
           (void *)p_PyObject_SetAttrString, (void *)p_Py_BuildValue);
    if (!p_PyCFunction_New || !p_PyImport_AddModule || !p_PyObject_SetAttrString || !p_Py_BuildValue) return 0;
    /* PyImport_AddModule("cratix_loader")：在 sys.modules 里建/取模块 ✅（借用引用 ✅），
     * 属性直接挂在这个模块上 ✅ —— 之前「自建模块 + AddModuleObject(name, mod)」
     * 的第二参会被忽略 ✅ → sys.modules 里留下的是空模块 ✅。 */
    mod = p_PyImport_AddModule("cratix_loader");
    if (!mod) return 0;
    fn = p_PyCFunction_New((void *)&cratix_loader_load_def, NULL);
    if (!fn) return 0;
    if (p_PyObject_SetAttrString(mod, "load", fn) != 0) {
        p_Py_DecRef(fn);
        return 0;
    }
    p_Py_DecRef(fn); /* mod 已持有引用 ✅ */
    fn = p_PyCFunction_New((void *)&cratix_loader_exec_def_def, NULL);
    if (!fn) return 0;
    if (p_PyObject_SetAttrString(mod, "exec_def", fn) != 0) {
        p_Py_DecRef(fn);
        return 0;
    }
    p_Py_DecRef(fn); /* mod 已持有引用 ✅ */
    fn = p_PyCFunction_New((void *)&cratix_loader_capsule_ptr_def, NULL);
    if (!fn) return 0;
    if (p_PyObject_SetAttrString(mod, "capsule_ptr", fn) != 0) {
        p_Py_DecRef(fn);
        return 0;
    }
    p_Py_DecRef(fn); /* mod 已持有引用 ✅ */
    return 1;
}

/* memdl 依赖解析钩子 ✅：PE 导入表里是裸 DLL 名 ✅，归档里的 DLL 按目录分层 ✅
 * → 按 basename 查归档 ✅ → memdl 映射 ✅（映射内部递归解析它自己的依赖 ✅）。
 * 模块名记 **basename** ✅ —— 后续 find_loaded(dll) 才能命中 ✅（§6.2「归档内 DLL 走 memdl」✅）。 */
static MemModule *resolve_dep_from_zip(const char *dll) {
    const zentry_t *e = zipidx_find_basename(&g_zip, dll);
    unsigned char *bytes = NULL;
    size_t len = 0;
    MemModule *m;
    /* 解释器本体不走 memdl 依赖解析 ✅：磁盘模式（B 形态 ✅）下它是 OS 模块 ✅
     * → memdl 再映射一份会造成**双解释器** ✅（状态分裂 ✅ 严禁 ✅）
     * → 返回 NULL ✅ → resolve_symbol 落到 resolve_system_module ✅
     * → GetModuleHandle 命中 OS 已加载的那份 ✅ ✅ */
    if (_stricmp(dll, "python314.dll") == 0) return NULL;
    if (!e || !zipidx_read(&g_zip, e, &bytes, &len)) return NULL;
    m = mem_map_bytes(bytes, len, dll);
    free(bytes);
    if (m) {
        if (getenv("CRATIX_DEBUG")) cy_log("[stub] dep resolved from payload: %s\n", dll);
        cratix_hook_loader(m); /* 依赖模块的加载面也要接管 ✅ */
        if (_stricmp(dll, "Qt6Core.dll") == 0) hook_qt6core_vfs(m);
    } else {
        /* **递归映射失败** ✅：外层的「unresolved import in <自身>」会覆盖内层错误 ✅
         * → 这里把**内层真因**记下来 ✅（Qt 链路定位必备 ✅）。 */
        cy_log("[stub] dep %s map failed: %s\n", dll, mem_last_error());
    }
    return m;
}

/* 归档条目 → 缓存文件 ✅（B 形态 ✅）：同大小跳过 ✅（后续启动零写入 ✅ 零落盘口径 ✅） */
static int extract_entry_to_file(const char *entry, const WCHAR *dest) {
    const zentry_t *e = zipidx_find(&g_zip, entry);
    unsigned char *bytes = NULL;
    size_t len = 0;
    HANDLE h;
    if (!e || !zipidx_read(&g_zip, e, &bytes, &len)) return 0;
    h = CreateFileW(dest, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        LARGE_INTEGER sz;
        int same = 0;
        if (GetFileSizeEx(h, &sz) && (size_t)sz.QuadPart == len) same = 1;
        CloseHandle(h);
        free(bytes);
        return same;
    }
    h = CreateFileW(dest, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        free(bytes);
        return 0;
    }
    {
        DWORD w = 0;
        WriteFile(h, bytes, (DWORD)len, &w, NULL);
        CloseHandle(h);
        free(bytes);
        return w == len;
    }
}

/* ---------- 解压（DEFLATE）支持 ✅ ---------- */
/* zip 的 method=8 存的是**裸 deflate** ✅（RFC 1951 ✅ —— 没有 zlib 头 ✅ 也没有 adler32 ✅）
 * → zlib 的 uncompress() ✅ 用不了 ✅（它要的是带头的 zlib 流 ✅ RFC 1950 ✅ —— 实测 Z_DATA_ERROR ✅）
 * → 正确姿势 ✅：inflateInit2_(windowBits = -15 ✅) + inflate() + inflateEnd() ✅。 */
typedef struct {
    const unsigned char *next_in;
    unsigned int avail_in;
    unsigned long total_in;
    unsigned char *next_out;
    unsigned int avail_out;
    unsigned long total_out;
    const char *msg;
    void *state;
    void *(*zalloc)(void *, unsigned int, unsigned int);
    void *(*zfree)(void *, void *);
    void *opaque;
    int data_type;
    unsigned long adler;
    unsigned long reserved;
} cy_z_stream;

typedef int (*fn_inflate_init2)(cy_z_stream *strm, int window_bits, const char *version, int stream_size);
typedef int (*fn_inflate)(cy_z_stream *strm, int flush);
typedef int (*fn_inflate_end)(cy_z_stream *strm);
typedef const char *(*fn_zlib_version)(void);

typedef struct {
    fn_inflate_init2 init2;
    fn_inflate inflate;
    fn_inflate_end end;
    fn_zlib_version version;
} cy_zlib_t;

static cy_zlib_t g_zlib;         /* 当前生效的实现 ✅ */
static cy_zlib_t g_zlib_mem;     /* memdl 映射版 ✅（零落盘 ✅） */
static HMODULE g_zlib_disk = NULL; /* OS 加载版 ✅（兜底 ✅） */

/* 运行时缓存目录 ✅（%LOCALAPPDATA%\winova\rt ✅）—— 两级目录逐级创建 ✅ */
static int cratix_cache_root(WCHAR *out) {
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", out, MAX_PATH);
    WCHAR *end;
    const WCHAR *sub[] = {L"\\winova", L"\\rt"};
    int si;
    if (n == 0) {
        out[0] = 0;
        return 0;
    }
    if (n >= MAX_PATH - 96) {
        /* **长 LOCALAPPDATA** ✅：先尝试 8.3 短路径 ✅（常见用户名/深目录 ✅）。
         * 实测：LOCALAPPDATA 长 225 时直接 LoadLibraryW 126 ✅ —— 路径超 MAX_PATH ✅。 */
        WCHAR shortp[MAX_PATH];
        DWORD sn = GetShortPathNameW(out, shortp, MAX_PATH);
        if (sn > 0 && sn < MAX_PATH - 64) {
            lstrcpyW(out, shortp);
            n = sn;
        } else {
            out[0] = 0;
            return 0;
        }
    }
    end = out + wcslen(out);
    for (si = 0; si < 2; si++) {
        DWORD attr;
        lstrcpyW(end, sub[si]);
        /* **根路径被文件/链接占位 → 先清掉再建目录**（A338/A339 同类 ✅）：
         * 旧行为 CreateDirectory 失败 → B 形态直接 exit 2 ✅（需手删缓存）。 */
        {
            DWORD _a = GetFileAttributesW(out);
            if (_a != INVALID_FILE_ATTRIBUTES && !(_a & FILE_ATTRIBUTE_DIRECTORY)) {
                DeleteFileW(out);
            } else if (_a != INVALID_FILE_ATTRIBUTES && (_a & FILE_ATTRIBUTE_REPARSE_POINT)) {
                RemoveDirectoryW(out);
            }
        }
        CreateDirectoryW(out, NULL);
        /* **不能是 junction/symlink** ✅：否则 B 提取会写到链接目标 ✅
         * （A203/A204/A206/A207/A208/A210/A211/A212/A213 同类 ✅）：宁可不用磁盘缓存 ✅。 */
        attr = GetFileAttributesW(out);
        if (attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_REPARSE_POINT)) {
            out[0] = 0;
            return 0;
        }
        end += lstrlenW(sub[si]);
    }

    return 1;
}

/* 从 memdl 模块取 zlib 导出 ✅ */
static int cratix_zlib_bind_mem(cy_zlib_t *out, MemModule *m) {
    out->init2 = (fn_inflate_init2)mem_get_export(m, "inflateInit2_");
    out->inflate = (fn_inflate)mem_get_export(m, "inflate");
    out->end = (fn_inflate_end)mem_get_export(m, "inflateEnd");
    out->version = (fn_zlib_version)mem_get_export(m, "zlibVersion");
    return out->init2 && out->inflate && out->end && out->version;
}

/* 从 OS 模块取 zlib 导出 ✅ */
static int cratix_zlib_bind_dll(cy_zlib_t *out, HMODULE h) {
    out->init2 = (fn_inflate_init2)GetProcAddress(h, "inflateInit2_");
    out->inflate = (fn_inflate)GetProcAddress(h, "inflate");
    out->end = (fn_inflate_end)GetProcAddress(h, "inflateEnd");
    out->version = (fn_zlib_version)GetProcAddress(h, "zlibVersion");
    return out->init2 && out->inflate && out->end && out->version;
}

/* 磁盘版 zlib ✅（兜底 ✅）：落到运行时缓存目录由 OS 加载 ✅ —— 懒加载 ✅
 * （memdl 版能用就不落盘 ✅ —— 零落盘口径 ✅）。 */
static int cratix_load_disk_zlib(cy_zlib_t *out) {
    WCHAR cache_w[MAX_PATH], dest[MAX_PATH];
    if (!g_zlib_disk) {
        if (!cratix_cache_root(cache_w)) return 0;
        lstrcpyW(dest, cache_w);
        lstrcatW(dest, L"\\zlib1.dll");
        if (!extract_entry_to_file("native/zlib1.dll", dest)) return 0;
        g_zlib_disk = LoadLibraryW(dest);
        if (!g_zlib_disk) return 0;
    }
    return cratix_zlib_bind_dll(out, g_zlib_disk);
}

/* zipread 的解压回调 ✅：裸 deflate ✅（windowBits=-15 ✅）—— 长度必须与中央目录一致 ✅ */
static int cratix_inflate(const unsigned char *src, size_t src_len, unsigned char *dst, size_t dst_len) {
    cy_z_stream strm;
    int rc;
    if (!g_zlib.inflate) return 0;
    memset(&strm, 0, sizeof(strm));
    strm.next_in = src;
    strm.avail_in = (unsigned int)src_len;
    strm.next_out = dst;
    strm.avail_out = (unsigned int)dst_len;
    rc = g_zlib.init2(&strm, -15 /*裸 deflate ✅*/, g_zlib.version(), (int)sizeof(cy_z_stream));
    if (rc != 0) {
        cy_log("[stub] inflateInit2 failed rc=%d\n", rc);
        return 0;
    }
    rc = g_zlib.inflate(&strm, 4 /*Z_FINISH ✅*/);
    if (rc != 1 /*Z_STREAM_END ✅*/ || (size_t)strm.total_out != dst_len) {
        cy_log("[stub] inflate failed rc=%d out=%lu want=%lu src=%lu msg=%s\n", rc, strm.total_out,
               (unsigned long)dst_len, (unsigned long)src_len, strm.msg ? strm.msg : "(none)");
        g_zlib.end(&strm);
        return 0;
    }
    g_zlib.end(&strm);
    return 1;
}

/* ---------- ZIP_LZMA 解码 ✅（M7 原生二进制压缩换代 ✅）---------- */
/* CPython zipfile 的 ZIP_LZMA（method 14 ✅）条目布局 ✅（zipfile.py LZMACompressor ✅）：
 *   [0]=9 [1]=4（版本 ✅）+ u16 props 长度 ✅ + props（LZMA1 滤镜属性 ✅ 5 字节 ✅）
 *   + 裸 LZMA1 流（FORMAT_RAW ✅ 含 EOS 标记 ✅）。
 * 解压长度取自中央目录 ✅ → 单次 LzmaDec_DecodeToDic 直达目标缓冲 ✅（零复制 ✅）。
 * LzmaDec.c 静态链接 ✅ —— 无 DLL 依赖 ✅（解释器还睡着时就要解 python314.dll ✅）。 */
static void *lzma_alloc_fn(ISzAllocPtr p, size_t size) {
    (void)p;
    return malloc(size);
}

static void lzma_free_fn(ISzAllocPtr p, void *addr) {
    (void)p;
    free(addr);
}

static ISzAlloc g_lzma_alloc = { lzma_alloc_fn, lzma_free_fn };

static int cratix_lzma_decode(const unsigned char *src, size_t src_len, unsigned char *dst, size_t dst_len) {
    unsigned props_len;
    CLzmaDec dec;
    ELzmaStatus status;
    SRes rc;
    SizeT in_processed, out_len = dst_len;
    if (src_len < 4 + LZMA_PROPS_SIZE) return 0;
    if (src[0] != 9 || src[1] != 4) return 0; /* zipfile 的版本头 ✅ */
    props_len = (unsigned)(src[2] | ((unsigned)src[3] << 8));
    if (props_len != LZMA_PROPS_SIZE || src_len < (size_t)(4 + props_len)) return 0;
    LzmaDec_Construct(&dec);
    if (LzmaDec_AllocateProbs(&dec, src + 4, LZMA_PROPS_SIZE, &g_lzma_alloc) != SZ_OK) return 0;
    /* 直接解到目标缓冲 ✅（输出 < 字典尺寸时全部引用都在已产出范围内 ✅） */
    dec.dic = dst;
    dec.dicBufSize = dst_len;
    LzmaDec_Init(&dec);
    in_processed = src_len - 4 - props_len;
    rc = LzmaDec_DecodeToDic(&dec, out_len, src + 4 + props_len, &in_processed, LZMA_FINISH_END, &status);
    LzmaDec_FreeProbs(&dec, &g_lzma_alloc);
    if (rc != SZ_OK || dec.dicPos != out_len) return 0;
    if (status != LZMA_STATUS_FINISHED_WITH_MARK && status != LZMA_STATUS_MAYBE_FINISHED_WITHOUT_MARK) return 0;
    return 1;
}

/* ZIP_ZSTANDARD（method 93 ✅）解码 ✅：标准 zstd 帧 ✅（zipfile ZstdCompressor 写的 ✅）
 * —— ZSTD_decompress 单次直达 ✅ 上下文内部自管 ✅。
 * 选型 ✅（§14 实测）：zstd-19 比 LZMA 大 ~8% 但解压快 ~70 倍 ✅
 * → 启动期几乎零惩罚 ✅（LZMA 要 +1.7s ✅）→ 二进制条目默认走它 ✅。 */
static int cratix_zstd_decode(const unsigned char *src, size_t src_len, unsigned char *dst, size_t dst_len) {
    size_t r = ZSTD_decompress(dst, dst_len, src, src_len);
    return r == dst_len;
}

/* 装载解压器 ✅：
 *   ① memdl 映射归档里的 zlib1.dll ✅（零落盘 ✅）—— zlib1.dll 只依赖
 *      api-ms-win-crt-* + KERNEL32 ✅ → 无静态 TLS ✅ → memdl 安全 ✅；
 *   ② 兜底 ✅：落到运行时缓存目录由 OS 加载 ✅（memdl 失败时用 ✅）。
 * 失败**不致命** ✅（纯 STORED 载荷照样能跑 ✅ —— 例如 M0 spike ✅）。 */
static int cratix_prepare_inflate(void) {
    const zentry_t *e;
    unsigned char *bytes = NULL;
    size_t len = 0;
    MemModule *m = NULL;
    e = zipidx_find(&g_zip, "native/zlib1.dll");
    if (e && zipidx_read(&g_zip, e, &bytes, &len)) {
        m = mem_map_bytes(bytes, len, "zlib1.dll");
        free(bytes);
        if (m && cratix_zlib_bind_mem(&g_zlib_mem, m)) {
            g_zlib = g_zlib_mem;
            zipidx_set_inflate(cratix_inflate);
            cy_log("[stub] inflate ready (memdl zlib1.dll)\n");
            return 1;
        }
    }
    /* memdl 版不可用 ✅ → 直接用磁盘版 ✅（OS 加载 ✅） */
    if (!cratix_load_disk_zlib(&g_zlib)) return 0;
    zipidx_set_inflate(cratix_inflate);
    cy_log("[stub] inflate ready (disk zlib1.dll)\n");
    return 1;
}

/* 解释器 DLL 条目查找 ✅（版本无关 ✅）
 *
 * 旧口径硬编码 "native/python314.dll" ✅ → 换 CPython 版本就找不到 ✅。
 * 这里：先精确匹配 ✅ → 再扫 native/python3*.dll ✅（取文件名最长的 ✅）。 */
static const zentry_t *find_interpreter_entry(const zipidx_t *z) {
    const zentry_t *e = zipidx_find(z, "native/python314.dll");
    const zentry_t *best = NULL;
    int i;
    if (e) return e;
    for (i = 0; i < z->count; i++) {
        const char *n = z->entries[i].name;
        size_t len;
        if (strncmp(n, "native/python3", 14) != 0) continue;
        len = strlen(n);
        if (len < 5 || strcmp(n + len - 4, ".dll") != 0) continue;
        if (!best || strlen(best->name) < len) best = &z->entries[i];
    }
    return best;
}

int main(void) {
    WCHAR self[MAX_PATH];
    char *filebuf = NULL;
    size_t filesize = 0;
    cookie_t ck;
    unsigned char *pybytes = NULL;
    size_t pylen = 0;
    MemModule *py = NULL;
    const char *err = NULL;

    cy_log_init(); /* 日志通道 ✅（控制台 / 文件 / NUL ✅）—— 必须最先 ✅ */
    cratix_diag_set_main_tid(GetCurrentThreadId()); /* 诊断用 ✅：主线程 tid ✅ */
    /* 诊断开关 ✅：CRATIX_VEH=0 ✅ → 不装 VEH ✅ → 进程按**原始**异常码退出 ✅
     * （VEH 自身一旦抛异常 ✅ → 后续只剩 single-step ✅ → 真因被掩盖 ✅）。 */
    {
        const char *nov = getenv("CRATIX_VEH");
        if (!(nov && nov[0] == '0')) AddVectoredExceptionHandler(1, veh_handler);
    }
    /* 崩溃兜底弹窗 ✅（M7 ✅）：未处理异常默认静默死进程 ✅ → 弹窗告知 ✅。
     * CRATIX_NO_ERRORBOX=1 ✅ 关（自动化验收 ✅）；CRATIX_VEH=0 ✅ 调试会话也一并关 ✅
     * （要保持原始退出码的诊断 ✅）。 */
    {
        const char *nov = getenv("CRATIX_VEH");
        if (!(nov && nov[0] == '0')) SetUnhandledExceptionFilter(cratix_unhandled_exception);
    }
    /* 宿主 TLS 池配置 ✅ —— **必须在映射任何模块之前** ✅
     * （zlib1.dll 之类的模块会先被映射 ✅ → 它们也要分到**独占**静态 TLS 槽 ✅
     *   → 池没配好就会走兜底路径 ✅ → 索引落不到加载器的槽空间 ✅）。 */
    {
        const char *imode = getenv("CRATIX_INTERP");
        /* **A 形态是默认** ✅（M6c ✅ 用户口径：不能解压释放文件 ✅）
         * CRATIX_INTERP=disk ✅ 显式回退 B 形态（解释器+Qt 落缓存 ✅）✅ */
        g_a_form = !(imode && imode[0] == 'd');
    }
    cratix_configure_host_tls();
    cratix_hook_crt_symbols(); /* CRT 的符号解析也要接管 ✅ */
    cratix_hook_exit(); /* 退出前摘 PEB 注册 ✅ */
    if (!GetModuleFileNameW(NULL, self, MAX_PATH)) {
        cratix_fail_dialog("GetModuleFileNameW 失败（无法定位自身路径）");
        return 2;
    }
    /* 规范化路径：GetModuleFileName 返回的是「调用时写的路径」（可能带 ./），
     * 而 Python 侧用的是 abspath 后的规范形式 —— 两边必须一致，
     * 否则「载荷条目 ↔ 虚拟路径」的换算会匹配不上。 */
    {
        WCHAR full[MAX_PATH];
        DWORD n = GetFullPathNameW(self, MAX_PATH, full, NULL);
        if (n > 0 && n < MAX_PATH) {
            wcscpy(self, full);
        }
    }
    WideCharToMultiByte(CP_UTF8, 0, self, -1, g_exe_path, MAX_PATH, NULL, NULL);
    strncpy(g_exe_dir, g_exe_path, MAX_PATH - 1);
    {
        char *slash = strrchr(g_exe_dir, '\\');
        size_t k;
        if (slash) *slash = 0;
        for (k = 0; g_exe_dir[k] && k + 1 < sizeof(g_exe_dir_slash); k++) {
            g_exe_dir_slash[k] = (g_exe_dir[k] == '\\') ? '/' : g_exe_dir[k];
        }
        g_exe_dir_slash[k] = 0;
    }
    cy_log("[stub] exe = %s\n", g_exe_path);

    filebuf = read_whole_file_w(self, &filesize);
    if (!filebuf) {
        cratix_fail_dialog("无法读取自身 exe 文件（可能被杀毒软件占用或权限不足）");
        return 2;
    }
    if (filesize < COOKIE_SIZE) {
        cratix_fail_dialog("exe 文件不完整（体积小于打包 cookie）——下载或复制可能被截断");
        return 2;
    }
    /* ---- 载荷来源二选一 ✅（D1 ✅） ---- */
    {
        static char *sidecar_buf = NULL; /* 旁置载荷缓冲区 ✅（进程生命周期内有效 ✅） */
        size_t sidecar_size = 0;
        int have_cookie = 0;
        if (filesize >= COOKIE_SIZE) {
            memcpy(&ck, filebuf + filesize - COOKIE_SIZE, COOKIE_SIZE);
            have_cookie = (memcmp(ck.magic, "CRTX1", 5) == 0);
        }
        if (have_cookie) {
            /* 内嵌 ✅（onefile ✅）：载荷在 exe 末尾 ✅ */
            if ((size_t)ck.off + (size_t)ck.size > filesize) {
                cratix_fail_dialog("exe 打包载荷越界（cookie out of range）——文件可能被截断");
                return 2;
            }
            cy_log("[stub] payload off=%llu size=%llu\n", ck.off, ck.size);
            /* **CRC 校验** ✅：assemble 写入的 ck.crc ✅ 与实际载荷比对 ✅ →
             * 损坏/截断/篡改在**加载任何代码之前**就报错 ✅。 */
            {
                unsigned int actual_crc = cratix_crc32((const unsigned char *)filebuf + ck.off, (size_t)ck.size);
                if (actual_crc != ck.crc) {
                    cy_log("[stub] payload CRC mismatch: cookie=0x%08X actual=0x%08X\n", ck.crc, actual_crc);
                    cratix_fail_dialog("载荷校验失败（CRC 不一致）—— exe 损坏或被篡改");
                    return 2;
                }
                cy_log("[stub] payload crc ok (0x%08X)\n", actual_crc);
            }
            g_payload_base = (const unsigned char *)filebuf + ck.off;
            g_payload_size = (size_t)ck.size;
            g_payload_embedded = 1;
        } else {
            /* 旁置 ✅（onedir ✅）：<exe_dir>/payload.zip ✅ */
            WCHAR dirw[MAX_PATH];
            WCHAR zpath[MAX_PATH];
            MultiByteToWideChar(CP_UTF8, 0, g_exe_dir, -1, dirw, MAX_PATH);
            _snwprintf(zpath, MAX_PATH, L"%s\\payload.zip", dirw);
            sidecar_buf = read_whole_file_w(zpath, &sidecar_size);
            if (!sidecar_buf) {
                cratix_fail_dialog("找不到旁置载荷 payload.zip —— 目录发行版不完整");
                return 2;
            }
            wcscpy(g_payload_zip_w, zpath);
            g_payload_base = (const unsigned char *)sidecar_buf;
            g_payload_size = sidecar_size;
            g_payload_embedded = 0;
            cy_log("[stub] sidecar payload loaded (%llu bytes)\n", (unsigned long long)sidecar_size);
        }
    }
    if (!zipidx_open(&g_zip, g_payload_base, g_payload_size)) {
        cratix_fail_dialog("打包载荷解析失败（zip 中央目录损坏）");
        return 2;
    }
    cy_log("[stub] zip entries = %d\n", g_zip.count);
    zipidx_set_lzma(cratix_lzma_decode); /* ZIP_LZMA 条目 ✅ */
    zipidx_set_zstd(cratix_zstd_decode); /* ZIP_ZSTANDARD 条目 ✅（二进制默认 ✅ M7 ✅） */
    mem_set_dep_resolver(resolve_dep_from_zip); /* 归档内 DLL 走 memdl ✅（要在映射解释器之前装好 ✅） */
    /* 解压器 ✅（载荷用 DEFLATE ✅ —— stub 侧的 zip 读取要能解压 ✅） */
    if (!cratix_prepare_inflate()) {
        cy_log("[stub] note: inflate unavailable（压缩条目会读失败 ✅）\n");
    }
    {
        /* VFS 初始化 ✅（M4 / D1 ✅）：exe 目录 → 归档路径映射 ✅
         * exe_path 也传入 ✅ —— Qt 的插件路径把 **exe 路径当目录前缀** 用 ✅（M6c ✅）。 */
        WCHAR exe_dir_w[512], exe_path_w[512];
        MultiByteToWideChar(CP_UTF8, 0, g_exe_dir, -1, exe_dir_w, 512);
        MultiByteToWideChar(CP_UTF8, 0, g_exe_path, -1, exe_path_w, 512);
        vfs_init(&g_zip, exe_dir_w, exe_path_w);
    }
    /* ✅ 载荷里的包数据也要能被 Python open() 读（A 形态）。 */
    cratix_hook_crt_files();

    /* 1) 手工映射解释器 */
    {
        const zentry_t *e = find_interpreter_entry(&g_zip);
        cy_log("[stub] interpreter entry = %s\n", e ? e->name : "MISSING");
        if (!e || !zipidx_read(&g_zip, e, &pybytes, &pylen)) {
            cratix_fail_dialog("载荷里没有解释器（native/python3xx.dll 缺失）——打包不完整");
            return 2;
        }
        cy_log("[stub] extracted %llu bytes\n", (unsigned long long)pylen);
    }
    {
        /* 缓存目录 ✅（B 形态 ✅）：CRATIX_QT_CACHE = %LOCALAPPDATA%\winova\rt ✅
         * 其下 PySide6\ 与 shiboken6\ 是两个子包 ✅（bootstrap 的 sys.path 指向缓存根 ✅）。
         * python314.dll / python3.dll 提取到 PySide6\ ✅ —— python3.dll 是转发器 ✅
         * OS 解析它的转发要按名加载 python314.dll ✅ → 必须有**同一份 OS 可见文件** ✅
         * （缓存里的文件与 stub LoadLibrary 的路径一致 ✅ → OS 复用同一模块 ✅）。 */
        WCHAR cache_w[MAX_PATH];
        wchar_t pydll_w[MAX_PATH];
        const char *mode = getenv("CRATIX_INTERP");
        /* **A 形态（默认 ✅ M6c ✅ 用户口径：不能解压释放文件 ✅）**：解释器手工映射 ✅
         * 标准库/项目/Cython 产物/第三方原生/Qt 全链**全部内存** ✅✅ 零落盘 ✅；
         * CRATIX_INTERP=disk ✅ → **B 形态回退** ✅：解释器由 OS 从缓存目录加载 ✅
         * （python3.dll 转发器要有 OS 可见的同一份文件 ✅）✅。 */
        int use_disk = (mode && mode[0] == 'd');
        /* Qt 运行时落盘缓存 ✅：**默认开** ✅（B 形态 ✅ —— PySide6/shiboken6 的 C++ 扩展
        * 在 memdl 手工映射下会崩 ✅ → 见 findings §9.4 ✅）；CRATIX_QT_DISK=0 可关掉 ✅
        * （A 形态实验用 ✅）。 */
        const char *qtd = getenv("CRATIX_QT_DISK");
        int qt_disk = qtd ? (qtd[0] == '0' ? 0 : 1) : 1;
        cache_w[0] = 0;
        if (!use_disk) {
            /* A 形态 ✅：不落 Qt 缓存 ✅ → bootstrap 不提取 ✅（finder 表保留 PySide6 ✅） */
            SetEnvironmentVariableW(L"CRATIX_QT_CACHE", qt_disk ? cache_w : L"");
            SetEnvironmentVariableW(L"CRATIX_PYTHON_DLL", L"");
            pydll_w[0] = 0;
            cy_log("[stub] A 形态：解释器内存映射 ✅ 不落盘 ✅\n");
        } else {
            if (cratix_cache_root(cache_w)) {
                /* **缓存目录按载荷 CRC 版本化** ✅：旧实现所有构建共用 rt\ ✅ + 只比文件大小 ✅
                 * → 升级 exe 后同大小的新 DLL 被旧缓存冒充 ✅（静默跑旧代码 ✅）。
                 * → 每份载荷独立子目录 rt\b<crc> ✅：旧缓存不被复用 ✅，也不需要删除正在被加载的
                 * 解压器（✅ 实测：清空缓存会撞上已加载的 _zstd.pyd ✅）。 */
                {
                    WCHAR ver[32];
                    /* **CRC+size** ✅：仅 CRC32 有碰撞概率 ✅ → 不同载荷共用提取目录 ✅。 */
                    _snwprintf(ver, 32, L"\\b%08X_%08X", ck.crc, (unsigned int)ck.size);
                    ver[31] = 0;
                    lstrcatW(cache_w, ver);
                }
                /* **版本目录本身被文件/链接占位 → 先清掉再建**（A341 同类 ✅）。 */
                {
                    DWORD _a = GetFileAttributesW(cache_w);
                    if (_a != INVALID_FILE_ATTRIBUTES && !(_a & FILE_ATTRIBUTE_DIRECTORY)) {
                        DeleteFileW(cache_w);
                    } else if (_a != INVALID_FILE_ATTRIBUTES && (_a & FILE_ATTRIBUTE_REPARSE_POINT)) {
                        RemoveDirectoryW(cache_w);
                    }
                }
                if (!CreateDirectoryW(cache_w, NULL) && GetLastError() != ERROR_ALREADY_EXISTS) {
                    /* **清晰报错** ✅：旧行为继续走 ✅ → pydllW 是相对路径 ✅
                     * → LoadLibrary 126 ✅、错误信息说不清楚原因 ✅。 */
                    cratix_fail_dialog("无法创建运行时缓存目录（%LOCALAPPDATA%\\winova\\rt）—— 路径过长/无写权限");
                    return 2;
                }
                {
                    DWORD attr = GetFileAttributesW(cache_w);
                    if (attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_REPARSE_POINT)) {
                        cratix_fail_dialog("运行时缓存目录不能是 junction/symlink");
                        return 2;
                    }
                }
                SetEnvironmentVariableW(L"CRATIX_QT_CACHE", qt_disk ? cache_w : L"");
                /* 提取 python314.dll / python3.dll ✅（同大小跳过 ✅ 后续启动零写入 ✅） */
                {
                    wchar_t dest[MAX_PATH];
                    WCHAR pyside_dir[MAX_PATH];
                    lstrcpyW(pyside_dir, cache_w);
                    lstrcatW(pyside_dir, L"\\PySide6");
                    /* **文件占位 → 先删文件再建目录** ✅：旧行为 CreateDirectory 失败 ✅
                     * → 解释器提取失败 ✅ → LoadLibrary 126（A336 只提示 ✅）。
                     * hardlink 只删链接本身 ✅（不写外部）；junction/symlink 仍由下面
                     * 的 reparse 检查明确拒绝 ✅（不自动删，避免意外）。 */
                    {
                        DWORD _pa = GetFileAttributesW(pyside_dir);
                        if (_pa != INVALID_FILE_ATTRIBUTES && !(_pa & FILE_ATTRIBUTE_DIRECTORY)) {
                            DeleteFileW(pyside_dir);
                        } else if (_pa != INVALID_FILE_ATTRIBUTES && (_pa & FILE_ATTRIBUTE_REPARSE_POINT)) {
                            /* **目录 junction/symlink**：RemoveDirectoryW 只删重解析点本身 ✅
                             * 不触碰目标 ✅；随后重建真实目录 ✅（外部目标保持 0 写入）。 */
                            RemoveDirectoryW(pyside_dir);
                        }
                    }
                    CreateDirectoryW(pyside_dir, NULL);
                    {
                        DWORD attr = GetFileAttributesW(pyside_dir);
                        if (attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_REPARSE_POINT)) {
                            cratix_fail_dialog("PySide6 缓存目录不能是 junction/symlink");
                            return 2;
                        }
                    }
                    lstrcpyW(dest, pyside_dir);
                    lstrcatW(dest, L"\\python314.dll");
                    extract_entry_to_file("native/python314.dll", dest);
                    lstrcpyW(dest, pyside_dir);
                    lstrcatW(dest, L"\\python3.dll");
                    extract_entry_to_file("native/python3.dll", dest);
                }
            } else {
                /* **根目录创建失败** ✅（LOCALAPPDATA 无效 / 极长且无 8.3 短路径 ✅）
                 * → 直接给人话 ✅，不要拿空路径去 LoadLibrary ✅。 */
                cratix_fail_dialog("无法创建运行时缓存目录（%LOCALAPPDATA%\\winova\\rt）—— LOCALAPPDATA 无效或路径过长");
                return 2;
            }
        }
        if (use_disk) {
        lstrcpyW(pydll_w, cache_w);
        lstrcatW(pydll_w, L"\\PySide6\\python314.dll");
        SetEnvironmentVariableW(L"CRATIX_PYTHON_DLL", pydll_w);
        }
        cy_log("[stub] loading interpreter (mode=%s)...\n", use_disk ? "disk" : "mem");
        if (use_disk) {
            HMODULE h = LoadLibraryW(pydll_w);
            if (!h) {
                DWORD err = GetLastError();
                char msg[300];
                _snprintf(msg, sizeof(msg) - 1, "解释器加载失败（B 形态 LoadLibraryW %lu）：%s", (unsigned long)err, pydll_w);
                msg[sizeof(msg) - 1] = 0;
                /* **缓存解释器目录被文件占位**（A335/§4.17）：这是 stub 在 Python 之前
                 * 就会失败的场景，bootstrap 修不到 ✅。至少把「删哪里」说清楚 ✅。 */
                WCHAR parent[MAX_PATH];
                lstrcpynW(parent, pydll_w, MAX_PATH);
                WCHAR *slash = wcsrchr(parent, L'\\');
                if (slash) *slash = 0;
                DWORD attrs = GetFileAttributesW(parent);
                if (attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY)) {
                    cy_log("[stub] 提示：缓存解释器目录被文件占位：%ls（删除该文件/缓存目录后重试）\n", parent);
                    size_t used = strlen(msg);
                    if (used + 32 < sizeof(msg)) {
                        _snprintf(msg + used, sizeof(msg) - used - 1, "（缓存目录被文件占位，删除后重试）");
                        msg[sizeof(msg) - 1] = 0;
                    }
                }
                cratix_fail_dialog(msg);
                return 2;
            }
            hpy_disk = h;
            cy_log("[stub] interpreter loaded by OS at %p\n", (void *)h);
        } else {
            py = mem_map_bytes(pybytes, pylen, "python314.dll");
            g_interp_mem = py;
            cy_log("[stub] map returned %p\n", (void *)py);
            g_module_base = mem_image_base(py);
            if (!py) {
                char msg[400];
                _snprintf(msg, sizeof(msg) - 1, "解释器内存映射失败：%s", mem_last_error() ? mem_last_error() : "?");
                msg[sizeof(msg) - 1] = 0;
                cratix_fail_dialog(msg);
                return 2;
            }
            cy_log("[stub] python314.dll mapped at %p (%llu bytes)\n", (void *)py, (unsigned long long)pylen);
            /* TLS 诊断 ✅：解释器的静态 TLS 索引 ✅ + 槽内容 ✅
             * （CPython 读的是「槽 + 8」✅ —— 正常加载时该处必须是 0 ✅）。 */
            {
                /* mem_nt_of 要的是**镜像基址** ✅ —— py 是 MemModule* ✅（之前传错 ✅ → NULL ✅ → 崩 ✅）。 */
                const IMAGE_NT_HEADERS64 *pnt = mem_nt_of(mem_image_base(py));
                const IMAGE_DATA_DIRECTORY *td =
                    &pnt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_TLS];
                if (td->VirtualAddress) {
                    const IMAGE_TLS_DIRECTORY *t =
                        (const IMAGE_TLS_DIRECTORY *)((BYTE *)mem_image_base(py) + td->VirtualAddress);
                    DWORD idx = *(const DWORD *)(uintptr_t)t->AddressOfIndex;
                    void **slots = mem_tls_slot_array();
                    cy_log("[stub] interp TLS index=%lu slots=%p\n", (unsigned long)idx, (void *)slots);
                    {
                        BYTE *blk = (BYTE *)cratix_tls_block(idx);
                        char hx[3 * 48 + 1];
                        size_t bi, hk = 0;
                        const char *digits = "0123456789abcdef";
                        cy_log("[stub] interp TLS block=%p:", (void *)blk);
                        if (blk) {
                            for (bi = 0; bi < 40; bi++) {
                                hx[hk++] = digits[blk[bi] >> 4];
                                hx[hk++] = digits[blk[bi] & 15];
                            }
                            hx[hk] = 0;
                            cy_log(" %s", hx);
                        }
                        cy_log("\n");
                    }
                    /* 槽内容不再读 ✅ —— 加载器的槽数组很短 ✅ → 按解释器索引取值会越界 ✅。 */
                }
            }
        }
    }

    /* 2) 保存真实 API 并改写 IAT */
    {
        HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
        g_real_LoadLibraryExW = (fn_LoadLibraryExW)GetProcAddress(k32, "LoadLibraryExW");
        g_real_LoadLibraryW = (fn_LoadLibraryW)GetProcAddress(k32, "LoadLibraryW");
        g_real_GetProcAddress = (fn_GetProcAddress)GetProcAddress(k32, "GetProcAddress");
        g_real_FreeLibrary = (fn_FreeLibrary)(void *)GetProcAddress(k32, "FreeLibrary");
    }
    if (py && !mem_redirect_tls_getaddr(py)) {
        cy_log("[stub] note: interpreter does not import ntdll!__tls_get_addr\n");
    }
    {
        const char *names[3] = {"LoadLibraryExW", "LoadLibraryW", "GetProcAddress"};
        const void *repls[3] = {(const void *)cratix_LoadLibraryExW, (const void *)cratix_LoadLibraryW,
                                 (const void *)cratix_GetProcAddress};
        {
            const char *nohook = getenv("CRATIX_NOHOOK");
            int ok = (nohook && nohook[0] == '1')
                         ? 1
                         : (hpy_disk ? mem_redirect_import_any_at((void *)hpy_disk, mem_nt_of(hpy_disk),
                                                names, repls, 3)
                                     : mem_redirect_import_any(py, names, repls, 3));
            if (!ok) {
                cy_log("[stub] IAT redirect failed (KERNEL32.dll not imported?)\n");
                return 2;
            }
            cy_log("[stub] IAT redirected: LoadLibraryExW/LoadLibraryW/GetProcAddress\n");
        }
        /* VFS 文件面也挂到**解释器**上 ✅（M4 ✅）：Python 侧的 Path.is_dir()/open() 走
         * python314.dll 的 IAT ✅ —— 不挂的话 PySide6/__init__.py 的 shiboken6 目录探测
         * 全部落空 ✅（实测 ImportError ✅）。VFS 的透传规则保证真实路径不受影响 ✅。 */
        if (!(getenv("CRATIX_NOVFS") && getenv("CRATIX_NOVFS")[0] == '1')) {
            int vok = 0;
            if (hpy_disk) {
                /* **B 形态也要挂** ✅：解释器是 OS 加载的 ✅ —— 旧实现只挂内存形态 ✅
                 * → B 形态 open() 拿到伪句柄后 ReadFile 落到真实 API ✅ → WinError 6 句柄无效 ✅（实测 ✅）。
                 * 对任意基址的 IAT 重定向用 mem_redirect_import_any_at ✅。 */
                const IMAGE_NT_HEADERS64 *pnt = mem_nt_of((void *)hpy_disk);
                if (pnt) {
                    vok = mem_redirect_import_any_at((void *)hpy_disk, pnt, vfs_kernel_redirect_names(),
                                                     vfs_kernel_redirect_funcs(), vfs_kernel_redirect_count());
                    mem_redirect_import_any_at((void *)hpy_disk, pnt, vfs_nt_redirect_names(),
                                               vfs_nt_redirect_funcs(), vfs_nt_redirect_count());
                    {
                        int crt_ok = mem_redirect_import_any_at((void *)hpy_disk, pnt, vfs_crt_redirect_names(),
                                                                vfs_crt_redirect_funcs(),
                                                                vfs_crt_redirect_count());
                        cy_log("[stub] CRT _wstat64i32 redirect: %s\n",
                               crt_ok ? "ok ✅" : "descriptor not found ✗");
                    }
                }
            } else if (py && mem_redirect_import_any(py, vfs_kernel_redirect_names(),
                                                     vfs_kernel_redirect_funcs(),
                                                     vfs_kernel_redirect_count())) {
                vok = 1;
                mem_redirect_import_any(py, vfs_nt_redirect_names(), vfs_nt_redirect_funcs(),
                                        vfs_nt_redirect_count());
                {
                    int crt_ok = mem_redirect_import_any(py, vfs_crt_redirect_names(),
                                                         vfs_crt_redirect_funcs(), vfs_crt_redirect_count());
                    cy_log("[stub] CRT _wstat64i32 redirect: %s\n",
                           crt_ok ? "ok ✅" : "descriptor not found ✗");
                }
            }
            if (vok) cy_log("[stub] VFS hooked: python314.dll ✅\n");
            /* os.dup：把解释器的 _dup 重定向到 VFS 包装（A/B 都尝试）。
             * CRATIX_NO_DUPHOOK=1 仅供诊断（隔离 mmap 的 DuplicateHandle 路径）。 */
            {
                const char *nohook = getenv("CRATIX_NO_DUPHOOK");
                if (!(nohook && nohook[0] == '1')) {
                    const char *dn[4] = {"_dup", "DuplicateHandle", "GetHandleInformation",
                                         "SetHandleInformation"};
                    const void *dr[4] = {(const void *)cratix_dup_crt, (const void *)cratix_DuplicateHandle,
                                         (const void *)cratix_GetHandleInformation,
                                         (const void *)cratix_SetHandleInformation};
                    int dok = hpy_disk
                                  ? mem_redirect_import_any_at((void *)hpy_disk, mem_nt_of((void *)hpy_disk), dn, dr, 4)
                                  : (py ? mem_redirect_import_any(py, dn, dr, 4) : 0);
                    cy_log("[stub] _dup redirect: %s\n", dok ? "ok ✅" : "not found ✗");
                }
            }
        }
    }

    /* 3) 解析 Python API */
    {
        if (hpy_disk) {
            p_Py_InitializeEx = (fn_Py_InitializeEx)GetProcAddress(hpy_disk, "Py_InitializeEx");
            p_Py_IsInitialized = (fn_Py_IsInitialized)GetProcAddress(hpy_disk, "Py_IsInitialized");
            p_Py_FinalizeEx = (fn_Py_FinalizeEx)GetProcAddress(hpy_disk, "Py_FinalizeEx");
            p_PyRun_SimpleString = (fn_PyRun_SimpleString)GetProcAddress(hpy_disk, "PyRun_SimpleString");
            p_PyErr_Print = (fn_PyErr_Print)GetProcAddress(hpy_disk, "PyErr_Print");
            p_PyImport_ImportModule = (fn_PyImport_ImportModule)GetProcAddress(hpy_disk, "PyImport_ImportModule");
            p_PyObject_GetAttrString = (fn_PyObject_GetAttrString)GetProcAddress(hpy_disk, "PyObject_GetAttrString");
            p_PyObject_CallObject = (fn_PyObject_CallObject)GetProcAddress(hpy_disk, "PyObject_CallObject");
            p_PyTuple_New = (fn_PyTuple_New)GetProcAddress(hpy_disk, "PyTuple_New");
            p_PyTuple_SetItem = (fn_PyTuple_SetItem)GetProcAddress(hpy_disk, "PyTuple_SetItem");
            p_PyLong_FromLongLong = (fn_PyLong_FromLongLong)GetProcAddress(hpy_disk, "PyLong_FromLongLong");
            p_PyModule_GetDef = (fn_PyModule_GetDef)GetProcAddress(hpy_disk, "PyModule_GetDef");
            p_PyModule_ExecDef = (fn_PyModule_ExecDef)GetProcAddress(hpy_disk, "PyModule_ExecDef");
            p_PyObject_IsInstance = (fn_PyObject_IsInstance)GetProcAddress(hpy_disk, "PyObject_IsInstance");
            p_PyModuleDef_Type = (void *)GetProcAddress(hpy_disk, "PyModuleDef_Type");
            p_PyArg_ParseTuple = (fn_PyArg_ParseTuple)GetProcAddress(hpy_disk, "PyArg_ParseTuple");
            p_PyErr_Format = (fn_PyErr_Format)GetProcAddress(hpy_disk, "PyErr_Format");
            p_PyModule_Create2 = (fn_PyModule_Create2)GetProcAddress(hpy_disk, "PyModule_Create2");
            p_PyModule_FromDefAndSpec = (fn_PyModule_FromDefAndSpec)GetProcAddress(hpy_disk, "PyModule_FromDefAndSpec2");
            p_PyImport_AppendInittab = (fn_PyImport_AppendInittab)GetProcAddress(hpy_disk, "PyImport_AppendInittab");
            p_PyExc_ImportError_addr = (void *)GetProcAddress(hpy_disk, "PyExc_ImportError");
            p_PyExc_ImportError = p_PyExc_ImportError_addr ? *(PyObject **)p_PyExc_ImportError_addr : NULL;
            p_PyModule_New = (fn_PyModule_New)GetProcAddress(hpy_disk, "PyModule_New");
            p_PyCFunction_New = (fn_PyCFunction_New)GetProcAddress(hpy_disk, "PyCFunction_New");
            p_PyImport_AddModule = (fn_PyImport_AddModule)GetProcAddress(hpy_disk, "PyImport_AddModule");
            p_Py_BuildValue = (fn_Py_BuildValue)GetProcAddress(hpy_disk, "Py_BuildValue");
            p_PyObject_SetAttrString = (fn_PyObject_SetAttrString)GetProcAddress(hpy_disk, "PyObject_SetAttrString");
            p_Py_DecRef = (fn_Py_DecRef)GetProcAddress(hpy_disk, "Py_DecRef");
            p_PyCapsule_GetPointer = (fn_PyCapsule_GetPointer)GetProcAddress(hpy_disk, "PyCapsule_GetPointer");
        } else {
            p_Py_InitializeEx = (fn_Py_InitializeEx)mem_get_export(py, "Py_InitializeEx");
            p_Py_IsInitialized = (fn_Py_IsInitialized)mem_get_export(py, "Py_IsInitialized");
            p_Py_FinalizeEx = (fn_Py_FinalizeEx)mem_get_export(py, "Py_FinalizeEx");
            p_PyRun_SimpleString = (fn_PyRun_SimpleString)mem_get_export(py, "PyRun_SimpleString");
            p_PyErr_Print = (fn_PyErr_Print)mem_get_export(py, "PyErr_Print");
            p_PyImport_ImportModule = (fn_PyImport_ImportModule)mem_get_export(py, "PyImport_ImportModule");
            p_PyObject_GetAttrString = (fn_PyObject_GetAttrString)mem_get_export(py, "PyObject_GetAttrString");
            p_PyObject_CallObject = (fn_PyObject_CallObject)mem_get_export(py, "PyObject_CallObject");
            p_PyTuple_New = (fn_PyTuple_New)mem_get_export(py, "PyTuple_New");
            p_PyTuple_SetItem = (fn_PyTuple_SetItem)mem_get_export(py, "PyTuple_SetItem");
            p_PyLong_FromLongLong = (fn_PyLong_FromLongLong)mem_get_export(py, "PyLong_FromLongLong");
            p_PyModule_GetDef = (fn_PyModule_GetDef)mem_get_export(py, "PyModule_GetDef");
            p_PyModule_ExecDef = (fn_PyModule_ExecDef)mem_get_export(py, "PyModule_ExecDef");
            p_PyObject_IsInstance = (fn_PyObject_IsInstance)mem_get_export(py, "PyObject_IsInstance");
            p_PyModuleDef_Type = (void *)mem_get_export(py, "PyModuleDef_Type");
            p_PyArg_ParseTuple = (fn_PyArg_ParseTuple)mem_get_export(py, "PyArg_ParseTuple");
            p_PyErr_Format = (fn_PyErr_Format)mem_get_export(py, "PyErr_Format");
            p_PyModule_Create2 = (fn_PyModule_Create2)mem_get_export(py, "PyModule_Create2");
            p_PyModule_FromDefAndSpec = (fn_PyModule_FromDefAndSpec)mem_get_export(py, "PyModule_FromDefAndSpec2");
            p_PyImport_AppendInittab = (fn_PyImport_AppendInittab)mem_get_export(py, "PyImport_AppendInittab");
            p_PyExc_ImportError_addr = (void *)mem_get_export(py, "PyExc_ImportError");
            p_PyExc_ImportError = p_PyExc_ImportError_addr ? *(PyObject **)p_PyExc_ImportError_addr : NULL;
            p_PyModule_New = (fn_PyModule_New)mem_get_export(py, "PyModule_New");
            p_PyCFunction_New = (fn_PyCFunction_New)mem_get_export(py, "PyCFunction_New");
            p_PyImport_AddModule = (fn_PyImport_AddModule)mem_get_export(py, "PyImport_AddModule");
            p_Py_BuildValue = (fn_Py_BuildValue)mem_get_export(py, "Py_BuildValue");
            p_PyObject_SetAttrString = (fn_PyObject_SetAttrString)mem_get_export(py, "PyObject_SetAttrString");
            p_Py_DecRef = (fn_Py_DecRef)mem_get_export(py, "Py_DecRef");
            p_PyCapsule_GetPointer = (fn_PyCapsule_GetPointer)mem_get_export(py, "PyCapsule_GetPointer");
        }
        cy_log("[stub] api: init=%p isinit=%p finalize=%p run=%p errprint=%p\n",
               (void *)p_Py_InitializeEx, (void *)p_Py_IsInitialized, (void *)p_Py_FinalizeEx,
               (void *)p_PyRun_SimpleString, (void *)p_PyErr_Print);
        /* 探针 ✅：先调 Py_GetVersion ✅（不需初始化 ✅）—— 判断 DLL 本身是否可用 ✅。
         * **A409** ✅：旧实现拿到的是**函数地址**却用 %s 打印 ✅ → 把机器码字节当字符串 ✅
         * 输出乱码（日志实测 "H\x89H..." ✅）—— 从未真正调用 ✅。改为先调用再打印 ✅
         * （Py_GetVersion 文档允许初始化前调用 ✅ 返回静态串 ✅）。 */
    {
        typedef const char *(*fn_Py_GetVersion)(void);
        fn_Py_GetVersion getver = py ? (fn_Py_GetVersion)mem_get_export(py, "Py_GetVersion") : NULL;
        if (getver) {
            const char *ver = getver();
            cy_log("[stub] Py_GetVersion = %s\r\n", ver ? ver : "(null)");
        } else {
            cy_log("[stub] Py_GetVersion not found\r\n");
        }
    }
    /* 归属诊断 ✅：API 指针必须来自**同一个**解释器镜像 ✅（双解释器 = 状态分裂 ✅ 崩溃 ✅） */
        {
            BYTE *ib = py ? mem_image_base(py) : NULL;
            const char *owner = mem_module_by_addr((void *)p_Py_InitializeEx);
            cy_log("[stub] api owner: base=%p init_off=%lld owner=%s\n", (void *)ib,
                   ib ? (long long)((BYTE *)p_Py_InitializeEx - ib) : -1LL, owner ? owner : "(none)");
            if (owner && ib && owner != "python314.dll")
                cy_log("[stub] ⚠ API 指针来自别的模块 ✅（双解释器 ✅）\n");
        }
    }
    if (!p_Py_InitializeEx || !p_PyRun_SimpleString) {
        cy_log("[stub] cannot resolve Python API: %s\n", mem_last_error());
        return 2;
    }

    /* 4) 让解释器把载荷 zip 加进 sys.path ✅（D2 ✅）
     *   内嵌 ✅（onefile ✅）：exe 自身就是 zip ✅ → PYTHONPATH=exe ✅（zipimport 支持带前缀的 zip ✅）；
     *   旁置 ✅（onedir ✅）：PYTHONPATH=<payload.zip 绝对路径> ✅（zipimport 读独立 zip ✅）。 */
    if (g_payload_embedded) {
        SetEnvironmentVariableW(L"PYTHONPATH", self);
    } else {
        SetEnvironmentVariableW(L"PYTHONPATH", g_payload_zip_w);
    }
    /* 载荷路径 ✅：运行期引导模块要按它读 meta.json ✅
     *   内嵌 ✅：exe 自身就是 zip ✅；旁置 ✅：payload.zip ✅。 */
    {
        WCHAR payload_env[MAX_PATH];
        if (g_payload_embedded) {
            wcscpy(payload_env, self);
        } else {
            wcscpy(payload_env, g_payload_zip_w);
        }
        SetEnvironmentVariableW(L"CRATIX_PAYLOAD", payload_env);
    }
    if (!cratix_legacy()) SetEnvironmentVariableW(L"PYTHONUNBUFFERED", L"1"); /* 诊断输出不缓冲 ✅（崩溃时缓冲区会丢 ✅） */
    {
        /* 标准库位置：默认用编译期 PYTHON_HOME，可用环境变量 CRATIX_PYTHONHOME 覆盖 */
        /* PYTHONHOME 默认指向 exe 所在目录：解释器找不到真实安装目录，
         * 标准库全部来自 exe 内的 zip（PYTHONPATH=exe 路径 → zipimport）。 */
        const char *home_env = getenv("CRATIX_PYTHONHOME");
        char narrow[512];
        WCHAR homew[MAX_PATH];
        if (home_env && *home_env) {
            strncpy(narrow, home_env, sizeof(narrow) - 1);
            narrow[sizeof(narrow) - 1] = 0;
        } else {
            strncpy(narrow, g_exe_dir, sizeof(narrow) - 1);
            narrow[sizeof(narrow) - 1] = 0;
        }
        MultiByteToWideChar(CP_UTF8, 0, narrow, -1, homew, MAX_PATH);
        SetEnvironmentVariableW(L"PYTHONHOME", homew);
    }
    {
        WCHAR dirw[MAX_PATH];
        MultiByteToWideChar(CP_UTF8, 0, g_exe_dir, -1, dirw, MAX_PATH);
        SetEnvironmentVariableW(L"CRATIX_EXE_DIR", dirw);
    }

    cy_log("[stub] calling Py_InitializeEx...\r\n");
    __try {
        if (p_Py_InitializeEx(0) != 0 || !p_Py_IsInitialized()) {
        cratix_fail_dialog("Python 解释器初始化失败（Py_InitializeEx）");
        return 2;
    }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        EXCEPTION_POINTERS *ep = GetExceptionInformation();
        char msg[300];
        _snprintf(msg, sizeof(msg) - 1, "Python 解释器初始化崩溃：异常 0x%08lX @ %p",
                  (unsigned long)ep->ExceptionRecord->ExceptionCode, ep->ExceptionRecord->ExceptionAddress);
        msg[sizeof(msg) - 1] = 0;
        cratix_fail_dialog(msg);
        return 3;
    }
    cy_log("[stub] Python initialized\n");
    /* sys.dllhandle ✅：解释器用「自己的 DLL 句柄」定位标准库目录 ✅
     * （A 形态 ✅：解释器在内存里 ✅ → 系统里没有 python314.dll ✅
     *   → GetModuleHandleEx(FROM_ADDRESS) 找不到 ✅ → sys.dllhandle 是无效句柄 ✅
     *   → sysconfig 读它报错 ✅ WinError 126 ✅）。
     * 口径 ✅：指向**本 exe** ✅（冻结形态 ✅ —— 标准库就在 exe 里 ✅）。 */
    if (!cratix_legacy()) {
        char code[512];
        long long h = (long long)(intptr_t)GetModuleHandleW(NULL);
        /* 只在**句柄无效**时才改 ✅ —— B 形态 ✅：sys.dllhandle 本来就是真的 ✅ → 不动 ✅；
         * A 形态 ✅：系统里没有 python314.dll ✅ → 句柄无效 ✅ → 指向本 exe ✅（标准库就在 exe 里 ✅）。 */
        sprintf(code,
                "import sys\n"
                "try:\n"
                "    import _winapi\n"
                "    _winapi.GetModuleFileName(sys.dllhandle)\n"
                "except Exception:\n"
                "    sys.dllhandle = %lld\n",
                h);
        p_PyRun_SimpleString(code);
    }

    /* 安装内存加载桥（动态建模块，避免手写 PyModuleDef 的布局风险 ✅） */
    if (!cratix_loader_install()) {
        cy_log("[stub] 安装 cratix_loader 失败\n");
        return 2;
    }
    cy_log("[stub] cratix_loader 已就绪\n");

    /* 5) 装载 Cython 写的运行时组件（bootstrap）：装模块解析 finder + 兼容层元数据 */
    {
        const zentry_t *be = zipidx_find(&g_zip, "runtime/cratix_bootstrap.pyd");
        unsigned char *bb = NULL;
        size_t bl = 0;
        MemModule *bm = NULL;
        void *sym = NULL;
        PyObject *bmod = NULL, *bootfn = NULL, *bargs = NULL, *bres = NULL;
        typedef PyObject *(*fn_init)(void);
        if (!be) {
            /* 兜底路径 ✅：zip 根的 cratix_bootstrap.pyc（纯 Python 引导模块 ✅ M0/M1）
             * → 直接 import ✅（PYTHONPATH=exe ✅ → zipimport ✅）；
             * M2 起自研 AOT 编译器产出 runtime/cratix_bootstrap.pyd ✅ → 走原生路径 ✅。 */
            const char *fallback_code =
                "import cratix_bootstrap\n"
                "cratix_bootstrap.cratix_boot()\n";
            if (p_PyRun_SimpleString(fallback_code) != 0) {
                cratix_py_fail_dialog("bootstrap 装载失败（cratix_boot 抛异常）");
                return 2;
            }
            cy_log("[stub] bootstrap ok（pyc 兜底路径）\n");
        } else if (!zipidx_read(&g_zip, be, &bb, &bl)) {
            cratix_fail_dialog("载荷里没有 runtime/cratix_bootstrap.pyd——打包不完整");
            return 2;
        } else {
        bm = mem_map_bytes(bb, bl, "cratix_bootstrap.pyd");
        free(bb);
        if (!bm) {
            char msg[400];
            _snprintf(msg, sizeof(msg) - 1, "bootstrap 模块映射失败：%s", mem_last_error() ? mem_last_error() : "?");
            msg[sizeof(msg) - 1] = 0;
            cratix_fail_dialog(msg);
            return 2;
        }
        sym = mem_get_export(bm, "PyInit_cratix_bootstrap");
        if (!sym) {
            cratix_fail_dialog("bootstrap 缺少 PyInit_cratix_bootstrap 导出——打包不完整");
            return 2;
        }
        /* 走 CPython 自己的扩展加载机制（虚拟路径 → 我们的 LoadLibraryExW 重定向 → 内存映射），
         * 这样多阶段初始化（PEP 489）等官方细节全部由 CPython 处理 ✅ */
        {
            char code[1200];
            char vpath[600];
            int i = 0;
            const char *dir = g_exe_dir;
            for (; dir[i] && i < 500; i++) vpath[i] = (dir[i] == '\\') ? '/' : dir[i];
            vpath[i] = 0;
            strcat(vpath, "/runtime/cratix_bootstrap.pyd");
            sprintf(code,
                    "import importlib.util, sys\n"
                    "_p = \"%s\"\n"
                    "_spec = importlib.util.spec_from_file_location('cratix_bootstrap', _p)\n"
                    "_mod = importlib.util.module_from_spec(_spec)\n"
                    "_spec.loader.exec_module(_mod)\n"
                    "sys.modules['cratix_bootstrap'] = _mod\n"
                    "_mod.cratix_boot()\n",
                    vpath);
            if (p_PyRun_SimpleString(code) != 0) {
                cratix_py_fail_dialog("bootstrap 装载失败（cratix_boot 抛异常）");
                return 2;
            }
        }
        cy_log("[stub] bootstrap ok（模块解析 finder 已装载）\n");
        }
    /* 诊断 ✅：CRATIX_CODE=<python 片段> ✅ —— 入口前跑一段脚本 ✅（定位崩溃点 ✅）。 */
    {
        char *code = env_utf8(L"CRATIX_CODE");
        if (code && code[0]) {
            cy_log("[stub] CRATIX_CODE running...\n");
            if (p_PyRun_SimpleString(code) != 0) {
                cy_log("[stub] CRATIX_CODE failed\n");
                p_PyErr_Print();
                free(code);
                return 5;
            }
            cy_log("[stub] CRATIX_CODE done\n");
        }
        free(code);
    }
    }

    /* 6) 入口：载荷里的 entry.txt 写着 "module:attr"（打包器写入，stub 不硬编码业务符号） */
    {
        const zentry_t *ee = zipidx_find(&g_zip, "entry.txt");
        unsigned char *blob = NULL;
        size_t blob_len = 0;
        char spec[512];
        /* 运行时换入口 ✅（验收/调试 ✅）：CRATIX_ENTRY=module:attr 覆盖载荷里的 entry.txt ✅ */
        char *env_entry = env_utf8(L"CRATIX_ENTRY"); /* UTF-8 ✅ —— 模块名也可能非 ASCII ✅ */
        size_t n;
        char *colon;
        const char *mod_name;
        const char *attr;
        PyObject *mod, *fn, *args, *result;
        if (!ee || !zipidx_read(&g_zip, ee, &blob, &blob_len)) {
            cy_log("[stub] 载荷里没有 entry.txt\n");
            return 2;
        }
        n = blob_len < sizeof(spec) - 1 ? blob_len : sizeof(spec) - 1;
        memcpy(spec, blob, n);
        spec[n] = 0;
        free(blob);
        for (size_t i = 0; i < n; i++) {
            if (spec[i] == '\r' || spec[i] == '\n') {
                spec[i] = 0;
                break;
            }
        }
        if (env_entry && env_entry[0]) {
            strncpy(spec, env_entry, sizeof(spec) - 1);
            spec[sizeof(spec) - 1] = 0;
            cy_log("[stub] entry overridden by CRATIX_ENTRY\n");
        }
        free(env_entry);
        colon = strchr(spec, ':');
        if (!colon) {
            cy_log("[stub] entry.txt 格式应为 module:attr\n");
            return 2;
        }
        *colon = 0;
        mod_name = spec;
        attr = colon + 1;
        cy_log("[stub] entry = %s:%s\n", mod_name, attr);
        mod = p_PyImport_ImportModule(mod_name);
        if (!mod) {
            char msg[600];
            _snprintf(msg, sizeof(msg) - 1, "导入入口模块失败：%s", mod_name);
            msg[sizeof(msg) - 1] = 0;
            cratix_py_fail_dialog(msg);
            return 3;
        }
        fn = p_PyObject_GetAttrString(mod, attr);
        if (!fn) {
            char msg[600];
            _snprintf(msg, sizeof(msg) - 1, "入口模块缺少属性 %s（入口配置错误）", attr);
            msg[sizeof(msg) - 1] = 0;
            cratix_py_fail_dialog(msg);
            return 3;
        }
        args = p_PyTuple_New(0);
        result = p_PyObject_CallObject(fn, args);
        if (!result) {
            cratix_py_fail_dialog("入口执行抛异常");
            return 4;
        }
        cy_log("[stub] entry ok\n");
    }

    /* 7) M0 自检（可选）：验证 IAT 重定向确实接管了 CPython 的扩展加载路径 */
    if (getenv("CRATIX_M0")) {
        if (p_PyRun_SimpleString(
                "import importlib.util, os, sys\n"
                "_d = os.environ['CRATIX_EXE_DIR']\n"
                "_spec = importlib.util.spec_from_file_location('m0_ext', os.path.join(_d, 'm0_ext.pyd'))\n"
                "_mod = importlib.util.module_from_spec(_spec)\n"
                "_spec.loader.exec_module(_mod)\n"
                "sys.modules['m0_ext'] = _mod\n"
                "print('m0_ext.add(2, 3) =', _mod.add(2, 3))\n"
                "print('m0_ext.fib(20) =', _mod.fib(20))\n") != 0) {
            cy_log("[stub] m0_ext failed\n");
            p_PyErr_Print();
        }
    }

    if (p_Py_FinalizeEx) p_Py_FinalizeEx();
    cy_log("[stub] done\n");
    return 0;
}
