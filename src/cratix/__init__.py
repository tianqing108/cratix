"""cratix —— 自研 Python 打包工具。

子系统：
    compiler/   自研 AOT 编译器（Python 源码 → 原生扩展）
    packaging/  打包半边（vendor 自 cypack：依赖闭包 / 载荷 / 拼装 / stub）
    build/      构建编排与报告
    runtime/    随包运行的引导模块（单一 Python 源码，可被自研编译器编译）
"""

__version__ = "0.1.0"