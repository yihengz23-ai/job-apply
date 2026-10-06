"""第二轮复查（两位审查员找到的问题）的回归测试。不调 AI、不碰 Gmail、不发信。"""

import json
import subprocess
from datetime import datetime, timedelta

import httplib2
import pytest
import requests
import urllib3
from googleapiclient.errors import HttpError

import app as panel
from jobapply import checks, config, fetch, gmail_client, jobqueue, llm, pipeline, records

N = config.CANDIDATE_NAME
LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}
JD = "投资实习生招聘，简历发送至 hr@abc-capital.com"


def result(**kw):
    r = {"company_name": "某资本", "job_title": "投资实习生", "jd_language": "中文", "apply_channel": "邮箱",
         "to_emails": ["hr@abc-capital.com"], "cc_emails": [], "apply_url": "", "contact_in_jd": "",
         "jd_rules": {}, "resume_version": "双语", "resume_filename": f"{N}-简历.pdf", "attach_report": False,
         "email_subject": f"投资实习生申请 - {N}",
         "email_body": f"您好，\n\n我是{N}，硕士在读，两周内可到岗，每周5天，可以连续实习6个月以上。\n\n简历见附件。\n\n{N}\n"}
    r.update(kw)
    return r


# ── 称呼修正只动称呼，不删正文 ─────────────────────────────

def test_greeting_and_body_on_same_line_keeps_body():
    r = result(email_body=f"王总您好，我是{N}，硕士在读，两周内可到岗，申请贵司投资实习生岗位。\n\n简历见附件。\n\n{N}\n")
    checks.autofix(r, JD)
    assert r["email_body"].startswith(f"您好，我是{N}，硕士在读")


def test_first_line_without_greeting_untouched():
    body = f"我是{N}，某大学硕士在读，人在上海，两周内可到岗，申请投资实习生。\n\n简历见附件。\n\n{N}\n"
    r = result(email_body=body)
    assert checks.autofix(r, JD) == [] and r["email_body"] == body


def test_english_greeting_line_keeps_sentence():
    r = result(jd_language="英文", email_body="Hi Kevin, I am Alex, a master's student.\n\nResume attached.\n\nAlex\n")
    checks.autofix(r, "Send your CV to hr@abc-capital.com")
    assert r["email_body"].startswith("Hello, I am Alex")


@pytest.mark.parametrize("line", ["尊敬的王总：", "王总：", "王总您好，"])
def test_unsupported_name_replaced(line):
    r = result(email_body=f"{line}\n\n正文足够长足够长足够长足够长足够长足够长。\n\n{N}\n")
    checks.autofix(r, JD)
    assert r["email_body"].startswith("您好，")


def test_name_matching_jd_contact_kept():
    jd = JD + "\n联系人：王女士"
    r = result(contact_in_jd="王女士", email_body=f"王总您好，\n\n正文足够长足够长足够长足够长足够长。\n\n{N}\n")
    checks.autofix(r, jd)
    assert r["email_body"].startswith("王总您好，")
    r2 = result(jd_language="英文", contact_in_jd="Jessica", email_body="Hi Jack,\n\nBody text long enough here.\n\nAlex\n")
    checks.autofix(r2, "Contact Jessica at hr@abc-capital.com")
    assert r2["email_body"].startswith("Hello,")   # Jack ≠ Jessica（以前只比首字母会放过）


# ── 占位符 / 邮箱 / 数字 / 链接 ───────────────────────────

def test_template_field_left_unfilled_is_error():
    jd = "邮件标题：[应聘岗位]-[姓名]-[学校]，简历发 hr@abc-capital.com"
    r = result(email_subject=f"[应聘岗位]-{N}-某大学")
    assert [i for i in checks.run(r, jd) if i["level"] == "error" and "占位" in i["msg"]]


def test_at_deobfuscation_does_not_break_real_email():
    jd = "Please contact our team lead at jane.doe@abc-capital.com for details."
    issues = [i for i in checks.run(result(to_emails=["jane.doe@abc-capital.com"]), jd) if i["field"] == "to"]
    assert issues == []
    assert "hr@abc-capital.com" in checks.jd_emails("send to hr at abc-capital.com.")[1]


