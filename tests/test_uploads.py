"""网申上传文件夹：照片按常见规格各做一份、简历和研究样本放进去、助手只许读这个文件夹。用 sips（macOS 自带），不调 AI。"""

import shutil
import subprocess

import pymupdf
import pytest

from jobapply import agent, config, resume, uploads

pytestmark = pytest.mark.skipif(not shutil.which("sips"), reason="没有 sips（不是 macOS）")


def _img(path, w, h):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), 0)
    pix.set_rect(pix.irect, (120, 120, 120))
    pix.save(str(path))


@pytest.fixture
def up(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "DIR", tmp_path / "网申上传")
    report = tmp_path / "研究样本.pdf"
    report.write_bytes(b"%PDF-1.4 sample")
    monkeypatch.setattr(config, "REPORT_PATH", report)
    monkeypatch.setattr(resume, "build_resume_files", lambda version, name, *a: [(name, b"%PDF-1.4 zh")])
    return uploads


def test_prepare_without_photos_lists_resume_and_says_photos_missing(up):
    text = up.prepare()
    names = sorted(p.name for p in up.DIR.iterdir())
    assert set(names) == {config.RESUME_DEFAULT_ZH.removesuffix(".pdf") + ".pdf", "研究样本.pdf"}
    assert "还没有证件照" in text and "还没有生活照" in text and str(up.DIR / "研究样本.pdf") in text


def test_photos_get_common_sizes_and_are_redone_only_when_changed(up):
    up.DIR.mkdir(parents=True)
    _img(up.DIR / "我的证件照.png", 400, 600)      # 比一寸更瘦长：从下面裁
    _img(up.DIR / "生活照.png", 1600, 1200)        # 横的、比 1200 大：只缩不裁
    text = up.prepare()
    g = uploads.GEN
    assert up._dims(up.DIR / f"证件照_295x413{g}.jpg") == (295, 413)
    assert up._dims(up.DIR / f"证件照_100x140{g}.jpg") == (100, 140)
    assert up._dims(up.DIR / f"证件照_长边1200{g}.jpg") == (400, 600)          # 小图不放大
    assert up._dims(up.DIR / f"生活照_长边1200{g}.jpg") == (1200, 900)
    assert up._dims(up.DIR / f"生活照_长边320{g}.jpg") == (320, 240)
    assert "还没有" not in text and "295×413 像素" in text
    assert set(up.masters()) == {"证件照", "生活照"} and up.masters()["证件照"].name == "我的证件照.png"   # 自动生成的不算
    before = (up.DIR / f"证件照_295x413{g}.jpg").stat().st_mtime
    up.prepare()
    assert (up.DIR / f"证件照_295x413{g}.jpg").stat().st_mtime == before        # 没换照片：不重做


def test_assistant_can_only_read_the_upload_folder(up, monkeypatch):
    allow, deny = up.read_rules()
    assert allow == [f"Read(/{up.DIR}/**)"] and f"Read(/{config.BASE_DIR}/**)" in deny      # 面板代码和密钥不许读
    monkeypatch.setattr(agent, "system_prompt", lambda *a: "SYS")
    args = agent._args({"session_id": ""})
    assert args[args.index("--tools") + 1] == "Read" and args[args.index("--add-dir") + 1] == str(up.DIR)
    assert allow[0] in args and f"Read(/{config.BASE_DIR}/**)" in args
    assert args.index(allow[0]) < args.index("--disallowedTools") < args.index(f"Read(/{config.BASE_DIR}/**)")
