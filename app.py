#!/usr/bin/env python3
"""求职投递面板（Flask）。启动：双击桌面「投递面板.command」，或 .venv/bin/python app.py"""

import hmac
import io
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import traceback
from datetime import datetime

from flask import Flask, abort, jsonify, render_template, request, send_file

from jobapply import agent, checks, config, fetch, gmail_client, jobqueue, llm, pipeline, records, resume, wsprofile, wstasks

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.json.ensure_ascii = False

LOCAL_HOSTS = {f"localhost:{config.PORT}", f"127.0.0.1:{config.PORT}", f"[::1]:{config.PORT}"}


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
    if request.path.startswith("/api/wsreadback/"):
        if _via_tunnel():
            abort(403)  # 读回只从本机的网申网站页面发来
        return None     # 跨域发来的（网申网站页面上），凭这条待办的读回口令认，见 api_wsreadback
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

STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _code_version():
    """面板代码版本：部署过的用部署的提交号；开发副本用 git 提交号（有没提交的改动加 +）；都没有就用代码文件最后修改的时间。"""
    mark = config.BASE_DIR / ".deployed_commit"
    if mark.exists():
        return "部署 " + mark.read_text().strip()[:8]
    try:
        out = subprocess.run(["git", "-C", str(config.BASE_DIR), "describe", "--always", "--dirty=+", "--tags"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    files = [config.BASE_DIR / "app.py", config.BASE_DIR / "wsfill.js", config.BASE_DIR / "templates" / "index.html",
             *(config.BASE_DIR / "jobapply").glob("*.py")]
    newest = max((f.stat().st_mtime for f in files if f.exists()), default=0)
    return "代码 " + datetime.fromtimestamp(newest).strftime("%m-%d %H:%M")


VERSION = _code_version()


@app.route("/api/health")
def api_health():
    """面板自己的情况：管理脚本（scripts/panelctl.py）和运行状态页用。"""
    return jsonify({"app": "jobapply", "env": config.ENV, "port": config.PORT, "pid": os.getpid(),
                    "started_at": STARTED_AT, "version": VERSION})


TEST_ENV_BANNER = ('<div style="position:fixed;top:0;left:0;right:0;z-index:99999;pointer-events:none;background:#b45309;'
                   'color:#fff;font:600 12px/22px sans-serif;text-align:center">测试环境（{port} 端口 · data_test 数据 · 假助手 · '
                   '不连 Gmail）—— 不是正在用的面板</div>')


@app.route("/")
def index():
    html = render_template("index.html")
    if config.IS_TEST_ENV:   # 测试环境：标题加前缀、顶上一条横幅，免得和正在用的面板搞混
        html = html.replace("<title>", "<title>【测试环境】", 1)
        html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + TEST_ENV_BANNER.format(port=config.PORT), html, count=1)
    return html


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
                               extra=d.get("extra", ""), publish_date=d.get("publish_date", ""))
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
    return jsonify(pipeline.review(result, d.get("jd_text", ""), source_label=d.get("source_label", ""),
                                   publish_date=d.get("publish_date", "")))


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


@app.route("/wsfill.js", methods=["GET", "OPTIONS"])
def wsfill_js():
    """网申填表引擎：面板里的助手在网申页面上从这里加载（只是一段通用脚本，不含个人资料）。"""
    if request.method == "OPTIONS":
        resp = app.response_class(status=204)
    else:   # 脚本里的面板地址换成这次请求的面板（测试环境 5002 的脚本就把读回发回 5002）
        base = f"http://{request.host}" if request.host in LOCAL_HOSTS else config.PANEL_BASE
        js = (config.BASE_DIR / "wsfill.js").read_text(encoding="utf-8").replace("__PANEL_BASE__", base)
        resp = app.response_class(js, mimetype="text/javascript")
    resp.headers.update({"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET",
                         "Access-Control-Allow-Private-Network": "true", "Cache-Control": "no-store"})
    return resp


