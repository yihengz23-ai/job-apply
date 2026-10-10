#!/usr/bin/env python3
"""用真实界面（templates/ 下的模板 + static/ 下的样式和脚本）+ demo/mock.js（虚构数据）生成单文件静态演示页 demo/index.html。
界面改了以后重新跑一次：.venv/bin/python scripts/build_demo.py"""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "demo" / "index.html"


def _inline(path, tag):
    """/static/ 下的一个文件原样包成 <style> / <script>（演示页是单文件，没有 /static/ 可取）。"""
    text = (BASE / "static" / path).read_text(encoding="utf-8")
    assert f"</{tag}" not in text.lower(), f"{path} 里有 </{tag}，没法内联"
    return f"<{tag}>\n{text}</{tag}>"


def build():
    env = Environment(loader=FileSystemLoader(str(BASE / "templates")), autoescape=True)   # 和面板（Flask）一样渲染，含 include
    html = env.get_template("index.html").render()
    assert "{%" not in html and "{{" not in html and "{#" not in html, "模板没渲染干净"
    first = html.find('<script src="/static/js/')
    assert first >= 0, "找不到主脚本位置"
    html = html[:first] + '<script src="mock.js"></script>\n' + html[first:]   # 虚构数据要在所有脚本之前
    html = re.sub(r'<link href="/static/([\w/.-]+\.css)(?:\?[^"]*)?" rel="stylesheet"/>', lambda m: _inline(m.group(1), "style"), html)
    html = re.sub(r'<script src="/static/([\w/.-]+\.js)(?:\?[^"]*)?"></script>', lambda m: _inline(m.group(1), "script"), html)
    assert "/static/" not in html, "还有没内联的 /static/ 引用"
    return html.replace("<title>求职投递面板</title>", "<title>求职投递面板 · 演示版</title>", 1)


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print("已生成", OUT)
