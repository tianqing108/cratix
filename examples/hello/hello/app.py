"""hello 示例入口。

被 cratix 打包成单文件 exe；运行时应打印一行并以 0 退出。
"""

import sys


def main() -> int:
    sys.stdout.write("hello from cratix" + chr(10))
    sys.stdout.flush()
    return 0