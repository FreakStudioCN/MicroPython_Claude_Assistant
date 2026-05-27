#!/usr/bin/env python3
"""安装 git pre-commit hook：每次提交前自动扫描依赖并更新 pyproject.toml。"""
import os
import sys
import stat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(ROOT, ".git", "hooks", "pre-commit")

HOOK_CONTENT = """\
#!/bin/sh
python scripts/update_deps.py
if git diff --quiet pyproject.toml; then
    exit 0
fi
git add pyproject.toml
echo "[pre-commit] pyproject.toml 依赖已更新并暂存"
"""

def main():
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        print("[错误] 当前目录不是 git 仓库")
        sys.exit(1)
    with open(HOOK_PATH, "w", newline="\n") as f:
        f.write(HOOK_CONTENT)
    os.chmod(HOOK_PATH, os.stat(HOOK_PATH).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[ok] pre-commit hook 已安装: {HOOK_PATH}")

if __name__ == "__main__":
    main()
