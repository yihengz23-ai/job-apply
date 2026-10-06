"""第二轮复核（再找出来的问题）的回归测试。不调 AI、不碰 Gmail、不发信。"""

import json
from datetime import datetime, timedelta

import pytest

import app as panel
from jobapply import checks, config, fetch, gmail_client, jobqueue, pipeline, records
from tests.test_review import FakeGmail, add_item, msg, result

N = config.CANDIDATE_NAME
LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}
JD = "投资实习生招聘，简历发送至 hr@abc-capital.com"


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(jobqueue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(jobqueue, "_maybe_notify", lambda: None)
    monkeypatch.setattr(jobqueue, "_submit", lambda item_id: None)
    monkeypatch.setattr(jobqueue.time, "sleep", lambda s: None)
    return jobqueue


@pytest.fixture
def store(tmp_path, monkeypatch):
    for k, v in (("RECORDS_PATH", tmp_path / "records.json"), ("BACKUP_DIR", tmp_path / "backups"),
                 ("MATERIALS_DIR", tmp_path), ("EXCEL_MIRROR_PATH", tmp_path / "mirror.xlsx")):
        monkeypatch.setattr(config, k, v)
    return tmp_path


# ── 称呼 / 占位 / 数字 / 变形邮箱 ──────────────────────────

@pytest.mark.parametrize("first,expect", [
    ("王经理，您好！我是某某，硕士在读。", "您好，我是某某，硕士在读。"),     # 不再变成「您好，您好！」
    ("听说贵司氛围很好，所以想申请。", "听说贵司氛围很好，所以想申请。"),     # 不是称呼，原样保留
    ("我是投资经理，想申请。", "我是投资经理，想申请。"),
])
def test_greeting_edge_lines(first, expect):
    r = result(email_body=f"{first}\n\n简历见附件。\n\n{N}\n")
    checks.autofix(r, JD)
    assert r["email_body"].splitlines()[0] == expect


def test_english_name_needs_whole_word():
    jd = "Online application, market research role. Email ma.lin@abc-capital.com"
    r = result(jd_language="英文", contact_in_jd="", email_body="Dear Ms. Ma,\n\nBody long enough here.\n\nAlex\n")
    checks.autofix(r, jd)
    assert r["email_body"].startswith("Hello,")


@pytest.mark.parametrize("subject,bad", [
    (f"[2027届实习]-{N}-某大学", False), (f"[Full-time Application] {N}", False),
    (f"[应聘岗位]-{N}-某大学", True), (f"[您的姓名]-投资实习生", True),
])
def test_placeholder_fullmatch(subject, bad):
    jd = "标题格式：[2027届实习]-姓名-学校 / [Full-time Application] Name / [应聘岗位]-[您的姓名]，简历发 hr@abc-capital.com"
    issues = [i for i in checks.run(result(email_subject=subject), jd) if i["level"] == "error" and "占位" in i["msg"]]
    assert bool(issues) == bad


def test_graduation_numbers_allowed():
    issues = checks.run(result(email_subject=f"投资实习生-{N}-某大学-27届-2027-06毕业"), JD, profile_text="每周5天，6个月")
    assert not [i for i in issues if "数字" in i["msg"]]


def test_dot_then_at_and_no_fake_other_emails():
    jd = "Send CV to hr at abc[dot]com. Learn more and look at jobs.abc.com"
    exact, deob = checks.jd_emails(jd)
    assert "hr@abc.com" in deob
    issues = [i for i in checks.run(result(to_emails=["hr@abc.com"]), jd) if i["field"] == "to"]
    assert [i["level"] for i in issues] == ["warn"]   # 只有「变形写法请核对」，没有「JD 里还有别的邮箱」


# ── 分享链接 ────────────────────────────────────────────

def test_share_links_with_resume_words_in_title():
    t = "【实习】某资本2027届投资实习生（简历直投合伙人） https://mp.weixin.qq.com/s/AAA"
    assert fetch.share_links(t) == ["https://mp.weixin.qq.com/s/AAA"]
    many = "\n".join(f"某资本招聘·投递通道{i} https://mp.weixin.qq.com/s/{i}" for i in range(3))
    assert len(fetch.share_links(many)) == 3
    assert fetch.share_links("投资实习生，简历发 hr@abc.com，网申 https://a.com/x") == []


# ── 队列：「可能已发出」的标记不会被重做清掉 ─────────────────

def test_uncertain_flag_survives_retry(q, monkeypatch):
    iid = add_item(q)
    q.claim(iid)
    q.mark_uncertain(iid, "超时")
    assert q.retry(iid)
    assert q._get(iid)["send_uncertain"] is True
    q._update(iid, status="待审核", analysis={"result": result(), "issues": [], "fixes": [], "meta": {}})
    monkeypatch.setattr(pipeline, "deliver", lambda *a, **k: pytest.fail("不该再发"))
    out = q.process_ready("send")
    assert out["done"] == [] and "不确定" in out["skipped"][0]


def test_resume_pending_marks_uncertain(q):
    iid = add_item(q)
    q.claim(iid)
    q.resume_pending()
    assert q._get(iid)["send_uncertain"] is True


def test_kit_edits_saved_while_waiting_for_wangshen(q):
    iid = add_item(q, apply_channel="邮箱+网申")
    q.mark_done(iid, "已发送", "r1")
    assert q.save_edits(iid, wangshen={"self_intro": "改过"})
    assert not q.save_edits(iid, result())             # 邮件已发：不能再改邮件
    q.mark_ws_recorded(iid)
    assert not q.save_edits(iid, wangshen={"self_intro": "再改"})


# ── 查回复 ─────────────────────────────────────────────

def test_manual_reply_status_not_overwritten(store, monkeypatch):
    rid = records.add(records.new_record(company_name="A", send_mode="发送", to_email="hr@a.com",
                                         reply_status="自动回复", reply_locked=True))
    monkeypatch.setattr(gmail_client, "check_replies", lambda recs, progress=None: {rid: {"reply_status": "有回复", "reply_checked_at": "x"}})
    pipeline.refresh_replies(campaign="全部", progress=lambda m: None)
    assert records.get(rid)["reply_status"] == "自动回复"


def test_partial_results_kept_when_auth_expires(store, monkeypatch):
    rid = records.add(records.new_record(company_name="A", send_mode="发送", to_email="hr@a.com"))

    def half(recs, progress=None):
        raise gmail_client.PartialAuthError("授权过期", {rid: {"reply_status": "有回复", "reply_checked_at": "x"}})
    monkeypatch.setattr(gmail_client, "check_replies", half)
    with pytest.raises(gmail_client.GmailAuthError):
        pipeline.refresh_replies(campaign="全部", progress=lambda m: None)
    assert records.get(rid)["reply_status"] == "有回复"


def test_unsent_draft_does_not_pick_up_other_mail():
    t0 = datetime(2026, 10, 7, 9, 30)
    svc = FakeGmail(thread=[msg("d", t0, config.SENDER_EMAIL, "草稿", labels=("DRAFT",))],
                    inbox=[msg("n", t0 + timedelta(hours=2), "hr@abc-capital.com", "面试邀请")])
    upd = gmail_client._check_one(svc, {"id": "1", "send_mode": "草稿", "to_email": "hr@abc-capital.com",
                                        "sent_at": "2026-10-07 09:30", "gmail_thread_id": "t"})
    assert "reply_status" not in upd and svc.queries == []


def test_short_company_name_searched_in_sender():
    svc = FakeGmail()
    gmail_client._check_one(svc, {"id": "1", "send_mode": "未发邮件", "company_name": "腾讯", "sent_at": "2026-10-07 09:30"})
    assert 'from:"腾讯"' in svc.queries[0]


def test_sent_time_prefers_timestamp():
    ts = datetime(2026, 10, 7, 9, 30).timestamp()
    assert gmail_client.sent_time({"sent_ts": ts, "sent_at": "1999-01-01 00:00"}) == datetime.fromtimestamp(ts)
    assert gmail_client.sent_time({"sent_at": "2026-10-07 09:30"}) == datetime(2026, 10, 7, 9, 30)
    assert records.new_record()["sent_ts"] > 0


def test_token_written_with_private_permissions(tmp_path):
    p = tmp_path / "token.json"
    gmail_client._write_atomic(p, '{"a": 1}')
    assert p.read_text() == '{"a": 1}' and oct(p.stat().st_mode & 0o777) == "0o600"
    assert [x.name for x in tmp_path.iterdir()] == ["token.json"]   # 没留下临时文件


# ── 面板接口 ───────────────────────────────────────────

@pytest.fixture
def client(q, store):
    panel.app.config["TESTING"] = True
    return panel.app.test_client()


def test_process_ready_requires_mode(client):
    assert client.post("/api/queue/process-ready", json={}, headers={**LOCAL, **XRW}).status_code == 400


def test_send_failed_releases_item(client, monkeypatch):
    iid = add_item(jobqueue)

    def refused(*a, **k):
        raise gmail_client.SendFailed("连不上 Gmail，这封没有发出")
    monkeypatch.setattr(pipeline, "deliver", refused)
    r = client.post("/api/send", json={"result": result(), "jd_text": JD, "queue_id": iid, "mode": "send"}, headers={**LOCAL, **XRW})
    it = jobqueue._get(iid)
    assert r.status_code == 502 and it["status"] == "待审核" and "没有发出" in it["error"] and not it.get("send_uncertain")


def test_manual_reply_status_locks(client):
    rid = records.add(records.new_record(company_name="A"))
    client.put(f"/api/records/{rid}", json={"reply_status": "自动回复"}, headers={**LOCAL, **XRW})
    assert records.get(rid)["reply_locked"] is True


def test_config_survives_corrupt_records(client):
    config.RECORDS_PATH.write_text("{坏掉的", encoding="utf-8")
    d = client.get("/api/config", headers=LOCAL).get_json()
    assert d["records_error"] and d["campaign"] in d["campaigns"]


def test_export_excel_leaves_no_temp_files(client, tmp_path, monkeypatch):
    import tempfile as tf
    records.add(records.new_record(company_name="A"))
    monkeypatch.setattr(tf, "tempdir", str(tmp_path / "t"))
    (tmp_path / "t").mkdir()
    r = client.get("/api/export-excel", headers=LOCAL)
    assert r.status_code == 200 and r.data[:2] == b"PK" and list((tmp_path / "t").iterdir()) == []


# ── 剪贴板模式 ─────────────────────────────────────────

def test_clipboard_reports_uncertain_send(monkeypatch, capsys):
    import sys
    import apply as clipboard
    monkeypatch.setattr(sys, "argv", ["apply.py"])
    monkeypatch.setattr(clipboard, "main", lambda: (_ for _ in ()).throw(gmail_client.SendUncertain("可能已经发出")))
    monkeypatch.setattr(clipboard, "notify", lambda *a, **k: None)
    with pytest.raises(SystemExit) as e:
        exec(compile(open(clipboard.__file__, encoding="utf-8").read().split('if __name__ == "__main__":')[1]
                     .replace("\n    ", "\n"), "apply_main", "exec"), clipboard.__dict__)
    assert e.value.code == 1 and "可能已经发出" in capsys.readouterr().out
