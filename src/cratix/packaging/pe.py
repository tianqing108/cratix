"""PE 导入表/导出表解析（构建期用于推导原生依赖闭包）。

只依赖标准库，不引入 pefile 等第三方包。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PeInfo:
    path: Path
    machine: int = 0
    is_pe32_plus: bool = True
    image_base: int = 0
    size_of_image: int = 0
    entry_point: int = 0
    sections: list[tuple[str, int, int, int]] = field(default_factory=list)  # (name, va, vsize, raw)
    raw_sizes: dict[tuple[int, int], int] = field(default_factory=dict)  # (va, raw) -> SizeOfRawData
    imports: dict[str, list[str]] = field(default_factory=dict)  # dll -> [symbol]
    exports: dict[str, int] = field(default_factory=dict)  # name -> rva
    forwarders: dict[str, str] = field(default_factory=dict)

    def rva_to_offset(self, rva: int) -> int | None:
        # 严格按节范围换算：放宽容差会命中错误节（曾把 pyside6.abi3.dll 解析崩）
        # **VirtualSize=0 的节按 SizeOfRawData 映射**（PE 规范允许；旧口径直接判不命中
        # → 导入/导出表整段消失）。
        for _name, va, vsize, raw in self.sections:
            rsize = self.raw_sizes.get((va, raw), 0)
            # **没有文件字节的节（BSS/rsize=0/raw=0）不能映射**：旧口径会返回
            # raw+offset（raw=0 时落到文件头）→ 解析到垃圾数据。
            if not raw or rsize <= 0:
                continue
            span = min(vsize if vsize else rsize, rsize)
            if va <= rva < va + span:
                return raw + (rva - va)
        return None


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def _cstr(data: bytes, off: int) -> str:
    """读 C 字符串 ✅（缺终止符时截断 ✅ —— 绝不抛 ✅ —— DLL 表损坏不该中断构建 ✅）。"""
    if off < 0 or off >= len(data):
        return ""
    end = data.find(b"\0", off)
    if end < 0:
        end = len(data)
    return data[off:end].decode("ascii", "replace")


def parse(path: Path) -> PeInfo:
    """解析 PE；导入/导出表损坏时只记录问题，不抛异常（构建不能因单个 DLL 中断）。"""
    try:
        # **接受 str/os.PathLike**：旧实现直接 path.read_bytes() ✅ →
        # 传字符串时 AttributeError 被降级成「<解析失败>」✅（静默丢失导入信息）。
        return parse_bytes(Path(path).read_bytes(), Path(path))
    except Exception as exc:  # noqa: BLE001 — 解析失败降级为「无导入信息」
        info = PeInfo(path=Path(path))
        info.imports["<解析失败>"] = [str(exc)]
        return info


def parse_bytes(data: bytes, path: Path | None = None) -> PeInfo:
    """解析**内存中的 PE 字节** ✅：调用方多半已经把 blob 读进内存 ✅
    （native_dependencies 的 entries ✅）—— 旧口径把 blob 写临时文件再读回来 ✅
    → 每轮 48+ 次白写白读（小文件 I/O 在本机 ~0.4ms/op ✅）。"""
    label = path or Path("<memory>")
    try:
        return _parse_data(data, label)
    except Exception as exc:  # noqa: BLE001 — 解析失败降级为「无导入信息」
        info = PeInfo(path=label)
        info.imports["<解析失败>"] = [str(exc)]
        return info


def _parse_data(data: bytes, path: Path) -> PeInfo:
    pe = _u32(data, 0x3C)
    if data[pe : pe + 4] != b"PE\0\0":
        raise ValueError(f"不是 PE 文件：{path}")
    nsec = _u16(data, pe + 6)
    optsz = _u16(data, pe + 20)
    magic = _u16(data, pe + 24)
    info = PeInfo(path=path, is_pe32_plus=(magic == 0x20B), machine=_u16(data, pe + 4))
    dd = pe + 24 + (112 if info.is_pe32_plus else 96)
    info.image_base = _u64(data, dd + 24) if info.is_pe32_plus else _u32(data, dd + 28)
    info.size_of_image = _u32(data, dd + 56)
    info.entry_point = _u32(data, dd + 16)
    so = pe + 24 + optsz
    for i in range(nsec):
        b = so + i * 40
        name = data[b : b + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, rsize, raw = struct.unpack_from("<IIII", data, b + 8)
        info.sections.append((name, va, vsize, raw))
        info.raw_sizes[(va, raw)] = rsize

    def r2o(rva: int) -> int | None:
        return info.rva_to_offset(rva)

    # 导入表
    imp_rva, _imp_sz = struct.unpack_from("<II", data, dd + 8)
    if imp_rva:
        off = r2o(imp_rva)
        if off is not None:
            pos = off
            while pos + 20 <= len(data):
                fields = struct.unpack_from("<IIIII", data, pos)
                if fields == (0, 0, 0, 0, 0):
                    break
                name_off = r2o(fields[3])
                if name_off is None:
                    # **坏描述符不能终止整张表** ✅：旧口径 break ✅ →
                    # 一个损坏的 Name RVA 就让后续 DLL 依赖全部消失 ✅
                    # （实测：10 个描述符只坏第 1 个 → 解析结果 0 个 ✅）。
                    pos += 20
                    continue
                dll = _cstr(data, name_off)
                thunk = fields[4]
                symbols: list[str] = []
                t = r2o(thunk)
                if t is not None:
                    step = 8 if info.is_pe32_plus else 4
                    fmt = "<Q" if info.is_pe32_plus else "<I"
                    while t + step <= len(data):
                        v = struct.unpack_from(fmt, data, t)[0]
                        if not v:
                            break
                        if v & (1 << 63 if info.is_pe32_plus else 1 << 31):
                            symbols.append(f"#{v & 0xFFFF}")
                        else:
                            hint = r2o(v & 0x7FFFFFFF)
                            if hint is not None and hint + 2 <= len(data):
                                symbols.append(_cstr(data, hint + 2))
                        t += step
                info.imports[dll] = symbols
                pos += 20

    # 导出表
    exp_rva, _exp_sz = struct.unpack_from("<II", data, dd)
    if exp_rva:
        off = r2o(exp_rva)
        if off is not None and off + 40 <= len(data):
            # **IMAGE_EXPORT_DIRECTORY 字段偏移** ✅（40 字节 ✅）：
            # +20 NumberOfFunctions ✅ / +24 NumberOfNames ✅ /
            # +28 AddressOfFunctions ✅ / +32 AddressOfNames ✅ / +36 AddressOfNameOrdinals ✅。
            # 旧实现把 +28 当 ords ✅、+36 当 funcs ✅——**两个字段互换** ✅ →
            # ord_idx 取自函数 RVA 的低 16 位 ✅ → 多数模块越界跳过 ✅ → exports 空 ✅
            # （实测：lxml etree 偶然命中✅、builder/objectify/sax 全空✅）。
            nnames = _u32(data, off + 24)
            funcs_rva = _u32(data, off + 28)
            names_rva = _u32(data, off + 32)
            ords_rva = _u32(data, off + 36)
            nn, oo, ff = r2o(names_rva), r2o(ords_rva), r2o(funcs_rva)
            if nn and oo and ff:
                # 名字表可能越界 ✅（损坏/伪造的 DLL ✅）—— 必须按文件长度夹紧 ✅
                limit = min(nnames, max(0, (len(data) - nn) // 4))
                for i in range(limit):
                    nr = _u32(data, nn + i * 4)
                    no = r2o(nr)
                    if no is None:
                        continue
                    nm = _cstr(data, no)
                    if oo + i * 2 + 2 > len(data):
                        break
                    ord_idx = _u16(data, oo + i * 2)
                    if ff + ord_idx * 4 + 4 > len(data):
                        continue
                    f = _u32(data, ff + ord_idx * 4)
                    info.exports[nm] = f
    return info


def imported_dlls(path: Path) -> list[str]:
    return sorted(parse(path).imports.keys(), key=str.lower)
