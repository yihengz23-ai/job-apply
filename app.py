#!/usr/bin/env python3
"""求职投递管理系统 — Flask Web App"""

import os
import sys
import json
import traceback
from flask import Flask, render_template, jsonify, request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from apply import (
    load_records, save_records, add_record, update_record_status,
    update_record_field, delete_record, analyze_jd_with_claude,
    validate_result, send_gmail, get_gmail_service,
    save_sent_record, gmail_full_sync,
    fetch_url_content, detect_jobs_in_content,
    RESUME_PATH, REPORT_PATH, SENDER_EMAIL,
)

app = Flask(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 页面路由
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tunnel-url")
def api_tunnel_url():
    """获取 Cloudflare Tunnel 公网链接"""
    try:
        with open("/tmp/tunnel_url.txt") as f:
            return jsonify({"url": f.read().strip()})
    except FileNotFoundError:
        return jsonify({"url": ""})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 端点
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route("/api/records")
def api_records():
    """获取所有投递记录"""
    records = load_records()
    # 按 sent_at 倒序
    records.sort(key=lambda r: r.get("sent_at") or "0000-00-00", reverse=True)
    return jsonify(records)


@app.route("/api/stats")
def api_stats():
    """获取统计数据"""
    records = load_records()
    from datetime import datetime, timedelta
    now = datetime.now()
    week_ago = now - timedelta(days=7)

    total = len(records)
    sent = total
    companies = len(set(r.get("company_name", "") for r in records if r.get("company_name")))

    week_new = 0
    followup = 0
    for r in records:
        try:
            dt = datetime.strptime(r.get("sent_at", ""), "%Y-%m-%d %H:%M")
            if dt >= week_ago:
                week_new += 1
            if r.get("status") == "已投递" and dt < week_ago:
                followup += 1
        except ValueError:
            pass

    # 状态分布
    status_dist = {}
    for r in records:
        s = r.get("status", "已投递")
        status_dist[s] = status_dist.get(s, 0) + 1

    # 公司性质分布（归一化，VC/PE 双类的看岗位来判断）
    type_dist = {}
    for r in records:
        t = (r.get("company_type", "") or "").strip()
        tl = t.lower()
        job = (r.get("job_title", "") or "").lower()
        industry = (r.get("focus_industry", "") or "").lower()
        if not t:
            cat = "其他"
        elif "fa" in tl or "投行" in tl or "并购" in tl:
            cat = "FA/投行"
        elif "vc" in tl and "pe" in tl:
            # 双类基金：看岗位和行业判断偏哪边
            if any(k in job + industry for k in ["早期", "风险", "创投", "seed", "天使"]):
                cat = "VC"
            elif any(k in job + industry for k in ["并购", "投后", "成长", "buyout"]):
                cat = "PE"
            else:
                cat = "PE"  # 默认偏 PE（大多数双币基金的实习偏 PE 方向）
        elif "vc" in tl or "风险投资" in tl or "创投" in tl:
            cat = "VC"
        elif "pe" in tl or "股权" in tl or "私募" in tl:
            cat = "PE"
        elif "券商" in tl:
            cat = "券商"
        elif any(k in tl for k in ["互联网", "大厂", "科技"]):
            cat = "互联网/科技"
        else:
            cat = "其他"
        type_dist[cat] = type_dist.get(cat, 0) + 1

    # 地点分布（归一化到城市）
    loc_dist = {}
    for r in records:
        loc = (r.get("job_location", "") or "").strip()
        if not loc:
            continue
        # 归一化
        if "上海" in loc:
            city = "上海"
        elif "北京" in loc:
            city = "北京"
        elif "深圳" in loc:
            city = "深圳"
        elif "杭州" in loc:
            city = "杭州"
        elif "香港" in loc:
            city = "香港"
        elif "线上" in loc or "远程" in loc:
            city = "远程"
        else:
            city = loc.split("/")[0].split("·")[0].strip()[:4]
        loc_dist[city] = loc_dist.get(city, 0) + 1

    # 行业分布（归一化同义词）
    _industry_map = {
        "前沿科技": "科技", "硬科技": "科技", "云计算": "AI",
        "算力": "AI", "物理AI": "AI", "集成电路": "半导体",
        "EV产业链": "新能源", "新材料": "新能源",
        "食品饮料": "消费", "消费出海": "消费",
        "基础设施": "房地产", "工业": "制造",
        "先进制造": "制造", "全周期": "",
    }
    industry_dist = {}
    for r in records:
        ind = (r.get("focus_industry", "") or "").strip()
        if ind and ind != "综合":
            for part in ind.split("/"):
                p = part.strip()
                p = _industry_map.get(p, p)  # 归一化
                if p:
                    industry_dist[p] = industry_dist.get(p, 0) + 1

    # 每日投递（最近30天）— 含每日公司列表
    daily = {}      # {day: count}
    daily_detail = {}  # {day: [公司名列表]}
    for r in records:
        try:
            dt = datetime.strptime(r.get("sent_at", ""), "%Y-%m-%d %H:%M")
            day = dt.strftime("%m/%d")
            daily[day] = daily.get(day, 0) + 1
            daily_detail.setdefault(day, []).append(r.get("company_name", ""))
        except ValueError:
            pass

    return jsonify({
        "total": total, "sent": sent,
        "companies": companies, "week_new": week_new, "followup": followup,
        "status_dist": status_dist, "type_dist": type_dist,
        "loc_dist": loc_dist, "industry_dist": industry_dist,
        "daily": daily, "daily_detail": daily_detail,
    })


@app.route("/api/fetch-url", methods=["POST"])
def api_fetch_url():
    """抓取 URL 内容并识别岗位"""
    if not request.json or not request.json.get("url"):
        return jsonify({"error": "请输入 URL"}), 400
    url = request.json["url"].strip()
    try:
        page = fetch_url_content(url)
        content = page.get("content", "")
        if len(content) < 30:
            return jsonify({"error": "页面内容为空或无法抓取"}), 400
        # 判断是否有多个岗位（检测"岗位一""岗位二"或多个邮箱等信号）
        import re
        multi_signals = re.findall(r'岗位[一二三四五六七八九十\d]|职位[一二三四五六七八九十\d]|方向[一二三四五六七八九十\d]|[一二三四]、.*?(?:实习|岗位|招聘|方向)', content)
        email_matches = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', content)
        has_multi = len(multi_signals) >= 2 or len(set(email_matches)) >= 2

        if has_multi:
            # 多岗位：调 Claude 识别
            jobs = detect_jobs_in_content(content)
        else:
            # 单岗位：直接用全文
            email_match = email_matches[0] if email_matches else ""
            jobs = [{"title": page.get("title", ""), "jd_text": content,
                     "email": email_match,
                     "location": "", "company_intro": ""}]
        return jsonify({
            "title": page.get("title", ""),
            "source": page.get("source", ""),
            "source_label": page.get("source_label", ""),
            "publish_date": page.get("publish_date", ""),
            "content_length": len(content),
            "jobs": jobs,
            "raw_content": content[:10000],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """分析 JD"""
    if not request.json:
        return jsonify({"error": "请求体为空"}), 400
    jd_text = request.json.get("jd_text", "")
    if len(jd_text.strip()) < 50:
        return jsonify({"error": "JD 内容过短（至少 50 字）"}), 400
    try:
        result = analyze_jd_with_claude(jd_text)
        errors = validate_result(result)
        # 重复检测（公司名 或 收件邮箱 匹配即报重复）
        duplicate = None
        company = result.get("company_name", "")
        to_email = (result.get("to_email", "") or "").strip().lower()
        for r in load_records():
            if (company and r.get("company_name") == company) or \
               (to_email and r.get("to_email", "").lower() == to_email):
                duplicate = {"company": r.get("company_name", company),
                             "sent_at": r.get("sent_at", "")}
                break
        return jsonify({"result": result, "errors": errors, "duplicate": duplicate})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/send", methods=["POST"])
def api_send():
    """直接发送邮件"""
    data = request.json
    if not data:
        return jsonify({"error": "请求体为空"}), 400
    result = data.get("result", {})
    jd_text = data.get("jd_text", "")
    to_email = data.get("to_email", result.get("to_email", ""))
    if not to_email:
        return jsonify({"error": "收件人邮箱为空"}), 400
    try:
        svc = get_gmail_service()
        attach_resume = data.get("attach_resume", True)
        msg_id = send_gmail(
            service=svc,
            to_email=to_email,
            subject=data.get("subject", result.get("email_subject", "")),
            body=data.get("body", result.get("email_body", "")),
            resume_path=RESUME_PATH if attach_resume else None,
            resume_filename=data.get("resume_filename",
                                     result.get("resume_filename", "YOUR_NAME-YOUR_UNIVERSITY-简历.pdf")),
            attach_report=result.get("attach_report", False),
            report_filename=result.get("report_filename", ""),
            cc_email=data.get("cc_email", result.get("cc_email", "")),
        )
        edited = {**result,
                  "to_email": to_email,
                  "email_subject": data.get("subject", result.get("email_subject", "")),
                  "email_body": data.get("body", result.get("email_body", "")),
                  }
        save_sent_record(edited, jd_text)
        return jsonify({"ok": True, "message_id": msg_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/record", methods=["POST"])
def api_record():
    """记录投递（网站投递，不发邮件）"""
    data = request.json
    if not data:
        return jsonify({"error": "请求体为空"}), 400
    result = data.get("result", {})
    jd_text = data.get("jd_text", "")
    try:
        save_sent_record(result, jd_text)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/gmail-sync", methods=["POST"])
def api_gmail_sync():
    """Gmail 全量同步"""
    logs = []
    try:
        result = gmail_full_sync(progress_fn=lambda msg: logs.append(msg))
        return jsonify({"ok": True, "result": result, "logs": logs})
    except Exception as e:
        return jsonify({"error": str(e), "logs": logs}), 500


@app.route("/api/records/<record_id>", methods=["PUT"])
def api_update_record(record_id):
    """更新记录"""
    if not request.json:
        return jsonify({"error": "请求体为空"}), 400
    # 检查记录是否存在
    if not any(r.get("id") == record_id for r in load_records()):
        return jsonify({"error": "记录不存在"}), 404
    data = request.json
    if "status" in data:
        update_record_status(record_id, data["status"])
    for field in ("job_source", "job_location", "notes", "focus_industry"):
        if field in data:
            update_record_field(record_id, field, data[field])
    return jsonify({"ok": True})


@app.route("/api/records/<record_id>", methods=["DELETE"])
def api_delete_record(record_id):
    """删除记录"""
    delete_record(record_id)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
