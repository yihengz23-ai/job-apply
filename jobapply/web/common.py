"""公共件：每个请求先过的拦截（本机 / 隧道口令 / 防跨站）、响应头、统一的出错格式、读请求体；面板页面、基础信息、Gmail 授权。
拦截、响应头、出错处理用 before_app_request / after_app_request / app_errorhandler 挂在整个面板上，不只管这个蓝图。"""

import hmac
import os
import re
import secrets
import subprocess
import threading
import traceback
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request

from jobapply import config, gmail_client, llm, records, resume

bp = Blueprint("common", __name__)

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


@bp.before_app_request
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
        return None     # 跨域发来的（网申网站页面上），凭这条待办的读回口令认，见 ws_routes.api_wsreadback
    if request.method in ("POST", "PUT", "DELETE") and request.headers.get("X-Requested-With") != "jobapply":
        abort(403)  # 防跨站请求（别的网页无法带这个头）


@bp.after_app_request
def remember_key(resp):
    if _via_tunnel() and request.args.get("k") == PANEL_KEY:
        resp.set_cookie("panel_key", PANEL_KEY, max_age=30 * 86400, httponly=True, secure=True, samesite="Lax")
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _err(msg, code=400, **extra):
    return jsonify({"error": msg, **extra}), code


@bp.app_errorhandler(Exception)
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
    files = [config.BASE_DIR / "app.py", config.BASE_DIR / "wsfill.js", *(config.BASE_DIR / "templates").rglob("*.html"),
             *(config.BASE_DIR / "static").rglob("*.*"), *(config.BASE_DIR / "jobapply").rglob("*.py")]   # 含拆出来的页面、脚本、接口
    newest = max((f.stat().st_mtime for f in files if f.exists()), default=0)
    return "代码 " + datetime.fromtimestamp(newest).strftime("%m-%d %H:%M")


VERSION = _code_version()


@bp.route("/api/health")
def api_health():
    """面板自己的情况：管理脚本（scripts/panelctl.py）和运行状态页用。"""
    return jsonify({"app": "jobapply", "env": config.ENV, "port": config.PORT, "pid": os.getpid(),
                    "started_at": STARTED_AT, "version": VERSION})


TEST_ENV_BANNER = ('<div style="position:fixed;top:0;left:0;right:0;z-index:99999;pointer-events:none;background:#b45309;'
                   'color:#fff;font:600 12px/22px sans-serif;text-align:center">测试环境（{port} 端口 · data_test 数据 · 假助手 · '
                   '不连 Gmail）—— 不是正在用的面板</div>')


def _static_v():
    """页面引用 /static/ 文件时带的版本号：取这些文件最新的修改时间。部署换了文件，网址就变，浏览器不会拿缓存里的旧脚本配新页面。"""
    root = config.BASE_DIR / "static"
    return str(int(max((f.stat().st_mtime for f in root.rglob("*") if f.is_file()), default=0)))


@bp.route("/")
def index():
    html = render_template("index.html", static_v=_static_v())
    if config.IS_TEST_ENV:   # 测试环境：标题加前缀、顶上一条横幅，免得和正在用的面板搞混
        html = html.replace("<title>", "<title>【测试环境】", 1)
        html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + TEST_ENV_BANNER.format(port=config.PORT), html, count=1)
    return html


@bp.route("/api/config")
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


@bp.route("/api/tunnel-url")
def api_tunnel_url():
    url = config.TUNNEL_URL_PATH.read_text().strip() if config.TUNNEL_URL_PATH.exists() else ""
    if not url or _via_tunnel():
        return jsonify({"url": ""})
    return jsonify({"url": f"{url}/?k={PANEL_KEY}"})


@bp.route("/api/gmail/status")
def api_gmail_status():
    return jsonify(gmail_client.auth_status())


_auth_lock = threading.Lock()


@bp.route("/api/gmail/auth", methods=["POST"])
def api_gmail_auth():
    if not _auth_lock.acquire(blocking=False):
        return _err("已经在授权中，请看浏览器里新打开的 Google 页面。")
    try:
        gmail_client.get_service(interactive=True)
        return jsonify(gmail_client.auth_status())
    finally:
        _auth_lock.release()
