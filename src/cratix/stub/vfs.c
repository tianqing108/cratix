/* vfs —— 虚拟文件系统（M4 / D1 ✅）实现见 vfs.h 注释 ✅。
 *
 * 三条铁律：
 *   1. 只有「exe 目录前缀 + 能命中归档（文件或目录）」的路径是虚拟的 ✅
 *      其余一律透传真实 API ✅（字体 / %APPDATA% / System32 ✅）；
 *   2. 伪 HANDLE 用 VfsHandle* 指针本体 + 登记表 ✅ —— 收到句柄先查伪表 ✅
 *      不在伪表的一律透传真实 API ✅（真实句柄（字体文件等）不受影响 ✅）；
 *   3. 虚拟文件**只读** ✅（业务写路径在 %APPDATA% ✅ 走透传 ✅）。
 *
 * LoadLibraryExW / LoadLibraryW / GetProcAddress 的重定向在 stub.c 里追加 ✅
 * （它们要用 stub 自己的 cratix_* 实现 ✅ 本文件只管**文件面** ✅）。
 */
#include "vfs.h" /* 先 ✅（_WIN32_WINNT 必须在 windows.h 之前 ✅） */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "zipread.h"

#define VFS_MAGIC 0x56565346u /* 'VVSF' */

/* stub.c 的日志 ✅（调试追踪 ✅） */
extern void cy_log(const char *fmt, ...);

/* 宽字符子串查找 ✅（调试日志用 ✅） */
static const WCHAR *strstr_w(const WCHAR *hay, const char *needle);

typedef struct {
    unsigned int magic;
    int is_dir;
    char rel[300];       /* 归档相对路径（POSIX 分隔符 ✅） */
    char pattern[64];    /* 通配模式（空=精确/全列举 ✅） */
    unsigned char *data; /* 文件：整条内容 ✅（目录枚举：当前子项名 ✅） */
    size_t size;
    size_t pos;
} VfsHandle;

static zipidx_t *g_zip = NULL;
static WCHAR g_exe_dir_w[512] = L"";
static size_t g_exe_dir_len = 0;
static WCHAR g_exe_base_w[128] = L""; /* exe 文件名 ✅（Qt 把 **exe 路径当目录前缀** 用 ✅） */
static size_t g_exe_base_len = 0;

static VfsHandle **g_handles = NULL;
static int g_handle_count = 0;
static int g_handle_cap = 0;

/* **并发保护** ✅：VFS 钩子会被**任意线程**调用（Qt ✅ / Python 文件 IO ✅ / 插件初始化 ✅），
 * 而句柄表与条目缓存都是**无锁全局结构** ✅ → 两个线程同时
 * open/close 时 realloc / 摘除会踩内存 ✅（原实现只用 Interlocked 计数器 ✅）。
 * lookup 用**共享锁** ✅（只读高频 ✅）；增删/缓存用**独占锁** ✅。 */
static SRWLOCK g_handle_lock = SRWLOCK_INIT;
static SRWLOCK g_cache_lock = SRWLOCK_INIT;

static void register_handle(VfsHandle *h) {
    AcquireSRWLockExclusive(&g_handle_lock);
    if (g_handle_count == g_handle_cap) {
        g_handle_cap = g_handle_cap ? g_handle_cap * 2 : 64;
        g_handles = (VfsHandle **)realloc(g_handles, (size_t)g_handle_cap * sizeof(VfsHandle *));
    }
    g_handles[g_handle_count++] = h;
    ReleaseSRWLockExclusive(&g_handle_lock);
}

static VfsHandle *lookup_pseudo(HANDLE h) {
    int i;
    VfsHandle *found = NULL;
    if (!h || h == INVALID_HANDLE_VALUE) return NULL;
    AcquireSRWLockShared(&g_handle_lock);
    for (i = 0; i < g_handle_count; i++) {
        if ((HANDLE)g_handles[i] == h) {
            found = g_handles[i];
            break;
        }
    }
    ReleaseSRWLockShared(&g_handle_lock);
    return found;
}

static void unregister_handle(VfsHandle *h) {
    int i;
    AcquireSRWLockExclusive(&g_handle_lock);
    for (i = 0; i < g_handle_count; i++) {
        if (g_handles[i] == h) {
            g_handles[i] = g_handles[--g_handle_count];
            break;
        }
    }
    ReleaseSRWLockExclusive(&g_handle_lock);
}

int vfs_is_pseudo_handle(HANDLE h) { return lookup_pseudo(h) != NULL; }

/* ---------- 目录索引 ✅ ---------- */
static char **g_dirs = NULL;
#define VFS_MAX_ROOTS 64
static char g_roots[VFS_MAX_ROOTS][64];
static int g_root_count = 0;
static int g_dir_count = 0;
static int g_dir_cap = 0;

static void add_dir(const char *dir) {
    int lo = 0, hi = g_dir_count - 1, mid, pos, i;
    char *copy;
    if (!dir || !*dir) return;
    while (lo <= hi) {
        mid = (lo + hi) / 2;
        if (strcmp(g_dirs[mid], dir) == 0) return;
        if (strcmp(g_dirs[mid], dir) < 0) lo = mid + 1;
        else hi = mid - 1;
    }
    pos = lo;
    if (g_dir_count == g_dir_cap) {
        g_dir_cap = g_dir_cap ? g_dir_cap * 2 : 256;
        g_dirs = (char **)realloc(g_dirs, (size_t)g_dir_cap * sizeof(char *));
    }
    copy = (char *)malloc(strlen(dir) + 1);
    strcpy(copy, dir);
    for (i = g_dir_count; i > pos; i--) g_dirs[i] = g_dirs[i - 1];
    g_dirs[pos] = copy;
    g_dir_count++;
}

static void build_dir_index(void) {
    int i;
    for (i = 0; i < g_zip->count; i++) {
        char name[300], *slash;
        strcpy(name, g_zip->entries[i].name);
        slash = strrchr(name, '/');
        if (!slash) continue;
        *slash = 0;
        add_dir(name);
        {
            char *p = name;
            while ((p = strchr(p, '/')) != NULL) {
                *p = 0;
                add_dir(name);
                *p = '/';
                p++;
            }
        }
    }
}

/* **目录下是否有代码条目** ✅：用于只暴露数据目录 ✅
 * 避免 Python FileFinder 因包目录可见而改走文件系统 ✅。 */
static int dir_has_code(const char *dir) {
    size_t dlen = strlen(dir);
    int i;
    for (i = 0; i < g_zip->count; i++) {
        const char *n = g_zip->entries[i].name;
        const char *tail;
        size_t tl;
        /* **前缀比较也要不区分大小写** ✅：否则 LOGURU 查询 ✅
         * → 字典里小写 loguru 匹配不到 ✅ → dir_has_code=False ✅ → 含代码包根被暴露 ✅。 */
        if (_strnicmp(n, dir, dlen) != 0 || n[dlen] != '/') continue;
        tail = n + dlen + 1;
        tl = strlen(tail);
        if ((tl >= 3 && _stricmp(tail + tl - 3, ".py") == 0) ||
            (tl >= 4 && (_stricmp(tail + tl - 4, ".pyc") == 0 || _stricmp(tail + tl - 4, ".pyd") == 0))) {
            return 1;
        }
    }
    return 0;
}

static void load_roots(void) {
    const zentry_t *e;
    unsigned char *b = NULL;
    size_t n = 0, i;
    g_root_count = 0;
    if (!g_zip) return;
    e = zipidx_find(g_zip, "cratix_roots.txt");
    if (!e || !zipidx_read(g_zip, e, &b, &n) || !b) return;
    i = 0;
    while (i < n && g_root_count < VFS_MAX_ROOTS) {
        size_t start = i, len;
        while (i < n && b[i] != '\n' && b[i] != '\r') i++;
        len = i - start;
        if (len > 0 && len < 64) {
            memcpy(g_roots[g_root_count], b + start, len);
            g_roots[g_root_count][len] = 0;
            g_root_count++;
        }
        while (i < n && (b[i] == '\n' || b[i] == '\r')) i++;
    }
    free(b);
}

static int root_allowed(const char *rel) {
    int i;
    for (i = 0; i < g_root_count; i++) {
        size_t len = strlen(g_roots[i]);
        if (_strnicmp(rel, g_roots[i], len) == 0 && (rel[len] == 0 || rel[len] == '/')) return 1;
    }
    return 0;
}

static int is_dir_indexed(const char *dir) {
    int i;
    if (!dir || !*dir) return 0;
    /* **Windows 路径不区分大小写** ✅：旧实现用 strcmp 二分 ✅
     * → NATIVE / Native 查不到 native ✅（实测虚拟目录大小写失败 ✅）。 */
    for (i = 0; i < g_dir_count; i++) {
        if (_stricmp(g_dirs[i], dir) == 0) return 1;
    }
    return 0;
}

static int entry_in_dir(const char *entry_name, const char *dir) {
    size_t dlen = strlen(dir);
    if (_strnicmp(entry_name, dir, dlen) != 0) return 0;
    if (entry_name[dlen] != '/') return 0;
    return strchr(entry_name + dlen + 1, '/') == NULL;
}

static int subdir_in_dir(const char *entry_name, const char *dir) {
    size_t dlen = strlen(dir);
    const char *rest, *slash;
    /* **子目录前缀也要不区分大小写** ✅：否则 NATIVE 列不出 PySide6 ✅ */
    if (_strnicmp(entry_name, dir, dlen) != 0) return 0;
    if (entry_name[dlen] != '/') return 0;
    rest = entry_name + dlen + 1;
    slash = strchr(rest, '/');
    if (!slash) return 0;
    return strchr(slash + 1, '/') == NULL;
}

static void child_name_of(const char *entry_name, const char *dir, char *out, size_t cap) {
    const char *sub = entry_name + strlen(dir) + 1;
    const char *slash = strchr(sub, '/');
    size_t n;
    if (slash) n = (size_t)(slash - sub); /* 子目录 ✅ */
    else n = strlen(sub);                 /* 文件 ✅ */
    if (n >= cap) n = cap - 1;
    memcpy(out, sub, n);
    out[n] = 0;
}

/* ---------- 路径解析 ✅ ---------- */
void vfs_init(zipidx_t *zip, const WCHAR *exe_dir, const WCHAR *exe_path) {
    size_t i;
    g_zip = zip;
    for (i = 0; exe_dir[i] && i + 1 < 512; i++) {
        WCHAR c = exe_dir[i];
        if (c == L'/') c = L'\\';
        if (c == L'\\' && i > 0 && g_exe_dir_w[i - 1] == L'\\') continue;
        g_exe_dir_w[g_exe_dir_len++] = c;
    }
    while (g_exe_dir_len > 0 && (g_exe_dir_w[g_exe_dir_len - 1] == L'\\' || g_exe_dir_w[g_exe_dir_len - 1] == L'/'))
        g_exe_dir_len--;
    g_exe_dir_w[g_exe_dir_len] = 0;
    if (exe_path) {
        const WCHAR *p, *slash = NULL;
        for (p = exe_path; *p; p++)
            if (*p == L'\\' || *p == L'/') slash = p;
        if (slash) {
            for (p = slash + 1, i = 0; *p && i + 1 < 128; p++, i++) g_exe_base_w[i] = *p;
            g_exe_base_w[i] = 0;
            g_exe_base_len = i;
        }
    }
    build_dir_index();
    load_roots();
}

