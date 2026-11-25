#ifndef CRATIX_VFS_H
#define CRATIX_VFS_H

/* vfs —— 虚拟文件系统（M4 / D1 ✅）
 *
 * 让 Qt（以及任何按路径读文件的代码）看到「虚拟路径 <exe>\...」并真的能读到内容：
 *   - exe 目录前缀的路径 → 归档条目 / 归档目录 ✅
 *   - 其余路径 → **透传真实文件系统** ✅（字体 / %APPDATA% / System32 ✅）
 *   - 伪 HANDLE：内存块实现（CreateFileW/NtCreateFile 返回 ✅ ReadFile 从内存拷 ✅）
 *   - 目录枚举 ✅（QPluginLoader 找插件依赖 ✅ FindFirstFileExW/FindNextFileW ✅）
 *   - 内存映射 ✅（Qt 读插件元数据走 CreateFileMappingW + MapViewOfFile ✅）
 *
 * 对外的挂接方式（stub 侧 ✅）：
 *   vfs_init(&zip, exe_dir_w) 在 zip 解析后调用 ✅；
 *   Qt6Core 的 IAT 按 vfs_kernel_redirects()/vfs_nt_redirects() 的清单重定向 ✅
 *   （见方案 §6.4 ✅）。
 */
#define _WIN32_WINNT 0x0A00 /* FILE_ATTRIBUTE_TAG_INFORMATION / FileBasicInfo 需要 ✅ */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stddef.h>
#include "zipread.h" /* zipidx_t ✅（前置声明与 typedef 匿名结构冲突 ✅ 直接引入 ✅） */

#ifdef __cplusplus
extern "C" {
#endif

/* zip 解析后、任何 Qt 模块映射前调用 ✅
 * exe_path 也传入 ✅ —— Qt 的插件路径把 **exe 路径当目录前缀** 用 ✅（M6c ✅）。 */
void vfs_init(struct zipidx_s *zip, const WCHAR *exe_dir, const WCHAR *exe_path);

/* KERNEL32 重定向清单 ✅（名字数组 + 对应的 vfs 实现 ✅ stub 用 mem_redirect_import 挂 ✅） */
const char *const *vfs_kernel_redirect_names(void);
const void *const *vfs_kernel_redirect_funcs(void);
int vfs_kernel_redirect_count(void);

/* NTDLL 重定向清单 ✅ */
const char *const *vfs_nt_redirect_names(void);
const void *const *vfs_nt_redirect_funcs(void);
int vfs_nt_redirect_count(void);

/* CRT 重定向清单 ✅（_wstat64i32 ✅ —— Python 3.14 的 os.stat 走它 ✅） */
const char *const *vfs_crt_redirect_names(void);
const void *const *vfs_crt_redirect_funcs(void);
int vfs_crt_redirect_count(void);

/* 伪 HANDLE 判定 ✅（非伪句柄一律透传真实 API ✅） */
int vfs_is_pseudo_handle(HANDLE h);

/* 复制伪 HANDLE（os.dup 用）：仅文件句柄；共享条目缓存。失败返回 NULL。 */
HANDLE vfs_dup_handle(HANDLE h);

/* GetFileInformationByName 的 VFS 实现 ✅（Python 3.14 os.stat 快路径 ✅
 * —— 经 stub 的 cratix_GetProcAddress 拦截挂入 ✅）。 */
void *vfs_file_information_by_name(void);

#ifdef __cplusplus
}
#endif
#endif