def test_other_emails_in_jd_listed():
    jd = "北京团队 bj@abc-capital.com；上海团队 sh@abc-capital.com"
    infos = [i for i in checks.run(result(to_emails=["sh@abc-capital.com"]), jd) if i["field"] == "to"]
    assert [i["level"] for i in infos] == ["info"] and "bj@abc-capital.com" in infos[0]["msg"]


def test_numbers_in_subject_are_checked_and_rules_dont_count():
    r = result(email_subject=f"投资实习生-{N}-可实习37个月")
    issues = checks.run(r, JD, rules_text="示例：每周37天")
    assert any("37" in i["msg"] for i in issues if i["field"] == "body")


@pytest.mark.parametrize("raw,clean", [
    ("https://jobs.feishu.cn/abc，截止10月31日", "https://jobs.feishu.cn/abc"),
    ("见链接 https://a.com/x。", "https://a.com/x"),
    ("1.5", ""), ("www.mokahr.com", "https://www.mokahr.com"),
])
def test_safe_url_edges(raw, clean):
    assert checks.safe_url(raw) == clean


# ── 公众号抓取 / 分享文本 ─────────────────────────────────

def test_email_split_across_lines_rejoined():
    assert fetch._join_split_emails("投递邮箱：hr\n@abc-capital.com\n截止") == "投递邮箱：hr@abc-capital.com\n截止"
    assert fetch._join_split_emails("hr@abc-capital\n.com") == "hr@abc-capital.com"


def test_share_links():
    assert fetch.share_links("【招聘】某资本投资实习生 https://mp.weixin.qq.com/s/AbC") == ["https://mp.weixin.qq.com/s/AbC"]
    short_jd = "岗位职责：行业研究。投递：网申 https://a.com/x 或官网 https://b.com/y"
    assert fetch.share_links(short_jd) == []
    assert jobqueue.split_input(short_jd) == [("text", short_jd)]


# ── 队列 ───────────────────────────────────────────────

@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(jobqueue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(jobqueue, "_maybe_notify", lambda: None)
    monkeypatch.setattr(jobqueue, "_submit", lambda item_id: None)
    monkeypatch.setattr(jobqueue.time, "sleep", lambda s: None)
    return jobqueue


def add_item(q, status="待审核", **kw):
    it = q._new_item("text", "JD" * 30)
    it.update(status=status, jd_text=JD, rev="r1", analysis={"result": result(**kw), "issues": [], "fixes": [], "meta": {}})
    items = q._load()
    items.append(it)
    q._save(items)
    return it["id"]


def test_process_ready_sends_latest_edit(q, monkeypatch):
    iid = add_item(q)
    stale = q.list_items()                              # 一键处理开始时的快照
    q.save_edits(iid, result(email_body="用户刚改过的正文"))  # 处理过程中用户又改了
    monkeypatch.setattr(q, "list_items", lambda: stale)
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": []})
    sent = []
    monkeypatch.setattr(pipeline, "deliver", lambda r, jd, **kw: sent.append(r["email_body"]) or {"mode": "发送", "record_id": "r"})
    q.process_ready("send")
    assert sent == ["用户刚改过的正文"]


def test_uncertain_send_not_retried(q, monkeypatch):
    iid = add_item(q)
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": []})
    calls = []

    def timeout(*a, **k):
        calls.append(1)
        raise gmail_client.SendUncertain("发送结果不确定（超时）")
    monkeypatch.setattr(pipeline, "deliver", timeout)
    q.process_ready("send")
    it = q._get(iid)
    assert it["status"] == "需处理" and "不确定" in it["error"]
    q.process_ready("send")                              # 再点一次一键处理：不会再发
    assert len(calls) == 1


def test_queue_guards(q):
    iid = add_item(q, status="排队中")
    assert q.retry(iid) is False                         # 排队中再点重做会处理两遍
    assert q.save_edits(iid, result()) is False          # 只有待审核 / 需处理能改
    jid = add_item(q)
    assert q.save_edits(jid, result(), rev="旧页面的") is False   # 重做过的条目，旧页面不能再写
    assert q.save_edits(jid, result(), rev="r1") is True
    q.claim(jid)
    assert q.delete(jid) is False                        # 发送中不能删


def test_same_title_jobs_get_location(q, monkeypatch):
    monkeypatch.setattr(fetch, "fetch_url", lambda u: {"content": "岗位一 a@x.com 岗位二 b@x.com" * 5, "title": "汇总",
                                                       "source_label": "号", "url": u})
    monkeypatch.setattr(llm, "detect_jobs", lambda text: [{"title": "投资实习生", "location": "北京"},
                                                          {"title": "投资实习生", "location": "上海"}])
    seen = []
    monkeypatch.setattr(pipeline, "analyze", lambda jd, **kw: seen.append(kw["target_job"]) or {"ok": False, "error": "x"})
    it = q._new_item("url", "https://mp.weixin.qq.com/s/x")
    q._save([it])
    q._process(it["id"])
    siblings = [x for x in q._load() if x["id"] != it["id"]]
    assert seen == ["投资实习生（北京）"] and siblings[0]["target_job"] == "投资实习生（上海）"


# ── 发送结果不确定：去「已发送」里找 ─────────────────────────

class FakeReq:
    def __init__(self, exc=None, value=None):
        self.exc, self.value = exc, value

    def execute(self, num_retries=0):
        if self.exc:
            raise self.exc
        return self.value


def http_error(status):
    return HttpError(httplib2.Response({"status": status}), b"{}")


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code, self._payload, self.text = status, payload or {}, json.dumps(payload or {})

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, outcome):
        self.outcome, self.posts = outcome, 0

    def post(self, url, json=None, timeout=None):
        self.posts += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def conn_error(reason):
    return requests.exceptions.ConnectionError(urllib3.exceptions.MaxRetryError(None, "/", reason))


