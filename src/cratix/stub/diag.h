#ifndef CRATIX_DIAG_H
#define CRATIX_DIAG_H

/* 崩溃诊断 ✅：解释器镜像（手工映射 ✅）不在 PEB 加载器表里 ✅ →
 * ntdll 的展开器找不到它的 .pdata ✅ → RtlCaptureStackBackTrace 只能看到一帧 ✅
 * → 栈里的真实调用链看不到 ✅。这里手动扫栈 ✅（按返回地址落在解释器镜像里统计 ✅）
 * → 递归一眼可见 ✅。 */
#include <windows.h>
#include <stddef.h>

void cratix_diag_set_main_tid(unsigned tid);
void cratix_diag_scan(void *rsp, const unsigned char *ibase, size_t isize);
void cratix_diag_lookup(const void *pc);
void cratix_diag_modhandle(const void *pc);

#endif
