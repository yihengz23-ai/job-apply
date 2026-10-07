"""简历文件：识别中/英文页、检查毕业时间、按版本生成附件。"""

import hashlib
import re

import pymupdf

from . import config

_CJK = re.compile(r"[一-鿿]")


def _page_lang(text):
    letters = len(re.findall(r"[A-Za-z]", text))
    cjk = len(_CJK.findall(text))
    return "中文" if cjk > letters * 0.15 else "英文"


def resume_status(path=None):
    """返回简历文件状态：页数、中英文页、毕业时间是否正确、指纹。"""
    path = path or config.RESUME_PATH
    if not path.exists():
        return {"ok": False, "error": f"找不到简历：{path}"}
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        return {"ok": False, "error": f"简历打不开：{e}"}
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text()
        pages.append({"index": i, "lang": _page_lang(text), "text": text})
    zh = [p["index"] for p in pages if p["lang"] == "中文"]
    en = [p["index"] for p in pages if p["lang"] == "英文"]
    problems = []
    for lang, must in config.RESUME_MUST_CONTAIN.items():
        lang_pages = [p for p in pages if p["lang"] == lang]
        if lang_pages and not any(must in p["text"] for p in lang_pages):
            problems.append(f"{lang}页没有「{must}」")
    raw = path.read_bytes()
    return {
        "ok": True, "path": str(path), "pages": len(pages), "zh_pages": zh, "en_pages": en,
        "grad_problems": problems, "sha": hashlib.sha1(raw).hexdigest()[:10],
        "size_kb": round(len(raw) / 1024), "text": "\n".join(p["text"] for p in pages),
        "en_text": "\n".join(p["text"] for p in pages if p["lang"] == "英文"),
    }


def _subset(doc, indexes):
    out = pymupdf.open()
    for i in indexes:
        out.insert_pdf(doc, from_page=i, to_page=i)
    return out


def _finish(doc):
    doc.set_metadata({"title": f"{config.CANDIDATE_NAME} 简历 / {config.CANDIDATE_NAME_EN} Resume",
                      "author": f"{config.CANDIDATE_NAME} ({config.CANDIDATE_NAME_EN})",
                      "subject": "", "keywords": "", "creator": "", "producer": ""})
    return doc.tobytes(garbage=3, deflate=True)


def build_resume_files(version, filename, filename_en=""):
    """按版本生成要附上的简历文件，返回 [(文件名, bytes)]。"""
    status = resume_status()
    if not status["ok"]:
        raise FileNotFoundError(status["error"])
    doc = pymupdf.open(config.RESUME_PATH)
    zh, en = status["zh_pages"], status["en_pages"]
    en_name = filename_en or config.RESUME_DEFAULT_EN
    if version == "中文" and zh:
        return [(filename, _finish(_subset(doc, zh)))]
    if version == "英文" and en:
        return [(filename, _finish(_subset(doc, en)))]
    if version == "中英两份" and zh and en:
        return [(filename, _finish(_subset(doc, zh))), (en_name, _finish(_subset(doc, en)))]
    return [(filename, _finish(doc))]  # 双语：原件


def resume_version_label(version):
    return {"双语": "中英双页原件", "中文": "仅中文页", "英文": "仅英文页", "中英两份": "中文、英文各一份"}.get(version, version)