/* 归档路径别名 ✅：**只服务打包专属前缀** ✅（PySide6 / shiboken6 / native / extstd / ext3rd）。
 * **stdlib 条目不虚拟** ✅ —— 实测 ✅：exe 目录前缀 + 顶层 stdlib 条目一旦可解析 ✅
 * → import 机制把 zipimporter 换成 FileFinder ✅ → 列目录/开文件走岔 ✅ →
 *   `from . import _compiler` 假性循环导入 ✅（json→re 全断 ✅）。
 * stdlib 全部由 zipimport 从 exe 尾部 zip 直读 ✅（VFS 不掺和 ✅）。 */
static int vfs_rel_allowed(const char *rel) {
    /* **前缀必须带分隔符** ✅：旧口径对 PySide6/shiboken6 只做前缀比较 ✅
     * → 真实同胞目录 PySide6evil/... / shiboken6x/... 也会被当成虚拟路径 ✅。 */
    return _stricmp(rel, "native") == 0 || _strnicmp(rel, "native/", 7) == 0 ||
           _stricmp(rel, "extstd") == 0 || _strnicmp(rel, "extstd/", 7) == 0 ||
           _stricmp(rel, "ext3rd") == 0 || _strnicmp(rel, "ext3rd/", 7) == 0 ||
           _stricmp(rel, "PySide6") == 0 || _strnicmp(rel, "PySide6/", 8) == 0 ||
           _stricmp(rel, "shiboken6") == 0 || _strnicmp(rel, "shiboken6/", 10) == 0;
}

static int resolve_alias(const char *rel, char *out, size_t cap) {
    int is_pyside = _strnicmp(rel, "PySide6/", 8) == 0 || _stricmp(rel, "PySide6") == 0;
    int is_shiboken = _strnicmp(rel, "shiboken6/", 10) == 0 || _stricmp(rel, "shiboken6") == 0;
    /* 1) 原样 ✅（打包专属前缀 ✅） */
    if (vfs_rel_allowed(rel)) {
        strcpy(out, rel);
        if (zipidx_find(g_zip, out)) return 1;
        if (is_dir_indexed(out)) return 2;
    }
    /* **只暴露数据目录** ✅：包根目录仍不可见 ✅（保护 FileFinder ✅）。
     * 包内的纯数据子目录（无 .py/.pyc/.pyd ✅）可以 isdir/listdir ✅。 */
    if (is_dir_indexed(rel) && (!dir_has_code(rel) || root_allowed(rel))) {
        strcpy(out, rel);
        return 2;
    }
    /* 2) 包专属别名 ✅ */
    if (is_pyside) {
        if (7 + strlen(rel) + 1 <= cap) {
            strcpy(out, "native/");
            strcat(out, rel);
            if (zipidx_find(g_zip, out)) return 1;
            if (is_dir_indexed(out)) return 2;
        }
    }
    if (is_shiboken) {
        const char *rest = strchr(rel, '/');
        static const char *const shib_dirs[] = {"ext3rd/shiboken6/", "native/PySide6/", NULL};
        int di;
        for (di = 0; shib_dirs[di]; di++) {
            size_t dlen = strlen(shib_dirs[di]);
            const char *tail = rest ? rest + 1 : rel; /* "shiboken6/x" → "x" ✅ */
            if (dlen + strlen(tail) + 1 > cap) continue;
            strcpy(out, shib_dirs[di]);
            strcat(out, tail);
            if (zipidx_find(g_zip, out)) return 1;
            if (is_dir_indexed(out)) return 2;
        }
    }
    /* **包数据** ✅：非代码条目（.json / .pxi / .h / .xml / .txt ✅）允许按原路径读 ✅
     * —— 旧策略只允许 PySide6/shiboken6/native/extstd/ext3rd ✅ → lxml/pypinyin 的包数据读不到 ✅
     * （实测：open(<exe>\lxml\etree.pyx) FileNotFoundError ✅）。
     * **.py / .pyc / .pyd 仍不走 VFS** ✅ —— 避免 import 机制把 zipimporter 换成 FileFinder ✅。 */
    /* **native 包根别名**：根可见后，Python 按 <exe>/<pkg>/<x>.pyd 找扩展；
     * 实际条目在 ext3rd/<pkg>/<x>.pyd → 这里返回别名路径让 open/stat 命中。 */
    if (root_allowed(rel)) {
        char alias[400];
        if (strlen(rel) + 9 < sizeof(alias)) {
            strcpy(alias, "ext3rd/");
            strcat(alias, rel);
            if (zipidx_find(g_zip, alias)) {
                strcpy(out, alias);
                return 1;
            }
            if (is_dir_indexed(alias)) {
                strcpy(out, alias);
                return 2;
            }
        }
    }
    if (zipidx_find(g_zip, rel) && rel[0]) {
        size_t rl = strlen(rel);
        int is_code = (rl >= 4 && (_stricmp(rel + rl - 4, ".pyc") == 0 || _stricmp(rel + rl - 4, ".pyd") == 0)) ||
                      (rl >= 3 && _stricmp(rel + rl - 3, ".py") == 0);
        if (!is_code || root_allowed(rel)) {
            strncpy(out, rel, cap - 1);
            out[cap - 1] = 0;
            return 1;
        }
    }
    return 0;
}

/* WIDE → 归档相对路径 ✅；返回 1=虚拟文件 2=虚拟目录 0=非虚拟 ✅
 * rel 输出**规范**归档路径 ✅（含别名前缀 ✅ —— 枚举用 ✅）。 */
static int resolve_path(const WCHAR *path, char *rel, size_t cap) {
    WCHAR norm[600];
    size_t i, n = 0, skip;
    char raw[300];
    int kind;
    int had_trail = 0;
    if (!path || !*path) return 0;
    if (wcsncmp(path, L"\\\\?\\UNC\\", 8) == 0) path += 6;
    else if (wcsncmp(path, L"\\\\?\\", 4) == 0) path += 4;
    if (wcsncmp(path, L"\\??\\", 4) == 0) path += 4; /* NT 命名空间前缀 ✅ */
    for (i = 0; path[i] && n + 1 < 600; i++) {
        WCHAR c = path[i];
        if (c == L'/') c = L'\\';
        if (c == L'\\' && n > 0 && norm[n - 1] == L'\\') continue;
        norm[n++] = c;
    }
    if (path[i]) return 0; /* **超长路径不截断** ✅：截断可能命中另一个条目 ✅ */
    norm[n] = 0;
    if (n <= g_exe_dir_len) return 0;
    if (_wcsnicmp(norm, g_exe_dir_w, g_exe_dir_len) != 0) return 0;
    /* **前缀后必须是分隔符** ✅：否则「<exe目录>native\x」会被当成「native/x」✅
     * （exe 目录 C:\app ✅、真实路径 C:\appnative\native\x ✅ → 被虚拟化到载荷条目 ✅）。
     * 根目录形态 g_exe_dir_w 是 "C:" ✅ → norm[len] 就是分隔符 ✅ 不受影响 ✅。 */
    if (norm[g_exe_dir_len] != L'\\' && norm[g_exe_dir_len] != L'/') return 0;
    skip = g_exe_dir_len;
    while (skip < n && (norm[skip] == L'\\' || norm[skip] == L'/')) skip++;
    if (skip >= n) return 0;
    /* exe 文件名前缀 ✅：Qt 的插件路径把 **exe 路径当目录** 用 ✅
     * （PluginsPath = <exe>\PySide6\plugins ✅）→ 剥掉 ✅。 */
    if (g_exe_base_len && n - skip > g_exe_base_len &&
        _wcsnicmp(norm + skip, g_exe_base_w, g_exe_base_len) == 0 &&
        (norm[skip + g_exe_base_len] == L'\\' || norm[skip + g_exe_base_len] == L'/')) {
        skip += g_exe_base_len;
        while (skip < n && (norm[skip] == L'\\' || norm[skip] == L'/')) skip++;
        if (skip >= n) return 0;
    }
    if (cap < 8) return 0;
    /* **逐位追加并显式维护终止符** ✅ —— 之前用 `raw[strlen(raw)] = c` ✅
     * 中途没有终止符 ✅ → 下一次 strlen 读到**未初始化栈内存** ✅ →
     * 字符写飞 ✅ → 路径坏 ✅ → **间歇性** resolve 失败 ✅（栈内容恰好干净时才对 ✅）✅ */
    {
        size_t rl = 0;
        for (i = skip; norm[i] && rl + 2 < sizeof(raw); i++) {
            WCHAR c = norm[i];
            if (c > 0x7F) return 0; /* 归档条目名是 ASCII ✅ 高位字符 → 非虚拟 ✅ */
            raw[rl++] = (char)(c == L'\\' ? '/' : c);
        }
        if (norm[i]) return 0; /* **超过 raw 容量不截断** ✅ */
        raw[rl] = 0;
    }
    /* **归一化 "." / ".." 段** ✅：Windows 对目录容忍 ✅；
     * 旧行为直接拿原样 rel → native/./ 与 native/../entry.txt 假 FileNotFound ✅。
     * ".." 只允许在虚拟根内回退 ✅：回退超出根就 return 0 ✅
     * （交给真实 API 处理原路径 ✅ 不在 VFS 里逃出 exe 目录 ✅）。 */
    {
        char normalized[300];
        size_t n2 = 0;
        char *p = raw;
        size_t raw_len0 = strlen(raw);
        int raw_trail = (raw_len0 > 0 && raw[raw_len0 - 1] == '/');
        while (*p) {
            char *seg;
            size_t len;
            while (*p == '/') p++;
            if (!*p) break;
            seg = p;
            while (*p && *p != '/') p++;
            len = (size_t)(p - seg);
            if (len == 1 && seg[0] == '.') {
                /* **末尾 "." 意味着目录** ✅：entry.txt/. 应失败 ✅
                 * （Windows 不允许把文件当目录访问 ✅）。 */
                char *q = p;
                while (*q == '/') q++;
                if (!*q) had_trail = 1;
                continue;
            }
            if (len == 2 && seg[0] == '.' && seg[1] == '.') {
                size_t k = n2;
                if (k == 0) return 0;
                while (k > 0 && normalized[k - 1] != '/') k--;
                n2 = (k > 0) ? k - 1 : 0;
                continue;
            }
            if (n2 + len + 2 >= sizeof(normalized)) return 0;
            if (n2) normalized[n2++] = '/';
            memcpy(normalized + n2, seg, len);
            n2 += len;
        }
        normalized[n2] = 0;
        memcpy(raw, normalized, n2 + 1);
        had_trail = had_trail || raw_trail;
    }
    /* **尾随分隔符** ✅：Windows 对目录容忍尾随 \ / ✅（Qt 插件路径常带 \ ✅）。
     * 旧行为："native/" 去查条目/目录索引 → 都不匹配 ✅ → 假 FileNotFound ✅。
     * 文件 + 尾随分隔符（entry.txt/ ✅）仍应失败 ✅ → 下面 kind==1 时返回 0 ✅。 */
    {
        size_t rl = strlen(raw);
        size_t orig_len = rl;
        while (rl > 0 && raw[rl - 1] == '/') raw[--rl] = 0;
        if (!raw[0]) return 0;
        had_trail = had_trail || (rl != orig_len);
    }
    kind = resolve_alias(raw, rel, cap);
    if (had_trail && kind == 1) return 0;
    if (getenv("CRATIX_DEBUG")) {
        char u8[300];
        WideCharToMultiByte(CP_UTF8, 0, path, -1, u8, sizeof(u8), NULL, NULL);
        cy_log("[vfs] resolve(%s) raw=%s kind=%d rel=%s\n", u8, raw, kind, kind ? rel : "-");
    }
    return kind;
}

