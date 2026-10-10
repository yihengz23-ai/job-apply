"""申请（一次投出 = 看板上一张卡）：按申请成组的看板、单家详情、「今天」、进展口述和撤销、建议采纳 / 不用、志愿方式、
「做完了」、来信看过了。数据都由 jobapply/app_api.py 算好，这里只转发。"""

from flask import Blueprint, jsonify, request

from jobapply import app_api, apps, llm, progress

from .common import _body, _err

bp = Blueprint("apps", __name__)


@bp.route("/api/apps/overview")
def api_apps_overview():
    return jsonify({"apps": app_api.overview(campaign=request.args.get("campaign"))})


@bp.route("/api/apps/today")
def api_apps_today():
    return jsonify(app_api.today())


@bp.route("/api/apps/<app_id>")
def api_app_detail(app_id):
    try:
        return jsonify(app_api.detail(app_id))
    except apps.NotFound:
        return _err("这次申请不存在了（可能刚被合并或删除），刷新一下", 404)


def _act(fn, *args):
    try:
        out = fn(*args)
    except apps.NotFound:
        return _err("没找到（可能已经处理过了），刷新一下", 404)
    except apps.Conflict:
        raise
    except ValueError as e:
        return _err(str(e))
    return jsonify({"ok": True, "result": out})


@bp.route("/api/apps/<app_id>/suggestions/<sid>", methods=["POST"])
def api_app_suggestion(app_id, sid):
    action = _body().get("action")
    if action not in ("accept", "dismiss"):
        return _err("action 只能是 accept 或 dismiss")
    return _act(app_api.accept_suggestion if action == "accept" else app_api.dismiss_suggestion, app_id, sid)


@bp.route("/api/apps/<app_id>/volunteer-mode", methods=["POST"])
def api_app_volunteer_mode(app_id):
    return _act(app_api.set_volunteer_mode, app_id, str(_body().get("mode") or ""))


@bp.route("/api/apps/<app_id>/step-done", methods=["POST"])
def api_app_step_done(app_id):
    d = request.get_json(silent=True) or {}
    expect = d.get("text") if isinstance(d.get("text"), str) else None
    try:
        return _act(app_api.step_done, app_id, expect)
    except apps.Conflict as e:
        return _err(str(e), 409)


@bp.route("/api/apps/<app_id>/step-undo", methods=["POST"])
def api_app_step_undo(app_id):
    return _act(lambda a: bool(app_api.step_undo(a)), app_id)


@bp.route("/api/apps/<app_id>/reply-handled", methods=["POST"])
def api_app_reply_handled(app_id):
    return _act(lambda a: bool(apps.handle_reply(a, by="本人")), app_id)


# ── 进展口述（WP18）────────────────────────────────────────

@bp.route("/api/progress", methods=["POST"])
def api_progress():
    """本人说一句进展。events 给了就不调 AI（点了候选之后送回来的）。"""
    d = _body()
    events = d.get("events")
    if events is not None and not isinstance(events, list):
        return _err("events 要是列表")
    try:
        return jsonify(progress.apply(str(d.get("text") or ""), events=events))
    except ValueError as e:
        return _err(str(e))
    except llm.LLMError as e:
        return _err(f"这句话没拆出来（AI 没调通：{e}）。过一会儿再试，或者直接在这家的详情里改。", 502)


@bp.route("/api/progress/recent")
def api_progress_recent():
    n = request.args.get("n", "20")
    return jsonify({"items": progress.recent(min(int(n), 200) if n.isdigit() else 20)})


@bp.route("/api/progress/<entry_id>/undo", methods=["POST"])
def api_progress_undo(entry_id):
    try:
        return jsonify({"ok": True, "undone": progress.undo(entry_id)})
    except KeyError:
        return _err("没找到这条口述记录", 404)
