# notes 示例 ✅（真实一点的应用 ✅）

一个 **命令行笔记** 示例 ✅ —— 刻意用上 AOT 支持的多种特性 ✅，
用来**端到端**检验工具链（不只是 hello world ✅）。

## 用法

```bash
cd examples/notes
python -m cratix build --config cratix.toml --backend aot --strict-aot
./build/public/notes_0.2.0.exe --store demo.json add "写文档" --category work
./build/public/notes_0.2.0.exe --store demo.json add "买牛奶"
./build/public/notes_0.2.0.exe --store demo.json done 0
./build/public/notes_0.2.0.exe --store demo.json list
./build/public/notes_0.2.0.exe --store demo.json stats
```

## 用到的特性 ✅

- 类 + 属性槽位 ✅（`Note` / `Store` / `Options`）
- **继承内置异常** ✅（`NoteError(Exception)` ✅）+ `super().__init__` ✅
- `@classmethod` 装饰方法 ✅
- f-string ✅ / 生成器表达式 ✅ / 列表推导 ✅
- `dict.get` / `str.join` / `sorted` ✅
- JSON 读写 ✅ / 文件系统 ✅

## 它抓到的两个真实缺口 ✅

写这个示例的价值就在这 ✅ —— 它一上来的两次构建/运行各自踩中一个**真实缺陷** ✅：

1. **同一个函数里两次"带关键字的方法调用"** ✅ -> 生成的 C 里 `cratix_kn` **重复声明** ✅（C2374 ✅）✅
   已修 ✅（改成临时变量 ✅）。
2. **打包后的 exe 收不到命令行参数** ✅ —— stub 的 `main(void)` 根本没接 argv ✅，
   `sys.argv[1:]` 是空的 ✅。**这条还没修** ✅（见 `docs/ROADMAP.md` ✅）。

## 已知限制 ✅

- 差分探针会忽略 `cratix_*` / `_cratix_*` 前缀的内部名字 ✅（见 `compiler/probe.py` ✅）；
- **带关键字的方法调用已修** ✅（详见 `docs/aot-pitfalls.md` #128 ✅）；
- 打包后的 exe **还收不到命令行参数** ✅（`docs/aot-pitfalls.md` #127 ✅）——
  所以这个示例目前只能用**默认参数**跑 ✅，命令行参数要等 stub 接上 argv ✅。
