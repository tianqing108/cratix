#ifndef CRATIX_TLSFIX_H
#define CRATIX_TLSFIX_H

/* 静态 TLS 修补 ✅ —— 让**手工映射**的模块也能拿到 per-thread 数据 ✅
 *
 * 背景（x64 事实 ✅）：
 *   静态 TLS 的访问序列是 ✅：
 *       mov  ecx, [rip+X]        ; 模块**自己**的 TLS 索引 ✅（运行时可改 ✅）
 *       mov  rax, gs:[0x58]      ; 线程的 TLS 指针数组 ✅（TEB+0x58 ✅）
 *       mov  rax, [rax+rcx*8]    ; 该模块的 per-thread 数据块 ✅
 *   加载器只为**自己加载的**模块分配索引与块 ✅（实测 ✅：索引按模块顺序 ✅，
 *   中间槽位为 NULL ✅）→ 手工映射的模块不在加载器表里 ✅ → 拿不到块 ✅。
 *
 * 做法 ✅：
 *   1) 每个模块登记一份 TLS 模板 ✅ → 拿到**我们自己的**索引 k ✅（1 起 ✅）；
 *   2) 写模块的 AddressOfIndex = k ✅（原代码读的就是它 ✅）；
 *   3) 把「取加载器数组」那条指令换成「取我们自己的每线程表」✅
 *      → 后续那条按索引取值的指令语义**完全不变** ✅ → table[k] ✅；
 *   4) 表是**每线程**的 ✅（Win32 TLS 槽 ✅）→ 新线程自动有一份 ✅
 *      （首次访问时按模板拷贝 ✅ —— 与加载器语义一致 ✅）。
 */
#include <windows.h>
#include <stddef.h>

#define CRATIX_TLS_MAX_MODULES 64

/* 登记一个带静态 TLS 的模块 ✅ → 返回它在表里的索引 ✅（1 起 ✅，0 = 失败 ✅） */
int cratix_tls_register(const unsigned char *tmpl, size_t size);
/* 当前线程的表 ✅（首次调用时懒分配 ✅） */
void *cratix_tls_table(void);
/* 汇编助手 ✅（每个目标寄存器一个 ✅ —— 只能破坏目标寄存器 ✅）—— */
void cratix_tls_asm_0(void);
void cratix_tls_asm_1(void);
void cratix_tls_asm_2(void);
void cratix_tls_asm_3(void);
void cratix_tls_asm_4(void);
void cratix_tls_asm_5(void);
void cratix_tls_asm_6(void);
void cratix_tls_asm_7(void);
void cratix_tls_asm_8(void);
void cratix_tls_asm_9(void);
void cratix_tls_asm_10(void);
void cratix_tls_asm_11(void);
void cratix_tls_asm_12(void);
void cratix_tls_asm_13(void);
void cratix_tls_asm_14(void);
void cratix_tls_asm_15(void);
/* 旧名字（MASM ✅ —— **名字必须与 C 函数区分** ✅：
 * 同名会让链接器把未引用的 MASM 符号丢掉 ✅ → 补丁的 call 落空 ✅） */
void *cratix_tls_table_asm(void);
/* 汇编助手调用的 C 实现 ✅（返回表指针 ✅） */
void *cratix_tls_table_c(void);
/* 按索引取当前线程的数据块 ✅ */
void *cratix_tls_block(unsigned int index);
/* 汇编助手探针 ✅（诊断用 ✅）—— 调用次数 ✅ / 返回值 ✅ / 调用点返回地址 ✅ */
extern volatile long long g_tlsfix_asm_calls;
extern volatile long long g_tlsfix_asm_ret;
extern volatile long long g_tlsfix_retaddr;
/* 补丁点上的标志位 ✅（诊断用 ✅） */
extern volatile long long g_tlsfix_saved_flags;
extern volatile long long g_tlsfix_flags_after;
/* 修补模块里的静态 TLS 访问点 ✅ → 返回改了多少处 ✅ */
int cratix_tls_patch(BYTE *base, const IMAGE_NT_HEADERS64 *nt);

#endif
