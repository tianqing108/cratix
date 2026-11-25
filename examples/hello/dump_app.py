import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, "../../src")
from cratix.compiler import driver

mc = driver.compile_module(Path("hello/app.py"), "hello.app")
lines = mc.c_source.splitlines()
for i, line in enumerate(lines):
    if "PyImport_Import" in line or "cratix_run_body" in line or "cratix_exec" in line or "PyModule_New" in line or "PyModule_AddFunctions" in line or "PyDict_SetItemString(cratix_globals" in line:
        print(i, line)