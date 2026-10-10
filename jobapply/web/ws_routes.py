"""网申：网申填表引擎 wsfill.js、网申待办（「网申」页）、让助手填 / 接着做、读回网站内容。"""

import threading

from flask import Blueprint, current_app, jsonify, request

from jobapply import agent, apps, checks, config, uploads, wstasks

from .common import LOCAL_HOSTS, _body, _err

bp = Blueprint("ws", __name__)


@bp.route("/wsfill.js", methods=["GET", "OPTIONS"])
def wsfill_js():
    """网申填表引擎：面板里的助手在网申页面上从这里加载（只是一段通用脚本，不含个人资料）。"""
    if request.method == "OPTIONS":
        resp = current_app.response_class(status=204)
    else:   # 脚本里的面板地址换成这次请求的面板（测试环境 5002 的脚本就把读回发回 5002）
        base = f"http://{request.host}" if request.host in LOCAL_HOSTS else config.PANEL_BASE
        js = (config.BASE_DIR / "wsfill.js").read_text(encoding="utf-8").replace("__PANEL_BASE__", base)
        resp = current_app.response_class(js, mimetype="text/javascript")
    resp.headers.update({"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET",
                         "Access-Control-Allow-Private-Network": "true", "Cache-Control": "no-store"})
    return resp


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


@bp.route("/api/wstasks")
def api_wstasks():
    items = wstasks.list_tasks()
    active, waiting = set(agent.active_chats()), agent.waiting_chats()
    for t in items:   # 助手这会儿在不在干这条：在干活 / 排队中（同时干活的满了、或者同一个网站有别的助手在填）
        cid = t.get("chat_id")
        t["agent_state"] = "在干活" if cid in active else "排队中" if cid in waiting else ""
        t["agent_wait"] = waiting.get(cid, "")
        t["last"] = agent.last_line(cid) if cid in active else ""   # 最新进展：它这会儿在做哪一步
        t["need_kind"] = apps.need_kind(t.get("todo")) if t.get("status") == "等你处理" else ""
    return jsonify({"tasks": items, "counts": wstasks.counts(items), "active": len(active), "waiting": len(waiting),
                    "max_parallel": config.AGENT_MAX_PARALLEL, "colors": wstasks.COLORS})


@bp.route("/api/wstasks", methods=["POST"])
def api_wstasks_add():
    d = _body()
    url = checks.safe_url((d.get("url") or "").strip())
    if not url:
        return _err("先贴网申页面的链接（https:// 开头）")
    task = wstasks.add(url, (d.get("company") or "").strip(), (d.get("job") or "").strip(), note=(d.get("note") or "").strip())
    return jsonify({"task": task})


@bp.route("/api/wstasks/<task_id>", methods=["PUT"])
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


@bp.route("/api/wstasks/<task_id>", methods=["DELETE"])
def api_wstasks_delete(task_id):
    return jsonify({"ok": wstasks.delete(task_id)})


def _continue_message(t):
    """本人在网申页点了「让助手接着做」（中途停了 / 等本人处理的事 / 要接着改）：接着原来的对话、在原来那个网页上做，不新开。
    这句话是面板发的，以「（面板）」开头，不替本人说「我弄好了」——让助手自己看网页确认。"""
    head = "（面板）本人在网申页点了「让助手接着做」。"
    if t.get("status") == "等你处理" and t.get("todo"):
        head += f"你上次说要本人做的是：{t['todo']}。先看一眼网页确认这件事做好了没有；没做好就跟本人说清楚还差什么。"
        if apps.need_kind(t["todo"]) == "证件号":
            head += "证件号那一栏用 __wsfill.hasValue 看填没填，不截那一栏、不点进去。"
    elif t.get("halted"):
        head += f"你上次停下的原因：{t['halted']}。"
    return (f"{head}接着填这家：{t.get('company') or '（公司见网页）'}｜{t.get('job') or '（岗位见网页）'}。"
            "就用你原来那个画了颜色框的网页（先用 tabs_context_mcp 看一眼）；找不到了就自己新开一个、画上框接着做，不要让本人拖标签页。"
            "先看清现在填到哪了，把没填的填完、能存的存上，停下来时照规矩写一行【网申记录】报状态。" + _files_now())


def _files_now():
    """本人可能刚往「网申上传」放了照片：把最新的文件清单带上（顺手把各种规格做好）。做不出来就不带。"""
    try:
        return "\n可以上传的文件（以这份为准）：\n" + uploads.prepare()
    except Exception:
        return ""


_ws_agent_lock = threading.Lock()   # 快速点两次「让助手填」：第二次要等第一次把对话建好、记到待办上，才不会开出两个对话


@bp.route("/api/wstasks/<task_id>/agent", methods=["POST"])
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


@bp.route("/api/wstasks/<task_id>/readback", methods=["POST"])
def api_wstasks_readback(task_id):
    """「读回网站内容 / 查最新进度」：助手去网站把实际提交的内容、进度、账号读回来，记进看板。"""
    try:
        chat, msg = agent.start_readback(task_id)
    except wstasks.NotFound:
        return _err("这条待办不存在了", 404)
    return jsonify({"chat": chat, "message": msg, "task": wstasks.get(task_id)})


@bp.route("/api/wsreadback/<task_id>", methods=["POST", "OPTIONS"])
def api_wsreadback(task_id):
    """助手在网申网站页面上执行 __wsfill.readback(...)：网站上实际提交的内容 / 进度发到这里，记进看板。
    请求是网申网站的页面跨域发来的，带不了面板自己的请求头，所以改认这条待办的读回口令（只写在发给助手的话里）。"""
    def err(msg, code):
        r = jsonify({"error": msg})
        r.status_code = code
        return r
    if request.method == "OPTIONS":
        resp = current_app.response_class(status=204)
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
