#ifndef CRATIX_ZIPREAD_H
#define CRATIX_ZIPREAD_H

#include <stddef.h>

/* 最小 zip 读取器：支持 STORED（method=0 ✅）、DEFLATE（method=8 ✅）、
 * ZIP_LZMA（method=14 ✅ —— CPython zipfile.ZIP_LZMA ✅）与
 * ZIP_ZSTANDARD（method=93 ✅ —— CPython zipfile.ZIP_ZSTANDARD ✅）条目。
 * 归档以任意前缀拼接在 exe 之后时，中央目录里的偏移相对 zip 起点，
 * 因此这里以「载荷起点」为基准解析。
 * DEFLATE ✅ 需要外部提供解压回调 ✅（stub 侧用 zlib1.dll 的 uncompress ✅）——
 * LZMA/zstd ✅ 同理 ✅（stub 侧 LZMA SDK / libzstd 解码器静态链接 ✅）——
 * 没有回调时读到压缩条目**直接失败** ✅（绝不返回半截数据 ✅）。
 */
typedef struct {
    char name[300];
    unsigned long comp_size;
    unsigned long uncomp_size;
    unsigned long local_off;
    unsigned short method;
} zentry_t;

typedef struct zipidx_s {
    const unsigned char *base;
    size_t size;
    zentry_t *entries;
    int count;
} zipidx_t;

int zipidx_open(zipidx_t *z, const unsigned char *base, size_t size);
const zentry_t *zipidx_find(const zipidx_t *z, const char *name);
/* 按 basename（大小写不敏感）找条目：memdl 依赖解析按裸 DLL 名查归档 ✅ */
const zentry_t *zipidx_find_basename(const zipidx_t *z, const char *basename);
int zipidx_read(const zipidx_t *z, const zentry_t *e, unsigned char **out, size_t *out_len);
void zipidx_free(zipidx_t *z);

/* 解压回调 ✅：src（压缩字节 ✅）→ dst（容量 dst_len ✅）—— 成功返回非 0 ✅ */
typedef int (*zip_inflate_fn)(const unsigned char *src, size_t src_len, unsigned char *dst, size_t dst_len);
/* 注册解压实现 ✅（stub 在装载 zlib1.dll 之后注册 ✅） */
void zipidx_set_inflate(zip_inflate_fn fn);
/* LZMA / zstd 解压实现 ✅（stub 启动时直接注册 ✅ 解码器静态链接 ✅ 无 DLL 依赖 ✅） */
void zipidx_set_lzma(zip_inflate_fn fn);
void zipidx_set_zstd(zip_inflate_fn fn);

#endif
