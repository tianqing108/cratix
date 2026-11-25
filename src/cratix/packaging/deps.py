"""依赖闭包：静态分析出「入口 → 需要的模块 / 数据文件 / 原生扩展」。

导入收集器沿用 scripts/build.py 的实现（importlib.import_module / __import__ 的静态识别、
相对导入解析、父包 re-export 追踪），此处抽成可复用模块并扩展：
    1. 记录数据文件（package_data、.qrc 产物等）；
    2. 记录原生扩展依赖（.pyd 的导入表 → 关联 DLL）。
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Closure:
    modules: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)  # 仓库内相对路径（POSIX）
    unresolved: set[str] = field(default_factory=set)
    top_level: set[str] = field(default_factory=set)


def _module_name_from_path(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = path.stem
    return ".".join(parts)


def _package_context(module_name: str, path: Path) -> str:
    if path.name == "__init__.py":
        return module_name
    if "." not in module_name:
        return ""
    return module_name.rsplit(".", 1)[0]


def _resolve_relative(module: str | None, level: int, package: str) -> str | None:
    if level <= 0:
        return module
    pkg_parts = package.split(".") if package else []
    # **越界判定** ✅：level-1 就是「往上走的层数」✅ —— 走到顶层之上就是
    # 非法相对导入 ✅（旧判定 `>` 差一 ✅ → `from ... import x` 超出顶层时被当成**绝对导入** ✅
    # → 可能把无关模块拉进闭包 ✅）。
    if level - 1 >= len(pkg_parts):
        return None
    base = pkg_parts[: len(pkg_parts) - (level - 1)]
    if module:
        base.extend(module.split("."))
    return ".".join(part for part in base if part)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _call_name(node.value)
        if left:
            return f"{left}.{node.attr}"
    return None


class ImportCollector(ast.NodeVisitor):
    def __init__(self, package: str):
        self.package = package
        self.imports: set[str] = set()
        # **动态 import 的别名** ✅：旧口径只认字面 importlib.import_module / __import__ ✅
        # → from importlib import import_module as im; im("pkg") 完全看不见 ✅。
        self.dynamic_names = {"importlib.import_module", "__import__"}

    def _add(self, module_name: str | None) -> None:
        if module_name:
            self.imports.add(module_name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "importlib":
                self.dynamic_names.add((alias.asname or "importlib") + ".import_module")
            self._add(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = _resolve_relative(node.module, node.level, self.package)
        if node.level == 0 and node.module in ("importlib", "builtins"):
            for alias in node.names:
                if node.module == "importlib" and alias.name == "import_module":
                    self.dynamic_names.add(alias.asname or "import_module")
                elif node.module == "builtins" and alias.name == "__import__":
                    self.dynamic_names.add(alias.asname or "__import__")
        self._add(base)
        for alias in node.names:
            if alias.name == "*":
                continue
            if base:
                self._add(f"{base}.{alias.name}")
            else:
                self._add(_resolve_relative(alias.name, node.level, self.package))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = _call_name(node.func)
        # **builtins.__import__ 也算动态导入**：旧口径只认裸 __import__ / importlib。
        if call_name == "builtins.__import__":
            call_name = "__import__"
        if call_name in self.dynamic_names:
            raw = None
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                raw = node.args[0].value
            if raw:
                if raw.startswith("."):
                    pkg = None
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        pkg = node.args[1].value
                    if pkg is None:
                        # **关键字 package= 也要认**：import_module(".x", package="pkg")
                        # 旧口径只看位置参数 → 相对导入按当前模块包解析 ✅（可能错）。
                        for kw in node.keywords:
                            if (
                                kw.arg == "package"
                                and isinstance(kw.value, ast.Constant)
                                and isinstance(kw.value.value, str)
                            ):
                                pkg = kw.value.value
                                break
                    pkg = pkg or self.package
                    level = len(raw) - len(raw.lstrip("."))
                    mod = raw[level:] or None
                    self._add(_resolve_relative(mod, level, pkg))
                else:
                    # **__import__(name, ..., level=N) 的相对形式**：旧口径只看 name
                    # 是否以 "." 开头 → level=1 的 "__import__('sub', ..., 1)"
                    # 被当成绝对导入，闭包漏/拉错模块。
                    level = 0
                    if call_name == "__import__" and len(node.args) > 4:
                        arg = node.args[4]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                            level = arg.value
                    if call_name == "__import__" and not level:
                        for kw in node.keywords:
                            if (
                                kw.arg == "level"
                                and isinstance(kw.value, ast.Constant)
                                and isinstance(kw.value.value, int)
                            ):
                                level = kw.value.value
                                break
                    if level > 0 and call_name == "__import__":
                        self._add(_resolve_relative(raw, level, self.package))
                    else:
                        self._add(raw)
        self.generic_visit(node)


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _parse_local_imports(
    path: Path,
    module_name: str,
    cache: dict | None = None,
    dirty: list[bool] | None = None,
    pre_text: dict[str, tuple[str, str]] | None = None,
) -> set[str]:
    import hashlib

    try:
        # **预读直取** ✅（A407）：文本 + sha1 已由 collect 的并行预读算好 ✅（同一份内容 ✅）。
        _pre = pre_text.get(str(path)) if pre_text is not None else None
        if _pre is not None:
            source, digest = _pre
        else:
            source = _read_text(path)
            digest = hashlib.sha1(source.encode("utf-8")).hexdigest()
        # **key 用规范拼接路径** ✅（A404）：path 已是 root.resolve() 的规范拼接 ✅
        # → resolve() 逐文件系统调用纯属重复 ✅（无符号链接时键值逐字节一致 ✅）。
        key = str(path)
        if cache is not None:
            hit = cache.get(key)
            if isinstance(hit, dict) and hit.get("sha1") == digest and isinstance(hit.get("imports"), list):
                return set(str(x) for x in hit["imports"])
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return set()
    collector = ImportCollector(package=_package_context(module_name, path))
    collector.visit(tree)
    if cache is not None:
        cache[key] = {"sha1": digest, "imports": sorted(collector.imports)}
        if dirty is not None:
            dirty[0] = True
    return collector.imports


def collect(root: Path, entry: Path, exclude_dirs: set[str] | None = None) -> Closure:
    """从 entry 出发收集仓库内模块闭包 + 涉及的文件。

    **resolve 只解一次** ✅（A404）：os.walk(root) 生成的 dirpath 已经是
    root 的规范拼接 ✅（followlinks=False 不下钻符号链接目录 ✅ root 本身入口解一次 ✅）
    → 旧口径对 ~490 个源文件逐个 `Path.resolve()`（GetFinalPathNameByHandle ✅
    实测 ~0.4 ms/次 ✅ ≈ 0.4 s/轮 ✅）纯属重复系统调用 ✅。
    """
    exclude_dirs = exclude_dirs or {".git", ".venv", "__pycache__", "build", "dist", "test", "tests", "docs"}
    root = Path(root).resolve()
    module_to_path: dict[str, Path] = {}
    # **先剪枝再扫描**：旧实现 root.rglob("*.py") 会先遍历 .venv/build/test 等
    # 数万目录，再在循环里按 exclude_dirs 丢弃 —— 实测 12k+ scandir / 6s。
    # os.walk 在进入目录前就剪掉排除项，保持原有 .py → .pyx 顺序。
    py_sources: list[Path] = []
    pyx_sources: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in exclude_dirs)
        base = Path(dirpath)
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                py_sources.append(base / filename)
            elif filename.endswith(".pyx"):
                pyx_sources.append(base / filename)
    for path in [*sorted(py_sources), *sorted(pyx_sources)]:
        name = _module_name_from_path(path, root)
        if name:
            module_to_path[name] = path

    import hashlib
    import json

    # **并行预读** ✅（A407）：BFS 逐文件串行读（本机 open ~0.4ms ✅ 161 个 ≈ 0.15s ✅）
    # → walk 出的全部 .py 预读进内存（文本 + sha1 ✅ 与 _parse_local_imports 同口径 ✅）
    # → BFS 只查内存 ✅；OSError 项回落串行读 ✅。单次 collect 一个池 ✅（每构建一次调用 ✅）。
    pre_text: dict[str, tuple[str, str]] = {}
    if len(py_sources) > 16:
        import concurrent.futures as futures

        def _preload(py: Path):
            try:
                _t = _read_text(py)
                return str(py), _t, hashlib.sha1(_t.encode("utf-8")).hexdigest()
            except OSError:
                return str(py), None, None

        with futures.ThreadPoolExecutor(min(8, (os.cpu_count() or 4) // 2)) as _pool_d:
            for key, t, d in _pool_d.map(_preload, py_sources):
                if t is not None:
                    pre_text[key] = (t, d)

    code_hash = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()
    cache_path = root / "build" / "deps_imports.json"
    cache: dict = {}
    try:
        _saved = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(_saved, dict) and _saved.get("__version__") == 1 and _saved.get("__code__") == code_hash:
            cache = _saved
    except OSError, ValueError:
        cache = {}
    dirty = [False]

    result = Closure()
    discovered: set[Path] = set()
    queue: list[Path] = [entry.resolve()]
    local_prefixes = {name.split(".", 1)[0] for name in module_to_path}

    while queue:
        current = queue.pop()
        if current in discovered:
            continue
        discovered.add(current)
        module_name = module_to_path.get(current, _module_name_from_path(current, root))
        result.modules.add(module_name)
        result.files.add(current.relative_to(root).as_posix())

        # 父包（__init__ 里的 re-export 也要跟进去）
        parts = module_name.split(".")
        for i in range(1, len(parts)):
            pkg_init = module_to_path.get(".".join(parts[:i]))
            if pkg_init and pkg_init not in discovered:
                queue.append(pkg_init)

        for imported in _parse_local_imports(current, module_name, cache, dirty, pre_text):
            if not imported:
                continue
            candidate = module_to_path.get(imported)
            if candidate:
                if candidate not in discovered:
                    queue.append(candidate)
                continue
            if "." in imported:
                parent = imported.rsplit(".", 1)[0]
                candidate = module_to_path.get(parent)
                if candidate:
                    if candidate not in discovered:
                        queue.append(candidate)
                    continue
            if imported.split(".", 1)[0] in local_prefixes:
                result.unresolved.add(imported)
            result.top_level.add(imported.split(".", 1)[0])
    if dirty[0]:
        cache["__version__"] = 1
        cache["__code__"] = code_hash
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            _tmp = cache_path.with_name(f"{cache_path.name}.tmp.{os.getpid()}")
            _tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(_tmp, cache_path)
        except OSError:
            try:
                _tmp.unlink(missing_ok=True)
            except OSError:
                pass

    return result
