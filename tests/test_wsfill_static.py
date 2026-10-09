"""wsfill.js（网申填表脚本）的静态检查：语法、版本号、给助手用的分页接口还在。真正的行为测试在 tests/wsfill_fixtures/（要浏览器）。"""
import re
import shutil
import subprocess

import pytest

from jobapply import config

JS = config.BASE_DIR / "wsfill.js"


def test_wsfill_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("没装 node")
    r = subprocess.run([node, "--check", str(JS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_wsfill_exports_and_version():
    t = JS.read_text(encoding="utf-8")
    assert re.search(r"const VERSION = '\d+\.\d+';", t)
    exports = re.search(r"window\.__wsfill = \{([^}]*)\}", t).group(1)
    for name in ("scan", "fill", "start", "progress", "view", "more", "opts", "peekText", "job", "fillText", "clickAdd",
                 "snapshot", "snapshotText", "readback", "mark"):
        assert re.search(rf"\b{name}\b", exports), name


def test_wsfill_readback_is_read_only_and_hides_ids():
    """读回只读页面：不点击、不改值；发回面板前证件号打码；发到面板的读回接口。"""
    t = JS.read_text(encoding="utf-8")
    body = t[t.index("function snapshot()"):t.index("window.__wsfill =")]
    assert ".click(" not in body and "setNative(" not in body and "fillOne(" not in body
    assert "hideIds(text)" in body and "/api/wsreadback/" in body and "'Content-Type': 'text/plain'" in body


def test_wsfill_never_clicks_submit():
    t = JS.read_text(encoding="utf-8")
    assert "SUBMIT_WORDS" in t and "这是提交类按钮，不点" in t
    assert "证件号码由本人自己填" in t