@pytest.mark.parametrize("outcome,expect", [
    (requests.exceptions.ReadTimeout("read timed out"), gmail_client.SendUncertain),       # 请求已到 Gmail
    (requests.exceptions.ChunkedEncodingError("broken"), gmail_client.SendUncertain),
    (requests.exceptions.ConnectionError("Connection aborted. RemoteDisconnected"), gmail_client.SendUncertain),
    (FakeResp(503), gmail_client.SendUncertain),
    (requests.exceptions.ConnectTimeout("connect timed out"), gmail_client.SendFailed),     # 根本没连上
    (conn_error(urllib3.exceptions.NewConnectionError(None, "refused")), gmail_client.SendFailed),
    (requests.exceptions.ProxyError("proxy down"), gmail_client.SendFailed),
    (FakeResp(400, {"error": {"message": "Invalid To header"}}), gmail_client.SendFailed),
    (FakeResp(401), gmail_client.GmailAuthError),
])
def test_post_once_classifies_and_never_retries(monkeypatch, outcome, expect):
    sess = FakeSession(outcome)
    monkeypatch.setattr(gmail_client, "_session", lambda: sess)
    with pytest.raises(expect):
        gmail_client._post_once("/messages/send", {"raw": "x"})
    assert sess.posts == 1


def test_post_once_ok(monkeypatch):
    monkeypatch.setattr(gmail_client, "_session", lambda: FakeSession(FakeResp(200, {"id": "m1", "threadId": "t1"})))
    assert gmail_client.send(to=["a@b.com"], cc=[], subject="s", body="b", attachments=[]) == {"message_id": "m1", "thread_id": "t1"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    for k, v in (("RECORDS_PATH", tmp_path / "records.json"), ("BACKUP_DIR", tmp_path / "backups"),
                 ("MATERIALS_DIR", tmp_path), ("EXCEL_MIRROR_PATH", tmp_path / "mirror.xlsx")):
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(pipeline.resume, "resume_status", lambda: {"ok": True, "text": "", "grad_problems": []})
    monkeypatch.setattr(pipeline.resume, "build_resume_files", lambda *a: [("简历.pdf", b"%PDF")])
    return tmp_path


def test_deliver_records_when_found_in_sent(store, monkeypatch):
    def timeout(**kw):
        raise gmail_client.SendUncertain("TimeoutError")
    monkeypatch.setattr(gmail_client, "send", timeout)
    monkeypatch.setattr(gmail_client, "find_sent", lambda to, subject, since: {"message_id": "m1", "thread_id": "t1"})
    out = pipeline.deliver(result(), JD, mode="send")
    rec = records.get(out["record_id"])
    assert rec["gmail_thread_id"] == "t1" and rec["send_mode"] == "发送"


def test_deliver_uncertain_when_not_found(store, monkeypatch):
    def timeout(**kw):
        raise gmail_client.SendUncertain("TimeoutError")
    monkeypatch.setattr(gmail_client, "send", timeout)
    monkeypatch.setattr(gmail_client, "find_sent", lambda *a: None)
    with pytest.raises(gmail_client.SendUncertain, match="Gmail「已发送」"):
        pipeline.deliver(result(), JD, mode="send")
    assert records.load() == []


def test_deliver_rejects_unknown_mode(store):
    with pytest.raises(ValueError):
        pipeline.deliver(result(), JD, mode="Draft")


def test_merge_is_idempotent(store):
    rid = records.add(records.new_record(company_name="某资本", send_mode="发送"))
    for _ in range(2):
        pipeline.record_web_application(result(), "JD", kit={"platform": "飞书"}, record_id=rid)
    assert records.get(rid)["notes"].count("已同时网申") == 1


def test_migrate_corrupt_file_is_friendly(store):
    config.RECORDS_PATH.write_text("{坏掉的", encoding="utf-8")
    with pytest.raises(records.RecordsCorrupt):
        records.migrate()


def test_trend_keys_have_year(store):
    records.add(records.new_record(company_name="A", sent_at="2026-12-31 10:00"))
    records.add(records.new_record(company_name="B", sent_at="2027-01-02 10:00"))
    days = list(records.stats(records.load())["daily"])
    assert sorted(days) == ["2026-12-31", "2027-01-02"]


# ── 查回复 ─────────────────────────────────────────────

@pytest.mark.parametrize("subject,snippet,headers,kind", [
    # 系统确认信：条件句里的「邀请您参加面试」不算邀请
    ("【某资本】感谢您的投递", "简历通过筛选后，我们会邀请您参加面试", {}, "自动回复"),
    ("感谢您的投递", "简历筛选通过者将在5个工作日内收到面试通知", {"auto-submitted": "auto-generated"}, "自动回复"),
    ("Thank you for your application", "Should we decide to make you an offer, we'll be in touch", {}, "自动回复"),
    # 系统发的真邀请
    ("您已通过筛选，诚邀您参加面试", "", {"auto-submitted": "auto-generated"}, "有回复"),
    ("某资本招聘通知", "诚邀您于周四参加线上面试，如时间不合适请回复本邮件", {"auto-submitted": "auto-generated"}, "有回复"),
    # 人写的回信：正文开头客套「感谢您的投递」也是回复
    ("Re: 投资实习生申请", "感谢您的投递！想请您本周四下午来公司聊一聊", {}, "有回复"),
    ("Re: Application", "Thank you for your application. Are you available for a quick call?", {}, "有回复"),
    ("Re: 实习申请", "该岗位已招满，感谢关注", {}, "有回复"),
    ("我们已收到您的申请", "请勿直接回复本邮件", {}, "自动回复"),
])
def test_reply_kind_edges(subject, snippet, headers, kind):
    assert gmail_client._reply_kind({"subject": subject, **headers}, snippet) == kind


def msg(mid, ts, frm, subject, labels=("INBOX",), **h):
    headers = [{"name": "From", "value": frm}, {"name": "Subject", "value": subject}]
    headers += [{"name": k, "value": v} for k, v in h.items()]
    return {"id": mid, "internalDate": str(int(ts.timestamp() * 1000)), "labelIds": list(labels),
            "snippet": "", "payload": {"headers": headers}}


class FakeGmail:
    """只实现查回复用到的几个接口。"""

    def __init__(self, thread=(), inbox=(), missing_thread=False):
        self.thread, self.inbox, self.missing_thread, self.queries = list(thread), {m["id"]: m for m in inbox}, missing_thread, []

    def users(self):
        return self

    def threads(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, **kw):
        if "metadataHeaders" in kw and id in self.inbox:
            return FakeReq(value=self.inbox[id])
        if self.missing_thread:
            return FakeReq(exc=http_error(404))
        return FakeReq(value={"messages": self.thread})

    def list(self, userId, q, maxResults=5, **kw):
        self.queries.append(q)
        return FakeReq(value={"messages": [{"id": i} for i in self.inbox]})


def test_thread_picks_real_reply_over_earlier_auto_reply():
    t0 = datetime(2026, 10, 7, 9, 30)
    svc = FakeGmail(thread=[
        msg("s", t0, config.SENDER_EMAIL, "投递", labels=("SENT",)),
        msg("a", t0 + timedelta(minutes=1), "hr@abc-capital.com", "自动回复：已收到", **{"Auto-Submitted": "auto-replied"}),
        msg("b", t0 + timedelta(days=1), "wang@abc-capital.com", "Re: 投递：方便明天聊一下吗"),
    ])
    upd = gmail_client._check_one(svc, {"id": "1", "send_mode": "发送", "to_email": "hr@abc-capital.com",
                                        "sent_at": "2026-10-07 09:30", "gmail_thread_id": "t"})
    assert upd["reply_status"] == "有回复" and upd["reply_from"] == "wang@abc-capital.com"


def test_new_mail_search_uses_timestamp_and_finds_morning_reply():
    sent = datetime(2026, 10, 7, 9, 30)
    svc = FakeGmail(thread=[msg("s", sent, config.SENDER_EMAIL, "投递", labels=("SENT",))],
                    inbox=[msg("n", sent + timedelta(hours=1, minutes=30), "zhang@abc-capital.com", "面试邀请")])
    upd = gmail_client._check_one(svc, {"id": "1", "send_mode": "发送", "to_email": "hr@abc-capital.com",
                                        "sent_at": "2026-10-07 09:30", "gmail_thread_id": "t"})
    assert upd["reply_status"] == "有回复"
    assert f"after:{int(sent.timestamp())}" in svc.queries[0]


def test_deleted_draft_is_not_a_network_failure(monkeypatch):
    svc = FakeGmail(missing_thread=True)
    monkeypatch.setattr(gmail_client, "get_service", lambda: svc)
    recs = [{"id": str(i), "send_mode": "草稿", "to_email": "hr@abc-capital.com", "sent_at": "2026-10-07 09:30",
             "gmail_thread_id": f"t{i}"} for i in range(6)]
    out = gmail_client.check_replies(recs)
    assert len(out) == 6 and all(u["gmail_thread_id"] == "" for u in out.values())


def test_draft_sent_from_gmail_keeps_manual_status():
    t0 = datetime(2026, 10, 7, 9, 30)
    svc = FakeGmail(thread=[msg("s", t0, config.SENDER_EMAIL, "投递", labels=("SENT",))])
    r = {"id": "1", "send_mode": "草稿", "status": "面试中", "to_email": "hr@abc-capital.com",
         "sent_at": "2026-10-07 09:00", "gmail_thread_id": "t"}
    upd = gmail_client._check_one(svc, r)
    assert upd["send_mode"] == "发送" and "status" not in upd


def test_company_mail_without_invite_is_just_mail():
    t0 = datetime(2026, 10, 7, 9, 30)
    svc = FakeGmail(inbox=[msg("n", t0 + timedelta(days=1), "events@abc-capital.com", "某资本：感谢你的申请，邀请你关注我们的年度峰会")])
    upd = gmail_client._check_one(svc, {"id": "1", "send_mode": "未发邮件", "company_name": "某某资本",
                                        "sent_at": "2026-10-07 09:30"})
    assert upd["reply_status"] in ("来信", "自动回复")
    assert "-from:zhipin.com" in svc.queries[0]


def test_new_reply_rank_does_not_downgrade(store, monkeypatch):
    rid = records.add(records.new_record(company_name="A", send_mode="发送", to_email="hr@a.com", reply_status="有回复"))
    monkeypatch.setattr(gmail_client, "check_replies", lambda recs, progress=None: {rid: {"reply_status": "来信", "reply_checked_at": "x"}})
    pipeline.refresh_replies(campaign="全部", progress=lambda m: None)
    assert records.get(rid)["reply_status"] == "有回复"


# ── 会员通道：不给 AI 任何工具和连接器 ─────────────────────────

def test_claude_code_call_has_no_tools(monkeypatch):
    seen = {}

    def fake_run(args, input, **kw):
        seen["args"] = args
        seen["msg"] = json.loads(input)
        out = json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": {"text": "ok"},
                          "usage": {}})
        return subprocess.CompletedProcess(args, 0, stdout='{"type":"system"}\n' + out + "\n", stderr="")
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    data, meta = llm._call_cc(system="s", content="c", schema={"type": "object"}, effort="low",
                              images=[(b"\x89PNG....", "image/png")])
    a = seen["args"]
    assert data == {"text": "ok"} and meta["backend"] == "会员额度"
    assert a[a.index("--tools") + 1] == "" and "--strict-mcp-config" in a and "--allowedTools" not in a
    assert seen["msg"]["message"]["content"][0]["type"] == "image"


