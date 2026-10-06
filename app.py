#!/usr/bin/env python3
"""求职投递面板（Flask）。启动：双击桌面「投递面板.command」，或 .venv/bin/python app.py"""

import hmac
import io
import secrets
import threading
import traceback

from flask import Flask, abort, jsonify, render_template, request, send_file

from jobapply import checks, config, fetch, gmail_client, llm, pipeline, records, resume

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.json.ensure_ascii = False

LOCAL_HOSTS = {"localhost:5001", "127.0.0.1:5001", "[::1]:5001"}


def _panel_key():
    if config.PANEL_KEY_PATH.exists():
        return config.PANEL_KEY_PATH.read_text().strip()
    key = secrets.token_urlsafe(18)
    config.PANEL_KEY_PATH.write_text(key)
    config.PANEL_KEY_PATH.chmod(0o600)
    return key


PANEL_KEY = _panel_key()


def _via_tunnel():
    return any(h in request.headers for h in ("Cf-Connecting-Ip", "Cf-Ray", "Cdn-Loop"))


@app.before_request
def guard():
    if _via_tunnel():
        key = request.args.get("k") or request.cookies.get("panel_key") or request.headers.get("X-Panel-Key", "")
        if not hmac.compare_digest(key, PANEL_KEY):
            abort(403)
        if request.path.startswith("/api/gmail/auth"):
            abort(403)  # 授权只能在电脑本机做
    elif request.host not in LOCAL_HOSTS:
        abort(403)  # 防 DNS rebinding
    if request.method in ("POST", "PUT", "DELETE") and request.headers.get("X-Requested-With") != "jobapply":
        abort(403)  # 防跨站请求（别的网页无法带这个头）


@app.after_request
def remember_key(resp):
    if _via_tunnel() and request.args.get("k") == PANEL_KEY:
        resp.set_cookie("panel_key", PANEL_KEY, max_age=30 * 86400, httponly=True, secure=True, samesite="Lax")
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _err(msg, code=400, **extra):
    return jsonify({"error": msg, **extra}), code


@app.errorhandler(Exception)
def on_error(e):
    if hasattr(e, "code") and isinstance(getattr(e, "code"), int):
        return _err(getattr(e, "description", str(e)), e.code)
    traceback.print_exc()
    return _err(f"{type(e).__name__}: {e}", 500)


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


# ── 页面 & 基础信息 ─────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def api_config():
    rs = resume.resume_status()
    return jsonify({
        "model": config.CLAUDE_MODEL, "effort": config.CLAUDE_EFFORT, "sender": config.SENDER_EMAIL,
        "campaign": config.CURRENT_CAMPAIGN, "campaigns": sorted({r.get("campaign") for r in records.load()} | {config.CURRENT_CAMPAIGN}),
        "statuses": records.STATUSES, "position_types": llm.POSITION_TYPES, "resume_versions": llm.RESUME_VERSIONS,
        "resume": {k: rs.get(k) for k in ("ok", "error", "pages", "zh_pages", "en_pages", "grad_problems", "sha", "size_kb")},
        "report_exists": config.REPORT_PATH.exists(), "report_default_name": config.REPORT_DEFAULT_NAME,
        "report_label": config.REPORT_LABEL, "resume_default_zh": config.RESUME_DEFAULT_ZH,
        "resume_default_en": config.RESUME_DEFAULT_EN, "candidate": config.CANDIDATE_NAME,
        "local": not _via_tunnel(),
    })


@app.route("/api/tunnel-url")
def api_tunnel_url():
    url = config.TUNNEL_URL_PATH.read_text().strip() if config.TUNNEL_URL_PATH.exists() else ""
    if not url or _via_tunnel():
        return jsonify({"url": ""})
    return jsonify({"url": f"{url}/?k={PANEL_KEY}"})


@app.route("/api/gmail/status")
def api_gmail_status():
    return jsonify(gmail_client.auth_status())


_auth_lock = threading.Lock()


@app.route("/api/gmail/auth", methods=["POST"])
def api_gmail_auth():
    if not _auth_lock.acquire(blocking=False):
        return _err("已经在授权中，请看浏览器里新打开的 Google 页面。")
    try:
        gmail_client.get_service(interactive=True)
        return jsonify(gmail_client.auth_status())
    finally:
        _auth_lock.release()


@app.route("/api/resume-preview")
def api_resume_preview():
    version = request.args.get("version", "双语")
    files = resume.build_resume_files(version, "简历预览.pdf", "Resume-Preview.pdf")
    idx = min(int(request.args.get("i", 0)), len(files) - 1)
    name, data = files[idx]
    return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=name)


@app.route("/api/resume-download", methods=["POST"])
def api_resume_download():
    """网申用：按 JD 要求的文件名下载对应版本的简历。"""
    data = _body()
    files = resume.build_resume_files(data.get("version", "双语"),
                                      checks.sanitize_filename(data.get("filename"), config.RESUME_DEFAULT_ZH),
                                      data.get("filename_en", ""))
    idx = min(int(data.get("i", 0)), len(files) - 1)
    name, blob = files[idx]
    return send_file(io.BytesIO(blob), mimetype="application/pdf", as_attachment=True, download_name=name)


