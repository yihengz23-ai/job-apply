#!/usr/bin/env python3
"""求职投递面板（Flask）。启动：双击桌面「投递面板.command」，或 .venv/bin/python app.py"""

import hmac
import io
import json
import secrets
import tempfile
import threading
import traceback

from flask import Flask, abort, jsonify, render_template, request, send_file

from jobapply import checks, config, fetch, gmail_client, jobqueue, llm, pipeline, records, resume

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
        if not hmac.compare_digest(key.encode(), PANEL_KEY.encode()):
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
    try:
        camps, records_error = {r.get("campaign") for r in records.load()}, ""
    except records.RecordsCorrupt as e:  # 记录文件坏了：面板照样打开，顶部提示
        camps, records_error = set(), str(e)
    return jsonify({
        "records_error": records_error,
        "model": config.CLAUDE_MODEL, "effort": config.CLAUDE_EFFORT, "backend": config.LLM_BACKEND,
        "sender": config.SENDER_EMAIL,
        "campaign": config.CURRENT_CAMPAIGN, "campaigns": sorted(c for c in camps | {config.CURRENT_CAMPAIGN} if c),
        "statuses": records.STATUSES, "position_types": llm.POSITION_TYPES, "resume_versions": llm.RESUME_VERSIONS, "report_hints": llm.REPORT_HINTS,
        "send_at": config.SEND_AT, "night_hours": config.NIGHT_HOURS,
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
    version = request.args.get("version", "中文")
    files = resume.build_resume_files(version, "简历预览.pdf", "Resume-Preview.pdf")
    idx = min(int(request.args.get("i", 0)), len(files) - 1)
    name, data = files[idx]
    return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=name)


@app.route("/api/resume-download", methods=["POST"])
def api_resume_download():
    """网申用：按 JD 要求的文件名下载对应版本的简历。"""
    data = _body()
    files = resume.build_resume_files(data.get("version", "中文"),
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
                               resume_hint=d.get("resume_hint", ""), report_hint=d.get("report_hint", ""),
                               extra=d.get("extra", ""))
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


QUEUE_TAKEN = "这条在队列里已经在发送或已经处理过了（可能在另一个窗口 / 一键发送里），刷新看看。"


@app.route("/api/send", methods=["POST"])
def api_send():
    d = _body()
    mode = d.get("mode")
    if mode not in ("send", "draft"):  # 不可逆操作：只认这两个值，不给默认
        return _err("发送方式不对（只能是 send 或 draft）")
    qid = d.get("queue_id")
    if qid and not jobqueue.claim(qid):
        return _err(QUEUE_TAKEN, 409, queue_taken=True)
    try:
        out = pipeline.deliver(d.get("result") or {}, d.get("jd_text", ""), mode=mode,
                               force=d.get("force") is True, source_label=d.get("source_label", ""),
                               source_url=d.get("source_url", ""), target_job=d.get("target_job", ""),
                               publish_date=d.get("publish_date", ""), source_type="网页面板")
    except pipeline.Blocked as e:
        qid and jobqueue.release(qid)
        return _err("还有必须处理的问题，没有发出", 409, issues=e.issues)
    except gmail_client.GmailAuthError as e:
        qid and jobqueue.release(qid)
        return _err(str(e), 401, need_auth=True)
    except gmail_client.SendUncertain as e:  # 可能已经发出：条目标成需处理，不让它回到待审核被再发一遍
        qid and jobqueue.mark_uncertain(qid, str(e))
        return _err(str(e), 504, uncertain=True, mode=mode)
    except gmail_client.SendFailed as e:  # 肯定没发出：退回去，改完可以再发
        qid and jobqueue.release(qid, error=str(e))
        return _err(str(e), 502)
    except Exception:
        qid and jobqueue.release(qid)
        raise
    if qid:
        jobqueue.mark_done(qid, "已存草稿" if out["mode"] == "草稿" else "已发送", out.get("record_id", ""))
    return jsonify(out)


@app.route("/api/schedule", methods=["POST"])
def api_schedule():
    """晚上点了发送：这封原样存好，明早 config.SEND_AT 由面板自己发（只用于队列里的条目）。"""
    d = _body()
    qid = d.get("queue_id")
    if not qid:
        return _err("只有队列里的邮件能定时发送")
    result = pipeline._normalize_edits(dict(d.get("result") or {}))
    rev = pipeline.review(result, d.get("jd_text", ""), source_label=d.get("source_label", ""))
    errors = [i for i in rev["issues"] if i["level"] == "error"]
    if errors and d.get("force") is not True:
        return _err("还有必须处理的问题，没有定时", 409, issues=rev["issues"])
    auth = gmail_client.auth_status()  # 现在就确认 Gmail 能用，免得明早到点才发现发不出去
    if not auth.get("ok"):
        return _err(auth.get("error") or "Gmail 用不了", 401, need_auth=True)
    it = jobqueue.schedule(qid, result, force=d.get("force") is True)
    if not it:
        return _err(QUEUE_TAKEN, 409, queue_taken=True)
    return jsonify({"ok": True, "send_at": it["send_at"]})


@app.route("/api/queue/schedule-drafts", methods=["POST"])
def api_queue_schedule_drafts():
    """队列里「已存草稿」的：明早 config.SEND_AT 从 Gmail 原样发出。ids 不传 = 全部。"""
    auth = gmail_client.auth_status()
    if not auth.get("ok"):
        return _err(auth.get("error") or "Gmail 用不了", 401, need_auth=True)
    ids = _body().get("ids")
    targets = [it["id"] for it in jobqueue.list_items() if it["status"] == "已存草稿" and (ids is None or it["id"] in ids)]
    done = [i for i in targets if jobqueue.schedule_draft(i)]
    return jsonify({"ok": True, "scheduled": len(done), "send_at": jobqueue.next_send_time()})


@app.route("/api/queue/<item_id>/unschedule", methods=["POST"])
def api_queue_unschedule(item_id):
    return jsonify({"ok": jobqueue.unschedule(item_id)})


@app.route("/api/record", methods=["POST"])
def api_record():
    """网申投完了：记一笔（连同当时用的网申问答）。"""
    d = _body()
    # 「邮箱+网申」邮件已发（有 record_id）：只在原记录上补记，队列条目已经是「已发送」，不用再认领
    qid = "" if d.get("record_id") else d.get("queue_id")
    if qid and not jobqueue.claim(qid):
        return _err(QUEUE_TAKEN, 409, queue_taken=True)
    try:
        out = pipeline.record_web_application(
            d.get("result") or {}, d.get("jd_text", ""), source_label=d.get("source_label", ""),
            source_url=d.get("source_url", ""), target_job=d.get("target_job", ""),
            publish_date=d.get("publish_date", ""), kit=d.get("kit"), upload_version=d.get("upload_version", ""),
            record_id=d.get("record_id", ""))
    except Exception:
        qid and jobqueue.release(qid)
        raise
    if qid:
        jobqueue.mark_done(qid, "已记录", out.get("record_id", ""))
    elif d.get("record_id") and d.get("queue_id"):
        jobqueue.mark_ws_recorded(d["queue_id"])
    return jsonify(out)


# ── 网申 ───────────────────────────────────────────────────

@app.route("/api/kit")
def api_kit():
    """网申表格常用字段（application_kit.json，和简历逐字一致），给「我的资料」页一键复制。"""
    path = config.BASE_DIR / "application_kit.json"
    if not path.exists():
        path = config.BASE_DIR / "application_kit.example.json"
    if not path.exists():
        return _err("没有找到网申资料文件 application_kit.json", 404)
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


@app.route("/api/wangshen", methods=["POST"])
def api_wangshen():
    """给这个岗位生成网申问答（自我介绍 / 为什么申请 / JD 里列出的问题）和投递步骤。"""
    d = _body()
    jd = d.get("jd_text", "")
    if len(jd.strip()) < 50:
        return _err("JD 内容太短，先把招聘信息贴进来。")
    try:
        kit, meta = pipeline.wangshen(jd, result=d.get("result") or {}, source_label=d.get("source_label", ""),
                                      target_job=d.get("target_job", ""))
    except llm.LLMError as e:
        return _err(str(e))
    if d.get("queue_id"):
        jobqueue.save_wangshen(d["queue_id"], kit)
    return jsonify({"kit": kit, "meta": meta})


# ── 批量队列 ───────────────────────────────────────────────

@app.route("/api/queue")
def api_queue():
    items = jobqueue.list_items()
    return jsonify({"items": items, "counts": jobqueue.counts(items), "workers": jobqueue.WORKERS})


@app.route("/api/queue", methods=["POST"])
def api_queue_add():
    try:
        d = _body()
        new = jobqueue.enqueue(d.get("text", ""), opts=d.get("opts"))
    except ValueError as e:
        return _err(str(e))
    return jsonify({"ok": True, "added": len(new)})


@app.route("/api/queue/<item_id>", methods=["PUT"])
def api_queue_edit(item_id):
    """审核时的改动：邮件内容（edited）、最新检查结果（issues）、改过的网申问答（wangshen），各自可选。"""
    d = _body()
    return jsonify({"ok": jobqueue.save_edits(item_id, d.get("edited"), issues=d.get("issues"),
                                              wangshen=d.get("wangshen"), rev=d.get("rev"))})


@app.route("/api/queue/<item_id>", methods=["DELETE"])
def api_queue_delete(item_id):
    return jsonify({"ok": jobqueue.delete(item_id)})


@app.route("/api/queue/<item_id>/regen", methods=["POST"])
def api_queue_regen(item_id):
    """按补充要求重写（后台做，写好存回这一条）。"""
    d = _body()
    opts = {k: d.get(k) for k in jobqueue.OPT_KEYS}
    return jsonify({"ok": jobqueue.regen(item_id, jd_text=d.get("jd_text", ""), target_job=d.get("target_job"), opts=opts) is not None})


@app.route("/api/queue/<item_id>/skip", methods=["POST"])
def api_queue_skip(item_id):
    return jsonify({"ok": jobqueue.skip(item_id)})


@app.route("/api/queue/<item_id>/retry", methods=["POST"])
def api_queue_retry(item_id):
    return jsonify({"ok": jobqueue.retry(item_id)})


@app.route("/api/queue/clear-done", methods=["POST"])
def api_queue_clear():
    return jsonify({"ok": True, "removed": jobqueue.clear_done()})


@app.route("/api/queue/process-ready", methods=["POST"])
def api_queue_process_ready():
    mode = _body().get("mode")
    if mode not in ("send", "draft", "schedule"):
        return _err("发送方式不对（只能是 send、draft 或 schedule）")
    try:
        out = jobqueue.process_ready(mode=mode)
    except jobqueue.Busy as e:
        return _err(str(e), 409)
    except gmail_client.GmailAuthError as e:
        return _err(str(e), 401, need_auth=True)
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
    out = records.stats(records.filter_campaign(records.load(), request.args.get("campaign")))
    out["excel"] = dict(records.EXCEL_STATUS)
    return jsonify(out)


EDITABLE = ("status", "job_source", "job_location", "notes", "focus_industry", "position_type",
            "company_name", "job_title", "campaign", "deadline", "reply_status")


@app.route("/api/records/<record_id>", methods=["PUT"])
def api_update_record(record_id):
    fields = {k: v for k, v in _body().items() if k in EDITABLE}
    if "reply_status" in fields:  # 手动纠正过的回复状态，以后查回复不再覆盖
        fields["reply_locked"] = True
    if not records.update(record_id, fields):
        return _err("记录不存在", 404)
    return jsonify({"ok": True})


@app.route("/api/records/<record_id>", methods=["DELETE"])
def api_delete_record(record_id):
    return jsonify({"ok": records.delete(record_id)})


_gmail_job_lock = threading.Lock()  # 同步 Gmail / 查回复：同一时间只跑一个（两个窗口同时点会重复建记录）


def _gmail_job(fn):
    if not _gmail_job_lock.acquire(blocking=False):
        return _err("另一个 Gmail 操作（同步 / 查回复）正在进行，等它结束再点。", 409)
    logs = []
    try:
        return jsonify({"ok": True, "result": fn(logs.append), "logs": logs})
    except gmail_client.GmailAuthError as e:
        return _err(str(e), 401, need_auth=True, logs=logs)
    finally:
        _gmail_job_lock.release()


@app.route("/api/gmail-sync", methods=["POST"])
def api_gmail_sync():
    return _gmail_job(lambda log: pipeline.sync_gmail(progress=log))


@app.route("/api/check-replies", methods=["POST"])
def api_check_replies():
    d = _body()
    return _gmail_job(lambda log: pipeline.refresh_replies(campaign=d.get("campaign"), progress=log,
                                                         record_ids=d.get("record_ids")))


@app.route("/api/export-excel")
def api_export_excel():
    # 导出到临时文件、读进内存再删掉，不碰桌面的 Excel 镜像（后台保存时也在写它）
    with tempfile.TemporaryDirectory(prefix="jobapply-") as tmp:
        path = records.export_excel(records.load(), path=f"{tmp}/投递记录.xlsx")
        data = open(path, "rb").read()
    return send_file(io.BytesIO(data), as_attachment=True, download_name="投递记录.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    try:
        records.migrate()
    except records.RecordsCorrupt as e:  # 面板照样打开（能看到提示），只是写记录会被拦下
        print(f"⚠️  {e}")
    resumed = jobqueue.resume_pending()
    if resumed:
        print(f"批量队列：接着处理上次没做完的 {resumed} 条")
    jobqueue.start_scheduler()   # 定时发送：到点由面板自己发
    threading.Thread(target=jobqueue.refresh_issues, daemon=True).start()   # 旧条目按现在的规则重新检查一遍
    print(f"投递面板：http://localhost:5001   模型：{config.CLAUDE_MODEL}   代理：{config.PROXY or '无'}")
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
