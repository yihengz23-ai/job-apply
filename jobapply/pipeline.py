"""流程编排（网页面板和剪贴板模式共用）：分析 → 自动修正 → 检查 → 发送/存草稿 → 记录。"""

from datetime import datetime

from . import checks, config, gmail_client, llm, records, resume


class Blocked(Exception):
    """有 error 级问题且没有强制发送。"""

    def __init__(self, issues):
        super().__init__("；".join(i["msg"] for i in issues if i["level"] == "error"))
        self.issues = issues


def _texts():
    return (config.PROFILE_PATH.read_text(encoding="utf-8"),
            config.RULES_PATH.read_text(encoding="utf-8"))


def _resume_public(rs):
    return {k: rs.get(k) for k in ("ok", "error", "pages", "zh_pages", "en_pages", "grad_problems", "sha", "size_kb")}


def review(result, jd_text, *, source_label="", exclude_id=None):
    profile, rules = _texts()
    emails = (result.get("to_emails") or []) + (result.get("cc_emails") or [])
    related = records.find_related(result.get("company_name", ""), emails, exclude_id=exclude_id)
    rs = resume.resume_status()
    issues = checks.run(result, jd_text, related=related, resume_status=rs, profile_text=profile,
                        rules_text=rules, source_label=source_label)
    return {"issues": issues, "related": related, "resume": _resume_public(rs)}


def analyze(jd_text, *, source_label="", target_job="", position_hint="", resume_hint="", extra=""):
    if len((jd_text or "").strip()) < 50:
        raise ValueError("JD 内容太短（至少 50 字），请粘贴完整的招聘信息。")
    result, meta = llm.analyze_jd(jd_text, source_label=source_label, target_job=target_job,
                                  position_hint=position_hint, resume_hint=resume_hint, extra=extra)
    if not result.get("is_jd", True):
        return {"ok": False, "error": "这段内容看起来不是招聘信息：" + (result.get("not_jd_reason") or ""), "meta": meta}
    if position_hint:
        result["position_type"] = position_hint
    if resume_hint:
        result["resume_version"] = resume_hint
    result["attach_resume"] = True
    fixes = checks.autofix(result, jd_text)
    return {"ok": True, "result": result, "fixes": fixes, "meta": meta,
            **review(result, jd_text, source_label=source_label)}


def _normalize_edits(result):
    """用户在界面上改过的内容：只做安全的格式整理，不改措辞。"""
    result["to_emails"] = checks.split_emails(result.get("to_emails"))
    result["cc_emails"] = [e for e in checks.split_emails(result.get("cc_emails")) if e not in result["to_emails"]]
    en_default = config.RESUME_DEFAULT_EN
    zh_default = en_default if result.get("resume_version") == "英文" else config.RESUME_DEFAULT_ZH
    result["resume_filename"] = checks.sanitize_filename(result.get("resume_filename"), zh_default)
    if result.get("resume_version") == "中英两份":
        result["resume_filename_en"] = checks.sanitize_filename(result.get("resume_filename_en"), en_default)
    if result.get("attach_report"):
        result["report_filename"] = checks.sanitize_filename(result.get("report_filename"), config.REPORT_DEFAULT_NAME)
    result["email_subject"] = (result.get("email_subject") or "").strip()
    result["email_body"] = (result.get("email_body") or "").replace("\r\n", "\n").strip() + "\n"
    return result


def campaign_for(sent_at):
    return config.OLD_CAMPAIGN if (sent_at or "") < config.OLD_CAMPAIGN_BEFORE else config.CURRENT_CAMPAIGN


def _record_fields(result, jd_text, *, source_label, source_url, target_job, publish_date, source_type):
    return dict(
        company_name=result.get("company_name", ""), company_type=result.get("company_type", ""),
        job_title=result.get("job_title", ""), job_location=result.get("job_location", ""),
        focus_industry=result.get("focus_industry", ""), position_type=result.get("position_type", ""),
        job_post_date=result.get("job_post_date") or publish_date or "", deadline=result.get("deadline", ""),
        apply_url=result.get("apply_url", ""), apply_channel=result.get("apply_channel", ""),
        to_email="; ".join(result.get("to_emails") or []), cc_email="; ".join(result.get("cc_emails") or []),
        subject=result.get("email_subject", ""), email_body=result.get("email_body", ""),
        jd_text=jd_text, job_source=source_label, source_url=source_url, target_job=target_job,
        language=result.get("jd_language", ""), source_type=source_type, model=config.CLAUDE_MODEL,
    )


