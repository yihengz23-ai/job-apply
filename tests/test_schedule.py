"""晚上点发送 → 明早自动发（定时发送），以及简历默认只发中文页。不调 AI、不碰 Gmail、不发信。"""

from datetime import datetime

import pytest

import app as panel
from jobapply import checks, config, gmail_client, jobqueue, llm, pipeline
from tests.test_review import add_item, result

LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}
JD = "投资实习生招聘，每周实习5天，简历发送至 hr@abc-capital.com。" * 2
DAY = datetime(2026, 10, 8, 10, 5)      # 北京时间白天
NIGHT = datetime(2026, 10, 8, 23, 30)   # 北京时间晚上


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(jobqueue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(jobqueue, "_maybe_notify", lambda: None)
    monkeypatch.setattr(jobqueue, "_notify", lambda *a: None)
    monkeypatch.setattr(jobqueue, "_submit", lambda item_id: None)
    monkeypatch.setattr(jobqueue.time, "sleep", lambda s: None)
    return jobqueue


@pytest.fixture
def clean_review(monkeypatch):
    """发信前检查不看本机文件（公开版没有简历 PDF）：默认全过，个别测试自己换成有问题。"""
    state = {"issues": []}
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": state["issues"], "related": [], "resume": {}})
    return state


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_deliver(result, jd, **kw):
        calls.append(kw)
        return {"ok": True, "mode": "发送", "record_id": "r1", "attachments": []}
    monkeypatch.setattr(pipeline, "deliver", fake_deliver)
    return calls


# ── 什么时候发 ─────────────────────────────────────────────

@pytest.mark.parametrize("now,expect", [
    (datetime(2026, 10, 7, 23, 30), "2026-10-08 10:00"),   # 晚上 → 第二天 10:00
    (datetime(2026, 10, 8, 1, 15), "2026-10-08 10:00"),    # 凌晨 → 当天 10:00
    (datetime(2026, 10, 8, 10, 0), "2026-10-09 10:00"),
])
def test_next_send_time(now, expect):
    assert jobqueue.next_send_time(now) == expect


@pytest.mark.parametrize("hour,night", [(21, True), (23, True), (0, True), (6, True), (7, False), (10, False), (20, False)])
def test_is_night(hour, night):
    assert checks.is_night(datetime(2026, 10, 8, hour, 0)) is night


# ── 定时 / 取消 / 到点发出 ───────────────────────────────────

def test_schedule_and_unschedule(q):
    iid = add_item(q, status="需处理")
    it = q.schedule(iid, result(email_subject="审核时改过的标题", to_emails="hr@abc-capital.com"), force=True,
                    send_at="2026-10-09 10:00")
    assert it["status"] == "已定时" and it["send_force"] is True and it["edited"]["to_emails"] == ["hr@abc-capital.com"]
    assert q.schedule(iid, result()) is None                    # 已经定时了，不能再定一次
    assert q.unschedule(iid) and q._get(iid)["status"] == "需处理"  # 退回原来的状态
    assert not q.unschedule(iid)


def test_due_mail_sent_in_daytime_exactly_as_confirmed(q, sent):
    iid = add_item(q)
    q.schedule(iid, result(email_subject="确认过的那封"), force=True, send_at="2026-10-08 10:00")
    later = add_item(q)
    q.schedule(later, result(), send_at="2026-10-09 10:00")
    out = q.send_due(now=DAY)
    assert len(out["sent"]) == 1 and len(sent) == 1 and sent[0]["force"] is True and sent[0]["source_type"] == "定时发送"
    assert q._get(iid)["status"] == "已发送" and q._get(iid)["record_id"] == "r1"
    assert q._get(later)["status"] == "已定时"                   # 没到点的不动


def test_overdue_at_night_waits_for_next_morning(q, sent):
    iid = add_item(q)
    q.schedule(iid, result(), send_at="2026-10-08 10:00")       # 电脑一整天没开，晚上才打开面板
    q.send_due(now=NIGHT)
    assert sent == [] and q._get(iid)["status"] == "已定时" and q._get(iid)["send_at"] == "2026-10-09 10:00"


@pytest.mark.parametrize("err,uncertain", [
    (gmail_client.GmailAuthError("授权过期"), False),
    (gmail_client.SendUncertain("读超时"), True),
    (gmail_client.SendFailed("连不上"), False),
])
def test_failed_scheduled_send_needs_you(q, monkeypatch, err, uncertain):
    iid = add_item(q)
    q.schedule(iid, result(), send_at="2026-10-08 10:00")

    def boom(*a, **k):
        raise err
    monkeypatch.setattr(pipeline, "deliver", boom)
    out = q.send_due(now=DAY)
    it = q._get(iid)
    assert out["failed"] and it["status"] == "需处理" and "定时发送" in it["error"]
    assert bool(it.get("send_uncertain")) is uncertain
    q.send_due(now=DAY)                                          # 失败的不会每 30 秒再试一遍
    assert q._get(iid)["status"] == "需处理"


def test_one_click_schedule_at_night(q, sent, clean_review):
    iid = add_item(q)
    out = q.process_ready("schedule")
    assert sent == [] and len(out["done"]) == 1 and q._get(iid)["status"] == "已定时"


def test_kit_edits_saved_while_scheduled(q):
    iid = add_item(q, apply_channel="邮箱+网申")
    q.schedule(iid, result(apply_channel="邮箱+网申"), send_at="2026-10-09 10:00")
    assert q.save_edits(iid, wangshen={"self_intro": "改过"})
    assert not q.save_edits(iid, result())                       # 邮件本身定时后不能再改（要改先取消定时）


# ── 面板接口 ───────────────────────────────────────────────

@pytest.fixture
def client(q, tmp_path, monkeypatch, clean_review):
    for k, v in (("RECORDS_PATH", tmp_path / "records.json"), ("BACKUP_DIR", tmp_path / "backups"),
                 ("MATERIALS_DIR", tmp_path), ("EXCEL_MIRROR_PATH", tmp_path / "mirror.xlsx")):
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(gmail_client, "auth_status", lambda: {"ok": True, "email": "me@example.com"})
    panel.app.config["TESTING"] = True
    return panel.app.test_client()


def test_api_schedule(client, monkeypatch):
    iid = add_item(jobqueue)
    body = {"result": result(), "jd_text": JD, "queue_id": iid}
    assert client.post("/api/schedule", json={**body, "queue_id": ""}, headers={**LOCAL, **XRW}).status_code == 400
    d = client.post("/api/schedule", json=body, headers={**LOCAL, **XRW}).get_json()
    assert d["ok"] and jobqueue._get(iid)["status"] == "已定时" and d["send_at"].endswith(config.SEND_AT)
    assert client.post("/api/schedule", json=body, headers={**LOCAL, **XRW}).status_code == 409   # 定过了
    assert client.post(f"/api/queue/{iid}/unschedule", json={}, headers={**LOCAL, **XRW}).get_json()["ok"]


def test_api_schedule_blocked_or_no_gmail(client, monkeypatch, clean_review):
    iid = add_item(jobqueue)
    clean_review["issues"] = [{"level": "error", "field": "body", "msg": "正文太短或为空。"}]
    bad = {"result": result(email_body="太短"), "jd_text": JD, "queue_id": iid}
    r = client.post("/api/schedule", json=bad, headers={**LOCAL, **XRW})
    assert r.status_code == 409 and r.get_json()["issues"] and jobqueue._get(iid)["status"] == "待审核"
    clean_review["issues"] = []
    monkeypatch.setattr(gmail_client, "auth_status", lambda: {"ok": False, "error": "授权过期"})
    r = client.post("/api/schedule", json={"result": result(), "jd_text": JD, "queue_id": iid}, headers={**LOCAL, **XRW})
    assert r.status_code == 401 and jobqueue._get(iid)["status"] == "待审核"   # 明早发不出去的不让定


def test_api_process_ready_accepts_schedule(client):
    add_item(jobqueue)
    d = client.post("/api/queue/process-ready", json={"mode": "schedule"}, headers={**LOCAL, **XRW}).get_json()
    assert len(d["done"]) == 1


def test_config_has_send_time(client):
    d = client.get("/api/config", headers=LOCAL).get_json()
    assert d["send_at"] == config.SEND_AT and d["night_hours"] == config.NIGHT_HOURS


# ── 简历默认只发中文页：双币基金、JD 要中英文时才发双语 ─────────────

@pytest.mark.parametrize("ai,company_type,jd,hint,expect", [
    ("双语", "人民币VC", JD, "", "中文"),
    ("双语", "美元VC", JD, "", "中文"),
    ("双语", "双币VC/PE", JD, "", "双语"),
    ("双语", "人民币VC", JD + "请附中英文简历。", "", "双语"),
    ("双语", "人民币VC", JD, "双语", "双语"),     # 你自己指定的不改
    ("英文", "美元VC", JD, "", "英文"),
])
def test_resume_defaults_to_chinese(monkeypatch, ai, company_type, jd, hint, expect):
    monkeypatch.setattr(llm, "analyze_jd", lambda jd, **kw: (result(resume_version=ai, company_type=company_type), {}))
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [], "related": [], "resume": {}})
    assert pipeline.analyze(jd, resume_hint=hint)["result"]["resume_version"] == expect


