"""看板：投递记录、统计、改 / 删记录、同步 Gmail 和查回复、导出 Excel。"""

import io
import tempfile
import threading

from flask import Blueprint, jsonify, request, send_file

from jobapply import gmail_client, pipeline, records

from .common import _body, _err

bp = Blueprint("board", __name__)

# ── 看板 ───────────────────────────────────────────────────

@bp.route("/api/records")
def api_records():
    recs = records.filter_campaign(records.load(), request.args.get("campaign"))
    recs.sort(key=lambda r: r.get("sent_at") or "", reverse=True)
    for r in recs:
        r["_type"] = records.norm_company_type(r.get("company_type"))
    return jsonify(recs)


@bp.route("/api/stats")
def api_stats():
    out = records.stats(records.filter_campaign(records.load(), request.args.get("campaign")))
    out["excel"] = dict(records.EXCEL_STATUS)
    return jsonify(out)


EDITABLE = ("status", "job_source", "job_location", "notes", "focus_industry", "position_type",
            "company_name", "job_title", "campaign", "deadline", "reply_status", "apply_account")


@bp.route("/api/records/<record_id>", methods=["PUT"])
def api_update_record(record_id):
    fields = {k: v for k, v in _body().items() if k in EDITABLE}
    if "reply_status" in fields:  # 手动纠正过的回复状态，以后查回复不再覆盖
        fields["reply_locked"] = True
    if not records.update(record_id, fields, by="本人"):
        return _err("记录不存在", 404)
    return jsonify({"ok": True})


@bp.route("/api/records/<record_id>", methods=["DELETE"])
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


@bp.route("/api/gmail-sync", methods=["POST"])
def api_gmail_sync():
    return _gmail_job(lambda log: pipeline.sync_gmail(progress=log))


@bp.route("/api/check-replies", methods=["POST"])
def api_check_replies():
    d = _body()
    return _gmail_job(lambda log: pipeline.refresh_replies(campaign=d.get("campaign"), progress=log,
                                                         record_ids=d.get("record_ids")))


@bp.route("/api/export-excel")
def api_export_excel():
    # 导出到临时文件、读进内存再删掉，不碰桌面的 Excel 镜像（后台保存时也在写它）
    with tempfile.TemporaryDirectory(prefix="jobapply-") as tmp:
        d = records.load_all()   # 带上申请：「下一步·截止」「网申实际提交」要用
        path = records.export_excel(d["records"], path=f"{tmp}/投递记录.xlsx", applications=d.get("applications"))
        data = open(path, "rb").read()
    return send_file(io.BytesIO(data), as_attachment=True, download_name="投递记录.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