/* 伪句柄统计 ✅（M6c ✅）：定位「谁在反复打开虚拟文件」/泄漏 ✅ */
static volatile long g_open_count = 0;           /* 累计打开次数 ✅ */
static volatile long g_open_live = 0;            /* 当前未关闭句柄数 ✅ */
static volatile long long g_open_bytes_live = 0; /* 当前未关闭句柄持有的解压字节 ✅ */

/* **条目解压缓存** ✅（M6c ✅）：同一条目多次 open 共享同一份解压字节 ✅
 * —— CloseHandle 不再 free 数据 ✅ —— 之前每次 open 解压一份、close 释放 ✅
 * 但 CreateFileMapping/MapViewOfFile 的**伪视图**与文件句柄共享同一块 ✅
 * → Qt 在视图还活着时先关文件 ✅ → use-after-free ✅ → 堆坏 ✅ → ntdll AV ✅（实测 ✅）。
 * 缓存按条目指针（g_zip->entries 稳定 ✅）去重 ✅ 总量封顶 ✅。 */
#define ENTRY_CACHE_MAX 1024
#define ENTRY_CACHE_BYTES_MAX (256u * 1024 * 1024)
typedef struct {
    const zentry_t *e;
    unsigned char *data;
    size_t size;
} EntryCache;
static EntryCache g_entry_cache[ENTRY_CACHE_MAX];
static int g_entry_cache_n;
static long long g_entry_cache_bytes = 0;

static unsigned char *entry_data(const zentry_t *e, size_t *out_size) {
    int i;
    unsigned char *result = NULL;
    AcquireSRWLockExclusive(&g_cache_lock);
    if (e) {
        for (i = 0; i < g_entry_cache_n; i++) {
            if (g_entry_cache[i].e == e) {
                *out_size = g_entry_cache[i].size;
                result = g_entry_cache[i].data;
                ReleaseSRWLockExclusive(&g_cache_lock);
                return result;
            }
        }
    }
    {
        unsigned char *bytes = NULL;
        size_t len = 0;
        if (zipidx_read(g_zip, e, &bytes, &len)) {
            if (e && g_entry_cache_n < ENTRY_CACHE_MAX &&
                g_entry_cache_bytes + (long long)len <= ENTRY_CACHE_BYTES_MAX) {
                g_entry_cache[g_entry_cache_n].e = e;
                g_entry_cache[g_entry_cache_n].data = bytes;
                g_entry_cache[g_entry_cache_n].size = len;
                g_entry_cache_n++;
                g_entry_cache_bytes += (long long)len;
            }
            *out_size = len;
            result = bytes;
        }
    }
    ReleaseSRWLockExclusive(&g_cache_lock);
    return result;
}

static VfsHandle *open_entry(const zentry_t *e, const char *rel, int is_dir) {
    VfsHandle *h = (VfsHandle *)calloc(1, sizeof(VfsHandle));
    if (!h) return NULL;
    h->magic = VFS_MAGIC;
    h->is_dir = is_dir;
    strcpy(h->rel, rel);
    if (e) {
        h->data = entry_data(e, &h->size);
        if (!h->data) {
            free(h);
            return NULL;
        }
    }
    register_handle(h);
    InterlockedIncrement(&g_open_count);
    InterlockedIncrement(&g_open_live);
    InterlockedAdd64(&g_open_bytes_live, (long long)h->size);
    /* 每 100 次打开打一条 ✅（诊断泄漏 ✅ 只在 CRATIX_DEBUG 下 ✅） */
    if (getenv("CRATIX_DEBUG") && (g_open_count % 100) == 0)
        cy_log("[vfs] opens=%ld live=%ld bytes_live=%lld MB=%lld\n", g_open_count, g_open_live,
               g_open_bytes_live, g_open_bytes_live / (1024 * 1024));
    return h;
}

/* ---------- 真实 API 透传 ✅ ---------- */
typedef HANDLE(WINAPI *fn_CreateFileW)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef BOOL(WINAPI *fn_ReadFile)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
typedef BOOL(WINAPI *fn_WriteFile)(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED);
typedef DWORD(WINAPI *fn_SetFilePointer)(HANDLE, LONG, PLONG, DWORD);
typedef BOOL(WINAPI *fn_SetFilePointerEx)(HANDLE, LARGE_INTEGER, PLARGE_INTEGER, DWORD);
typedef DWORD(WINAPI *fn_GetFileAttributesW)(LPCWSTR);
typedef BOOL(WINAPI *fn_GetFileAttributesExW)(LPCWSTR, GET_FILEEX_INFO_LEVELS, LPVOID);
typedef BOOL(WINAPI *fn_GetFileInformationByHandle)(HANDLE, LPBY_HANDLE_FILE_INFORMATION);
typedef BOOL(WINAPI *fn_GetFileInformationByHandleEx)(HANDLE, FILE_INFO_BY_HANDLE_CLASS, LPVOID, DWORD);
typedef HANDLE(WINAPI *fn_FindFirstFileExW)(LPCWSTR, FINDEX_INFO_LEVELS, LPVOID, FINDEX_SEARCH_OPS, LPVOID, DWORD);
typedef BOOL(WINAPI *fn_FindNextFileW)(HANDLE, LPVOID);
typedef BOOL(WINAPI *fn_FindClose)(HANDLE);
typedef HANDLE(WINAPI *fn_CreateFileMappingW)(HANDLE, LPSECURITY_ATTRIBUTES, DWORD, DWORD, DWORD, LPCWSTR);
typedef LPVOID(WINAPI *fn_MapViewOfFile)(HANDLE, DWORD, DWORD, DWORD, SIZE_T);
typedef BOOL(WINAPI *fn_UnmapViewOfFile)(LPCVOID);
typedef BOOL(WINAPI *fn_DeleteFileW)(LPCWSTR);
typedef BOOL(WINAPI *fn_MoveFileW)(LPCWSTR, LPCWSTR);
typedef BOOL(WINAPI *fn_MoveFileExW)(LPCWSTR, LPCWSTR, DWORD);
typedef BOOL(WINAPI *fn_CopyFileW)(LPCWSTR, LPCWSTR, BOOL);
typedef BOOL(WINAPI *fn_CreateDirectoryW)(LPCWSTR, LPSECURITY_ATTRIBUTES);
typedef BOOL(WINAPI *fn_RemoveDirectoryW)(LPCWSTR);
typedef BOOL(WINAPI *fn_SetEndOfFile)(HANDLE);
typedef BOOL(WINAPI *fn_FlushFileBuffers)(HANDLE);
typedef BOOL(WINAPI *fn_SetFileTime)(HANDLE, const FILETIME *, const FILETIME *, const FILETIME *);
typedef BOOL(WINAPI *fn_SetFileInformationByHandle)(HANDLE, FILE_INFO_BY_HANDLE_CLASS, LPVOID, DWORD);
typedef DWORD(WINAPI *fn_GetFileType)(HANDLE);
typedef BOOL(WINAPI *fn_GetFileSizeEx)(HANDLE, PLARGE_INTEGER);
typedef DWORD(WINAPI *fn_GetFileSize)(HANDLE, LPDWORD);
typedef BOOL(WINAPI *fn_CloseHandle)(HANDLE);
typedef BOOL(WINAPI *fn_FreeLibrary)(HMODULE);
typedef LONG(WINAPI *fn_NtCreateFile)(PHANDLE, ACCESS_MASK, PVOID, PVOID, PLARGE_INTEGER, ULONG, ULONG, ULONG, ULONG, PVOID, ULONG);
typedef LONG(WINAPI *fn_NtQueryVolumeInformationFile)(HANDLE, PVOID, PVOID, ULONG, ULONG);

static fn_CreateFileW real_CreateFileW = NULL;
static fn_ReadFile real_ReadFile = NULL;
static fn_WriteFile real_WriteFile = NULL;
static fn_SetFilePointer real_SetFilePointer = NULL;
static fn_SetFilePointerEx real_SetFilePointerEx = NULL;
static fn_GetFileAttributesW real_GetFileAttributesW = NULL;
static fn_GetFileAttributesExW real_GetFileAttributesExW = NULL;
static fn_GetFileInformationByHandle real_GetFileInformationByHandle = NULL;
static fn_GetFileInformationByHandleEx real_GetFileInformationByHandleEx = NULL;
static fn_FindFirstFileExW real_FindFirstFileExW = NULL;
static fn_FindNextFileW real_FindNextFileW = NULL;
static fn_FindClose real_FindClose = NULL;
static fn_CreateFileMappingW real_CreateFileMappingW = NULL;
static fn_MapViewOfFile real_MapViewOfFile = NULL;
static fn_UnmapViewOfFile real_UnmapViewOfFile = NULL;
static fn_DeleteFileW real_DeleteFileW = NULL;
static fn_MoveFileW real_MoveFileW = NULL;
static fn_MoveFileExW real_MoveFileExW = NULL;
static fn_CopyFileW real_CopyFileW = NULL;
static fn_CreateDirectoryW real_CreateDirectoryW = NULL;
static fn_RemoveDirectoryW real_RemoveDirectoryW = NULL;
static fn_SetEndOfFile real_SetEndOfFile = NULL;
static fn_FlushFileBuffers real_FlushFileBuffers = NULL;
static fn_SetFileTime real_SetFileTime = NULL;
static fn_SetFileInformationByHandle real_SetFileInformationByHandle = NULL;
static fn_GetFileType real_GetFileType = NULL;
static fn_GetFileSizeEx real_GetFileSizeEx = NULL;
static fn_GetFileSize real_GetFileSize = NULL;
static fn_CloseHandle real_CloseHandle = NULL;
static fn_FreeLibrary real_FreeLibrary = NULL;
static fn_NtCreateFile real_NtCreateFile = NULL;
static fn_NtQueryVolumeInformationFile real_NtQueryVolumeInformationFile = NULL;

