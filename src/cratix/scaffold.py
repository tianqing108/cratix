"""项目脚手架 ✅（`cratix init` ✅）。

生成一个**最小可用**的 cratix 项目 ✅：
    <dir>/cratix.toml     配置 ✅
    <dir>/<pkg>/__init__.py
    <dir>/<pkg>/app.py    入口 ✅
    <dir>/README.md       构建与运行说明 ✅
"""

from __future__ import annotations

from pathlib import Path

TOML_TEMPLATE = """
[project]
name = "{name}"
version = "0.1.0"
entry = "{pkg}.app:main"

[build]
targets = ["onefile", "onedir"]
console = true

[compiler]
backend = "pyc"
probe = "on"

[payload]
exclude_modules = []
exclude_stdlib = []
exclude_packages = []
"""

# **注意** ✅：模板里含 docstring ✅ —— 外层用单引号 ✅（避免三引号嵌套 ✅ ✅）。

def scaffold(target: Path, name: str | None = None, package: str | None = None) -> list:
    """生成项目骨架 ✅ —— 返回创建的文件列表 ✅。"""
    target = Path(target)
    name = name or target.name or "demo"
    package = package or "app"
    pkg_dir = target / package
    pkg_dir.mkdir(parents=True, exist_ok=True)

    files = []
    toml_path = target / "cratix.toml"
    toml_path.write_text(
        TOML_TEMPLATE.format(name=name, pkg=package), encoding="utf-8"
    )
    files.append(toml_path)

    init_path = pkg_dir / "__init__.py"
    init_path.write_text(INIT_PY.format(pkg=package), encoding="utf-8")
    files.append(init_path)

    app_path = pkg_dir / "app.py"
    app_path.write_text(APP_PY.format(pkg=package), encoding="utf-8")
    files.append(app_path)

    readme_path = target / "README.md"
    readme_path.write_text(README.format(name=name), encoding="utf-8")
    files.append(readme_path)

    return files
INIT_PY = '"""' + "{pkg} 示例包。" + '"""' + "\n"

APP_PY = '"""' + "{pkg} 的入口 ✅ —— 被打包成 exe ✅。" + '"""' + """

import sys


def greet(name: str = "world") -> str:
    return f"hello, {{name}}"


def main() -> int:
    sys.stdout.write(greet() + "\\n")
    return 0
"""

README = """
# {name}

## 构建

```powershell
$env:PYTHONPATH = "<cratix 仓库>/src"
python -m cratix build --config cratix.toml
```

产物在 `build/public/` ✅：`{name}_0.1.0.exe`（单文件 ✅）与 `{name}_0.1.0/`（目录版 ✅）。

## 运行

```powershell
./build/public/{name}_0.1.0.exe
```

## 验收

```powershell
python -m cratix verify --config cratix.toml --zero-disk
```
"""