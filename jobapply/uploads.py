"""网申要上传的材料：桌面「自动投递/网申上传」文件夹。面板助手只能读这个文件夹，网站要传照片、简历时它自己挑着传。

本人只管往里放两张固定的照片（文件名带「证件照」「生活照」）；单页中文简历、研究样本由面板放进去（简历换了跟着更新）。
网站对照片尺寸 / 大小常有要求（比如有的银行要证件照 100×140、30KB 以内），面板按常见规格各做一份，助手照网站写的要求挑。"""

import re
import subprocess
from pathlib import Path

from . import config, resume

DIR = config.MATERIALS_DIR / "网申上传"
GEN = "（自动生成）"                                  # 面板做的文件名都带这个；本人放的照片不带
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".heic")
ID_SIZES = ((295, 413), (100, 140))                   # 一寸（5:7）；小尺寸（银行等老规格）


def _sips(*args):
    subprocess.run(["sips", *map(str, args)], capture_output=True, check=True)


def _dims(path):
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], capture_output=True, text=True).stdout
    w, h = re.search(r"pixelWidth: (\d+)", out), re.search(r"pixelHeight: (\d+)", out)
    return (int(w.group(1)), int(h.group(1))) if w and h else (0, 0)


def masters():
    """本人放的照片：文件名带「证件」「生活」的（面板自己做的不算），每样取第一张。"""
    out = {}
    for p in sorted(DIR.iterdir()) if DIR.exists() else []:
        if p.suffix.lower() not in PHOTO_EXT or GEN in p.name or p.name.startswith("."):
            continue
        kind = "证件照" if "证件" in p.name else "生活照" if "生活" in p.name else ""
        if kind and kind not in out:
            out[kind] = p
    return out


def _fresh(out, src):
    return out.exists() and out.stat().st_mtime >= src.stat().st_mtime


def _photo_variants(kind, src):
    """证件照：裁成一寸的 5:7 竖版（太宽左右各裁一点，太高从下面裁，头顶不动），出 295×413、100×140 两种；
    两种照片都再出一份长边 1200 的 JPG（原图太大、或者是 HEIC / PNG 时用）。源照片换了才重做。"""
    jobs = [(f"{kind}_长边1200{GEN}.jpg", None)]
    if kind == "证件照":
        jobs += [(f"证件照_{w}x{h}{GEN}.jpg", (w, h)) for w, h in ID_SIZES]
    else:
        jobs += [(f"生活照_长边320{GEN}.jpg", None)]
    for name, size in jobs:
        out = DIR / name
        if _fresh(out, src):
            continue
        tmp = DIR / f".tmp_{kind}.jpg"
        try:
            _sips("-s", "format", "jpeg", src, "--out", tmp)
            if size:
                w, h = size
                sw, sh = _dims(tmp)
                if sw * h > sh * w:
                    cw = round(sh * w / h)
                    _sips("--cropToHeightWidth", sh, cw, "--cropOffset", 0, (sw - cw) // 2, tmp, "--out", tmp)
                elif sw * h < sh * w:
                    _sips("--cropToHeightWidth", round(sw * h / w), sw, "--cropOffset", 0, 0, tmp, "--out", tmp)
                _sips("-z", h, w, "-s", "formatOptions", 85, tmp, "--out", out)
            else:
                cap = 320 if "长边320" in name else 1200
                if max(_dims(tmp)) > cap:   # 只缩不放大
                    _sips("-Z", cap, tmp, "--out", tmp)
                _sips("-s", "formatOptions", 85, tmp, "--out", out)
        finally:
            tmp.unlink(missing_ok=True)


def _copy_if_changed(blob, dest):
    if not dest.exists() or dest.read_bytes() != blob:
        dest.write_bytes(blob)


def prepare():
    """放好要上传的材料（简历、研究样本、照片的各种规格），返回给助手看的清单文字。出错不影响助手干活。"""
    DIR.mkdir(parents=True, exist_ok=True)
    notes = []
    try:
        name = re.sub(r"\.pdf$", "", config.RESUME_DEFAULT_ZH, flags=re.I) + ".pdf"
        for fname, blob in resume.build_resume_files("中文", name):
            _copy_if_changed(blob, DIR / fname)
    except Exception as e:  # 简历文件有问题：清单里照实写
        notes.append(f"（单页中文简历没放进去：{e}）")
    if config.REPORT_PATH.exists():
        _copy_if_changed(config.REPORT_PATH.read_bytes(), DIR / config.REPORT_PATH.name)
    found = masters()
    for kind, src in found.items():
        try:
            _photo_variants(kind, src)
        except (subprocess.CalledProcessError, OSError) as e:
            notes.append(f"（{kind}的各种规格没做出来：{e}）")
    for kind in ("证件照", "生活照"):
        if kind not in found:
            notes.append(f"（还没有{kind}：本人还没往文件夹里放，要传{kind}的话跳过，收尾时请本人自己传）")
    return listing() + ("\n" + "\n".join(notes) if notes else "")


def listing():
    lines = []
    for p in sorted(DIR.iterdir()) if DIR.exists() else []:
        if p.name.startswith(".") or not p.is_file():
            continue
        kb = max(1, round(p.stat().st_size / 1024))
        if p.suffix.lower() in PHOTO_EXT:
            w, h = _dims(p)
            lines.append(f"- {p} （{w}×{h} 像素，{kb}KB）")
        else:
            lines.append(f"- {p} （{kb}KB）")
    return "\n".join(lines) or "（文件夹是空的）"


def read_rules():
    """助手的读文件权限：只许读这个文件夹（上传要用）；面板代码 / 密钥、vault、系统目录一律不许读。"""
    allow = [f"Read(/{DIR}/**)"]
    deny = [f"Read(/{config.BASE_DIR}/**)"] + [f"Read(/{Path.home()}/{d}/**)" for d in ("vault", ".claude", ".ssh", ".config", "Library")]
    return allow, deny