static void ensure_real_apis(void) {
    HMODULE k32, ntdll;
    static int done = 0;
    if (done) return;
    done = 1;
    k32 = GetModuleHandleW(L"kernel32.dll");
    real_CreateFileW = (fn_CreateFileW)GetProcAddress(k32, "CreateFileW");
    real_ReadFile = (fn_ReadFile)GetProcAddress(k32, "ReadFile");
    real_WriteFile = (fn_WriteFile)GetProcAddress(k32, "WriteFile");
    real_SetFilePointer = (fn_SetFilePointer)GetProcAddress(k32, "SetFilePointer");
    real_SetFilePointerEx = (fn_SetFilePointerEx)GetProcAddress(k32, "SetFilePointerEx");
    real_GetFileAttributesW = (fn_GetFileAttributesW)GetProcAddress(k32, "GetFileAttributesW");
    real_GetFileAttributesExW = (fn_GetFileAttributesExW)GetProcAddress(k32, "GetFileAttributesExW");
    real_GetFileInformationByHandle = (fn_GetFileInformationByHandle)GetProcAddress(k32, "GetFileInformationByHandle");
    real_GetFileInformationByHandleEx = (fn_GetFileInformationByHandleEx)GetProcAddress(k32, "GetFileInformationByHandleEx");
    real_FindFirstFileExW = (fn_FindFirstFileExW)GetProcAddress(k32, "FindFirstFileExW");
    real_FindNextFileW = (fn_FindNextFileW)GetProcAddress(k32, "FindNextFileW");
    real_FindClose = (fn_FindClose)GetProcAddress(k32, "FindClose");
    real_CreateFileMappingW = (fn_CreateFileMappingW)GetProcAddress(k32, "CreateFileMappingW");
    real_MapViewOfFile = (fn_MapViewOfFile)GetProcAddress(k32, "MapViewOfFile");
    real_UnmapViewOfFile = (fn_UnmapViewOfFile)GetProcAddress(k32, "UnmapViewOfFile");
    real_DeleteFileW = (fn_DeleteFileW)GetProcAddress(k32, "DeleteFileW");
    real_MoveFileW = (fn_MoveFileW)GetProcAddress(k32, "MoveFileW");
    real_MoveFileExW = (fn_MoveFileExW)GetProcAddress(k32, "MoveFileExW");
    real_CopyFileW = (fn_CopyFileW)GetProcAddress(k32, "CopyFileW");
    real_CreateDirectoryW = (fn_CreateDirectoryW)GetProcAddress(k32, "CreateDirectoryW");
    real_RemoveDirectoryW = (fn_RemoveDirectoryW)GetProcAddress(k32, "RemoveDirectoryW");
    real_SetEndOfFile = (fn_SetEndOfFile)GetProcAddress(k32, "SetEndOfFile");
    real_FlushFileBuffers = (fn_FlushFileBuffers)GetProcAddress(k32, "FlushFileBuffers");
    real_SetFileTime = (fn_SetFileTime)GetProcAddress(k32, "SetFileTime");
    real_SetFileInformationByHandle = (fn_SetFileInformationByHandle)GetProcAddress(k32, "SetFileInformationByHandle");
    real_GetFileType = (fn_GetFileType)GetProcAddress(k32, "GetFileType");
    real_GetFileSizeEx = (fn_GetFileSizeEx)GetProcAddress(k32, "GetFileSizeEx");
    real_GetFileSize = (fn_GetFileSize)GetProcAddress(k32, "GetFileSize");
    real_CloseHandle = (fn_CloseHandle)GetProcAddress(k32, "CloseHandle");
    real_FreeLibrary = (fn_FreeLibrary)GetProcAddress(k32, "FreeLibrary");
    ntdll = GetModuleHandleW(L"ntdll.dll");
    real_NtCreateFile = (fn_NtCreateFile)GetProcAddress(ntdll, "NtCreateFile");
    real_NtQueryVolumeInformationFile = (fn_NtQueryVolumeInformationFile)GetProcAddress(ntdll, "NtQueryVolumeInformationFile");
}

static void fill_times(FILETIME *ft) {
    ULARGE_INTEGER u;
    u.QuadPart = 132500000000000000ULL;
    ft->dwLowDateTime = u.LowPart;
    ft->dwHighDateTime = u.HighPart;
}

static void fill_find_data(WIN32_FIND_DATAW *fd, const char *child, int is_dir, unsigned long fsize) {
    WCHAR wname[300];
    size_t k = 0;
    memset(fd, 0, sizeof(*fd));
    for (; child[k] && k + 1 < 300; k++) {
        WCHAR c = (WCHAR)(unsigned char)child[k];
        if (c == L'/') c = L'\\';
        wname[k] = c;
    }
    wname[k] = 0;
    lstrcpyW(fd->cFileName, wname);
    fd->dwFileAttributes = is_dir ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
    fill_times(&fd->ftCreationTime);
    fd->ftLastAccessTime = fd->ftCreationTime;
    fd->ftLastWriteTime = fd->ftCreationTime;
    fd->nFileSizeHigh = (DWORD)(fsize >> 32);
    fd->nFileSizeLow = (DWORD)(fsize & 0xFFFFFFFFu);
}

/* FindFirstFileW ✅：os.listdir 走它 ✅（不是 ExW ✅ —— 实测 CPython 3.14 的
 * _listdir_windows_no_opendir 用 FindFirstFileW ✅）→ 等价转发 ExW ✅。 */
static HANDLE WINAPI vfs_FindFirstFileExW(LPCWSTR pattern, FINDEX_INFO_LEVELS level, LPVOID data,
                                          FINDEX_SEARCH_OPS ops, LPVOID filter, DWORD flags);
static HANDLE WINAPI vfs_FindFirstFileW(LPCWSTR pattern, WIN32_FIND_DATAW *data) {
    return vfs_FindFirstFileExW(pattern, FindExInfoStandard, (LPVOID)data, FindExSearchNameMatch, NULL, 0);
}

/* CloseHandle ✅：伪句柄 → 摘登记 ✅ **数据不释放** ✅（条目缓存共享 ✅
 * —— 伪视图可能还活着 ✅ free 会 use-after-free ✅ 实测 ✅；
 * 缓存总量封顶 256MB ✅ 泄漏可控 ✅）。 */
HANDLE vfs_dup_handle(HANDLE h) {
    VfsHandle *vh = lookup_pseudo(h);
    VfsHandle *copy;
    if (!vh || vh->is_dir) return NULL;
    copy = (VfsHandle *)malloc(sizeof(*copy));
    if (!copy) return NULL;
    memcpy(copy, vh, sizeof(*copy));
    register_handle(copy);
    InterlockedIncrement(&g_open_live);
    return (HANDLE)copy;
}

static BOOL WINAPI vfs_CloseHandle(HANDLE h) {
    VfsHandle *vh = lookup_pseudo(h);
    if (!vh) return real_CloseHandle(h);
    unregister_handle(vh);
    free(vh);
    InterlockedDecrement(&g_open_live);
    return TRUE;
}

/* ---------- KERNEL32 重定向实现 ✅ ---------- */

static HANDLE WINAPI vfs_CreateFileW(LPCWSTR name, DWORD access, DWORD share,
                                     LPSECURITY_ATTRIBUTES sa, DWORD disposition,
                                     DWORD flags, HANDLE tmpl) {
    char rel[300];
    int kind;
    VfsHandle *h;
    ensure_real_apis();
    if (getenv("CRATIX_DEBUG") && name) {
        char u8[300];
        WideCharToMultiByte(CP_UTF8, 0, name, -1, u8, sizeof(u8), NULL, NULL);
        cy_log("[vfs] CreateFileW(%s) flags=0x%x\n", u8, flags);
    }
    if (!name) return real_CreateFileW(name, access, share, sa, disposition, flags, tmpl);
    kind = resolve_path(name, rel, sizeof(rel));
    if (!kind) return real_CreateFileW(name, access, share, sa, disposition, flags, tmpl);
    if (access & (GENERIC_WRITE | FILE_APPEND_DATA | FILE_WRITE_DATA | DELETE)) {
        SetLastError(ERROR_ACCESS_DENIED);
        return INVALID_HANDLE_VALUE;
    }
    h = open_entry(kind == 1 ? zipidx_find(g_zip, rel) : NULL, rel, kind == 2);
    if (!h) {
        SetLastError(ERROR_ACCESS_DENIED);
        return INVALID_HANDLE_VALUE;
    }
    return (HANDLE)h;
}

