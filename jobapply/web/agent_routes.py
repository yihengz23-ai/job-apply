"""面板里的助手：对话列表、新对话、看对话、发话、停下、删除。"""

from flask import Blueprint, jsonify, request

from jobapply import agent, config

from .common import _body, _err

bp = Blueprint("agent", __name__)

# ── 面板里的助手（聊天 + 操作 Chrome 代填网申）──────────────────

@bp.route("/api/agent")
def api_agent_list():
    return jsonify({"chats": agent.list_chats(), "running": agent.running_chat(), "active": agent.active_chats(),
                    "waiting": agent.waiting_chats(), "max_parallel": config.AGENT_MAX_PARALLEL})


@bp.route("/api/agent/new", methods=["POST"])
def api_agent_new():
    return jsonify(agent.new_chat())


@bp.route("/api/agent/<chat_id>")
def api_agent_get(chat_id):
    try:
        return jsonify(agent.get(chat_id, since=request.args.get("since", 0, type=int)))
    except KeyError:
        return _err("这个对话不存在了", 404)


@bp.route("/api/agent/<chat_id>/send", methods=["POST"])
def api_agent_send(chat_id):
    try:
        return jsonify(agent.send(chat_id, _body().get("text", "")))
    except KeyError:
        return _err("这个对话不存在了", 404)
    except agent.Busy as e:
        return _err(str(e), 409)
    except ValueError as e:
        return _err(str(e))


@bp.route("/api/agent/<chat_id>/stop", methods=["POST"])
def api_agent_stop(chat_id):
    try:
        return jsonify({"ok": agent.stop(chat_id)})
    except KeyError:
        return _err("这个对话不存在了", 404)


@bp.route("/api/agent/<chat_id>", methods=["DELETE"])
def api_agent_delete(chat_id):
    try:
        agent.delete(chat_id)
    except KeyError:
        return _err("这个对话不存在了", 404)
    except agent.Busy as e:
        return _err(str(e), 409)
    return jsonify({"ok": True})
