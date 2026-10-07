"""流程编排（网页面板和剪贴板模式共用）：分析 → 自动修正 → 检查 → 发送/存草稿 → 记录。"""

import re
import time
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


def source_of(result, source_label=""):
    """招聘信息来源（只进自己的投递记录，不写进邮件）：抓链接时的公众号名 / 网站，没有就用 AI 从 JD 文字里认出来的。"""
    return (source_label or "").strip() or ((result or {}).get("source_name") or "").strip()


def review(result, jd_text, *, source_label="", exclude_id=None, publish_date=""):
    profile, rules = _texts()
    source_label = source_of(result, source_label)
    emails = (result.get("to_emails") or []) + (result.get("cc_emails") or [])
    related = records.find_related(result.get("company_name", ""), emails, exclude_id=exclude_id)
    rs = resume.resume_status()
    issues = checks.run(result, jd_text, related=related, resume_status=rs, profile_text=profile,
                        rules_text=rules, source_label=source_label, publish_date=publish_date)
    return {"issues": issues, "related": related, "resume": _resume_public(rs)}


# JD 明确要中英文 / 英文简历（「能快速阅读中英文材料」这种说的是能力，不算）
BILINGUAL_ASK = re.compile(r"(?:中英文?|英文|双语)(?:版|版本)?(?:的)?(?:简历|CV|resume)|(?:english|bilingual)\s+(?:cv|resume)"
                           r"|(?:cv|resume)s?\s+in\s+(?:english|both)", re.I)


def chinese_resume_by_default(result, jd_text):
    """简历默认只发中文页：双币基金、JD 要中英文时才发双语。改了返回 True。"""
    if (result.get("resume_version") == "双语" and result.get("company_type") != "双币VC/PE"
            and not BILINGUAL_ASK.search(jd_text or "")):
        result["resume_version"] = "中文"
        return True
    return False


def extra_path(a):
    """自己加的附件在 uploads/ 里的位置；不在 uploads/ 里（或文件没了）返回 None。"""
    try:
        p = (config.UPLOADS_DIR / str(a.get("path", ""))).resolve()
    except (OSError, ValueError):
        return None
    root = config.UPLOADS_DIR.resolve()
    return p if root in p.parents and p.is_file() else None


def attachment_names(result):
    """这封要带的附件文件名（给 AI 自查用：正文提到的附件必须都在里面）。"""
    names = []
    if result.get("attach_resume", True):
        names.append(result.get("resume_filename") or config.RESUME_DEFAULT_ZH)
        if result.get("resume_version") == "中英两份":
            names.append(result.get("resume_filename_en") or config.RESUME_DEFAULT_EN)
    if result.get("attach_report"):
        names.append(result.get("report_filename") or config.REPORT_DEFAULT_NAME)
    names += [a.get("name", "") for a in result.get("extra_attachments") or [] if a.get("name")]
    return names


def self_check(result, jd_text, *, notes=""):
    """写完自查：AI 当审稿人对着档案和 JD 挑错并直接改好（经历拼接、角色升级、编细节、提了没附的附件、篇幅……）。
    返回改动说明列表；网申岗位（没有邮件）不查。"""
    if not checks.split_emails(result.get("to_emails")) or not (result.get("email_body") or "").strip():
        return []
    try:
        out, _ = llm.self_review(jd_text, result, attachments=attachment_names(result), notes=notes)
    except llm.LLMError as e:
        return [f"AI 自查这次没跑成（{e}），按原稿"]
    changes = [c for c in out.get("changes") or [] if isinstance(c, dict)]
    if not changes:
        return []
    if (out.get("email_subject") or "").strip():
        result["email_subject"] = out["email_subject"].strip()
    if (out.get("email_body") or "").strip():
        result["email_body"] = out["email_body"].strip() + "\n"
    return [f"AI 自查改了：{c.get('problem', '')}（「{c.get('before', '')}」→「{c.get('after', '')}」）" for c in changes]