static BOOL WINAPI vfs_ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD got, LPOVERLAPPED ov) {
    VfsHandle *vh = lookup_pseudo(h);
    size_t avail;
    if (!vh) return real_ReadFile(h, buf, n, got, ov);
    if (vh->is_dir || ov) {
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    avail = vh->size > vh->pos ? vh->size - vh->pos : 0;
    if (n > avail) n = (DWORD)avail;
    memcpy(buf, vh->data + vh->pos, n);
    vh->pos += n;
    if (got) *got = n;
    return TRUE;
}

static BOOL WINAPI vfs_WriteFile(HANDLE h, LPCVOID buf, DWORD n, LPDWORD got, LPOVERLAPPED ov) {
    if (!lookup_pseudo(h)) return real_WriteFile(h, buf, n, got, ov);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static DWORD WINAPI vfs_SetFilePointer(HANDLE h, LONG dist, PLONG high, DWORD method) {
    VfsHandle *vh = lookup_pseudo(h);
    LARGE_INTEGER li, cur;
    if (!vh) return real_SetFilePointer(h, dist, high, method);
    if (vh->is_dir) {
        SetLastError(ERROR_ACCESS_DENIED);
        return INVALID_SET_FILE_POINTER;
    }
    li.QuadPart = dist;
    if (high) li.QuadPart |= ((long long)*high) << 32;
    cur.QuadPart = (long long)vh->pos;
    if (method == FILE_BEGIN) cur = li;
    else if (method == FILE_CURRENT) cur.QuadPart += li.QuadPart;
    else cur.QuadPart = (long long)vh->size + li.QuadPart;
    if (cur.QuadPart < 0) {
        SetLastError(ERROR_NEGATIVE_SEEK);
        return INVALID_SET_FILE_POINTER;
    }
    vh->pos = (size_t)cur.QuadPart;
    if (high) *high = (LONG)(cur.QuadPart >> 32);
    return (DWORD)(cur.QuadPart & 0xFFFFFFFFu);
}

static BOOL WINAPI vfs_SetFilePointerEx(HANDLE h, LARGE_INTEGER dist, PLARGE_INTEGER newpos, DWORD method) {
    VfsHandle *vh = lookup_pseudo(h);
    LARGE_INTEGER cur;
    if (!vh) return real_SetFilePointerEx(h, dist, newpos, method);
    if (vh->is_dir) {
        SetLastError(ERROR_ACCESS_DENIED);
        return FALSE;
    }
    cur.QuadPart = (long long)vh->pos;
    if (method == FILE_BEGIN) cur = dist;
    else if (method == FILE_CURRENT) cur.QuadPart += dist.QuadPart;
    else cur.QuadPart = (long long)vh->size + dist.QuadPart;
    if (cur.QuadPart < 0) {
        SetLastError(ERROR_NEGATIVE_SEEK);
        return FALSE;
    }
    vh->pos = (size_t)cur.QuadPart;
    if (newpos) newpos->QuadPart = cur.QuadPart;
    return TRUE;
}

static DWORD WINAPI vfs_GetFileAttributesW(LPCWSTR name) {
    char rel[300];
    int kind;
    ensure_real_apis();
    if (!name) return real_GetFileAttributesW(name);
    kind = resolve_path(name, rel, sizeof(rel));
    if (getenv("CRATIX_DEBUG") && name) {
        char u8[300];
        WideCharToMultiByte(CP_UTF8, 0, name, -1, u8, sizeof(u8), NULL, NULL);
        cy_log("[vfs] GetFileAttributesW(%s) -> kind=%d rel=%s\n", u8, kind, kind ? rel : "-");
    }
    if (!kind) return real_GetFileAttributesW(name);
    return kind == 2 ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
}

/* 宽字符子串查找 ✅（调试日志用 ✅ 大小写敏感 ✅） */
static const WCHAR *strstr_w(const WCHAR *hay, const char *needle) {
    size_t nlen = strlen(needle);
    for (; *hay; hay++) {
        size_t i;
        for (i = 0; i < nlen; i++) {
            WCHAR c = (WCHAR)(unsigned char)needle[i];
            if (hay[i] != c) break;
        }
        if (i == nlen) return hay;
    }
    return NULL;
}

static BOOL WINAPI vfs_GetFileAttributesExW(LPCWSTR name, GET_FILEEX_INFO_LEVELS level, LPVOID buf) {
    char rel[300];
    int kind;
    const zentry_t *e;
    ensure_real_apis();
    if (getenv("CRATIX_DEBUG") && name) {
        char u8[300];
        WideCharToMultiByte(CP_UTF8, 0, name, -1, u8, sizeof(u8), NULL, NULL);
        cy_log("[vfs] GetFileAttributesExW(%s)\n", u8);
    }
    if (!name || level != GetFileExInfoStandard) return real_GetFileAttributesExW(name, level, buf);
    kind = resolve_path(name, rel, sizeof(rel));
    if (!kind) return real_GetFileAttributesExW(name, level, buf);
    {
        WIN32_FILE_ATTRIBUTE_DATA *d = (WIN32_FILE_ATTRIBUTE_DATA *)buf;
        e = kind == 1 ? zipidx_find(g_zip, rel) : NULL;
        d->dwFileAttributes = kind == 2 ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
        fill_times(&d->ftCreationTime);
        d->ftLastAccessTime = d->ftCreationTime;
        d->ftLastWriteTime = d->ftCreationTime;
        d->nFileSizeHigh = e ? (DWORD)((unsigned long long)e->uncomp_size >> 32) : 0;
        d->nFileSizeLow = e ? (DWORD)(e->uncomp_size & 0xFFFFFFFFu) : 0;
        return TRUE;
    }
}

static BOOL WINAPI vfs_GetFileInformationByHandle(HANDLE h, LPBY_HANDLE_FILE_INFORMATION buf) {
    VfsHandle *vh = lookup_pseudo(h);
    if (!vh) return real_GetFileInformationByHandle(h, buf);
    memset(buf, 0, sizeof(*buf));
    buf->dwFileAttributes = vh->is_dir ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
    fill_times(&buf->ftCreationTime);
    buf->ftLastAccessTime = buf->ftCreationTime;
    buf->ftLastWriteTime = buf->ftCreationTime;
    buf->nFileSizeHigh = (DWORD)(vh->size >> 32);
    buf->nFileSizeLow = (DWORD)(vh->size & 0xFFFFFFFFu);
    buf->nNumberOfLinks = 1;
    /* **虚拟句柄也要有独立 FileId/Volume** ✅：否则 fstat 全 0 ✅
     * → sameopenfile/samefile 把不同文件当同一个 ✅。
     * **用 rel 哈希而不是句柄指针** ✅：同一文件不同大小写打开 ✅
     * → 句柄指针不同 ✅ → sameopenfile 会误判 False ✅。 */
    buf->dwVolumeSerialNumber = 0x00C0FFEEu;
    {
        unsigned long long h = 1469598103934665603ULL;
        const char *s = vh->rel;
        while (*s) {
            unsigned char ch = (unsigned char)*s++;
            if (ch >= 'A' && ch <= 'Z') ch = (unsigned char)(ch + 32);
            h ^= ch;
            h *= 1099511628211ULL;
        }
        if (vh->is_dir) h ^= 0x9E3779B97F4A7C15ULL;
        if (!h) h = 1;
        buf->nFileIndexHigh = (DWORD)(h >> 32);
        buf->nFileIndexLow = (DWORD)(h & 0xFFFFFFFFu);
    }
    return TRUE;
}

static BOOL WINAPI vfs_GetFileInformationByHandleEx(HANDLE h, FILE_INFO_BY_HANDLE_CLASS cls, LPVOID buf, DWORD size) {
    VfsHandle *vh = lookup_pseudo(h);
    if (!vh) return real_GetFileInformationByHandleEx(h, cls, buf, size);
    if (cls == FileAttributeTagInfo && size >= sizeof(FILE_ATTRIBUTE_TAG_INFO)) {
        FILE_ATTRIBUTE_TAG_INFO *d = (FILE_ATTRIBUTE_TAG_INFO *)buf;
        d->FileAttributes = vh->is_dir ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
        d->ReparseTag = 0;
        return TRUE;
    }
    if (cls == FileStandardInfo && size >= sizeof(FILE_STANDARD_INFO)) {
        FILE_STANDARD_INFO *d = (FILE_STANDARD_INFO *)buf;
        d->AllocationSize.QuadPart = (long long)vh->size;
        d->EndOfFile.QuadPart = (long long)vh->size;
        d->NumberOfLinks = 1;
        d->DeletePending = FALSE;
        d->Directory = vh->is_dir;
        return TRUE;
    }
    if (cls == FileIdInfo && size >= sizeof(FILE_ID_INFO)) {
        FILE_ID_INFO *d = (FILE_ID_INFO *)buf;
        d->VolumeSerialNumber = 0x00C0FFEEu;
        memset(&d->FileId, 0, sizeof(d->FileId));
        {
            unsigned long long h = 1469598103934665603ULL;
            const char *s = vh->rel;
            while (*s) {
                unsigned char ch = (unsigned char)*s++;
                if (ch >= 'A' && ch <= 'Z') ch = (unsigned char)(ch + 32);
                h ^= ch;
                h *= 1099511628211ULL;
            }
            if (vh->is_dir) h ^= 0x9E3779B97F4A7C15ULL;
            if (!h) h = 1;
            memcpy(&d->FileId, &h, sizeof(h));
        }
        return TRUE;
    }
    if (cls == FileBasicInfo && size >= sizeof(FILE_BASIC_INFO)) {
        FILE_BASIC_INFO *d = (FILE_BASIC_INFO *)buf;
        memset(d, 0, sizeof(*d));
        d->FileAttributes = vh->is_dir ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
        return TRUE;
    }
    SetLastError(ERROR_NOT_SUPPORTED);
    return FALSE;
}

/* **通配匹配（* / ?，大小写不敏感）** ✅：路径末段模式 ✅ */
static int wild_match(const char *name, const char *pat) {
    const char *n = name, *p = pat;
    const char *star = NULL, *nstar = NULL;
    while (*n) {
        unsigned char a = (unsigned char)*n, b = *p ? (unsigned char)*p : 0;
        if (a >= 'A' && a <= 'Z') a = (unsigned char)(a + 32);
        if (b >= 'A' && b <= 'Z') b = (unsigned char)(b + 32);
        if (*p == '?' || (b && a == b)) {
            n++;
            p++;
        } else if (*p == '*') {
            star = p++;
            nstar = n;
        } else if (star) {
            p = star + 1;
            n = ++nstar;
        } else {
            return 0;
        }
    }
    while (*p == '*') p++;
    return *p == 0;
}

/* 目录枚举上下文 ✅：rel=父目录 ✅ pos=zip 游标 ✅ data=上一子项名（去重 ✅）
 * exact=精确匹配模式 ✅（FindFirstFile(<具体文件>) ✅ → FindNext 直接 NO_MORE_FILES ✅） */
static HANDLE WINAPI vfs_FindFirstFileExW(LPCWSTR pattern, FINDEX_INFO_LEVELS level, LPVOID data,
                                          FINDEX_SEARCH_OPS ops, LPVOID filter, DWORD flags) {
    char rel[300];
    int kind;
    VfsHandle *f;
    char parent[300];
    WIN32_FIND_DATAW *fd = (WIN32_FIND_DATAW *)data;
    ensure_real_apis();
    if (getenv("CRATIX_DEBUG") && pattern) {
        char u8[300];
        WideCharToMultiByte(CP_UTF8, 0, pattern, -1, u8, sizeof(u8), NULL, NULL);
        cy_log("[vfs] FindFirst(%s) level=%d flags=0x%lx\n", u8, (int)level, (unsigned long)flags);
    }
    /* FindExInfoStandard(0)/FindExInfoBasic(1) 都接 ✅ —— WIN32_FIND_DATAW 布局相同 ✅
     * （Basic 只是不回短文件名 ✅）；Python 的 os.listdir 走 **Basic** ✅
     * （不接的话归档目录列不出来 ✅ → FileFinder 空缓存 ✅ → ImportError ✅ 实测 ✅）。 */
    if (!pattern || (level != FindExInfoStandard && level != FindExInfoBasic))
        return real_FindFirstFileExW(pattern, level, data, ops, filter, flags);
    kind = resolve_path(pattern, rel, sizeof(rel));
    if (kind == 1) {
        /* 精确虚拟文件 ✅（FindFirstFile(<具体文件>) ✅ 单条 ✅） */
        const zentry_t *e = zipidx_find(g_zip, rel);
        f = (VfsHandle *)calloc(1, sizeof(VfsHandle));
        if (!f) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return INVALID_HANDLE_VALUE;
        }
        f->magic = VFS_MAGIC;
        f->is_dir = 0;
        strcpy(f->rel, rel);
        f->pos = 0;
        register_handle(f);
        fill_find_data(fd, strrchr(rel, '/') ? strrchr(rel, '/') + 1 : rel, 0,
                       e ? (unsigned long)e->uncomp_size : 0);
        return (HANDLE)f;
    }
    if (kind == 2) {
        /* 精确虚拟目录 ✅（返回目录自身 ✅） */
        f = (VfsHandle *)calloc(1, sizeof(VfsHandle));
        if (!f) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return INVALID_HANDLE_VALUE;
        }
        f->magic = VFS_MAGIC;
        f->is_dir = 1;
        strcpy(f->rel, rel);
        f->pos = 0;
        register_handle(f);
        fill_find_data(fd, strrchr(rel, '/') ? strrchr(rel, '/') + 1 : rel, 1, 0);
        return (HANDLE)f;
    }
    /* 通配枚举 ✅：pattern = <父>\<通配> ✅ → 父必须是虚拟目录 ✅ */
    {
        /* pattern 的父目录在 WIDE 里切 ✅ */
        const WCHAR *pslash = NULL, *p = pattern;
        size_t plen, i;
        if (!p) return real_FindFirstFileExW(pattern, level, data, ops, filter, flags);
        for (; *p; p++) {
            if (*p == L'\\' || *p == L'/') pslash = p;
        }
        if (!pslash) return real_FindFirstFileExW(pattern, level, data, ops, filter, flags);
        plen = (size_t)(pslash - pattern);
        if (plen >= 600) return real_FindFirstFileExW(pattern, level, data, ops, filter, flags);
        {
            WCHAR wparent[600];
            for (i = 0; i < plen; i++) wparent[i] = pattern[i];
            wparent[plen] = 0;
            kind = resolve_path(wparent, parent, sizeof(parent));
        }
        /* **任意通配模式** ✅：旧口径只接 "*" / "*.*" ✅
         * → FindFirstFileW("*.dll") 落真实实现 ✅ → 虚拟目录列不出来 ✅。 */
        {
            const WCHAR *wlast = pslash + 1;
            if (!*wlast || (!wcschr(wlast, L'*') && !wcschr(wlast, L'?')))
                return real_FindFirstFileExW(pattern, level, data, ops, filter, flags);
        }
        if (kind != 2) return real_FindFirstFileExW(pattern, level, data, ops, filter, flags);
    }
    f = (VfsHandle *)calloc(1, sizeof(VfsHandle));
    if (!f) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return INVALID_HANDLE_VALUE;
    }
    f->magic = VFS_MAGIC;
    f->is_dir = 1;
    strcpy(f->rel, parent);
    f->pos = (size_t)-1;
    {
        const WCHAR *wlast = pattern, *q = pattern;
        size_t k = 0;
        for (; *q; q++)
            if (*q == L'\\' || *q == L'/') wlast = q + 1;
        while (wlast[k] && k + 1 < sizeof(f->pattern)) {
            WCHAR c = wlast[k];
            if (c > 0x7F) c = L'?';
            f->pattern[k] = (char)c;
            k++;
        }
        f->pattern[k] = 0;
        /* **Windows 的 "*.*" 匹配所有文件** ✅（包括无扩展名 ✅）。
         * 旧通配匹配把 "*.*" 当成「必须有点」✅ → 无扩展名文件漏列 ✅。 */
        if (strcmp(f->pattern, "*.*") == 0) strcpy(f->pattern, "*");
    }
    register_handle(f);
    /* 找第一个未列过的子项 ✅ */
    {
        int i;
        for (i = 0; i < g_zip->count; i++) {
            const char *name = g_zip->entries[i].name;
            char child[300];
            const zentry_t *e;
            if (!entry_in_dir(name, parent) && !subdir_in_dir(name, parent)) continue;
            child_name_of(name, parent, child, sizeof(child));
            if (f->pattern[0] && !wild_match(child, f->pattern)) continue;
            if (f->data && strcmp((const char *)f->data, child) == 0) continue;
            f->pos = (size_t)i;
            {
                char *keep = (char *)malloc(strlen(child) + 1);
                strcpy(keep, child);
                free(f->data);
                f->data = (unsigned char *)keep;
            }
            e = zipidx_find(g_zip, name);
            fill_find_data(fd, child, subdir_in_dir(name, parent) != 0, e ? (unsigned long)e->uncomp_size : 0);
            return (HANDLE)f;
        }
    }
    unregister_handle(f);
    free(f);
    SetLastError(ERROR_FILE_NOT_FOUND);
    return INVALID_HANDLE_VALUE;
}

