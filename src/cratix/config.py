"""cratix.toml 配置读取与校验。

配置是**唯一**的裁剪/裁剪表来源（见 packaging/defaults.py）：
    [project]  name / version / entry
    [build]    targets / console / icon / root
    [compiler] backend / probe / scope / packages / skip
    [payload]  裁剪表（exclude_* / native_in_package / project_tops）
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "cratix.toml"


class ConfigError(Exception):
    """配置错误（用法错误 → 退出码 3）。"""


@dataclass
class Config:
    path: Path
    project_root: Path
    name: str
    version: str
    entry: str
    entry_module: str
    entry_attr: str
    targets: list[str] = field(default_factory=lambda: ["onefile"])
    console: bool = False
    icon: Path | None = None
    backend: str = "pyc"
    probe: str = "on"
    # **AOT 严格模式** ✅：任何函数回退 / 模块回退都算构建失败 ✅（CI 用 ✅）
    strict_aot: bool = False
    scope: str = "project"
    packages: list[str] = field(default_factory=list)
    skip: list[str] = field(default_factory=list)
    policy: dict = field(default_factory=dict)

    @property
    def build_dir(self) -> Path:
        return self.project_root / "build" / "cratix"

    @property
    def out_dir(self) -> Path:
        return self.project_root / "build" / "public"


def _split_entry(entry: str) -> tuple[str, str]:
    """校验 module:attr —— attr 必须是单个标识符（不做点号路径 ✅ 与 cypack 同口径 ✅）。"""
    if not entry or ":" not in entry:
        raise ConfigError(f"entry 必须是 module:attr 形式：{entry!r}")
    module, _, attr = entry.partition(":")
    if not module or not attr:
        raise ConfigError(f"entry 必须是 module:attr 形式：{entry!r}")
    if ":" in attr:
        raise ConfigError(f"entry 的 attr 不能含冒号：{entry!r}")
    if not attr.isidentifier():
        raise ConfigError(f"entry 的 attr 必须是单个标识符：{entry!r}")
    if module != module.strip() or attr != attr.strip():
        raise ConfigError(f"entry 不能含首尾空白：{entry!r}")
    return module, attr


def find_config(start: Path | None = None) -> Path | None:
    """从当前目录向上找 cratix.toml（最多 5 层）。"""
    cur = (start or Path.cwd()).resolve()
    for _ in range(5):
        candidate = cur / DEFAULT_CONFIG_NAME
        if candidate.is_file():
            return candidate
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def load_config(path: Path | None = None, overrides: dict | None = None) -> Config:
    """读取并校验配置。overrides 覆盖 CLI 显式给出的值（None 表示未指定）。"""
    overrides = overrides or {}
    if path is None:
        path = find_config()
    if path is None:
        raise ConfigError("找不到 cratix.toml（用 --config 指定）")
    path = Path(path).resolve()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 配置错误统一转成 ConfigError
        raise ConfigError(f"配置解析失败：{path}：{exc}") from exc

    project = data.get("project") or {}
    build = data.get("build") or {}
    compiler = data.get("compiler") or {}
    payload = data.get("payload") or {}

    name = str(project.get("name") or path.parent.name)
    version = str(project.get("version") or "0.0.0")
    entry = str(project.get("entry") or "")
    entry_module, entry_attr = _split_entry(entry)

    root = Path(str(build.get("root") or "."))
    project_root = (path.parent / root).resolve()
    if not project_root.is_dir():
        raise ConfigError(f"项目根不存在：{project_root}")

    targets = list(build.get("targets") or ["onefile"])
    for target in targets:
        if target not in ("onefile", "onedir"):
            raise ConfigError(f"未知构建目标：{target}（可选 onefile / onedir）")

    icon_raw = str(build.get("icon") or "")
    icon = None
    if icon_raw:
        icon = (path.parent / icon_raw).resolve()
        if not icon.is_file():
            raise ConfigError(f"图标文件不存在：{icon}")
        if icon.suffix.lower() != ".ico":
            raise ConfigError(f"图标必须是 .ico：{icon}")
        if icon.stat().st_size == 0:
            raise ConfigError(f"图标文件为空：{icon}")

    backend = str(compiler.get("backend") or "pyc")
    if backend not in ("pyc", "aot"):
        raise ConfigError(f"未知编译后端：{backend}（可选 pyc / aot）")
    probe = str(compiler.get("probe") or "on")
    if probe not in ("on", "off", "strict"):
        raise ConfigError(f"未知 probe 模式：{probe}（可选 on / off / strict）")

    cfg = Config(
        path=path,
        project_root=project_root,
        name=name,
        version=version,
        entry=entry,
        entry_module=entry_module,
        entry_attr=entry_attr,
        targets=targets,
        console=bool(build.get("console", False)),
        icon=icon,
        backend=backend,
        probe=probe,
        scope=str(compiler.get("scope") or "project"),
        packages=[str(x) for x in (compiler.get("packages") or [])],
        skip=[str(x) for x in (compiler.get("skip") or [])],
        policy=payload,
    )

    # CLI 覆盖
    if overrides.get("target"):
        cfg.targets = list(overrides["target"])
    if overrides.get("entry"):
        cfg.entry = str(overrides["entry"])
        cfg.entry_module, cfg.entry_attr = _split_entry(cfg.entry)
    if cfg.backend == "aot":
        cfg.strict_aot = bool(compiler.get("strict_aot", False))
    if overrides.get("strict_aot") is not None:
        cfg.strict_aot = bool(overrides["strict_aot"])
    if overrides.get("console") is not None:
        cfg.console = bool(overrides["console"])
    if overrides.get("icon"):
        icon = Path(str(overrides["icon"])).resolve()
        if not icon.is_file():
            raise ConfigError(f"图标文件不存在：{icon}")
        cfg.icon = icon
    if overrides.get("probe"):
        cfg.probe = str(overrides["probe"])
    if overrides.get("backend"):
        cfg.backend = str(overrides["backend"])
    return cfg