def deliver(result, jd_text, *, mode="send", force=False, source_label="", source_url="",
            target_job="", publish_date="", source_type="网页面板"):
    """mode: send=直接发送；draft=存为 Gmail 草稿。"""
    result = _normalize_edits(dict(result))
    rev = review(result, jd_text, source_label=source_label)
    errors = [i for i in rev["issues"] if i["level"] == "error"]
    if errors and not force:
        raise Blocked(rev["issues"])

    attachments, att_meta = [], []
    version = result.get("resume_version") or "双语"
    if result.get("attach_resume", True):
        for name, data in resume.build_resume_files(version, result["resume_filename"],
                                                    result.get("resume_filename_en", "")):
            attachments.append((name, data))
            att_meta.append({"kind": "简历", "filename": name, "version": version})
    if result.get("attach_report"):
        attachments.append((result["report_filename"], config.REPORT_PATH.read_bytes()))
        att_meta.append({"kind": "研究样本", "filename": result["report_filename"], "version": config.REPORT_PATH.stem})

    kw = dict(to=result["to_emails"], cc=result["cc_emails"], subject=result["email_subject"],
              body=result["email_body"], attachments=attachments)
    if mode == "draft":
        ids = gmail_client.create_draft(**kw)
        send_mode, status = "草稿", "草稿"
    else:
        ids = gmail_client.send(**kw)
        send_mode, status = "发送", "已投递"

    out = {"ok": True, "mode": send_mode, "attachments": [a["filename"] for a in att_meta], **ids}
    try:
        rec = records.new_record(
            **_record_fields(result, jd_text, source_label=source_label, source_url=source_url,
                             target_job=target_job, publish_date=publish_date, source_type=source_type),
            status=status, send_mode=send_mode, attach_report=bool(result.get("attach_report")),
            attachments=att_meta, resume_version=version if result.get("attach_resume", True) else "未附简历",
            resume_sha=rev["resume"].get("sha", ""), gmail_message_id=ids.get("message_id", ""),
            gmail_thread_id=ids.get("thread_id", ""), gmail_draft_id=ids.get("draft_id", ""),
            issues_at_send=[i["msg"] for i in rev["issues"] if i["level"] in ("error", "warn")],
        )
        out["record_id"] = records.add(rec)
    except Exception as e:  # 邮件已经发出，记录失败要明确告诉用户
        out["record_error"] = f"邮件已{send_mode}，但写入投递记录失败：{e}"
    return out


def record_web_application(result, jd_text, *, source_label="", source_url="", target_job="",
                           publish_date="", source_type="网页面板"):
    """网申 / 链接投递：不发邮件，只记录。"""
    result = _normalize_edits(dict(result))
    rec = records.new_record(
        **_record_fields(result, jd_text, source_label=source_label, source_url=source_url,
                         target_job=target_job, publish_date=publish_date, source_type=source_type),
        status="已投递", send_mode="未发邮件", attach_report=False, resume_version="网申（按网站要求上传）",
    )
    return {"ok": True, "record_id": records.add(rec)}


def sync_gmail(progress=print):
    recs = records.load()
    candidates = gmail_client.find_unrecorded_sent(recs, progress=progress)
    progress(f"其中 {len(candidates)} 封不在记录里，交给 AI 判断是不是投递…")
    added, others = 0, []
    for i in range(0, len(candidates), 10):
        batch = candidates[i:i + 10]
        for e, c in zip(batch, llm.classify_sent_emails(batch)):
            if not c.get("is_application"):
                others.append(e["id"])
                continue
            rec = records.new_record(
                company_name=c.get("company_name", ""), company_type=c.get("company_type", ""),
                job_title=c.get("job_title", ""), job_location=c.get("job_location", ""),
                position_type=c.get("position_type", ""), to_email="; ".join(checks.split_emails(e["to"])),
                cc_email="; ".join(checks.split_emails(e["cc"])), subject=e["subject"],
                email_body=e["body"][:6000], sent_at=e["sent_at"], created_at=e["sent_at"],
                source_type="Gmail同步", send_mode="发送", gmail_message_id=e["id"],
                gmail_thread_id=e["thread_id"], campaign=campaign_for(e["sent_at"]),
                resume_version="（Gmail 同步，未知）")
            records.add(rec)
            added += 1
        progress(f"已判断 {min(i + 10, len(candidates))}/{len(candidates)}")
    gmail_client.mark_processed(others)
    progress(f"完成：新增 {added} 条投递记录")
    return {"candidates": len(candidates), "added": added}


def refresh_replies(campaign=None, progress=print, record_ids=None):
    recs = records.filter_campaign(records.load(), campaign)
    if record_ids:
        recs = [r for r in recs if r.get("id") in set(record_ids)]
    progress(f"检查 {len(recs)} 条记录的回复…")
    updates = gmail_client.check_replies(recs, progress=progress)

    def _apply(all_recs):
        n = 0
        for r in all_recs:
            upd = updates.get(r.get("id"))
            if upd:
                if r.get("reply_status") == "有回复" and upd.get("reply_status") in ("自动回复", "退信"):
                    upd = {k: v for k, v in upd.items() if not k.startswith("reply_") or k == "reply_checked_at"}
                r.update(upd)
                n += 1 if upd.get("reply_status") == "有回复" else 0
        return n
    replied = records.mutate(_apply)
    progress(f"完成：{replied} 条有回复 / 来信")
    return {"checked": len(recs), "replied": replied, "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