@app.route("/api/wangshen-profile")
def api_wsprofile():
    """网申底稿（简历以外的个人信息）+ 还缺哪些 + 核对提示。"""
    profile, example = wsprofile.load()
    return jsonify({"profile": profile, "example": example, "missing": wsprofile.missing(profile),
                    "notes": profile.get("_核对提示") or []})


@app.route("/api/wangshen-profile", methods=["PUT"])
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


# ── 面板里的助手（聊天 + 操作 Chrome 代填网申）──────────────────

@app.route("/api/agent")
def api_agent_list():
    return jsonify({"chats": agent.list_chats(), "running": agent.running_chat(), "active": agent.active_chats(),
                    "waiting": agent.waiting_chats(), "max_parallel": config.AGENT_MAX_PARALLEL})


@app.route("/api/agent/new", methods=["POST"])
def api_agent_new():
    return jsonify(agent.new_chat())


@app.route("/api/agent/<chat_id>")
def api_agent_get(chat_id):
    try:
        return jsonify(agent.get(chat_id, since=request.args.get("since", 0, type=int)))
    except KeyError:
        return _err("这个对话不存在了", 404)


@app.route("/api/agent/<chat_id>/send", methods=["POST"])
def api_agent_send(chat_id):
    try:
        return jsonify(agent.send(chat_id, _body().get("text", "")))
    except KeyError:
        return _err("这个对话不存在了", 404)
    except agent.Busy as e:
        return _err(str(e), 409)
    except ValueError as e:
        return _err(str(e))


@app.route("/api/agent/<chat_id>/stop", methods=["POST"])
def api_agent_stop(chat_id):
    try:
        return jsonify({"ok": agent.stop(chat_id)})
    except KeyError:
        return _err("这个对话不存在了", 404)


@app.route("/api/agent/<chat_id>", methods=["DELETE"])
def api_agent_delete(chat_id):
    try:
        agent.delete(chat_id)
    except KeyError:
        return _err("这个对话不存在了", 404)
    except agent.Busy as e:
        return _err(str(e), 409)
    return jsonify({"ok": True})


# ── 网申待办（「网申」页）──────────────────────────────────

def _fill_message(t):
    """本人在网申页点了「让助手填」：发给助手的第一句话（面板发的，以「（面板）」开头）。"""
    msg = (f"（面板）本人在网申页点了「让助手填」：请填这家网申：{t.get('company') or '（公司见网页）'}"
           f"｜{t.get('job') or '（岗位见网页）'}\n")
    msg += f"网申链接：{t['url']}\n" if t.get("url") else "网申链接：还没有，先问本人要。\n"
    if t.get("jd_text"):
        msg += "岗位 JD（开放问题可以参考）：\n" + t["jd_text"].strip()[:8000] + "\n"
    return msg + ("按网申底稿和简历填，填完暂存；最后列出：要本人在网页上做的事（哪个颜色框的网页、哪一栏），"
                  "以及你替本人做的选择。")


@app.route("/api/wstasks")
def api_wstasks():
    items = wstasks.list_tasks()
    active, waiting = set(agent.active_chats()), agent.waiting_chats()
    for t in items:   # 助手这会儿在不在干这条：在干活 / 排队中（同时干活的满了、或者同一个网站有别的助手在填）
        cid = t.get("chat_id")
        t["agent_state"] = "在干活" if cid in active else "排队中" if cid in waiting else ""
        t["agent_wait"] = waiting.get(cid, "")
        t["last"] = agent.last_line(cid) if cid in active else ""   # 最新进展：它这会儿在做哪一步
    return jsonify({"tasks": items, "counts": wstasks.counts(items), "active": len(active), "waiting": len(waiting),
                    "max_parallel": config.AGENT_MAX_PARALLEL, "colors": wstasks.COLORS})


