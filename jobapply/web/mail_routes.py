"""写信、发送、定时、批量队列：贴 JD 抓取和分析、发信前检查、发送 / 存草稿、定时发送、网申投完记一笔、网申问答、队列条目的各种操作、审核时加附件。"""

import secrets

from flask import Blueprint, jsonify, request

from jobapply import checks, config, fetch, gmail_client, jobqueue, llm, pipeline

from .common import _body, _err

bp = Blueprint("mail", __name__)

# ── 新投递 ─────────────────────────────────────────────────

@bp.route("/api/fetch-url", methods=["POST"])
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


@bp.route("/api/analyze", methods=["POST"])
def api_analyze():
    d = _body()
    try:
        out = pipeline.analyze(d.get("jd_text", ""), source_label=d.get("source_label", ""),
                               target_job=d.get("target_job", ""), position_hint=d.get("position_hint", ""),
                               resume_hint=d.get("resume_hint", ""), report_hint=d.get("report_hint", ""),
                               extra=d.get("extra", ""), publish_date=d.get("publish_date", ""))
    except (ValueError, llm.LLMError) as e:
        return _err(str(e))
    if not out.get("ok"):
        return _err(out.get("error", "分析失败"), meta=out.get("meta"))
    return jsonify(out)


@bp.route("/api/check", methods=["POST"])
def api_check():
    """用户在界面上改完后重新检查（不调 AI）。"""
    d = _body()
    result = pipeline._normalize_edits(dict(d.get("result") or {}))
    return jsonify(pipeline.review(result, d.get("jd_text", ""), source_label=d.get("source_label", ""),
                                   publish_date=d.get("publish_date", "")))


QUEUE_TAKEN = "这条在队列里已经在发送或已经处理过了（可能在另一个窗口 / 一键发送里），刷新看看。"


@bp.route("/api/send", methods=["POST"])
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
                               publish_date=d.get("publish_date", ""), source_type="网页面板",
                               app_id=(jobqueue._get(qid) or {}).get("app_id", "") if qid else "")
    except pipeline.Blocked as e:
        qid and jobqueue.release(qid)
        if e.hard:
            return _err("这几条不能跳过，改完再发：" + "；".join(i["msg"] for i in e.hard), 409, issues=e.issues, hard=e.hard)
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


@bp.route("/api/schedule", methods=["POST"])
def api_schedule():
    """晚上点了发送：这封原样存好，明早 config.SEND_AT 由面板自己发（只用于队列里的条目）。"""
    d = _body()
    qid = d.get("queue_id")
    if not qid:
        return _err("只有队列里的邮件能定时发送")
    result = pipeline._normalize_edits(dict(d.get("result") or {}))
    rev = pipeline.review(result, d.get("jd_text", ""), source_label=d.get("source_label", ""))
    errors = [i for i in rev["issues"] if i["level"] == "error"]
    hard = [i for i in errors if checks.unskippable(i)]
    if hard and d.get("force") is True:
        return _err("这几条不能跳过，改完再发：" + "；".join(i["msg"] for i in hard), 409, issues=rev["issues"], hard=hard)
    if errors and d.get("force") is not True:
        return _err("还有必须处理的问题，没有定时", 409, issues=rev["issues"])
    auth = gmail_client.auth_status()  # 现在就确认 Gmail 能用，免得明早到点才发现发不出去
    if not auth.get("ok"):
        return _err(auth.get("error") or "Gmail 用不了", 401, need_auth=True)
    it = jobqueue.schedule(qid, result, force=d.get("force") is True)
    if not it:
        return _err(QUEUE_TAKEN, 409, queue_taken=True)
    return jsonify({"ok": True, "send_at": it["send_at"]})


@bp.route("/api/queue/schedule-drafts", methods=["POST"])
def api_queue_schedule_drafts():
    """队列里「已存草稿」的：明早 config.SEND_AT 从 Gmail 原样发出。ids 不传 = 全部。"""
    auth = gmail_client.auth_status()
    if not auth.get("ok"):
        return _err(auth.get("error") or "Gmail 用不了", 401, need_auth=True)
    ids = _body().get("ids")
    targets = [it["id"] for it in jobqueue.list_items() if it["status"] == "已存草稿" and (ids is None or it["id"] in ids)]
    done = [i for i in targets if jobqueue.schedule_draft(i)]
    return jsonify({"ok": True, "scheduled": len(done), "send_at": jobqueue.next_send_time()})


