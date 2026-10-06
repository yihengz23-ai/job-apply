#!/usr/bin/env python3
"""用真实界面 templates/index.html + demo/mock.js（虚构数据）生成静态演示页 demo/index.html。
界面改了以后重新跑一次：.venv/bin/python scripts/build_demo.py"""

from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
html = (BASE / "templates" / "index.html").read_text(encoding="utf-8")
marker = "<script>\n// ━━━ 工具 ━━━"
assert marker in html, "找不到主脚本位置"
html = html.replace(marker, '<script src="mock.js"></script>\n' + marker, 1)
html = html.replace("<title>求职投递面板</title>", "<title>求职投递面板 · 演示版</title>", 1)
(BASE / "demo" / "index.html").write_text(html, encoding="utf-8")
print("已生成", BASE / "demo" / "index.html")