def analyze(jd_text, *, source_label="", target_job="", position_hint="", resume_hint="", report_hint="", extra="",
            publish_date=""):
    if len((jd_text or "").strip()) < 50:
        raise ValueError("JD 内容太短（至少 50 字），请粘贴完整的招聘信息。")
    position_hint = position_hint if position_hint in llm.POSITION_TYPES else ""
    resume_hint = resume_hint if resume_hint in llm.RESUME_VERSIONS else ""
    report_hint = report_hint if report_hint in llm.REPORT_HINTS else ""
    result, meta = llm.analyze_jd(jd_text, target_job=target_job, position_hint=position_hint,
                                  resume_hint=resume_hint, report_hint=report_hint,
                                  extra=extra.strip() if isinstance(extra, str) else "")
    if not result.get("is_jd", True):
        return {"ok": False, "error": "这段内容看起来不是招聘信息：" + (result.get("not_jd_reason") or ""), "meta": meta}
    if position_hint:
        result["position_type"] = position_hint
    if resume_hint:
        result["resume_version"] = resume_hint
    else:
        chinese_resume_by_default(result, jd_text)
    if report_hint:
        result["attach_report"] = report_hint == "附上"
    result["attach_resume"] = True
    names = [source_label, result.get("source_name") or ""]
    fixes = checks.autofix(result, jd_text, source=names)
    fixes += self_check(result, jd_text, notes=extra.strip() if isinstance(extra, str) else "")
    fixes += checks.autofix(result, jd_text, source=names)   # 自查改过的稿子再规范一遍（称呼、占位、链接）
    return {"ok": True, "result": result, "fixes": fixes, "meta": meta,
            **review(result, jd_text, source_label=source_label, publish_date=publish_date)}


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
    result["apply_url"] = checks.safe_url(result.get("apply_url"))
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
        jd_text=jd_text, job_source=source_of(result, source_label), source_url=source_url, target_job=target_job,
        language=result.get("jd_language", ""), source_type=source_type, model=config.CLAUDE_MODEL,
    )


UNCERTAIN_HINT = ("发送结果不确定（{err}）：邮件可能已经发出了。请先到 Gmail「已发送」里确认有没有这封："
                  "发出去了就不要再发（点看板的「Gmail 同步」把它补进记录）；确认没有再点发送。")
DRAFT_UNCERTAIN_HINT = ("存草稿结果不确定（{err}）：请到 Gmail「草稿」里看看有没有这封，有就不用再存了"
                        "（草稿发出后点看板的「Gmail 同步」会补进记录）。")


def deliver(result, jd_text, *, mode="send", force=False, source_label="", source_url="",
            target_job="", publish_date="", source_type="网页面板"):
    """mode: send=直接发送；draft=存为 Gmail 草稿（只认这两个值，写错就报错，不会默认去发）。"""
    if mode not in ("send", "draft"):
        raise ValueError(f"不认识的发送方式：{mode!r}")
    result = _normalize_edits(dict(result))
    rev = review(result, jd_text, source_label=source_label, publish_date=publish_date)
    errors = [i for i in rev["issues"] if i["level"] == "error"]
    if errors and not force:
        raise Blocked(rev["issues"])

    attachments, att_meta = [], []
    version = result.get("resume_version") or "中文"
    if result.get("attach_resume", True):
        for name, data in resume.build_resume_files(version, result["resume_filename"],
                                                    result.get("resume_filename_en", "")):
            attachments.append((name, data))
            att_meta.append({"kind": "简历", "filename": name, "version": version})
    if result.get("attach_report"):
        attachments.append((result["report_filename"], config.REPORT_PATH.read_bytes()))
        att_meta.append({"kind": "研究样本", "filename": result["report_filename"], "version": config.REPORT_PATH.stem})
    for a in result.get("extra_attachments") or []:   # 审核时自己加的（文章、作品、成绩单……）
        path = extra_path(a)
        if not path:
            raise Blocked([{"level": "error", "field": "attach", "msg": f"附件「{a.get('name', '')}」找不到了，重新加一次"}])
        attachments.append((a["name"], path.read_bytes()))
        att_meta.append({"kind": "其他", "filename": a["name"], "version": ""})

    kw = dict(to=result["to_emails"], cc=result["cc_emails"], subject=result["email_subject"],
              body=result["email_body"], attachments=attachments)
    if mode == "draft":
        try:
            ids = gmail_client.create_draft(**kw)
        except gmail_client.SendUncertain as e:
            raise gmail_client.SendUncertain(DRAFT_UNCERTAIN_HINT.format(err=e)) from e
        send_mode, status = "草稿", "草稿"
    else:
        t0 = time.time()
        try:
            ids = gmail_client.send(**kw)
        except gmail_client.SendUncertain as e:
            # 超时 / 断线：先去「已发送」里找一下，真发出去了就照常记录，找不到才让用户去确认（绝不自动重发）
            try:
                ids = gmail_client.find_sent(kw["to"], kw["subject"], t0)
            except Exception:
                ids = None
            if not ids:
                raise gmail_client.SendUncertain(UNCERTAIN_HINT.format(err=e)) from e
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