@bp.route("/api/queue/<item_id>/unschedule", methods=["POST"])
def api_queue_unschedule(item_id):
    return jsonify({"ok": jobqueue.unschedule(item_id)})


@bp.route("/api/record", methods=["POST"])
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


@bp.route("/api/wangshen", methods=["POST"])
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

@bp.route("/api/queue")
def api_queue():
    items = jobqueue.list_items()
    return jsonify({"items": items, "counts": jobqueue.counts(items), "workers": jobqueue.WORKERS})


@bp.route("/api/queue", methods=["POST"])
def api_queue_add():
    try:
        d = _body()
        new = jobqueue.enqueue(d.get("text", ""), opts=d.get("opts"))
    except ValueError as e:
        return _err(str(e))
    return jsonify({"ok": True, "added": len(new)})


@bp.route("/api/queue/<item_id>", methods=["PUT"])
def api_queue_edit(item_id):
    """审核时的改动：邮件内容（edited）、最新检查结果（issues）、改过的网申问答（wangshen），各自可选。"""
    d = _body()
    return jsonify({"ok": jobqueue.save_edits(item_id, d.get("edited"), issues=d.get("issues"),
                                              wangshen=d.get("wangshen"), rev=d.get("rev"))})


@bp.route("/api/queue/<item_id>", methods=["DELETE"])
def api_queue_delete(item_id):
    return jsonify({"ok": jobqueue.delete(item_id)})


MAX_ATTACHMENT = 15 * 1024 * 1024   # app.py 按它定整个请求的大小上限（MAX_CONTENT_LENGTH）


@bp.route("/api/attachment", methods=["POST"])
def api_attachment():
    """审核时自己加附件（文章、作品、成绩单……）：存进 uploads/，返回名字和位置，发信时一起带上。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return _err("没收到文件")
    data = f.read(MAX_ATTACHMENT + 1)
    if len(data) > MAX_ATTACHMENT:
        return _err("文件超过 15MB，邮件带不了这么大的附件")
    name = checks.FILENAME_BAD.sub("-", f.filename.strip())[-120:] or "附件"   # 保留原来的扩展名（PDF、图片、Word 都行）
    rel = f"{secrets.token_hex(4)}-{name}"
    config.UPLOADS_DIR.mkdir(exist_ok=True)
    (config.UPLOADS_DIR / rel).write_bytes(data)
    return jsonify({"ok": True, "name": name, "path": rel, "size_kb": round(len(data) / 1024)})


@bp.route("/api/queue/<item_id>/regen", methods=["POST"])
def api_queue_regen(item_id):
    """按补充要求重写（后台做，写好存回这一条）。"""
    d = _body()
    opts = {k: d.get(k) for k in jobqueue.OPT_KEYS}
    return jsonify({"ok": jobqueue.regen(item_id, jd_text=d.get("jd_text", ""), target_job=d.get("target_job"), opts=opts) is not None})


@bp.route("/api/queue/<item_id>/skip", methods=["POST"])
def api_queue_skip(item_id):
    return jsonify({"ok": jobqueue.skip(item_id)})


@bp.route("/api/queue/<item_id>/recheck", methods=["POST"])
def api_queue_recheck(item_id):
    """按现在的规则重查一封（AI 审稿 + 自动修正），notes 是要特别改的地方。"""
    return jsonify({"ok": jobqueue.recheck(item_id, _body().get("notes", "")) is not None})


@bp.route("/api/queue/<item_id>/retry", methods=["POST"])
def api_queue_retry(item_id):
    return jsonify({"ok": jobqueue.retry(item_id)})


@bp.route("/api/queue/clear-done", methods=["POST"])
def api_queue_clear():
    return jsonify({"ok": True, "removed": jobqueue.clear_done()})


@bp.route("/api/queue/process-ready", methods=["POST"])
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