static BOOL WINAPI vfs_FindNextFileW(HANDLE h, LPVOID data) {
    VfsHandle *vh = lookup_pseudo(h);
    WIN32_FIND_DATAW *fd = (WIN32_FIND_DATAW *)data;
    int i;
    const char *dir;
    if (!vh) return real_FindNextFileW(h, data);
    if (!vh->is_dir || vh->pos == (size_t)-1) {
        /* 精确模式 ✅ 或非枚举句柄 ✅ */
        SetLastError(ERROR_NO_MORE_FILES);
        return FALSE;
    }
    dir = vh->rel;
    for (i = (int)vh->pos + 1; i < g_zip->count; i++) {
        const char *name = g_zip->entries[i].name;
        char child[300];
        const zentry_t *e;
        int j, seen = 0;
        if (!entry_in_dir(name, dir) && !subdir_in_dir(name, dir)) continue;
        child_name_of(name, dir, child, sizeof(child));
        if (vh->pattern[0] && !wild_match(child, vh->pattern)) continue;
        if (vh->data && strcmp((const char *)vh->data, child) == 0) continue;
        /* 同名去重 ✅（文件条目 + 子目录条目可能同名 ✅ 只列一次 ✅） */
        for (j = 0; j < i && !seen; j++) {
            const char *n2 = g_zip->entries[j].name;
            char c2[300];
            if (!entry_in_dir(n2, dir) && !subdir_in_dir(n2, dir)) continue;
            child_name_of(n2, dir, c2, sizeof(c2));
            if (strcmp(c2, child) == 0) seen = 1;
        }
        if (seen) continue;
        vh->pos = (size_t)i;
        {
            char *keep = (char *)malloc(strlen(child) + 1);
            strcpy(keep, child);
            free(vh->data);
            vh->data = (unsigned char *)keep;
        }
        e = zipidx_find(g_zip, name);
        fill_find_data(fd, child, subdir_in_dir(name, dir) != 0, e ? (unsigned long)e->uncomp_size : 0);
        return TRUE;
    }
    SetLastError(ERROR_NO_MORE_FILES);
    return FALSE;
}

static BOOL WINAPI vfs_FindClose(HANDLE h) {
    VfsHandle *vh = lookup_pseudo(h);
    if (!vh) return real_FindClose(h);
    free(vh->data);
    unregister_handle(vh);
    free(vh);
    return TRUE;
}

static HANDLE WINAPI vfs_CreateFileMappingW(HANDLE h, LPSECURITY_ATTRIBUTES sa, DWORD protect,
                                            DWORD max_hi, DWORD max_lo, LPCWSTR name) {
    VfsHandle *vh = lookup_pseudo(h);
    if (!vh) return real_CreateFileMappingW(h, sa, protect, max_hi, max_lo, name);
    if (vh->is_dir || vh->size == 0) {
        SetLastError(ERROR_ACCESS_DENIED);
        return NULL;
    }
    {
        VfsHandle *sec = (VfsHandle *)calloc(1, sizeof(VfsHandle));
        if (!sec) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return NULL;
        }
        sec->magic = VFS_MAGIC;
        sec->is_dir = 0;
        sec->data = vh->data;
        sec->size = vh->size;
        strcpy(sec->rel, "section:");
        strcat(sec->rel, vh->rel);
        register_handle(sec);
        return (HANDLE)sec;
    }
}

static LPVOID WINAPI vfs_MapViewOfFile(HANDLE sec, DWORD mode, DWORD off_hi, DWORD off_lo, SIZE_T bytes) {
    VfsHandle *vh = lookup_pseudo(sec);
    size_t off;

    if (!vh) return real_MapViewOfFile(sec, mode, off_hi, off_lo, bytes);
    off = ((size_t)off_hi << 32) | off_lo;
    if (off > vh->size) return NULL;
    if (bytes == 0) bytes = vh->size - off;
    if (off + bytes > vh->size) bytes = vh->size - off;
    return vh->data + off;
}

static BOOL WINAPI vfs_UnmapViewOfFile(LPCVOID p) {
    (void)p;
    return TRUE;
}

