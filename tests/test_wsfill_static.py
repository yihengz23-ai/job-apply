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


def test_id_field_left_alone_hasvalue_only_says_filled_or_empty():
    """证件号那一栏本人自己手动填：脚本里没有往里放光标、标框的 focusField 了；hasValue（只在网站不填就不让往下时用）
    只回「已填 / 还在输 / 空 / 找不到」，不写值、不回内容、不碰这一栏；fillOne 照样拒绝写证件号，清空也不碰，不标框。"""
    t = JS.read_text(encoding="utf-8")
    exports = re.search(r"window\.__wsfill = \{([^}]*)\}", t).group(1)
    assert "focusField" not in t and re.search(r"\bhasValue\b", exports)
    body = t[t.index("function inputOf(id)"):t.index("function mark(id, ok)")]
    assert "setNative(" not in body and ".value =" not in body and "fillOne(" not in body and "press(" not in body
    assert "focusOn(" not in body and "outline" not in body and "click(" not in body
    has = body[body.index("function hasValue(id)"):]
    for expr in re.findall(r"return ([^;]+);", has):                      # 回的只有这几个字，号码出不去
        rest = re.sub(r"'(已填|还在输|空|找不到)'", "", expr)
        assert re.fullmatch(r"[\s()&|?:]*(?:(?:changed|focused|short)[\s()&|?:]*)*", rest), expr
    assert "证件号码由本人自己填" in t and "ID_LABEL.test(labelOf(el))" in t
    clear = t[t.index("async function clear(id)"):t.index("async function clear(id)") + 400]
    assert "ID_LABEL.test(" in clear                                      # 清空也不碰证件号
    assert t.count("if (r.reason !== ID_REFUSE) mark(step.id, r.ok)") == 2   # fill、start 都不给它标框


def test_view_shows_field_hints_and_length_limits():
    """输入框里的灰字常常就是要求（招商「可用5个词描述你的性格」）：view 里照列，字数上限也列；「请输入姓名」这种不列。"""
    t = JS.read_text(encoding="utf-8")
    view = t[t.index("function view("):t.index("async function opts(id)")]
    assert "〔提示：" in view and "〔≤${f.maxlength}字〕" in view and "请(输入|填写|选择)" in view
    assert "f.maxlength = el.maxLength" in t