# ── 存过的草稿：明早从 Gmail 原样发出 ────────────────────────

def test_send_draft_posts_draft_id(monkeypatch):
    from tests.test_review import FakeResp, FakeSession
    sess = FakeSession(FakeResp(200, {"id": "m9", "threadId": "t9"}))
    monkeypatch.setattr(gmail_client, "_session", lambda: sess)
    assert gmail_client.send_draft("d1") == {"message_id": "m9", "thread_id": "t9"}
    url, kw = sess.calls[0]
    assert url.endswith("/gmail/v1/users/me/drafts/send") and kw["json"] == {"id": "d1"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    from jobapply import records
    for k, v in (("RECORDS_PATH", tmp_path / "records.json"), ("BACKUP_DIR", tmp_path / "backups"),
                 ("MATERIALS_DIR", tmp_path), ("EXCEL_MIRROR_PATH", tmp_path / "mirror.xlsx")):
        monkeypatch.setattr(config, k, v)
    return records


def test_saved_draft_sent_and_record_updated(store, monkeypatch):
    rid = store.add(store.new_record(company_name="A", send_mode="草稿", status="草稿", gmail_draft_id="d1", sent_at="2026-10-08 01:01"))
    monkeypatch.setattr(gmail_client, "send_draft", lambda did: {"message_id": "m9", "thread_id": "t9"})
    pipeline.send_saved_draft(rid)
    r = store.get(rid)
    assert r["send_mode"] == "发送" and r["status"] == "已投递" and r["gmail_message_id"] == "m9" and not r["gmail_draft_id"]
    assert r["sent_at"] != "2026-10-08 01:01"                     # 按真正发出的时间记
    with pytest.raises(gmail_client.SendFailed):
        pipeline.send_saved_draft(rid)                            # 已经发过了：不会再发一次


def test_scheduled_draft_flow(q, monkeypatch):
    iid = add_item(q, status="已存草稿")
    assert q.schedule_draft(iid) is None                          # 没有记录的不行
    q._update(iid, record_id="r1")
    assert q.schedule_draft(iid, send_at="2026-10-08 10:00")["status"] == "已定时"
    calls = []
    monkeypatch.setattr(pipeline, "send_saved_draft", lambda rid: calls.append(rid) or {"ok": True, "record_id": rid})
    monkeypatch.setattr(pipeline, "deliver", lambda *a, **k: pytest.fail("草稿不该重新写一封发"))
    q.send_due(now=DAY)
    assert calls == ["r1"] and q._get(iid)["status"] == "已发送"


def test_scheduled_draft_failure_goes_back_to_draft(q, monkeypatch):
    iid = add_item(q, status="已存草稿")
    q._update(iid, record_id="r1")
    q.schedule_draft(iid, send_at="2026-10-08 10:00")
    monkeypatch.setattr(pipeline, "send_saved_draft", lambda rid: (_ for _ in ()).throw(gmail_client.SendFailed("404 找不到草稿")))
    q.send_due(now=DAY)
    it = q._get(iid)
    assert it["status"] == "已存草稿" and "Gmail" in it["error"] and not it.get("send_draft")   # 不会变成「需处理」被再发一封新的
    q.schedule_draft(iid, send_at="2026-10-09 10:00")
    assert q.unschedule(iid) and q._get(iid)["status"] == "已存草稿"


def test_api_schedule_drafts(client):
    a = add_item(jobqueue, status="已存草稿")
    jobqueue._update(a, record_id="r1")
    add_item(jobqueue, status="待审核")
    d = client.post("/api/queue/schedule-drafts", json={}, headers={**LOCAL, **XRW}).get_json()
    assert d["scheduled"] == 1 and jobqueue._get(a)["status"] == "已定时" and jobqueue._get(a)["send_draft"] is True