def is_wangshen(result):
    return result.get("apply_channel") in ("网申/链接", "邮箱+网申") or not checks.split_emails(result.get("to_emails"))


def send_saved_draft(record_id):
    """定时：把之前存的 Gmail 草稿发出去，记录改成「已发送」（发送时间按真正发出的时间）。"""
    rec = records.get(record_id) or {}
    if rec.get("send_mode") != "草稿" or not rec.get("gmail_draft_id"):
        raise gmail_client.SendFailed("找不到这封草稿的记录（可能已经在 Gmail 里发出或删掉了）")
    ids = gmail_client.send_draft(rec["gmail_draft_id"])
    records.update(record_id, {
        "send_mode": "发送", "sent_at": records.now_str(), "sent_ts": time.time(), "gmail_draft_id": "",
        "gmail_message_id": ids["message_id"], "gmail_thread_id": ids["thread_id"] or rec.get("gmail_thread_id", ""),
        "status": "已投递" if rec.get("status") in ("草稿", "", None) else rec["status"]})
    return {"ok": True, "record_id": record_id, **ids}


def wangshen(jd_text, *, result=None, source_label="", target_job=""):
    """生成网申资料包（AI 部分）。返回 (kit, meta)。"""
    return llm.wangshen_kit(jd_text, result=result, source_label=source_of(result, source_label), target_job=target_job)


KIT_KEYS = ("platform", "apply_steps", "self_intro_short", "self_intro", "why_this_role", "fit_points",
            "custom_answers", "notes")


def record_web_application(result, jd_text, *, source_label="", source_url="", target_job="",
                           publish_date="", source_type="网页面板", kit=None, upload_version="", record_id=""):
    """网申 / 链接投递：不发邮件，只记录（连同当时填表用的问答，面试前可以回看）。
    「邮箱+网申」的岗位邮件已经发过（有 record_id）：不新建记录，在原记录上补一笔「已同时网申」。"""
    result = _normalize_edits(dict(result))
    kit = {k: v for k, v in (kit or {}).items() if k in KIT_KEYS}
    version = upload_version or "中文"
    if record_id:
        def _merge(recs):
            for r in recs:
                if r.get("id") == record_id:
                    r.update(platform=kit.get("platform", "") or r.get("platform", ""), wangshen=kit or r.get("wangshen"))
                    if "已同时网申" not in (r.get("notes") or ""):  # 重复点 / 重复请求只记一次
                        r["notes"] = ((r.get("notes") or "") + f"\n[{records.now_str()}] 已同时网申（上传{version}简历）").strip()
                    return True
            return False
        if records.mutate(_merge):
            return {"ok": True, "record_id": record_id, "merged": True}
    rec = records.new_record(
        **_record_fields(result, jd_text, source_label=source_label, source_url=source_url,
                         target_job=target_job, publish_date=publish_date, source_type=source_type),
        status="已投递", send_mode="未发邮件", attach_report=False,
        resume_version=f"网申上传（{version}）", platform=kit.get("platform", ""), wangshen=kit,
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
                email_body=e["body"][:6000], sent_at=e["sent_at"], sent_ts=e.get("sent_ts", 0), created_at=e["sent_at"],
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
    auth_error = None
    try:
        updates = gmail_client.check_replies(recs, progress=progress)
    except gmail_client.PartialAuthError as e:  # 查到一半授权失效：先把查到的存上，再提示重新授权
        updates, auth_error = e.updates, e
    rank = gmail_client.REPLY_RANK

    def _apply(all_recs):
        n = 0
        for r in all_recs:
            upd = updates.get(r.get("id"))
            if upd:
                # 你在看板里手动改过回复状态的不动；已经记过更重要的（如「有回复」），后来的自动回复 / 来信不覆盖它
                if upd.get("reply_status") and (r.get("reply_locked")
                                                or rank.get(upd["reply_status"], 0) < rank.get(r.get("reply_status"), 0)):
                    upd = {k: v for k, v in upd.items() if not k.startswith("reply_") or k == "reply_checked_at"}
                r.update(upd)
                n += 1 if upd.get("reply_status") == "有回复" else 0
        return n
    replied = records.mutate(_apply)
    if auth_error:
        progress(f"授权中途失效，已保存前面查到的结果（{replied} 条有回复）")
        raise gmail_client.GmailAuthError(str(auth_error))
    progress(f"完成：{replied} 条有回复 / 来信")
    return {"checked": len(recs), "replied": replied, "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