# ── 新投递 ─────────────────────────────────────────────────

@app.route("/api/fetch-url", methods=["POST"])
def api_fetch_url():
    url = (_body().get("url") or "").strip()
    if not url:
        return _err("请输入链接")
    try:
        page = fetch.fetch_url(url)
    except fetch.FetchError as e:
        return _err(str(e))
    except llm.LLMError as e:
        return _err(f"图片识别失败：{e}")
    content = page["content"]
    n_multi, n_emails = fetch.count_job_signals(content)
    jobs = []
    if n_multi >= 2 or n_emails >= 2:
        try:
            jobs = llm.detect_jobs(content)
        except llm.LLMError:
            jobs = []
    return jsonify({**page, "jobs": jobs if len(jobs) > 1 else [], "content_length": len(content)})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    d = _body()
    try:
        out = pipeline.analyze(d.get("jd_text", ""), source_label=d.get("source_label", ""),
                               target_job=d.get("target_job", ""), position_hint=d.get("position_hint", ""),
                               resume_hint=d.get("resume_hint", ""), extra=d.get("extra", ""))
    except (ValueError, llm.LLMError) as e:
        return _err(str(e))
    if not out.get("ok"):
        return _err(out.get("error", "分析失败"), meta=out.get("meta"))
    return jsonify(out)


@app.route("/api/check", methods=["POST"])
def api_check():
    """用户在界面上改完后重新检查（不调 AI）。"""
    d = _body()
    result = pipeline._normalize_edits(dict(d.get("result") or {}))
    return jsonify(pipeline.review(result, d.get("jd_text", ""), source_label=d.get("source_label", "")))


@app.route("/api/send", methods=["POST"])
def api_send():
    d = _body()
    try:
        out = pipeline.deliver(d.get("result") or {}, d.get("jd_text", ""), mode=d.get("mode", "send"),
                               force=bool(d.get("force")), source_label=d.get("source_label", ""),
                               source_url=d.get("source_url", ""), target_job=d.get("target_job", ""),
                               publish_date=d.get("publish_date", ""), source_type="网页面板")
    except pipeline.Blocked as e:
        return _err("还有必须处理的问题，没有发出", 409, issues=e.issues)
    except gmail_client.GmailAuthError as e:
        return _err(str(e), 401, need_auth=True)
    return jsonify(out)


@app.route("/api/record", methods=["POST"])
def api_record():
    d = _body()
    out = pipeline.record_web_application(d.get("result") or {}, d.get("jd_text", ""),
                                          source_label=d.get("source_label", ""), source_url=d.get("source_url", ""),
                                          target_job=d.get("target_job", ""), publish_date=d.get("publish_date", ""))
    return jsonify(out)


# ── 看板 ───────────────────────────────────────────────────

@app.route("/api/records")
def api_records():
    recs = records.filter_campaign(records.load(), request.args.get("campaign"))
    recs.sort(key=lambda r: r.get("sent_at") or "", reverse=True)
    for r in recs:
        r["_type"] = records.norm_company_type(r.get("company_type"))
    return jsonify(recs)


@app.route("/api/stats")
def api_stats():
    return jsonify(records.stats(records.filter_campaign(records.load(), request.args.get("campaign"))))


EDITABLE = ("status", "job_source", "job_location", "notes", "focus_industry", "position_type",
            "company_name", "job_title", "campaign", "deadline", "reply_status")


@app.route("/api/records/<record_id>", methods=["PUT"])
def api_update_record(record_id):
    fields = {k: v for k, v in _body().items() if k in EDITABLE}
    if not records.update(record_id, fields):
        return _err("记录不存在", 404)
    return jsonify({"ok": True})


@app.route("/api/records/<record_id>", methods=["DELETE"])
def api_delete_record(record_id):
    return jsonify({"ok": records.delete(record_id)})


@app.route("/api/gmail-sync", methods=["POST"])
def api_gmail_sync():
    logs = []
    try:
        result = pipeline.sync_gmail(progress=logs.append)
    except gmail_client.GmailAuthError as e:
        return _err(str(e), 401, need_auth=True, logs=logs)
    return jsonify({"ok": True, "result": result, "logs": logs})


@app.route("/api/check-replies", methods=["POST"])
def api_check_replies():
    logs = []
    try:
        d = _body()
        result = pipeline.refresh_replies(campaign=d.get("campaign"), progress=logs.append,
                                          record_ids=d.get("record_ids"))
    except gmail_client.GmailAuthError as e:
        return _err(str(e), 401, need_auth=True, logs=logs)
    return jsonify({"ok": True, "result": result, "logs": logs})


@app.route("/api/export-excel")
def api_export_excel():
    path = records.export_excel(records.load())
    return send_file(path, as_attachment=True, download_name="投递记录.xlsx")


if __name__ == "__main__":
    records.migrate()
    print(f"投递面板：http://localhost:5001   模型：{config.CLAUDE_MODEL}   代理：{config.PROXY or '无'}")
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
