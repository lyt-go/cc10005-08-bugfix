"""把每个路由文件的 GET /export 移到 GET /{entry_id} 之前（幂等）。

FastAPI 按注册顺序匹配，/{entry_id} 在前会把字面量 "export" 当成 entry_id 解析，
导致导出入口 422。该脚本只调整函数定义顺序，不改任何业务代码。
"""
from __future__ import annotations

import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    for path in sorted(glob.glob(os.path.join(ROOT, "app", "routers", "*.py"))):
        src = open(path, encoding="utf-8").read()
        lines = src.splitlines(keepends=True)
        export_idx = next((i for i, line in enumerate(lines) if line.strip().startswith('@router.get("/export")')), None)
        entry_idx = next((i for i, line in enumerate(lines) if line.strip().startswith('@router.get("/{entry_id}"')), None)
        if export_idx is None or entry_idx is None:
            continue
        name = os.path.basename(path)
        if export_idx < entry_idx:
            print(f"skip (ordered): {name}")
            continue
        block = lines[export_idx:]
        while block and block[-1].strip() == "":
            block.pop()
        before = lines[:export_idx]
        while before and before[-1].strip() == "":
            before.pop()
        new_lines = before[:entry_idx] + block + ["\n", "\n", "\n"] + before[entry_idx:]
        open(path, "w", encoding="utf-8").write("".join(new_lines))
        check = open(path, encoding="utf-8").read()
        assert check.index('@router.get("/export")') < check.index('@router.get("/{entry_id}"'), name
        print(f"fixed: {name}")


if __name__ == "__main__":
    main()