@app.route("/api/wstasks", methods=["POST"])
def api_wstasks_add():
    d = _body()
    url = checks.safe_url((d.get("url") or "").strip())
    if not url:
        return _err("先贴网申页面的链接（https:// 开头）")
    task = wstasks.add(url, (d.get("company") or "").strip(), (d.get("job") or "").strip(), note=(d.get("note") or "").strip())
    return jsonify({"task": task})


@app.route("/api/wstasks/<task_id>", methods=["PUT"])
def api_wstasks_update(task_id):
    d = _body()
    try:
        fields = {k: str(d[k]).strip() for k in ("company", "job", "url", "note", "deadline", "account") if k in d}
        task = wstasks.update(task_id, **fields) if fields else wstasks.get(task_id)
        if d.get("status"):
            before = task.get("status")
            task = wstasks.set_status(task_id, d["status"])
    except wstasks.NotFound:
        return _err("这条待办不存在了", 404)
    except ValueError as e:
        return _err(str(e))
    out = {"task": task}
    if d.get("status") == "已提交" and before != "已提交" and d.get("readback", True):
        out["readback"] = agent.start_readback(task_id)[1]   # 点了「我已提交」：助手去网站把实际提交的内容读回来
        out["task"] = wstasks.get(task_id)
    return jsonify(out)


@app.route("/api/wstasks/<task_id>", methods=["DELETE"])
def api_wstasks_delete(task_id):
    return jsonify({"ok": wstasks.delete(task_id)})


def _continue_message(t):
    """本人在网申页点了「让助手接着做」（中途停了 / 等本人处理的事 / 要接着改）：接着原来的对话、在原来那个网页上做，不新开。
    这句话是面板发的，以「（面板）」开头，不替本人说「我弄好了」——让助手自己看网页确认。"""
    head = "（面板）本人在网申页点了「让助手接着做」。"
    if t.get("status") == "等你处理" and t.get("todo"):
        head += f"你上次说要本人做的是：{t['todo']}。先看一眼网页确认这件事做好了没有；没做好就跟本人说清楚还差什么。"
    elif t.get("halted"):
        head += f"你上次停下的原因：{t['halted']}。"
    return (f"{head}接着填这家：{t.get('company') or '（公司见网页）'}｜{t.get('job') or '（岗位见网页）'}。"
            "就用你原来那个画了颜色框的网页（先用 tabs_context_mcp 看一眼）；找不到了就自己新开一个、画上框接着做，不要让本人拖标签页。"
            "先看清现在填到哪了，把没填的填完、能存的存上，停下来时照规矩写一行【网申记录】报状态。")


_ws_agent_lock = threading.Lock()   # 快速点两次「让助手填」：第二次要等第一次把对话建好、记到待办上，才不会开出两个对话


@app.route("/api/wstasks/<task_id>/agent", methods=["POST"])
def api_wstasks_agent(task_id):
    """让助手填这条：第一次开一个跟这条待办绑在一起的对话；以前开过就接着那个对话（同一家网申只用一个对话、一个网页）。
    填完它报的结果会自动更新这条待办和看板。"""
    with _ws_agent_lock:
        return _wstasks_agent(task_id)


def _wstasks_agent(task_id):
    try:
        t = wstasks.get(task_id)
    except wstasks.NotFound:
        return _err("这条待办不存在了", 404)
    if t.get("chat_id"):
        try:
            agent.get(t["chat_id"])
            return jsonify(agent.send(t["chat_id"], _continue_message(t)))
        except KeyError:   # 对话被删了：下面重新开
            pass
    chat = agent.new_chat(task_id=task_id, title=wstasks.short_name(t))
    try:
        out = agent.send(chat["id"], _fill_message(t))
    except agent.Busy as e:
        agent.delete(chat["id"])
        return _err(str(e), 409)
    wstasks.update(task_id, chat_id=chat["id"])
    return jsonify(out)


@app.route("/api/wstasks/<task_id>/readback", methods=["POST"])
def api_wstasks_readback(task_id):
    """「读回网站内容 / 查最新进度」：助手去网站把实际提交的内容、进度、账号读回来，记进看板。"""
    try:
        chat, msg = agent.start_readback(task_id)
    except wstasks.NotFound:
        return _err("这条待办不存在了", 404)
    return jsonify({"chat": chat, "message": msg, "task": wstasks.get(task_id)})


