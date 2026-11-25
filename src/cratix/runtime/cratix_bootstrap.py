"""cratix 运行期引导模块（随包运行）。

职责：
    1. 读取载荷里的 meta.json，装配"模块名 -> 载荷条目 / init 符号"解析表；
    2. 把 AOT finder 装到 sys.meta_path —— 让编译产物（ext/*.pyd）从内存加载；
    3. 注入兼容层元数据（sys.frozen / sys._cratix），供应用判定运行形态。

**源码约束**（为落在自研 AOT 编译器的 v1 子集内，M2 起本文件会被自研编译器编译）：
    - 不继承 importlib.abc.*（鸭子类型即可 —— CPython 不强制要求）；
    - 不用装饰器 / 闭包 / 生成器 / async / dataclass；
    - 只用标准库。

设计说明（为什么不用 CPython 官方扩展加载器）：
    CPython 的扩展加载器按"模块名末段"查 PyInit_xxx —— 同名末段的模块会互相冲突；
    我们用**唯一 init 符号**（PyInit_cratix_<hash>）+ 自建 loader；
    模块体在 exec_module 里执行（不能在 create_module 里执行 —— 包的 __init__
    自我导入会导致无限递归）。
"""

import importlib.util
import json
import os
import sys
import zipfile

_ZIP = None
_META = None
_TABLE = {}
_VIRTUAL_ROOT = ""


def _payload_path():
    """载荷 zip 的路径 ✅。

    onefile ✅：exe 自身就是 zip ✅（载荷追加在末尾 ✅ → zipfile 支持带前缀的 zip ✅）；
    onedir ✅：旁置的 payload.zip ✅（stub 用 CRATIX_PAYLOAD 告知 ✅）。
    """
    env = os.environ.get("CRATIX_PAYLOAD")
    if env:
        return env
    return sys.executable


def _zip():
    """打开载荷 zip ✅（只开一次 ✅ —— 反复重开要重解析中央目录 ✅）。"""
    global _ZIP
    if _ZIP is None:
        _ZIP = zipfile.ZipFile(_payload_path())
    return _ZIP


def _read_entry(name):
    return _zip().read(name)


def _load_meta():
    global _META
    if _META is None:
        _META = json.loads(_read_entry("meta.json").decode("utf-8"))
    return _META


def _virtual_prefix(fullname):
    """包的虚拟 __path__ —— 必须是 <根>/<点号转斜杠> 前缀形式，裸根会让子模块不可达。"""
    return _VIRTUAL_ROOT + "/" + fullname.replace(".", "/")


def _ensure_std_streams():
    """GUI 子系统下 Explorer 双击没有任何标准句柄 -> sys.stdout/stderr 是 None，
    应用里 logger.add(None) 会直接 TypeError。兜底成 devnull。"""
    if sys.stdout is None:
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = open(os.devnull, "w")
        except OSError:
            pass
    if sys.stdin is None:
        try:
            sys.stdin = open(os.devnull)
        except OSError:
            pass


def _compiled_marker():
    """__compiled__ 标记（冻结形态的兼容元数据）。"""
    import types

    return types.SimpleNamespace(containing_dir=_VIRTUAL_ROOT, onefile=True)


class _MemExtLoader:
    """从内存加载编译产物（M2 起生效；无编译产物时整个 finder 不会被安装）。"""

    def __init__(self, rec):
        self.rec = rec

    def create_module(self, spec):
        import cratix_loader

        mod = cratix_loader.load(self.rec["entry"], self.rec["init"], spec)
        if mod is None:
            raise ImportError("cratix: 内存加载 " + str(spec.name) + " 失败")
        return mod

    def exec_module(self, module):
        import cratix_loader

        module.__file__ = self.rec.get("virtual", self.rec["entry"])
        module.__dict__["__compiled__"] = _compiled_marker()
        if self.rec.get("kind") == "package":
            module.__path__ = [_virtual_prefix(module.__name__)]
        cratix_loader.exec_def(module)


class _AotFinder:
    """sys.meta_path 查找器：按 meta.json 的 extensions 表加载编译产物。"""

    def __init__(self, table):
        self.table = table

    def find_spec(self, fullname, path=None, target=None):
        rec = self.table.get(fullname)
        if rec is None:
            return None
        loader = _MemExtLoader(rec)
        origin = rec.get("virtual")
        spec = importlib.util.spec_from_loader(
            fullname, loader, origin=origin, is_package=(rec.get("kind") == "package")
        )
        if spec is None:
            return None
        if rec.get("kind") == "package":
            spec.submodule_search_locations = [_virtual_prefix(fullname)]
        return spec


def _install_aot_finder(table):
    """安装编译产物 finder（幂等）。"""
    for finder in sys.meta_path:
        if isinstance(finder, _AotFinder):
            finder.table = table
            return
    sys.meta_path.insert(0, _AotFinder(table))


def _apply_compat(meta):
    """注入冻结形态的兼容元数据。"""
    sys.frozen = True
    sys._cratix = {
        "kind": "cratix",
        "version": meta.get("version", ""),
        "product": meta.get("product", ""),
        "build_hash": meta.get("cratix", {}).get("build_hash", ""),
        "exe_path": sys.executable,
        "virtual_root": _VIRTUAL_ROOT,
        "payload_path": _payload_path(),
        "mode": meta.get("cratix", {}).get("mode", "onefile"),
    }


def cratix_boot():
    """stub 在解释器就绪后调用本函数（入口）。"""
    global _TABLE, _VIRTUAL_ROOT

    meta = _load_meta()
    _TABLE = {}
    for rec in meta.get("extensions", []):
        _TABLE[rec["module"]] = rec
    _VIRTUAL_ROOT = os.path.dirname(os.path.abspath(sys.executable))

    _ensure_std_streams()
    _apply_compat(meta)

    if _TABLE:
        _install_aot_finder(_TABLE)

    # 冻结形态：工作目录锚定到 exe 目录（相对路径的数据文件因此落在 exe 旁边）
    try:
        os.chdir(_VIRTUAL_ROOT)
    except OSError:
        pass

    if not sys.argv or sys.argv[0] != sys.executable:
        sys.argv = [sys.executable] + list(sys.argv[1:])

    return 0