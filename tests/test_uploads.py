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
    assert "还没有证件照" in text and "还没有生活照" in text and "还没有全身照" in text and str(up.DIR / "研究样本.pdf") in text


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
    assert (up.DIR / f"生活照_1M到5M{g}.jpg").exists() and f"生活照_1M到5M{g}.jpg" in text   # 要求 1M 以上的网站用这份
    assert not list(up.DIR.glob(f"证件照_1M到5M*"))                                       # 证件照不做（一寸照要的是小文件）
    assert "还没有证件照" not in text and "还没有生活照" not in text and "295×413 像素" in text   # 全身照没放：只提醒这一样
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


def test_full_body_photo_is_its_own_kind(up):
    """「生活照-全身」算全身照（不顶替半身的生活照）；两种都做常用规格。"""
    up.DIR.mkdir(parents=True)
    _img(up.DIR / "生活照-全身.png", 600, 1200)
    _img(up.DIR / "生活照.png", 900, 1200)
    text = up.prepare()
    m = up.masters()
    assert m["全身照"].name == "生活照-全身.png" and m["生活照"].name == "生活照.png"
    g = uploads.GEN
    for name in (f"全身照_长边1200{g}.jpg", f"全身照_长边320{g}.jpg", f"全身照_1M到5M{g}.jpg", f"生活照_1M到5M{g}.jpg"):
        assert (up.DIR / name).exists(), name
    assert "还没有全身照" not in text and "还没有生活照" not in text


@pytest.mark.parametrize("sizes,expect_calls,expect_mb", [
    ({3200: 2.0}, [3200], 2.0),                                       # 一次就落在中间
    ({3200: 0.8, 4000: 1.3}, [3200, 4000], 1.3),                      # 小了：放大再试
    ({3200: 6.0, 2400: 3.0}, [3200, 2400], 3.0),                      # 大了：缩小再试
    ({3200: 0.5, 4000: 0.7, 4800: 0.9}, [3200, 4000, 4800], 0.9),     # 都不够：留最接近的
    ({3200: 0.9, 4000: 0.6, 4800: 0.4}, [3200, 4000, 4800, 3200], 0.9),   # 最接近的不是最后一份：换回去
])
def test_band_lands_between_1m_and_5m(up, monkeypatch, sizes, expect_calls, expect_mb):
    """「1M≤文件≤5M」：长边 3200 起试，小了往大、大了往小，落进去就停；都不行留最接近的。"""
    calls = []

    def fake(*args):
        args = [str(a) for a in args]
        edge = int(args[args.index("-Z") + 1])
        calls.append(edge)
        up.DIR.joinpath("out.jpg").write_bytes(b"x" * int(sizes[edge] * up.MB))
    monkeypatch.setattr(up, "_sips", fake)
    up.DIR.mkdir(parents=True)
    up._band(up.DIR / "src.jpg", up.DIR / "out.jpg")
    assert calls == expect_calls and (up.DIR / "out.jpg").stat().st_size == int(expect_mb * up.MB)