@app.route("/api/wsreadback/<task_id>", methods=["POST", "OPTIONS"])
def api_wsreadback(task_id):
    """助手在网申网站页面上执行 __wsfill.readback(...)：网站上实际提交的内容 / 进度发到这里，记进看板。
    请求是网申网站的页面跨域发来的，带不了面板自己的请求头，所以改认这条待办的读回口令（只写在发给助手的话里）。"""
    def err(msg, code):
        r = jsonify({"error": msg})
        r.status_code = code
        return r
    if request.method == "OPTIONS":
        resp = app.response_class(status=204)
    elif (request.content_length or 0) > 400_000:
        resp = err("内容太长", 413)
    else:
        d = request.get_json(force=True, silent=True)
        if not isinstance(d, dict) or not wstasks.check_readback_key(task_id, d.get("key", "")):
            resp = err("口令不对：照面板发来的那句话原样执行（task 和 key 都要带上）", 403)
        else:
            try:
                task, rid = wstasks.save_readback(task_id, str(d.get("kind") or ""), str(d.get("text") or ""),
                                                  url=str(d.get("url") or ""), title=str(d.get("title") or ""),
                                                  status=str(d.get("status") or ""), account=str(d.get("account") or ""),
                                                  position=str(d.get("position") or ""), location=str(d.get("location") or ""),
                                                  positions=d.get("positions") if isinstance(d.get("positions"), (list, str)) else None)
                resp = jsonify({"ok": True, "record": rid, "status": task.get("status")})
            except wstasks.NotFound:
                resp = err("这条待办不存在了", 404)
            except ValueError as e:
                resp = err(str(e), 400)
    resp.headers.update({"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST",
                         "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Private-Network": "true"})
    return resp


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


MAX_ATTACHMENT = 15 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_ATTACHMENT + 1024 * 1024


@app.route("/api/attachment", methods=["POST"])
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


@app.route("/api/queue/<item_id>/regen", methods=["POST"])
def api_queue_regen(item_id):
    """按补充要求重写（后台做，写好存回这一条）。"""
    d = _body()
    opts = {k: d.get(k) for k in jobqueue.OPT_KEYS}
    return jsonify({"ok": jobqueue.regen(item_id, jd_text=d.get("jd_text", ""), target_job=d.get("target_job"), opts=opts) is not None})


@app.route("/api/queue/<item_id>/skip", methods=["POST"])
def api_queue_skip(item_id):
    return jsonify({"ok": jobqueue.skip(item_id)})


@app.route("/api/queue/<item_id>/recheck", methods=["POST"])
def api_queue_recheck(item_id):
    """按现在的规则重查一封（AI 审稿 + 自动修正），notes 是要特别改的地方。"""
    return jsonify({"ok": jobqueue.recheck(item_id, _body().get("notes", "")) is not None})


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
            "company_name", "job_title", "campaign", "deadline", "reply_status", "apply_account")


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
    if not config.IS_TEST_ENV:   # 测试环境不发信、不碰 Gmail 草稿
        jobqueue.start_scheduler()   # 定时发送：到点由面板自己发
    agent.recover()              # 上次没做完就关了面板的助手对话：标成已停止
    agent.tidy_chats()           # 一家网申只留一个对话：多出来的旧对话收起来
    if not config.IS_TEST_ENV:
        threading.Thread(target=jobqueue.startup_tasks, daemon=True).start()   # 定时草稿对齐 + 旧条目按新规则重查
    env = f"【测试环境】数据 {config.DATA_DIR}  " if config.IS_TEST_ENV else ""
    print(f"投递面板：{config.PANEL_BASE}   {env}模型：{config.CLAUDE_MODEL}   代理：{config.PROXY or '无'}", flush=True)
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)
