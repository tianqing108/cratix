#include "zipread.h"
#include <stdlib.h>
#include <string.h>

#define EOCD_SIG 0x06054b50u
#define CD_SIG 0x02014b50u
#define LFH_SIG 0x04034b50u

static unsigned long rd32(const unsigned char *p) {
    return (unsigned long)p[0] | ((unsigned long)p[1] << 8) | ((unsigned long)p[2] << 16) |
           ((unsigned long)p[3] << 24);
}

static unsigned short rd16(const unsigned char *p) {
    return (unsigned short)((unsigned short)p[0] | ((unsigned short)p[1] << 8));
}

int zipidx_open(zipidx_t *z, const unsigned char *base, size_t size) {
    size_t i;
    long eocd = -1;
    unsigned long cd_off, cd_size;
    unsigned long pos, count;
    memset(z, 0, sizeof(*z));
    z->base = base;
    z->size = size;
    if (size < 22) return 0;
    /* 从尾部回扫找 EOCD（允许前面有拼接前缀） */
    for (i = size - 22 + 1; i-- > 0;) {
        if (rd32(base + i) == EOCD_SIG) {
            eocd = (long)i;
            break;
        }
        if (size - i > 65557) break;
    }
    if (eocd < 0) return 0;
    cd_size = rd32(base + eocd + 12);
    cd_off = rd32(base + eocd + 16);
    if ((size_t)cd_off + cd_size > size) return 0;
    count = 0;
    pos = 0;
    while (pos + 46 <= cd_size) {
        const unsigned char *rec = base + cd_off + pos;
        if (rd32(rec) != CD_SIG) break;
        count++;
        pos += 46 + rd16(rec + 28) + rd16(rec + 30) + rd16(rec + 32);
    }
    if (!count) return 0;
    z->entries = (zentry_t *)calloc(count, sizeof(zentry_t));
    if (!z->entries) return 0;
    z->count = (int)count;
    pos = 0;
    {
        int n = 0;
        while (pos + 46 <= cd_size && n < z->count) {
            const unsigned char *rec = base + cd_off + pos;
            unsigned short nlen = rd16(rec + 28);
            zentry_t *e = &z->entries[n];
            e->method = rd16(rec + 10);
            e->comp_size = rd32(rec + 20);
            e->uncomp_size = rd32(rec + 24);
            e->local_off = rd32(rec + 42);
            if (nlen < sizeof(e->name)) {
                memcpy(e->name, rec + 46, nlen);
                e->name[nlen] = 0;
            } else {
                e->name[0] = 0;
            }
            n++;
            pos += 46 + nlen + rd16(rec + 30) + rd16(rec + 32);
        }
        z->count = n;
    }
    return z->count > 0;
}

const zentry_t *zipidx_find(const zipidx_t *z, const char *name) {
    int i;
    for (i = 0; i < z->count; i++) {
        if (strcmp(z->entries[i].name, name) == 0) return &z->entries[i];
    }
    /* 大小写不敏感兜底 —— **必须整串比较**：
     * 旧实现逐字符比较后只看 *a/*b，若差异恰在最后一个字符，两个指针都
     * 已越过该字符指向 NUL → 把 .pyc 当成 .pyw/.pyd 的同名条目 ✅
     * （实测 tenacity/asyncio/__init__.pyw → 命中 .pyc → SourceFileLoader
     *   把 pyc 当源码 → SyntaxError: null bytes）。 */
    for (i = 0; i < z->count; i++) {
        if (_stricmp(z->entries[i].name, name) == 0) return &z->entries[i];
    }
    return NULL;
}

const zentry_t *zipidx_find_basename(const zipidx_t *z, const char *basename) {
    /* 按 basename（大小写不敏感 ✅）找条目 ✅ —— memdl 依赖解析用 ✅：
     * 归档里的 DLL 按目录分层（native/PySide6/pyside6.abi3.dll ✅），
     * 而 PE 导入表里只有裸 DLL 名 ✅ → 按 basename 匹配 ✅ */
    int i;
    for (i = 0; i < z->count; i++) {
        const char *a = z->entries[i].name;
        const char *slash = strrchr(a, '/');
        const char *p = slash ? slash + 1 : a;
        if (_stricmp(p, basename) == 0) return &z->entries[i];
    }
    return NULL;
}