static BOOL WINAPI vfs_DeleteFileW(LPCWSTR name) {
    char rel[300];
    ensure_real_apis();
    if (!name || !resolve_path(name, rel, sizeof(rel))) return real_DeleteFileW(name);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static BOOL WINAPI vfs_MoveFileW(LPCWSTR a, LPCWSTR b) {
    char rel[300];
    ensure_real_apis();
    if (!a || !resolve_path(a, rel, sizeof(rel))) return real_MoveFileW(a, b);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static BOOL WINAPI vfs_MoveFileExW(LPCWSTR a, LPCWSTR b, DWORD flags) {
    char rel[300];
    ensure_real_apis();
    if (!a || !resolve_path(a, rel, sizeof(rel))) return real_MoveFileExW(a, b, flags);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static BOOL WINAPI vfs_CopyFileW(LPCWSTR a, LPCWSTR b, BOOL fail_if_exists) {
    char rel[300];
    ensure_real_apis();
    if (!a || !resolve_path(a, rel, sizeof(rel))) return real_CopyFileW(a, b, fail_if_exists);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static BOOL WINAPI vfs_CreateDirectoryW(LPCWSTR name, LPSECURITY_ATTRIBUTES sa) {
    char rel[300];
    ensure_real_apis();
    if (!name || !resolve_path(name, rel, sizeof(rel))) return real_CreateDirectoryW(name, sa);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static BOOL WINAPI vfs_RemoveDirectoryW(LPCWSTR name) {
    char rel[300];
    ensure_real_apis();
    if (!name || !resolve_path(name, rel, sizeof(rel))) return real_RemoveDirectoryW(name);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static BOOL WINAPI vfs_SetEndOfFile(HANDLE h) {
    if (!lookup_pseudo(h)) return real_SetEndOfFile(h);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static BOOL WINAPI vfs_FlushFileBuffers(HANDLE h) {
    if (!lookup_pseudo(h)) return real_FlushFileBuffers(h);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static BOOL WINAPI vfs_SetFileTime(HANDLE h, const FILETIME *a, const FILETIME *b, const FILETIME *c) {
    if (!lookup_pseudo(h)) return real_SetFileTime(h, a, b, c);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static BOOL WINAPI vfs_SetFileInformationByHandle(HANDLE h, FILE_INFO_BY_HANDLE_CLASS cls, LPVOID buf, DWORD size) {
    if (!lookup_pseudo(h)) return real_SetFileInformationByHandle(h, cls, buf, size);
    SetLastError(ERROR_ACCESS_DENIED);
    return FALSE;
}

static DWORD WINAPI vfs_GetFileType(HANDLE h) {
    if (!lookup_pseudo(h)) return real_GetFileType(h);
    return FILE_TYPE_DISK;
}

/* **GetFileSize(Ex)** ✅：Python 的 mmap 会先取文件大小 ✅ → 不接的话伪句柄
 * 落到真实 API ✅ → WinError 6 ✅（实测 mmap(虚拟条目) ✅）。 */
static BOOL WINAPI vfs_GetFileSizeEx(HANDLE h, PLARGE_INTEGER size) {
    VfsHandle *vh = lookup_pseudo(h);
    if (!vh) {
        ensure_real_apis();
        return real_GetFileSizeEx(h, size);
    }
    if (!size) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    size->QuadPart = (LONGLONG)vh->size;
    return TRUE;
}

static DWORD WINAPI vfs_GetFileSize(HANDLE h, LPDWORD high) {
    VfsHandle *vh = lookup_pseudo(h);
    if (!vh) {
        ensure_real_apis();
        return real_GetFileSize(h, high);
    }
    if (high) *high = (DWORD)((unsigned long long)vh->size >> 32);
    return (DWORD)((unsigned long long)vh->size & 0xFFFFFFFFu);
}

static BOOL WINAPI vfs_FreeLibrary(HMODULE m) {
    /* 伪模块常驻 ✅ 真模块走真实 FreeLibrary ✅ */
    return real_FreeLibrary(m);
}

/* ---------- NTDLL 重定向实现 ✅ ---------- */

static LONG WINAPI vfs_NtCreateFile(PHANDLE ph, ACCESS_MASK access, PVOID objattr,
                                    PVOID io, PLARGE_INTEGER size, ULONG attr, ULONG share,
                                    ULONG disp, ULONG opts, PVOID eabuf, ULONG ealen) {
    /* OBJECT_ATTRIBUTES（64 位 ✅）：Length(4)+pad(4) + RootDirectory(8) + ObjectName(8) ✅
     * ObjectName 在偏移 0x10 ✅；UNICODE_STRING：Length(2)+Max(2)+pad(4)+Buffer(8) ✅ */
    void *us;
    unsigned short wlen;
    WCHAR *buf, wpath[600];
    size_t i;
    char rel[300];
    int kind;
    VfsHandle *h;
    ensure_real_apis();
    if (getenv("CRATIX_DEBUG") && objattr && ph) {
        void *us_t = *(void **)((char *)objattr + 0x10);
        if (us_t) {
            unsigned short wl = *(unsigned short *)us_t;
            WCHAR *bp = *(WCHAR **)((char *)us_t + 8);
            WCHAR wp[600];
            size_t k;
            if (wl / 2 > 599) wl = 599 * 2;
            for (k = 0; k < (size_t)(wl / 2); k++) wp[k] = bp[k];
            wp[k] = 0;
            {
                char u8[300];
                WideCharToMultiByte(CP_UTF8, 0, wp, -1, u8, sizeof(u8), NULL, NULL);
                cy_log("[vfs] NtCreateFile(%s)\n", u8);
            }
        }
    }
    if (!objattr || !ph) goto real;
    us = *(void **)((char *)objattr + 0x10);
    if (!us) goto real;
    wlen = *(unsigned short *)us;
    buf = *(WCHAR **)((char *)us + 8);
    if (!buf) goto real;
    if (wlen / 2 > 599) wlen = 599 * 2;
    for (i = 0; i < (size_t)(wlen / 2); i++) wpath[i] = buf[i];
    wpath[i] = 0;
    kind = resolve_path(wpath, rel, sizeof(rel));
    if (!kind) goto real;
    if (access & (FILE_WRITE_DATA | FILE_APPEND_DATA | DELETE | FILE_WRITE_ATTRIBUTES)) {
        return 0xC0000045u; /* STATUS_ACCESS_DENIED ✅ 只读 ✅ */
    }
    h = open_entry(kind == 1 ? zipidx_find(g_zip, rel) : NULL, rel, kind == 2);
    if (!h) return 0xC0000045u;
    *ph = (HANDLE)h;
    if (io) memset(io, 0, 24); /* IO_STATUS_BLOCK ✅ */
    return 0; /* STATUS_SUCCESS ✅ */
real:
    if (!real_NtCreateFile) return 0xC0000001u;
    return real_NtCreateFile(ph, access, objattr, io, size, attr, share, disp, opts, eabuf, ealen);
}

static LONG WINAPI vfs_NtQueryVolumeInformationFile(HANDLE h, PVOID io, PVOID buf, ULONG len, ULONG cls) {
    VfsHandle *vh = lookup_pseudo(h);
    if (!vh) return real_NtQueryVolumeInformationFile(h, io, buf, len, cls);
    if (cls == 1 && len >= 32) {
        /* FileFsVolumeInformation ✅ */
        WCHAR label[] = L"CRATIX";
        unsigned long *serial = (unsigned long *)((char *)buf + 8);
        unsigned short *llen = (unsigned short *)((char *)buf + 16);
        ULONG *loff = (ULONG *)((char *)buf + 20);
        memset(buf, 0, len);
        *serial = 0x00C0FFEEu;
        *llen = (unsigned short)(6 * 2);
        *loff = 24;
        memcpy((char *)buf + *loff, label, *llen);
        if (io) *(ULONG *)io = 24 + *llen;
        return 0;
    }
    if (cls == 5 && len >= 16) {
        /* FileFsAttributeInformation ✅ */
        WCHAR label[] = L"NTFS";
        unsigned long *flags = (unsigned long *)buf;
        unsigned long *llen = (unsigned long *)((char *)buf + 4);
        memset(buf, 0, len);
        *flags = 0x00070000u; /* CASE_PRESERVED | CASE_SENSITIVE_SEARCH | UNICODE_ON_DISK ✅ */
        *llen = 4 * 2;
        memcpy((char *)buf + 8, label, *llen);
        if (io) *(ULONG *)io = 8 + *llen;
        return 0;
    }
    return 0xC0000003u; /* STATUS_INVALID_PARAMETER ✅ */
}

/* AddDllDirectoryW / RemoveDllDirectoryW ✅（M6c ✅）：PySide6 的 bootstrap 对
 * 虚拟目录调 os.add_dll_directory ✅ → 真实现 WinError 3 ✅（实测 ✅）。
 * 虚拟目录 ✅ → 发**伪 cookie** ✅（登记表 ✅）；Remove 时认领 ✅；其余透传 ✅。 */
typedef DWORD(WINAPI *fn_AddDllDirectory)(PCWSTR);
typedef BOOL(WINAPI *fn_RemoveDllDirectory)(DWORD);
static fn_AddDllDirectory real_AddDllDirectory = NULL;
static fn_RemoveDllDirectory real_RemoveDllDirectory = NULL;
static DWORD g_fake_dll_dir_cookie = 0x56565346u; /* 'VVSF' ✅ 递增 ✅ */

static DWORD WINAPI vfs_AddDllDirectory(PCWSTR path) {
    char rel[300];
    int kind;
    ensure_real_apis();
    kind = path ? resolve_path(path, rel, sizeof(rel)) : 0;
    if (!kind) {
        if (!real_AddDllDirectory) {
            HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
            /* **API 名字是 AddDllDirectory（没有 W 后缀 ✅）** —— 旧代码查 AddDllDirectoryW ✅
         * 永远 NULL ✅ → 真实目录也被报 ERROR_INVALID_PARAMETER ✅（实测 B 形态
         * PySide6 _setupQtDirectories 的 os.add_dll_directory → WinError 87 ✅）。 */
        real_AddDllDirectory = (fn_AddDllDirectory)(void *)GetProcAddress(k32, "AddDllDirectory");
        }
        if (real_AddDllDirectory) return real_AddDllDirectory(path);
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    return ++g_fake_dll_dir_cookie;
}

static BOOL WINAPI vfs_RemoveDllDirectory(DWORD cookie) {
    ensure_real_apis();
    if (cookie > 0x56565346u && cookie <= g_fake_dll_dir_cookie) return TRUE; /* 伪 cookie ✅ */
    if (!real_RemoveDllDirectory) {
        HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
        real_RemoveDllDirectory = (fn_RemoveDllDirectory)(void *)GetProcAddress(k32, "RemoveDllDirectoryW");
    }
    if (real_RemoveDllDirectory) return real_RemoveDllDirectory(cookie);
    SetLastError(ERROR_INVALID_PARAMETER);
    return FALSE;
}

/* ---------- 重定向清单 ✅ ---------- */
static DWORD WINAPI vfs_GetFinalPathNameByHandleW(HANDLE h, LPWSTR buf, DWORD cch, DWORD flags);
static const char *const k_names[] = {
    "CreateFileW", "ReadFile", "WriteFile", "SetFilePointer", "SetFilePointerEx",
    "GetFileAttributesW", "GetFileAttributesExW", "GetFileInformationByHandle",
    "GetFileInformationByHandleEx", "FindFirstFileExW", "FindFirstFileW", "FindNextFileW", "FindClose",
    "CreateFileMappingW", "MapViewOfFile", "UnmapViewOfFile",
    "DeleteFileW", "MoveFileW", "MoveFileExW", "CopyFileW",
    "CreateDirectoryW", "RemoveDirectoryW", "SetEndOfFile", "FlushFileBuffers",
    "SetFileTime", "SetFileInformationByHandle", "GetFileType", "GetFileSizeEx", "GetFileSize",
    "GetFinalPathNameByHandleW", "CloseHandle",
    "AddDllDirectory", "RemoveDllDirectory",
    NULL};
static const void *const k_funcs[] = {
    (const void *)vfs_CreateFileW, (const void *)vfs_ReadFile, (const void *)vfs_WriteFile,
    (const void *)vfs_SetFilePointer, (const void *)vfs_SetFilePointerEx,
    (const void *)vfs_GetFileAttributesW, (const void *)vfs_GetFileAttributesExW,
    (const void *)vfs_GetFileInformationByHandle, (const void *)vfs_GetFileInformationByHandleEx,
    (const void *)vfs_FindFirstFileExW, (const void *)vfs_FindFirstFileW, (const void *)vfs_FindNextFileW,
    (const void *)vfs_FindClose,
    (const void *)vfs_CreateFileMappingW, (const void *)vfs_MapViewOfFile, (const void *)vfs_UnmapViewOfFile,
    (const void *)vfs_DeleteFileW, (const void *)vfs_MoveFileW, (const void *)vfs_MoveFileExW,
    (const void *)vfs_CopyFileW, (const void *)vfs_CreateDirectoryW, (const void *)vfs_RemoveDirectoryW,
    (const void *)vfs_SetEndOfFile, (const void *)vfs_FlushFileBuffers, (const void *)vfs_SetFileTime,
    (const void *)vfs_SetFileInformationByHandle, (const void *)vfs_GetFileType,
    (const void *)vfs_GetFileSizeEx, (const void *)vfs_GetFileSize,
    (const void *)vfs_GetFinalPathNameByHandleW, (const void *)vfs_CloseHandle,
    (const void *)vfs_AddDllDirectory, (const void *)vfs_RemoveDllDirectory,
    NULL};

static const char *const n_names[] = {"NtCreateFile", "NtQueryVolumeInformationFile", NULL};
static const void *const n_funcs[] = {
    (const void *)vfs_NtCreateFile, (const void *)vfs_NtQueryVolumeInformationFile, NULL};

/* CRT stat 重定向 ✅：Python 3.14 的 os.stat 走 _wstat64i32 ✅（api-ms CRT DLL ✅
 * OS 加载 ✅ 它自己的 IAT 我们够不着 ✅ → 在**解释器的 IAT** 上重定向 ✅）。
 * 否则 Python 侧的 Path.is_dir()/exists() 全部落真实盘 ✗（实测 ✅）。 */

/* MSVC 的 struct _stat64i32 布局 ✅（corecrt_stat.h ✅ 48 字节 ✅） */
struct cratix_stat64i32 {
    unsigned int st_dev;
    unsigned short st_ino;
    unsigned short st_mode;
    short st_nlink;
    short st_uid;
    short st_gid;
    unsigned int st_rdev;
    long st_size;
    long long st_atime;
    long long st_mtime;
    long long st_ctime;
};

typedef int(__cdecl *fn_wstat64i32)(const wchar_t *, void *);
static fn_wstat64i32 real_wstat64i32 = NULL;

static int __cdecl vfs_wstat64i32(const wchar_t *path, struct cratix_stat64i32 *buf) {
    char rel[300];
    int kind;
    const zentry_t *e;
    ensure_real_apis();
    if (getenv("CRATIX_DEBUG") && path) {
        char u8[300];
        WideCharToMultiByte(CP_UTF8, 0, path, -1, u8, sizeof(u8), NULL, NULL);
        cy_log("[vfs] wstat(%s)\n", u8);
    }
    if (!buf) return -1;
    kind = resolve_path(path, rel, sizeof(rel));
    if (!kind) {
        if (!real_wstat64i32) {
            HMODULE u = GetModuleHandleW(L"ucrtbase.dll");
            real_wstat64i32 = (fn_wstat64i32)GetProcAddress(u ? u : GetModuleHandleW(L"api-ms-win-crt-filesystem-l1-1-0.dll"),
                                                             "_wstat64i32");
            if (!real_wstat64i32) {
                SetLastError(ERROR_PROC_NOT_FOUND);
                return -1;
            }
        }
        return real_wstat64i32(path, buf);
    }
    memset(buf, 0, sizeof(*buf));
    if (kind == 2) {
        buf->st_mode = 0x4000u | 0x0200u | 0x0080u; /* _S_IFDIR | _S_IREAD | _S_IWRITE ✅ */
    } else {
        e = zipidx_find(g_zip, rel);
        buf->st_mode = 0x8000u | 0x0200u | 0x0080u; /* _S_IFREG | _S_IREAD | _S_IWRITE ✅ */
        buf->st_size = e ? (long)e->uncomp_size : 0;
    }
    {
        long long t = 132500000000000000ULL / 10000000 - 11644473600LL; /* FILETIME → epoch ✅ */
        buf->st_atime = t;
        buf->st_mtime = t;
        buf->st_ctime = t;
    }
    return 0;
}

static const char *const crt_names[] = {"_wstat64i32", NULL};
static const void *const crt_funcs[] = {(const void *)vfs_wstat64i32, NULL};

const char *const *vfs_kernel_redirect_names(void) { return k_names; }
const void *const *vfs_kernel_redirect_funcs(void) { return k_funcs; }
int vfs_kernel_redirect_count(void) {
    int n = 0;
    while (k_names[n]) n++;
    return n;
}
const char *const *vfs_crt_redirect_names(void) { return crt_names; }
const void *const *vfs_crt_redirect_funcs(void) { return crt_funcs; }
int vfs_crt_redirect_count(void) {
    int n = 0;
    while (crt_names[n]) n++;
    return n;
}
/* GetFileInformationByName 重定向 ✅（M6c / Python 3.14 ✅）
 *
 * CPython 3.14 的 os.stat 走 **win32_xstat_impl 快路径** ✅：
 *   `_Py_GetFileInformationByName` → **LoadLibraryW("api-ms-win-core-file-l2-1-4")**
 *   → GetProcAddress("GetFileInformationByName") → 直接调 ✅
 * —— 它**绕过**全部 CreateFileW/GetFileAttributesW 挂钩 ✅（动态解析 ✅）→
 *   虚拟文件一律 ERROR_FILE_NOT_FOUND ✅ → os.path.isdir/isfile 全 False ✅（实测 ✅）。
 * 解法 ✅：stub 的 cratix_GetProcAddress 拦下这个名字 ✅ → 给 VFS 实现 ✅：
 *   虚拟路径 → 按归档条目填 FILE_STAT_BASIC_INFORMATION ✅（布局见 pycore_fileutils_windows.h ✅）
 *   其余 → 回落真实实现 ✅。 */
typedef BOOL(WINAPI *fn_GetFileInformationByName)(PCWSTR, int, PVOID, ULONG);
static fn_GetFileInformationByName real_GetFileInformationByName = NULL;

static BOOL WINAPI vfs_GetFileInformationByName(PCWSTR name, int cls, PVOID buf, ULONG size) {
    char rel[300];
    int kind;
    const zentry_t *e;
    ensure_real_apis();
    if (getenv("CRATIX_DEBUG") && name) {
        char u8[300];
        WideCharToMultiByte(CP_UTF8, 0, name, -1, u8, sizeof(u8), NULL, NULL);
        cy_log("[vfs] GFIBN entry(%s) cls=%d size=%lu\n", u8, cls, (unsigned long)size);
    }
    /* FileStatBasicByNameInfo = 3 ✅（CPython 只用它 ✅）；结构 104 字节 ✅
     * （FileId 8 + 5×时间/大小 8×5=40 … 见 pycore_fileutils_windows.h ✅）。 */
    if (!name || cls != 3 || !buf || size < 104) goto real;
    kind = resolve_path(name, rel, sizeof(rel));
    if (!kind) goto real;
    if (getenv("CRATIX_DEBUG")) {
        char u8[300];
        WideCharToMultiByte(CP_UTF8, 0, name, -1, u8, sizeof(u8), NULL, NULL);
        cy_log("[vfs] GetFileInformationByName(%s) kind=%d rel=%s\n", u8, kind, kind ? rel : "-");
    }
    memset(buf, 0, size);
    e = kind == 1 ? zipidx_find(g_zip, rel) : NULL;
    {
        unsigned char *p = (unsigned char *)buf;
        FILETIME ft;
        LARGE_INTEGER fsz;
        fill_times(&ft);
        fsz.QuadPart = e ? (LONGLONG)e->uncomp_size : 0;
        memcpy(p + 8, &ft, 8);   /* CreationTime ✅ */
        memcpy(p + 16, &ft, 8);  /* LastAccessTime ✅ */
        memcpy(p + 24, &ft, 8);  /* LastWriteTime ✅ */
        memcpy(p + 32, &ft, 8);  /* ChangeTime ✅ */
        memcpy(p + 40, &fsz, 8); /* AllocationSize ✅ */
        memcpy(p + 48, &fsz, 8); /* EndOfFile ✅ */
        *(ULONG *)(void *)(p + 56) = (kind == 2) ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
        *(ULONG *)(void *)(p + 64) = 1; /* NumberOfLinks ✅ */
        /* **实测 os.path.isfile 需要这些字段** ✅：真实文件
         * DeviceType=7 / DeviceCharacteristics=0x100 / FileId128 非零 ✅；虚拟条目全 0 ✅
         * → isfile False ✅（os.stat 本身仍正常 ✅）。 */
        *(ULONG *)(void *)(p + 68) = 7;    /* FILE_DEVICE_DISK ✅ */
        *(ULONG *)(void *)(p + 72) = 0x100; /* FILE_DEVICE_IS_MOUNTED ✅ */
        {
            /* **FileId 用 rel 哈希** ✅：旧口径用 e 指针 ✅ → 目录 e=NULL ✅
             * → 所有目录 FileId 都是 1 ✅ → os.path.samefile(不同目录)=True ✅。 */
            unsigned long long h = 1469598103934665603ULL;
            const char *s = rel;
            while (*s) {
                unsigned char ch = (unsigned char)*s++;
                /* **Windows 路径不区分大小写** ✅：哈希也要归一化 ✅
                 * → 否则 samefile(native, NATIVE) 为 False ✅。 */
                if (ch >= 'A' && ch <= 'Z') ch = (unsigned char)(ch + 32);
                h ^= ch;
                h *= 1099511628211ULL;
            }
            if (kind == 2) h ^= 0x9E3779B97F4A7C15ULL;
            if (!h) h = 1;
            memcpy(p, &h, 8);      /* FileId ✅ */
            memcpy(p + 88, &h, 8); /* FileId128 低 8 字节 ✅ */
        }
        *(ULONG *)(void *)(p + 80) = 0x00C0FFEEu; /* VolumeSerialNumber ✅（与 NtQueryVolumeInformationFile 同值 ✅） */
        /* ReparseTag=0 / DeviceType=0 / DeviceCharacteristics=0 / FileId128=0 ✅（memset 过 ✅） */
    }
    return TRUE;
real:
    if (!real_GetFileInformationByName) {
        HMODULE h = LoadLibraryW(L"api-ms-win-core-file-l2-1-4");
        if (h) real_GetFileInformationByName =
                    (fn_GetFileInformationByName)(void *)GetProcAddress(h, "GetFileInformationByName");
    }
    if (real_GetFileInformationByName)
        return real_GetFileInformationByName(name, cls, buf, size);
    SetLastError(ERROR_NOT_SUPPORTED);
    return FALSE;
}

void *vfs_file_information_by_name(void) { return (void *)vfs_GetFileInformationByName; }

static HANDLE WINAPI vfs_FindFirstFileW(LPCWSTR pattern, LPWIN32_FIND_DATAW data);
static BOOL WINAPI vfs_FindNextFileW(HANDLE h, LPWIN32_FIND_DATAW data);
static BOOL WINAPI vfs_FindClose(HANDLE h);
HANDLE WINAPI vfs_dynamic_FindFirstFileW(LPCWSTR pattern, LPWIN32_FIND_DATAW data) {
    return vfs_FindFirstFileW(pattern, data);
}
BOOL WINAPI vfs_dynamic_FindNextFileW(HANDLE h, LPWIN32_FIND_DATAW data) {
    return vfs_FindNextFileW(h, data);
}
BOOL WINAPI vfs_dynamic_FindClose(HANDLE h) { return vfs_FindClose(h); }

HANDLE WINAPI vfs_dynamic_CreateFileMappingW(HANDLE h, LPSECURITY_ATTRIBUTES sa, DWORD protect,
                                             DWORD max_hi, DWORD max_lo, LPCWSTR name) {
    return vfs_CreateFileMappingW(h, sa, protect, max_hi, max_lo, name);
}
LPVOID WINAPI vfs_dynamic_MapViewOfFile(HANDLE sec, DWORD mode, DWORD off_hi, DWORD off_lo, SIZE_T bytes) {
    return vfs_MapViewOfFile(sec, mode, off_hi, off_lo, bytes);
}
BOOL WINAPI vfs_dynamic_UnmapViewOfFile(LPCVOID p) { return vfs_UnmapViewOfFile(p); }

/* **动态 GetProcAddress 用的包装** ✅：Python 3.14 的 os.path.isfile 等
 * 可能动态取 GetFileAttributesW ✅ → 不经 IAT ✅ → 看不到虚拟文件 ✅。 */
DWORD WINAPI vfs_dynamic_GetFileAttributesW(LPCWSTR name) {
    return vfs_GetFileAttributesW(name);
}

BOOL WINAPI vfs_dynamic_GetFileAttributesExW(LPCWSTR name, GET_FILEEX_INFO_LEVELS level, LPVOID buf) {
    return vfs_GetFileAttributesExW(name, level, buf);
}

/* GetFinalPathNameByHandleW ✅（M6c ✅）：Python 的 os.path.realpath 走它 ✅
 * （打开文件后按**句柄**取最终路径 ✅）—— 伪句柄进真实现 → WinError 6 ✅（实测 ✅）。
 * 伪句柄 ✅ → 返回「<exe 目录>\<规范 rel>」✅（与 CreateFileW 打开时的路径语义一致 ✅）；
 * 真句柄 ✅ → 透传 ✅。 */
typedef DWORD(WINAPI *fn_GetFinalPathNameByHandleW)(HANDLE, LPWSTR, DWORD, DWORD);
static fn_GetFinalPathNameByHandleW real_GetFinalPathNameByHandleW = NULL;

static DWORD WINAPI vfs_GetFinalPathNameByHandleW(HANDLE h, LPWSTR buf, DWORD cch, DWORD flags) {
    VfsHandle *vh = lookup_pseudo(h);
    WCHAR full[600];
    size_t k = 0, i;
    DWORD need;
    ensure_real_apis();
    if (!vh) {
        if (!real_GetFinalPathNameByHandleW) {
            HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
            real_GetFinalPathNameByHandleW =
                (fn_GetFinalPathNameByHandleW)(void *)GetProcAddress(k32, "GetFinalPathNameByHandleW");
        }
        if (real_GetFinalPathNameByHandleW) return real_GetFinalPathNameByHandleW(h, buf, cch, flags);
        SetLastError(ERROR_INVALID_HANDLE);
        return 0;
    }
    if (vh->is_dir || !vh->rel[0]) {
        SetLastError(ERROR_INVALID_FUNCTION);
        return 0;
    }
    /* 拼最终路径 ✅：<exe 目录>\<rel（'/'→'\' ✅）> ✅ */
    for (k = 0; g_exe_dir_w[k] && k + 1 < 600; k++) full[k] = g_exe_dir_w[k];
    if (k + 2 >= 600) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return 0;
    }
    full[k++] = L'\\';
    for (i = 0; vh->rel[i] && k + 1 < 600; i++) full[k++] = (WCHAR)(unsigned char)(vh->rel[i] == '/' ? '\\' : vh->rel[i]);
    full[k] = 0;
    /* VOLUME_NAME_NONE(0x4) ✅ → 不要盘符前缀 ✅；GUID/NT ✅ → 不支持 ✅（回落真实 ✅
     * —— 伪句柄没有真实卷 ✅）。 */
    need = (DWORD)k;
    if ((flags & 0x04u) && need > 3 && full[1] == L':' && full[2] == L'\\') {
        /* 剥掉 "X:\" ✅ */
        DWORD inner = need - 3;
        if (inner + 1 > cch) {
            SetLastError(ERROR_INSUFFICIENT_BUFFER);
            return inner + 1;
        }
        memcpy(buf, full + 3, inner * sizeof(WCHAR));
        buf[inner] = 0;
        return inner;
    }
    if (flags & 0x03u) { /* VOLUME_NAME_GUID/NT ✅ 伪句柄不支持 ✅ */
        SetLastError(ERROR_INVALID_FUNCTION);
        return 0;
    }
    if (need + 1 > cch) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return need + 1;
    }
    memcpy(buf, full, need * sizeof(WCHAR));
    buf[need] = 0;
    return need;
}

const char *const *vfs_nt_redirect_names(void) { return n_names; }
const void *const *vfs_nt_redirect_funcs(void) { return n_funcs; }
int vfs_nt_redirect_count(void) {
    int n = 0;
    while (n_names[n]) n++;
    return n;
}