# ── 面板接口 ───────────────────────────────────────────

@pytest.fixture
def client(q, store):
    panel.app.config["TESTING"] = True
    return panel.app.test_client()


def test_send_uncertain_marks_item_needs_check(client, monkeypatch):
    iid = add_item(jobqueue)

    def timeout(*a, **k):
        raise gmail_client.SendUncertain("发送结果不确定：请先到 Gmail「已发送」里确认")
    monkeypatch.setattr(pipeline, "deliver", timeout)
    r = client.post("/api/send", json={"result": result(), "jd_text": JD, "queue_id": iid, "mode": "send"}, headers={**LOCAL, **XRW})
    assert r.status_code == 504 and r.get_json()["uncertain"] and jobqueue._get(iid)["status"] == "需处理"


def test_send_mode_and_force_are_strict(client, monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "deliver", lambda *a, **k: seen.append(k) or {"ok": True, "mode": "草稿", "attachments": []})
    assert client.post("/api/send", json={"result": result(), "mode": "Draft"}, headers={**LOCAL, **XRW}).status_code == 400
    client.post("/api/send", json={"result": result(), "mode": "draft", "force": "false"}, headers={**LOCAL, **XRW})
    assert seen[0]["force"] is False and seen[0]["mode"] == "draft"


def test_gmail_jobs_one_at_a_time(client):
    assert panel._gmail_job_lock.acquire(blocking=False)
    try:
        assert client.post("/api/check-replies", json={}, headers={**LOCAL, **XRW}).status_code == 409
    finally:
        panel._gmail_job_lock.release()


def test_non_ascii_tunnel_key_is_403(client):
    tun = {"Host": "abc.trycloudflare.com", "Cf-Ray": "x"}
    assert client.get("/api/records?k=密钥", headers=tun).status_code == 403


def test_export_excel_does_not_touch_mirror(client, store):
    records.add(records.new_record(company_name="A"))
    mirror = config.EXCEL_MIRROR_PATH
    before = mirror.stat().st_mtime
    r = client.get("/api/export-excel", headers=LOCAL)
    assert r.status_code == 200 and r.data[:2] == b"PK" and mirror.stat().st_mtime == before


def test_record_merge_marks_queue_item(client):
    iid = add_item(jobqueue, apply_channel="邮箱+网申")
    rid = records.add(records.new_record(company_name="某资本", send_mode="发送"))
    jobqueue.mark_done(iid, "已发送", rid)
    d = client.post("/api/record", json={"result": result(), "jd_text": "JD", "queue_id": iid, "record_id": rid},
                    headers={**LOCAL, **XRW}).get_json()
    assert d["merged"] and jobqueue._get(iid)["ws_recorded"] is True and jobqueue._get(iid)["status"] == "已发送"