/* 解压回调 ✅（DEFLATE 条目 ✅）—— stub 注册 ✅（zlib1.dll 的 uncompress ✅） */
static zip_inflate_fn g_inflate = NULL;

void zipidx_set_inflate(zip_inflate_fn fn) {
    g_inflate = fn;
}

/* LZMA 解压回调 ✅（method 14 = zipfile.ZIP_LZMA ✅）—— stub 注册 ✅
 * （LZMA SDK 的 LzmaDec ✅ 公有领域 ✅ —— 见 stub.c 的 cratix_lzma_decode ✅）。
 * Zstd 解压回调 ✅（method 93 = zipfile.ZIP_ZSTANDARD ✅ —— libzstd 解码器 ✅）。
 * **为什么加它们** ✅：原生二进制（.pyd/.dll ✅）DEFLATE → 换代压缩 ✅
 * （LZMA 实测 23.4 → 17.6 MB / -25%；zstd-19 19.2 MB / -18% 但解压快 70 倍 ✅ §14）
 * ——Python 侧 zipfile 原生支持 14/93 ✅ → B 形态提取 / bootstrap / 验收脚本零改动 ✅；
 * zipimport 只认 STORED/DEFLATE ✅ → **.pyc 保持 DEFLATE** ✅（payload.py 的策略表 ✅）。 */
static zip_inflate_fn g_lzma = NULL;
static zip_inflate_fn g_zstd = NULL;

void zipidx_set_lzma(zip_inflate_fn fn) {
    g_lzma = fn;
}

void zipidx_set_zstd(zip_inflate_fn fn) {
    g_zstd = fn;
}

/* 按 method 选解压回调 ✅（NULL = 未注册 ✅） */
static zip_inflate_fn pick_decoder(unsigned short method) {
    if (method == 8) return g_inflate;
    if (method == 14) return g_lzma;
    if (method == 93) return g_zstd;
    return NULL;
}

int zipidx_read(const zipidx_t *z, const zentry_t *e, unsigned char **out, size_t *out_len) {
    const unsigned char *lh;
    unsigned short nlen, elen;
    unsigned long data_off;
    unsigned char *buf;
    if (!e) return 0;
    if (e->method != 0 && e->method != 8 && e->method != 14 && e->method != 93)
        return 0; /* STORED / DEFLATE / ZIP_LZMA / ZIP_ZSTANDARD */
    if ((size_t)e->local_off + 30 > z->size) return 0;
    lh = z->base + e->local_off;
    if (rd32(lh) != LFH_SIG) return 0;
    nlen = rd16(lh + 26);
    elen = rd16(lh + 28);
    data_off = e->local_off + 30 + nlen + elen;
    if (data_off + e->comp_size > z->size) return 0;
    if (e->method != 0) {
        /* 压缩条目 ✅：压缩字节 → 解压回调 ✅（zlib1.dll / LzmaDec / libzstd ✅）
         * —— 长度取自中央目录 ✅ 缓冲管线三种方法共用 ✅ */
        unsigned char *src, *dst;
        zip_inflate_fn dec = pick_decoder(e->method);
        if (!dec) return 0;
        src = (unsigned char *)malloc(e->comp_size ? e->comp_size : 1);
        if (!src) return 0;
        memcpy(src, z->base + data_off, e->comp_size);
        dst = (unsigned char *)malloc(e->uncomp_size ? e->uncomp_size : 1);
        if (!dst) {
            free(src);
            return 0;
        }
        if (!dec(src, e->comp_size, dst, e->uncomp_size)) {
            free(src);
            free(dst);
            return 0;
        }
        free(src);
        *out = dst;
        *out_len = e->uncomp_size;
        return 1;
    }
    buf = (unsigned char *)malloc(e->comp_size ? e->comp_size : 1);
    if (!buf) return 0;
    memcpy(buf, z->base + data_off, e->comp_size);
    *out = buf;
    *out_len = e->comp_size;
    return 1;
}

void zipidx_free(zipidx_t *z) {
    if (z->entries) free(z->entries);
    z->entries = NULL;
    z->count = 0;
}
