"""我的资料：简历预览和下载（网申按 JD 要求的文件名）、网申资料包、网申底稿。证件号不存（网申时本人自己手动填）。"""

import io
import json

from flask import Blueprint, jsonify, request, send_file

from jobapply import checks, config, resume, wsprofile

from .common import _body, _err

bp = Blueprint("profile", __name__)


@bp.route("/api/resume-preview")
def api_resume_preview():
    version = request.args.get("version", "中文")
    files = resume.build_resume_files(version, "简历预览.pdf", "Resume-Preview.pdf")
    idx = min(int(request.args.get("i", 0)), len(files) - 1)
    name, data = files[idx]
    return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=name)


@bp.route("/api/resume-download", methods=["POST"])
def api_resume_download():
    """网申用：按 JD 要求的文件名下载对应版本的简历。"""
    data = _body()
    files = resume.build_resume_files(data.get("version", "中文"),
                                      checks.sanitize_filename(data.get("filename"), config.RESUME_DEFAULT_ZH),
                                      data.get("filename_en", ""))
    idx = min(int(data.get("i", 0)), len(files) - 1)
    name, blob = files[idx]
    return send_file(io.BytesIO(blob), mimetype="application/pdf", as_attachment=True, download_name=name)


@bp.route("/api/kit")
def api_kit():
    """网申表格常用字段（application_kit.json，和简历逐字一致），给「我的资料」页一键复制。"""
    path = config.BASE_DIR / "application_kit.json"
    if not path.exists():
        path = config.BASE_DIR / "application_kit.example.json"
    if not path.exists():
        return _err("没有找到网申资料文件 application_kit.json", 404)
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


@bp.route("/api/wangshen-profile")
def api_wsprofile():
    """网申底稿（简历以外的个人信息）+ 还缺哪些 + 核对提示。"""
    profile, example = wsprofile.load()
    return jsonify({"profile": profile, "example": example, "missing": wsprofile.missing(profile),
                    "notes": profile.get("_核对提示") or []})


@bp.route("/api/wangshen-profile", methods=["PUT"])
def api_wsprofile_save():
    """页面带上「打开时的底稿」（base）：只把这次改了的格子合并进最新的底稿，别处刚改的不会被冲掉。"""
    d = _body()
    if "base" not in d:   # 面板升级前打开的旧页面：整份发回来会冲掉别处的改动
        return _err("面板更新过了：刷新一下页面再改（免得把别处刚改的内容盖掉）", 409)
    current, example = wsprofile.load()
    edited = d.get("profile") or {}
    try:
        profile = wsprofile.save(edited if example else wsprofile.merge(d.get("base"), edited, current))
    except wsprofile.Invalid as e:
        return _err(str(e))
    return jsonify({"ok": True, "profile": profile, "missing": wsprofile.missing(profile), "notes": profile.get("_核对提示") or []})
